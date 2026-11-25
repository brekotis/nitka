"""Проверка и правка трека: выбросы от спуфинга GPS, разрывы сигнала, перерисовка участка, отмена, чистый GPX.

Выброс — трек вдруг улетает (к аэропорту, в чужой город, по кругу) и потом возвращается почти туда же,
откуда улетел. Такой кусок вырезается, края соединяются прямой — или участок перерисовывается вручную.
"""
import json, math, statistics, urllib.parse, urllib.request
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

def check(proj):
    """Находки по всем кускам: dict(kind='excursion'|'gap', piece, name, day, i0, i1, km, at, lat, lon)."""
    vmax = SPEED_MAX.get(proj.get('meta', {}).get('kind'), 40)
    tz = trip_tz(proj, next((p['pts'][0][1] for p in proj.get('pieces', []) if p.get('pts')), None))
    out = []
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
    out.sort(key=lambda f: (f['kind'] != 'excursion', f['day'] or 999))
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


def replace(proj, d):
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
    has_ele = any(len(q) > 2 for q in old)
    eles = [known.get(_k(q), (None, None))[0] for q in new]; tms = [known.get(_k(q), (None, None))[1] for q in new]
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
    if not p or not (0 <= i0 < len(p['pts']) and 0 <= i1 < len(p['pts'])): return dict(ok=False, msg='Кусок изменился — обнови страницу.')
    a, b = (i0 - 1, i0) if d.get('kind') == 'excursion' else (i0, i1)      # у выброса помечаем прыжок «туда» и «обратно»
    keys = [f'{_k(p["pts"][a])}|{_k(p["pts"][b])}']
    if d.get('kind') == 'excursion' and i1 + 1 < len(p['pts']): keys.append(f'{_k(p["pts"][i1])}|{_k(p["pts"][i1 + 1])}')
    p['okseg'] = list(dict.fromkeys((p.get('okseg') or []) + keys))[-500:]
    return dict(ok=True, msg='Отмечено: это не ошибка. Проверка больше не покажет это место.')


def route(a, b, kind=''):
    """Путь по тропам OpenStreetMap между двумя точками — через публичный BRouter (brouter.de). None — не получилось."""
    prof = 'trekking' if kind == 'вело' else 'hiking-mountain'
    url = 'https://brouter.de/brouter?' + urllib.parse.urlencode(dict(lonlats=f'{a[1]:.6f},{a[0]:.6f}|{b[1]:.6f},{b[0]:.6f}', profile=prof, alternativeidx=0, format='geojson'))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'nitka-hiking-report-maps/1.0'}), timeout=15) as r:
            g = json.loads(r.read().decode('utf-8'))
        c = g['features'][0]['geometry']['coordinates']
        return [[round(q[1], 6), round(q[0], 6)] for q in c]
    except Exception:
        return None


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
