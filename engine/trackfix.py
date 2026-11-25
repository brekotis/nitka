"""Проверка и правка трека: выбросы от спуфинга GPS, разрывы сигнала, перерисовка участка, отмена, чистый GPX.

Выброс — трек вдруг улетает (к аэропорту, в чужой город, по кругу) и потом возвращается почти туда же,
откуда улетел. Такой кусок вырезается, края соединяются прямой — или участок перерисовывается вручную.
"""
import json, math, statistics, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr
from .classify import dist, length_km, piece_times, set_times, trip_tz, hm, uid

SPEED_MAX = {'пеший': 12, 'горный': 12, 'лыжный': 25, 'вело': 60, 'водный': 20}      # км/ч — быстрее группа не ходит
EDIT_ROLES = ('main', 'radial', 'alt', 'exit')
HIST_MAX = 8


def _pl(n, a, b, c):
    n = abs(n) % 100; m = n % 10
    return c if 11 <= n <= 14 else a if m == 1 else b if 2 <= m <= 4 else c

def _k(q):
    """Ключ точки: координаты до ~1 м."""
    return f'{q[0]:.5f},{q[1]:.5f}'

def _mark_manual(p, pts):
    """Эти точки поставил человек — проверка трека их больше не трогает."""
    m = list(p.get('man') or []); have = {_k(q) for q in m}
    for q in pts:
        if _k(q) not in have: m.append([round(q[0], 5), round(q[1], 5)]); have.add(_k(q))
    alive = {_k(q) for q in p['pts']}
    p['man'] = [q for q in m if _k(q) in alive][-5000:]

def _interp(vals, pos):
    """Пропуски (None) — по соседям, пропорционально пути pos."""
    known = [i for i, v in enumerate(vals) if v is not None]
    if not known: return None
    out = list(vals)
    for i in range(len(out)):
        if out[i] is not None: continue
        a = max((k for k in known if k < i), default=None); b = min((k for k in known if k > i), default=None)
        if a is None: out[i] = out[b]
        elif b is None: out[i] = out[a]
        else:
            f = (pos[i] - pos[a]) / (pos[b] - pos[a]) if pos[b] > pos[a] else 0.5
            out[i] = out[a] + (out[b] - out[a]) * f
    return out

TANGLE_R, TANGLE_PATH, TANGLE_RATIO = 60.0, 250.0, 4.0

def tangles(pts, skip_pt=lambda i: False):
    """«Клубки»: трек долго путается в круге 60 м (стоянка без времени в треке, плохой сигнал) — путь в нём
    в 4+ раза длиннее расстояния между его краями и не меньше 250 м. [(i, j, лишний путь, м)] — края i и j остаются."""
    n = len(pts); out = []; i = 0
    while i < n - 2:
        j = i
        while j + 1 < n and dist(pts[i][:2], pts[j + 1][:2]) < TANGLE_R: j += 1
        if j - i >= 6 and not any(skip_pt(k) for k in range(i, j + 1)):
            path = sum(dist(pts[k][:2], pts[k + 1][:2]) for k in range(i, j)); span = dist(pts[i][:2], pts[j][:2])
            if path >= TANGLE_PATH and path >= TANGLE_RATIO * max(span, 20.0):
                out.append((i, j, path - span)); i = j; continue
        i += 1
    return out

JOIN_M, JOIN_TAIL = 300.0, 400

def neighbours(proj):
    """Соседние по ходу маршрута куски, которые стыкуются (конец одного у начала другого): [(A, B)]."""
    ps = sorted((p for p in proj.get('pieces', []) if p.get('role') in ('main', 'radial') and len(p.get('pts') or []) >= 2),
                key=lambda p: (p.get('day') or 999, p.get('order') or 0))
    return [(a, b) for a, b in zip(ps, ps[1:]) if dist(a['pts'][-1][:2], b['pts'][0][:2]) < JOIN_M]

def junction_tangles(proj):
    """Клубки на стыке двух кусков (часы перезапустили на стоянке, на ночёвке начался новый файл):
    [(A, B, i0 — край в A, i1 — край в B, лишний путь, м)]."""
    out = []
    for A, B in neighbours(proj):
        a, b = A['pts'], B['pts']
        ta = max(0, len(a) - JOIN_TAIL); tail, head = a[ta:], b[:JOIN_TAIL]
        man = {_k(q) for q in (A.get('man') or []) + (B.get('man') or [])}
        same = len(tail) and len(head) and dist(tail[-1][:2], head[0][:2]) < 0.5      # конец A = начало B (так сохраняет редактор)
        chain = tail + (head[1:] if same else head); nA = len(tail); off = 1 if same else 0
        oks = set(A.get('okseg') or [])
        for i, j, extra in tangles(chain, lambda k: _k(chain[k]) in man):
            if i >= nA - 1 or j < nA: continue      # не через стык
            i0, i1 = ta + i, j - nA + off
            if f'tangle:{_k(a[i0])}' in oks or i1 >= len(b) - 1 or i0 < 1: continue
            out.append((A, B, i0, i1, extra))
    return out

def _untangle_join(A, B, i0, i1):
    """Спрямить клубок на стыке: A — до i0 и прямо к B[i1], B — с i1."""
    _remember(A); _remember(B)
    ta, tb = piece_times(A), piece_times(B)
    before = length_km(A['pts']) + length_km(B['pts'])
    A['pts'] = A['pts'][:i0 + 1] + [list(B['pts'][i1])]
    if ta: set_times(A, ta[:i0 + 1] + [max(ta[i0], tb[i1]) if tb else ta[-1]])
    B['pts'] = B['pts'][i1:]
    if tb: set_times(B, tb[i1:])
    for p in (A, B): p['km'] = round(length_km(p['pts']), 2)
    return before - A['km'] - B['km']

def untangle(proj, piece=None, i0=None, i1=None, piece2=None):
    """Спрямить клубок (i0, i1 — края; на стыке — i0 в куске piece, i1 в piece2) или все клубки во всех кусках
    (или в одном), в том числе на стыках. Шаг можно отменить."""
    done = 0; saved = 0.0
    if piece2:
        A = next((p for p in proj.get('pieces', []) if p['id'] == piece), None); B = next((p for p in proj.get('pieces', []) if p['id'] == piece2), None)
        try: i0, i1 = int(i0), int(i1)
        except (TypeError, ValueError): return dict(ok=False, msg='Не указан участок.')
        if not A or not B or not (0 < i0 < len(A['pts'])) or not (0 <= i1 < len(B['pts']) - 1):
            return dict(ok=False, msg='Куски изменились — обнови страницу.')
        saved = _untangle_join(A, B, i0, i1)
        return dict(ok=True, msg=f'Клубок на стыке «{A.get("name", "")}» и «{B.get("name", "")}» спрямлён, убрано {str(round(saved, 2)).replace(".", ",")} км. Не то — «Отменить правку» у обоих кусков.')
    if piece is None and i0 is None:
        for A, B, a, b, _ in junction_tangles(proj):
            if len(A['pts']) > a and len(B['pts']) > b + 1: saved += _untangle_join(A, B, a, b); done += 1
    for p in proj.get('pieces', []):
        if p.get('role') not in EDIT_ROLES or (piece and p['id'] != piece) or len(p.get('pts', [])) < 4: continue
        man = {_k(q) for q in p.get('man') or []}; oks = set(p.get('okseg') or [])
        pts = p['pts']
        if i0 is not None:
            T = [(int(i0), int(i1), 0.0)]
        else:
            T = [t for t in tangles(pts, lambda k: _k(pts[k]) in man) if f'tangle:{_k(pts[t[0]])}' not in oks]
        T = [t for t in T if 0 <= t[0] < t[1] < len(pts) and t[1] - t[0] >= 2]
        if not T: continue
        _remember(p); ts = piece_times(p)
        before = length_km(pts)
        drop = set(k for a, b, _ in T for k in range(a + 1, b))
        p['pts'] = [q for k, q in enumerate(pts) if k not in drop]
        if ts: set_times(p, [t for k, t in enumerate(ts) if k not in drop])
        p['km'] = round(length_km(p['pts']), 2); saved += before - p['km']; done += len(T)
    if not done: return dict(ok=False, msg='Клубков не нашлось.')
    return dict(ok=True, msg=f'Спрямлено: {done} {_pl(done, "клубок", "клубка", "клубков")}, убрано {str(round(saved, 2)).replace(".", ",")} км лишнего пути. Не то — «Отменить правку» у куска.')

def check(proj):
    """Находки по всем кускам: dict(kind='excursion'|'gap', piece, name, day, i0, i1, km, at, lat, lon)."""
    vmax = SPEED_MAX.get(proj.get('meta', {}).get('kind'), 40)
    tz = trip_tz(proj, next((p['pts'][0][1] for p in proj.get('pieces', []) if p.get('pts')), None))
    out = []
    J = junction_tangles(proj)
    covered = {}      # кусок → [(от, до)] — эти точки уже в клубке на стыке
    for A, B, a, b, extra in J:
        covered.setdefault(A['id'], []).append((a, len(A['pts']) - 1)); covered.setdefault(B['id'], []).append((0, b))
        ta = piece_times(A)
        out.append(dict(piece=A['id'], piece2=B['id'], name=f'{A.get("name", "")} → {B.get("name", "")}', day=A.get('day'), at=hm(ta[a], tz) if ta else None,
                        lat=A['pts'][a][0], lon=A['pts'][a][1], kind='tangle', i0=a, i1=b, km=round(extra / 1000, 2),
                        pts=len(A['pts']) - 1 - a + b, join=True))
    for p in proj.get('pieces', []):
        pts = p.get('pts') or []
        if p.get('role') not in EDIT_ROLES or len(pts) < 4: continue
        ts = piece_times(p); n = len(pts)
        man = {_k(q) for q in p.get('man') or []}; oks = set(p.get('okseg') or [])
        skip = lambda i: _k(pts[i]) in man or _k(pts[i + 1]) in man or f'{_k(pts[i])}|{_k(pts[i + 1])}' in oks
        seg = [dist(pts[i][:2], pts[i + 1][:2]) for i in range(n - 1)]
        med = statistics.median(seg) or 1.0
        big_m = max(300.0, 20 * med)      # записанный трек идёт через 10–30 м: пропуск в 300 м — уже потеря сигнала
        i = 0
        while i < n - 1:
            fast = ts and seg[i] > 200 and seg[i] / max(1.0, ts[i + 1] - ts[i]) * 3.6 > vmax
            if (seg[i] < big_m and not fast) or skip(i): i += 1; continue
            a = pts[i][:2]; back = None
            for j in range(i + 1, min(n - 1, i + 600)):
                if dist(pts[j + 1][:2], a) < max(300.0, 3 * med): back = j; break
            base = dict(piece=p['id'], name=p.get('name', ''), day=p.get('day'), at=hm(ts[i], tz) if ts else None, lat=a[0], lon=a[1])
            if back is not None:
                far = max(dist(pts[k][:2], a) for k in range(i + 1, back + 1))
                if far > 500:
                    out.append(dict(base, kind='excursion', i0=i + 1, i1=back, km=round(far / 1000, 1), pts=back - i))
                    i = back + 1; continue
            if seg[i] >= big_m:      # долгий отрезок без возврата — пропал сигнал (или трек так и нарисован)
                out.append(dict(base, kind='gap', i0=i, i1=i + 1, km=round(seg[i] / 1000, 2),
                                mins=round((ts[i + 1] - ts[i]) / 60) if ts else None))
            i += 1
        for a, b, extra in tangles(pts, lambda k: _k(pts[k]) in man):
            if f'tangle:{_k(pts[a])}' in oks or any(x <= b and a <= y for x, y in covered.get(p['id'], [])): continue
            out.append(dict(piece=p['id'], name=p.get('name', ''), day=p.get('day'), at=hm(ts[a], tz) if ts else None, lat=pts[a][0], lon=pts[a][1],
                            kind='tangle', i0=a, i1=b, km=round(extra / 1000, 2), pts=b - a - 1))
    order = {'excursion': 0, 'gap': 1, 'tangle': 2}
    out.sort(key=lambda f: (order[f['kind']], f['day'] or 999))
    return out


def _remember(p):
    """Перед правкой: исходный трек (один раз) и шаг для «отменить»."""
    snap = dict(pts=p['pts'], t0=p.get('t0'), tm=p.get('tm'))
    if 'orig' not in p: p['orig'] = snap
    p['hist'] = (p.get('hist') or [])[-(HIST_MAX - 1):] + [snap]

def _restore(p, snap):
    p['pts'] = snap['pts']
    if snap.get('tm'): p['t0'], p['tm'] = snap['t0'], snap['tm']
    else: p.pop('t0', None); p.pop('tm', None)
    p['km'] = round(length_km(p['pts']), 2)


def edit(proj, d):
    """Правка куска: cut — вырезать точки i0..i1 (края соединяются прямой); redraw — между точками i0 и i1 новый путь;
    undo — шаг назад; reset — вернуть исходный трек."""
    p = next((x for x in proj.get('pieces', []) if x['id'] == d.get('piece')), None)
    if not p: return dict(ok=False, msg='Кусок не найден — обнови страницу.')
    act = d.get('action')
    if act == 'undo':
        if not p.get('hist'): return dict(ok=False, msg='Отменять нечего.')
        _restore(p, p['hist'].pop()); return dict(ok=True, msg='Последняя правка отменена.')
    if act == 'reset':
        if not p.get('orig'): return dict(ok=False, msg='Этот кусок не правился.')
        _restore(p, p['orig']); p.pop('orig', None); p.pop('hist', None); return dict(ok=True, msg='Кусок — как был при загрузке.')
    pts = p['pts']; n = len(pts); ts = piece_times(p)
    try: i0, i1 = sorted((int(d['i0']), int(d['i1'])))
    except (KeyError, TypeError, ValueError): return dict(ok=False, msg='Не указан участок.')
    if not (0 <= i0 < i1 < n): return dict(ok=False, msg='Участок вне куска — обнови страницу.')
    if act == 'cut':
        if i0 == 0 and i1 == n - 1: return dict(ok=False, msg='Так вырежется весь кусок — лучше отметь его «не использовать».')
        _remember(p)
        keep = pts[:i0] + pts[i1 + 1:]; kt = (ts[:i0] + ts[i1 + 1:]) if ts else None
        if len(keep) < 2: return dict(ok=False, msg='Почти весь кусок — выброс? Проверь на карте.')
        p['pts'] = keep; set_times(p, kt) if kt else None; p['km'] = round(length_km(keep), 2)
        return dict(ok=True, msg=f'Вырезано: {i1 - i0 + 1} {_pl(i1 - i0 + 1, "точка", "точки", "точек")}. Края соединены прямой.')
    if act == 'redraw':
        try: new = [[round(float(a), 6), round(float(b), 6)] for a, b in (d.get('pts') or [])][:3000]
        except (TypeError, ValueError): return dict(ok=False, msg='Неверные точки.')
        _remember(p)
        a, b = pts[i0], pts[i1]
        ea, eb = (a[2] if len(a) > 2 else None), (b[2] if len(b) > 2 else None)
        L = [0.0]; chain = [a[:2]] + new + [b[:2]]
        for x, y in zip(chain, chain[1:]): L.append(L[-1] + dist(x, y))
        mid = []
        for k, q in enumerate(new, 1):      # высота и время новых точек — по долям пути между краями
            f = L[k] / L[-1] if L[-1] else 0.5
            mid.append(q + ([round(ea + (eb - ea) * f, 1)] if ea is not None and eb is not None else []))
        p['pts'] = pts[:i0 + 1] + mid + pts[i1:]
        if ts:
            tmid = [ts[i0] + (ts[i1] - ts[i0]) * (L[k] / L[-1] if L[-1] else 0.5) for k in range(1, len(new) + 1)]
            set_times(p, ts[:i0 + 1] + tmid + ts[i1:])
        p['km'] = round(length_km(p['pts']), 2); _mark_manual(p, mid or [pts[i0], pts[i1]])
        return dict(ok=True, msg=f'Участок перерисован: {len(new)} {_pl(len(new), "точка", "точки", "точек")}.')
    return dict(ok=False, msg='Неизвестное действие.')


def replace_many(proj, d):
    """Сохранение из редактора нескольких кусков сразу (правка через стык). Сначала проверяем все, потом пишем."""
    items = d.get('items') or []
    if not items: return dict(ok=False, msg='Нечего сохранять.')
    by = {p['id']: p for p in proj.get('pieces', [])}
    if any(it.get('piece') not in by for it in items): return dict(ok=False, msg='Кусок не найден — обнови страницу.')
    if any(len(it.get('pts') or []) < 2 for it in items): return dict(ok=False, msg='В куске должно остаться хотя бы две точки.')
    extra = {}
    for it in items:      # точка соседнего куска, ставшая концом этого (стык), сохраняет свою высоту и время
        p = by[it['piece']]; ts = piece_times(p)
        for i, q in enumerate(p['pts']): extra.setdefault(_k(q), (q[2] if len(q) > 2 else None, ts[i] if ts else None))
    for A, B in neighbours(proj):
        tb = piece_times(B); q = B['pts'][0]; extra.setdefault(_k(q), (q[2] if len(q) > 2 else None, tb[0] if tb else None))
    res = [replace(proj, it, extra) for it in items]
    bad = next((r for r in res if not r['ok']), None)
    if bad: return bad
    if len(items) == 1: return res[0]
    total = sum(by[it['piece']]['km'] for it in items)
    return dict(ok=True, msg=f'Сохранено кусков: {len(items)}, вместе {str(round(total, 2)).replace(".", ",")} км. Отменить — «Отменить правку» у каждого.')

def replace(proj, d, extra=None):
    """Сохранение из редактора: кусок целиком. Точки, которые не менялись, сохраняют высоту и время;
    новые и передвинутые получают их по соседям (пропорционально пути) и считаются поставленными человеком."""
    p = next((x for x in proj.get('pieces', []) if x['id'] == d.get('piece')), None)
    if not p: return dict(ok=False, msg='Кусок не найден — обнови страницу.')
    try: new = [[round(float(q[0]), 6), round(float(q[1]), 6)] for q in (d.get('pts') or [])][:20000]
    except (TypeError, ValueError, IndexError): return dict(ok=False, msg='Неверные точки.')
    if len(new) < 2: return dict(ok=False, msg='В куске должно остаться хотя бы две точки. Ненужный кусок отметь «не использовать».')
    old = p['pts']; ts = piece_times(p)
    known = {}
    for i, q in enumerate(old): known.setdefault(_k(q), (q[2] if len(q) > 2 else None, ts[i] if ts else None))
    own = set(known)
    for k, v in (extra or {}).items(): known.setdefault(k, v)
    has_ele = any(len(q) > 2 for q in old)
    eles = [known.get(_k(q), (None, None))[0] for q in new]; tms = [known.get(_k(q), (None, None))[1] for q in new]
    if ts: tms = [t if _k(q) in own or i in (0, len(new) - 1) else None for i, (q, t) in enumerate(zip(new, tms))]      # время чужих точек — только для краёв
    pos = [0.0]
    for a, b in zip(new, new[1:]): pos.append(pos[-1] + dist(a, b))
    if has_ele:
        eles = _interp(eles, pos)
        if eles: new = [q + [round(e, 1)] for q, e in zip(new, eles)]
    nt = _interp(tms, pos) if ts else None
    if nt:
        for i in range(1, len(nt)): nt[i] = max(nt[i], nt[i - 1])      # время не идёт назад, даже если точку утащили «против хода»
    manual = [q for q in new if _k(q) not in known]
    _remember(p)
    p['pts'] = new; set_times(p, nt) if nt else (p.pop('tm', None), p.pop('t0', None))
    p['km'] = round(length_km(new), 2); _mark_manual(p, manual)
    return dict(ok=True, msg=f'Трек сохранён: {len(new)} {_pl(len(new), "точка", "точки", "точек")}, {str(p["km"]).replace(".", ",")} км.')


def accept(proj, d):
    """«Это не ошибка»: отрезок больше не попадает в проверку трека."""
    p = next((x for x in proj.get('pieces', []) if x['id'] == d.get('piece')), None)
    try: i0, i1 = int(d['i0']), int(d['i1'])
    except (KeyError, TypeError, ValueError): return dict(ok=False, msg='Не указан участок.')
    if not p or not (0 <= i0 < len(p['pts']) and (d.get('kind') == 'tangle' or 0 <= i1 < len(p['pts']))): return dict(ok=False, msg='Кусок изменился — обнови страницу.')
    if d.get('kind') == 'tangle':
        keys = [f'tangle:{_k(p["pts"][i0])}']
        p['okseg'] = list(dict.fromkeys((p.get('okseg') or []) + keys))[-500:]
        return dict(ok=True, msg='Отмечено: это не ошибка. Проверка больше не покажет это место.')
    a, b = (i0 - 1, i0) if d.get('kind') == 'excursion' else (i0, i1)      # у выброса помечаем прыжок «туда» и «обратно»
    keys = [f'{_k(p["pts"][a])}|{_k(p["pts"][b])}']
    if d.get('kind') == 'excursion' and i1 + 1 < len(p['pts']): keys.append(f'{_k(p["pts"][i1])}|{_k(p["pts"][i1 + 1])}')
    p['okseg'] = list(dict.fromkeys((p.get('okseg') or []) + keys))[-500:]
    return dict(ok=True, msg='Отмечено: это не ошибка. Проверка больше не покажет это место.')


_BR = threading.BoundedSemaphore(2)      # публичный BRouter ограничивает частоту: не больше двух запросов сразу
_BR_GAP, _BR_LAST, _BR_LOCK = 0.35, [0.0], threading.Lock()
BUSY = [0]      # сколько раз BRouter так и не ответил (перегружен) — такой отказ не значит «тропы нет»

def _brouter(q):
    """Запрос к BRouter с кэшем на диске (30 дней). «Повтори позже» (403/429/5xx) и сбои связи — пауза и повтор;
    ошибка маршрута («пути нет») — None и запоминается. Не дождались — None и BUSY += 1 (в кэш не пишется)."""
    import hashlib, os
    from . import mapkit
    qs = urllib.parse.urlencode(sorted(q.items()))
    path = os.path.join(mapkit.CACHE, 'brouter', hashlib.sha1(qs.encode()).hexdigest() + '.json')
    try:
        if time.time() - os.path.getmtime(path) < 30 * 86400:
            with open(path, encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError): pass
    def keep(v):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + '.tmp', 'w', encoding='utf-8') as f: json.dump(v, f)
            os.replace(path + '.tmp', path)
        except OSError: pass
        return v
    url = 'https://brouter.de/brouter?' + qs
    for attempt in range(5):
        try:
            with _BR:
                with _BR_LOCK:
                    wait = _BR_GAP - (time.time() - _BR_LAST[0])
                    if wait > 0: time.sleep(wait)
                    _BR_LAST[0] = time.time()
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'nitka-hiking-report-maps/1.0'}), timeout=25) as r:
                    g = json.loads(r.read().decode('utf-8'))
            return keep([[round(c[1], 6), round(c[0], 6)] for c in g['features'][0]['geometry']['coordinates']])
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503, 504): time.sleep(min(12, 1.5 * 2 ** attempt)); continue
            return keep(None)      # BRouter сказал, что пути нет
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(1.5 * (attempt + 1)); continue
        except Exception:
            return keep(None)
    BUSY[0] += 1
    return None


def route(a, b, kind='', nogos=()):
    """Путь по тропам OpenStreetMap между двумя точками — через публичный BRouter (brouter.de). None — не получилось.
    nogos — [(lat, lon, радиус м)]: места, через которые идти нельзя (седловины запрещённых перевалов)."""
    prof = 'trekking' if kind == 'вело' else 'hiking-mountain'
    q = dict(lonlats=f'{a[1]:.6f},{a[0]:.6f}|{b[1]:.6f},{b[0]:.6f}', profile=prof, alternativeidx=0, format='geojson')
    if nogos: q['nogos'] = '|'.join(f'{lo:.6f},{la:.6f},{int(r)}' for la, lo, r in nogos)
    return _brouter(q)


def route_via(pts, kind='', nogos=()):
    """Путь по тропам через несколько точек (BRouter, точки по порядку). None — не получилось."""
    prof = 'trekking' if kind == 'вело' else 'hiking-mountain'
    q = dict(lonlats='|'.join(f'{p[1]:.6f},{p[0]:.6f}' for p in pts), profile=prof, alternativeidx=0, format='geojson')
    if nogos: q['nogos'] = '|'.join(f'{lo:.6f},{la:.6f},{int(r)}' for la, lo, r in nogos)
    return _brouter(q)


def export_gpx(proj, step=0, split='day'):
    """Чистый GPX: нитка по дням (или одним треком), варианты отдельными треками, точки; время — если было.
    step — прореживание: точки не ближе step метров друг к другу (начало и конец остановок остаются)."""
    meta = proj.get('meta', {})
    def thin(pts, ts):
        if not step or len(pts) < 3: return list(zip(pts, ts or [None] * len(pts)))
        out = [(pts[0], ts[0] if ts else None)]; last = pts[0]
        for k in range(1, len(pts) - 1):
            q = pts[k]; stop = ts and (dist(q[:2], pts[k - 1][:2]) < 1 or dist(q[:2], pts[k + 1][:2]) < 1)
            if stop or dist(q[:2], last[:2]) >= step: out.append((q, ts[k] if ts else None)); last = q
        out.append((pts[-1], ts[-1] if ts else None)); return out
    def trkpts(seq):
        x = []
        for q, t in seq:
            e = f'<ele>{q[2]:.1f}</ele>' if len(q) > 2 and q[2] is not None else ''
            tt = f'<time>{datetime.fromtimestamp(t, timezone.utc):%Y-%m-%dT%H:%M:%SZ}</time>' if t else ''
            x.append(f'<trkpt lat="{q[0]:.6f}" lon="{q[1]:.6f}">{e}{tt}</trkpt>')
        return ''.join(x)
    pcs = sorted([p for p in proj.get('pieces', []) if p.get('role') in EDIT_ROLES and len(p.get('pts', [])) > 1], key=lambda p: (p.get('day') or 999, p.get('order', 0)))
    start = meta.get('start_date')
    trks = []; days = {}
    for p in pcs:
        if p['role'] in ('main', 'radial'): days.setdefault(p.get('day') or 0, []).append(p)
    if split == 'one':
        trks.append((meta.get('title') or 'Маршрут', [thin(p['pts'], piece_times(p)) for ps in days.values() for p in ps]))
    else:
        for d, ps in sorted(days.items()):
            nm = f'День {d}' if d else 'Без дня'
            if d and start:
                from datetime import date, timedelta
                nm += ' · ' + (date.fromisoformat(start) + timedelta(days=d - 1)).strftime('%d.%m.%Y')
            trks.append((nm, [thin(p['pts'], piece_times(p)) for p in ps]))
    vars_ = {v['id']: v for v in proj.get('variants', [])}
    for p in pcs:
        if p['role'] in ('alt', 'exit'):
            v = vars_.get(p.get('var')) or {}
            trks.append((('Запасной вариант' if p['role'] == 'alt' else 'Аварийный выход') + ' — ' + (v.get('title') or p.get('name') or ''), [thin(p['pts'], piece_times(p))]))
    wpts = []
    for q in proj.get('points', []):
        if q.get('type') == 'skip': continue
        nm = (q.get('raw') or q.get('name') or '').strip() or q.get('type')
        if q.get('time') and not nm.startswith(q['time']): nm = f"{q['time']} {nm}"
        wpts.append(f'<wpt lat="{q["lat"]:.6f}" lon="{q["lon"]:.6f}">{"<ele>%d</ele>" % q["h"] if q.get("h") else ""}<name>{escape(nm)}</name><type>{escape(q.get("type") or "")}</type></wpt>')
    body = ''.join(f'<trk><name>{escape(n)}</name>' + ''.join(f'<trkseg>{trkpts(s)}</trkseg>' for s in segs) + '</trk>' for n, segs in trks)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Нитка" xmlns="http://www.topografix.com/GPX/1/1">'
            f'<metadata><name>{escape(meta.get("title") or "Поход")}</name></metadata>' + ''.join(wpts) + body + '</gpx>')
