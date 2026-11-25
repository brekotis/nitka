"""Подбор маршрута (эксперимент): старт, финиш, длина, категории и число перевалов → несколько вариантов.

1. Кандидаты — перевалы каталога нужных категорий внутри эллипса вокруг старта и финиша (сумма расстояний ≤ бюджет).
2. Лучевой поиск цепочек старт → перевалы → финиш с прямой длиной около бюджета (длина / коэффициент извилистости троп);
   штрафы за возвраты назад и за перевалы вплотную друг к другу.
3. Из лучших берутся непохожие (разные наборы перевалов), каждый прокладывается по тропам (BRouter), считаются
   настоящие км и набор по рельефу; варианты пересортировываются по близости к заданной длине.
4. Если задано число дней — ночёвки встают у границ дней, в самой низкой точке рядом (в долине, а не на перевале).
Результат каждого варианта — план в том же виде, что у планировщика (engine/planner.py), его можно сразу открыть и поправить.
"""
import math, threading
from concurrent.futures import ThreadPoolExecutor
from .classify import dist
from . import dem, suggest, terrain, trackfix

GRADES = ['н/к', '1А', '1Б', '2А', '2Б', '3А', '3Б']
DETOUR = 1.35          # тропа длиннее прямой примерно на треть
MIN_GAP = 1500.0       # перевалы ближе 1,5 км друг к другу — один и тот же гребень или дубль каталога
BEAM = 400
_RC = {}; _RC_LOCK = threading.Lock()     # кэш отрезков по тропам: «ещё варианты» не спрашивают BRouter заново


def _angle(a, b, c):
    """Поворот в точке b (0° — прямо, 180° — назад)."""
    k = math.cos(math.radians(b[0]))
    v1 = ((b[1] - a[1]) * k, b[0] - a[0]); v2 = ((c[1] - b[1]) * k, c[0] - b[0])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0: return 0.0
    cs = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cs))


def _cat_item(c):
    g = suggest.GR.get(str(c.get('grade_eng') or '').lower())
    try: p = (float(c['latlon'][0]), float(c['latlon'][1]))
    except Exception: return None
    try: h = int(float(c.get('elevation'))) if c.get('elevation') else None
    except Exception: h = None
    raw = str(c.get('name') or '').strip()
    nm = ('пер. ' + raw) if raw and not raw.replace(' ', '').isdigit() else f'перевал {raw or h or ""}'.strip()      # безымянные в каталоге записаны высотой
    return dict(id=str(c.get('id') or f'{p[0]:.5f},{p[1]:.5f}'), p=p, grade=g, h=h, name=nm, note=str(c.get('connects') or '').strip()[:160])


def candidates(start, finish, budget, quota, must=()):
    """Перевалы нужных категорий (у которых «до» > 0) внутри эллипса; обязательные — всегда. → (кандидаты, обязательные вне эллипса)."""
    out, far = [], []
    for c in suggest.catalog():
        if c.get('is_summit'): continue
        it = _cat_item(c)
        if not it: continue
        is_must = it['id'] in must
        if not is_must and (it['grade'] not in quota or quota[it['grade']][1] <= 0): continue
        if dist(start, it['p']) + dist(it['p'], finish) > budget * 1.05:
            if is_must: far.append(it)
            continue
        out.append(it)
    return out, far


def ends(c, frm):
    """Вход и выход у перевала: долина ближе к тому, откуда пришли, — вход, противоположная — выход.
    Без седловины по рельефу (неточные координаты, плато) — сам перевал. → (вход, выход, путь через седловину, ориентация)"""
    sd = c.get('sides')
    if not sd: return c['p'], c['p'], 0.0, 0
    s1, s2 = tuple(sd[0]), tuple(sd[1]); o = 0 if dist(frm, s1) <= dist(frm, s2) else 1
    a, b = (s1, s2) if o == 0 else (s2, s1)
    return a, b, dist(a, c['p']) + dist(c['p'], b), o


def search(start, finish, budget, C, quota, must=(), exclude=(), on_depth=None):
    """Лучевой поиск цепочек перевалов с квотами по категориям. Перевал — это переход в другую долину: путь приходит
    к его входу, переваливает седловину и уходит от выхода. → [(оценка, [(индекс, ориентация)], прямая длина)] лучшие сверху."""
    must = set(must)
    C = [c for c in C if c['id'] not in exclude or c['id'] in must]
    if not C: return [], C
    G = sorted(quota); gi = {g: i for i, g in enumerate(G)}
    lo = [quota[g][0] for g in G]; hi = [quota[g][1] for g in G]
    mi = {j for j, c in enumerate(C) if c['id'] in must}
    need_min = sum(lo)
    nmax = max(1, min(6, max(sum(hi), len(mi))))
    BACK, PEN = 140.0, 0.3      # следующий перевал — назад через только что пройденный гребень
    zero = tuple(0 for _ in G)
    states = [((), 0.0, start, 0.0, zero, None)]; done = []
    for depth in range(1, nmax + 1):
        scored = []
        for seq, L, last, pen, cnt, lastp in states:
            used = {x[0] for x in seq}
            for j, c in enumerate(C):
                if j in used or any(dist(C[k]['p'], c['p']) < MIN_GAP for k in used): continue
                g = c['grade']; k_ = gi.get(g)
                if j not in mi and (k_ is None or cnt[k_] >= hi[k_]): continue      # этой категории уже достаточно
                cnt2 = cnt if k_ is None else cnt[:k_] + (cnt[k_] + 1,) + cnt[k_ + 1:]
                lack = sum(max(0, lo[i] - cnt2[i]) for i in range(len(G))) + len(mi - used - {j})
                if lack > nmax - depth: continue
                s_in, s_out, inner, o = ends(c, last)
                L2 = L + dist(last, s_in) + inner
                tot = L2 + dist(s_out, finish)
                if tot > budget * 1.15: continue
                p2 = pen + (PEN if lastp is not None and _angle(lastp, last, s_in) > BACK else 0.0)
                scored.append((abs(tot - budget) / budget + p2 + 0.12 * lack, seq + ((j, o),), L2, s_out, p2, tot, cnt2, lack, c['p']))
        scored.sort(key=lambda x: x[0])
        tried = len(scored)
        scored = scored[:BEAM]
        states = [(x[1], x[2], x[3], x[4], x[6], x[8]) for x in scored]
        if on_depth: on_depth(depth, nmax, tried, [[j for j, _ in x[1]] for x in scored[:10]])      # что сейчас перебирается — показать на карте
        for sc, seq, L2, p, pen, tot, cnt2, lack, _ in scored:
            if lack == 0 and len(seq) >= max(1, need_min) and abs(tot - budget) / budget < 0.35:
                done.append((abs(tot - budget) / budget + pen, list(seq), tot))
        if not states: break
    done.sort(key=lambda x: x[0])
    return done, C


def diverse(found, C, k, must=()):
    """Непохожие цепочки: обязательные перевалы есть у всех — сравниваем остальные."""
    pick = []
    for sc, seq, tot in found:
        ids = {C[j]['id'] for j, _ in seq} - set(must)
        if any(ids and s and len(ids & s) / min(len(ids), len(s)) > 0.5 for _, s in pick): continue
        if any(not ids and not s for _, s in pick): continue
        pick.append(((sc, seq, tot), ids))
        if len(pick) >= k: break
    return [p[0] for p in pick]


def _leg(a, b, trail, kind, avoid=()):
    """Отрезок по тропам с подходами по рельефу; без троп — весь по рельефу (не прямой через гребни). avoid — запретные седловины."""
    st = dist(a, b); avoid = tuple(x for x in avoid if dist((x[0], x[1]), a) + dist((x[0], x[1]), b) < st + 4000)
    key = (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5), kind, trail, avoid)
    with _RC_LOCK: hit = _RC.get(key)
    if hit: return hit
    busy0 = trackfix.BUSY[0]
    if trail:
        pts, ok, mode, _ = terrain.trail_leg(a, b, kind, avoid=avoid)
    else:
        pts, info = terrain.path(a, b, avoid=avoid); ok, mode = True, 'terrain'
        if not pts: pts, mode = [[a[0], a[1]], [b[0], b[1]]], 'line'
    res = (pts, ok, mode)
    if trackfix.BUSY[0] > busy0: return res      # отказ из-за перегрузки BRouter не запоминаем
    with _RC_LOCK:
        if len(_RC) > 5000: _RC.clear()
        _RC[key] = res
    return res


_TC = {}
def _climb(a, b, avoid=()):
    """Подъём от долины на седловину (или спуск) — по рельефу, в обход запретных седловин."""
    avoid = tuple(x for x in avoid if dist((x[0], x[1]), a) + dist((x[0], x[1]), b) < dist(a, b) + 3000)
    key = (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5), avoid)
    if key in _TC: return _TC[key]
    pts, _ = terrain.path(a, b, avoid=avoid)
    res = (pts or [list(a), list(b)], True, 'terrain')
    if len(_TC) > 5000: _TC.clear()
    _TC[key] = res
    return res


_SIDE = {}
def _side(c, frm, other, avoid=(), kind=''):
    """Тропа с одной стороны перевала: BRouter от долины (frm) до седловины. Годится, если доходит до самого перевала
    (до 150 м), начинается со своей стороны гребня и не петляет. → точки от долины до перевала или None (тогда — по рельефу)."""
    p = tuple(c['p']); key = (c['id'], round(frm[0], 5), round(frm[1], 5), kind, tuple(avoid))
    if key in _SIDE: return _SIDE[key]
    near = [x for x in avoid if dist((x[0], x[1]), p) > 400]
    res = None
    busy0 = trackfix.BUSY[0]
    try: pts = trackfix.route_via([tuple(frm), p], kind, near)
    except Exception: pts = None
    if pts is None and trackfix.BUSY[0] > busy0: return None      # BRouter перегружен — это не «тропы нет», не запоминаем
    if pts and len(pts) > 1:
        a, b = pts[0], pts[-1]
        L = sum(dist(x, y) for x, y in zip(pts, pts[1:]))
        k = math.cos(math.radians(p[0]))
        side = lambda q: ((q[1] - p[1]) * k * (frm[1] - p[1]) * k + (q[0] - p[0]) * (frm[0] - p[0]))      # > 0 — та же сторона гребня, что и долина frm
        if dist(b, p) <= 150 and side(a) > 0 and dist(a, p) > 300 and L <= 3 * dist(frm, p) + 3000:
            res = [list(q) for q in pts[:-1]] + [list(p)]
    if len(_SIDE) > 5000: _SIDE.clear()
    _SIDE[key] = res
    return res


def _near_idx(G, p, lim=220.0):
    k = math.cos(math.radians(p[0])); best = None
    for i in range(len(G)):
        if abs(G[i][0] - p[0]) > 0.004 or abs(G[i][1] - p[1]) > 0.006: continue
        d = math.hypot((G[i][0] - p[0]) * 110540, (G[i][1] - p[1]) * 111320 * k)
        if best is None or d < best[0]: best = (d, i)
    return best[1] if best and best[0] <= lim else None


def build(start, finish, chain, trail, kind, days, ex, sites=(), allp=(), quota=None, forbid=()):
    """Цепочка перевалов → настоящий маршрут. К каждому перевалу — тропой до входной долины, по рельефу на седловину,
    вниз в другую долину. На готовой нитке распознаются перевалы «по пути» (она через них переваливает) и проверяется,
    что перевалы цепочки пройдены насквозь. → вариант с планом для планировщика."""
    keys = [dict(lat=start[0], lon=start[1], kind='via', name='старт')] + \
           [dict(lat=c['p'][0], lon=c['p'][1], kind='pass', name=c['name'], h=c['h'], grade=c['grade'], ref=c['id']) for c in chain] + \
           [dict(lat=finish[0], lon=finish[1], kind='via', name='финиш')]
    # запретные седловины (категории, которых не просили, и «не через него»), кроме тех, что вплотную к выбранным точкам
    keep_clear = [tuple(start), tuple(finish)] + [tuple(c['p']) for c in chain] + [tuple(c['sin']) for c in chain] + [tuple(c['sout']) for c in chain]
    avoid = tuple((f['p'][0], f['p'][1], 300) for f in forbid if all(dist(f['p'], q) > 700 for q in keep_clear))
    # «по тропам»: подъём на перевал и спуск — тропой с той стороны, где она доходит до седловины; где нет — по рельефу
    sides_jobs = [(c, c['sin'], c['sout']) for c in chain if c.get('sides')] + [(c, c['sout'], c['sin']) for c in chain if c.get('sides')]
    got = dict(zip([(c['id'], tuple(f)) for c, f, o in sides_jobs], ex.map(lambda x: _side(x[0], x[1], x[2], avoid, kind) if trail else None, sides_jobs)))
    halves = []
    for c in chain:
        up_ = got.get((c['id'], tuple(c['sin']))) if c.get('sides') else None
        dn_ = got.get((c['id'], tuple(c['sout']))) if c.get('sides') else None
        halves.append((up_, [q for q in reversed(dn_)] if dn_ else None))
    ins = [tuple(start)] + [tuple(hv[0][0]) if hv[0] else tuple(c['sin']) for c, hv in zip(chain, halves)] + [tuple(finish)]
    outs = [tuple(start)] + [tuple(hv[1][-1]) if hv[1] else tuple(c['sout']) for c, hv in zip(chain, halves)] + [tuple(finish)]
    jobs = []
    for i in range(len(keys) - 1):
        a, b = (keys[i]['lat'], keys[i]['lon']), (keys[i + 1]['lat'], keys[i + 1]['lon'])
        part = []
        hv_a = halves[i - 1] if 0 < i <= len(chain) else None; hv_b = halves[i] if i < len(chain) else None
        if hv_a and hv_a[1]: part.append(('k', hv_a[1]))      # спуск с перевала по тропе
        elif outs[i] != a: part.append(('c', a, outs[i]))      # тропы нет — по рельефу
        part.append(('l', outs[i], ins[i + 1]))
        if hv_b and hv_b[0]: part.append(('k', hv_b[0]))      # подъём на перевал по тропе
        elif ins[i + 1] != b: part.append(('c', ins[i + 1], b))
        jobs.append(part)
    flat = [x for part in jobs for x in part]
    res = list(ex.map(lambda x: (x[1], True, 'trail') if x[0] == 'k' else _climb(x[1], x[2], avoid) if x[0] == 'c' else _leg(x[1], x[2], trail, kind, avoid), flat))
    legs = []; it = iter(res)
    for part in jobs:
        pts, ok, mode = [], True, 'terrain'
        for x in part:
            kind_ = x[0]
            q, ok_, m_ = next(it)
            pts += q if not pts else q[1:]
            if kind_ == 'l': ok, mode = ok_, m_
        legs.append((pts, ok, mode))
    # вся нитка подряд, с метками ключевых точек
    G = [[keys[0]['lat'], keys[0]['lon']]]; marks = [0]
    for pts, ok, mode in legs:
        for q in pts[1:]:
            if dist(G[-1], q) > 120:      # прямые и редкие участки — дробим, иначе рельеф между точками не виден
                a = G[-1]; n = int(dist(a, q) // 100)
                for k in range(1, n + 1): G.append([a[0] + (q[0] - a[0]) * k / (n + 1), a[1] + (q[1] - a[1]) * k / (n + 1)])
            G.append(list(q))
        marks.append(len(G) - 1)
    S = dem.Sampler(G, z=12)
    E = [S(q[0], q[1]) for q in G]
    E = [(E[max(0, i - 1)] + E[i] + E[min(len(E) - 1, i + 1)]) / 3 for i in range(len(E))]
    D = [0.0]
    for i in range(1, len(G)): D.append(D[-1] + dist(G[i - 1], G[i]))
    up = sum(max(0.0, E[i] - E[i - 1]) for i in range(1, len(E))); dn = sum(max(0.0, E[i - 1] - E[i]) for i in range(1, len(E)))
    total = D[-1]
    # перевалы цепочки — пройдены насквозь? и какие ещё перевалы нитка переваливает по пути
    uturn = [c['name'] for c, m in zip(chain, marks[1:-1]) if not terrain.crosses(G, E, D, m)]
    mine = {c['id'] for c in chain}; extra = []
    for c in allp:
        if c['id'] in mine: continue
        i = _near_idx(G, c['p'])
        if i is None or not terrain.crosses(G, E, D, i): continue
        if any(abs(D[i] - D[x['i']]) < 800 for x in extra) or any(abs(D[i] - D[m]) < 800 for m in marks[1:-1]): continue      # одна седловина — один перевал
        extra.append(dict(c, i=i))
    # перевалы категорий, которых не просили (или «не через него»), — вариант не годится, его не покажем
    fids = {f['id'] for f in forbid}
    banned = [x['name'] + (f' ({x["grade"]})' if x.get('grade') else '') for x in extra if x['id'] in fids]
    # квоты: перевалов какой категории вышло больше, чем просили
    over = []
    if quota:
        cnt = {}
        for c in list(chain) + extra: cnt[c['grade']] = cnt.get(c['grade'], 0) + 1
        for g, n in sorted(cnt.items(), key=lambda x: str(x[0])):
            lim = quota.get(g, (0, 0))[1]
            if n > lim: over.append(f'{g or "без категории"} — {n} вместо {lim}' if lim else f'{g or "без категории"} — {n}, а не просил')
    pass_d = [D[m] for m in marks[1:-1]] + [D[x['i']] for x in extra]
    # ночёвки: стоянка OSM у границы дня, иначе самая низкая точка рядом; не ближе 1 км к перевалу
    camps, cname = [], {}
    if days and days > 1:
        day = total / days
        near = []
        for la, lo, nm, kd in sites:
            i = _near_idx(G, (la, lo), 400)
            if i is not None: near.append((i, nm if nm and kd in nm.lower() else f'{kd} «{nm}»' if nm else kd))
        for k in range(1, days):
            t = day * k
            ok_site = [(abs(D[i] - t), i, nm) for i, nm in near if abs(D[i] - t) <= 0.25 * day and not any(abs(D[i] - pd) < 1000 for pd in pass_d) and i not in camps]
            if ok_site:
                _, i, nm = min(ok_site); camps.append(i); cname[i] = nm; continue
            lo_, hi_ = t - 0.15 * day, t + 0.15 * day
            best = None
            for i in range(len(G)):
                if not lo_ <= D[i] <= hi_ or any(abs(D[i] - pd) < 1000 for pd in pass_d): continue
                if best is None or E[i] < E[best]: best = i
            if best is None: best = min(range(len(G)), key=lambda i: abs(D[i] - t))
            camps.append(best)
        camps.sort()
    # ключевые точки по ходу: старт, перевалы (и «по пути»), ночёвки, финиш
    K = [(marks[i], keys[i]) for i in range(len(keys))] + \
        [(x['i'], dict(lat=round(x['p'][0], 6), lon=round(x['p'][1], 6), kind='pass', name=x['name'], h=x['h'], grade=x['grade'], ref=x['id'])) for x in extra] + \
        [(i, dict(lat=round(G[i][0], 6), lon=round(G[i][1], 6), kind='camp', name=cname.get(i, ''), h=int(E[i]))) for i in camps]
    K.sort(key=lambda x: (x[0], 0 if x[1]['kind'] != 'camp' else 1))
    def leg_at(gi):
        for L in range(len(legs)):
            if marks[L] <= gi < marks[L + 1]: return L
        return len(legs) - 1
    plan_pts, plan_legs = [], []
    for n, (gi, k) in enumerate(K):
        mode = legs[leg_at(gi)][2]
        if n + 1 < len(K):
            gj = K[n + 1][0]
            seg = G[gi:gj + 1] if gj > gi else [G[gi], G[gj]]
            if k['kind'] == 'pass' and gi not in marks: seg = [[k['lat'], k['lon']]] + seg[1:]      # «по пути» — точно на перевале
            if K[n + 1][1]['kind'] == 'pass' and gj not in marks: seg = seg[:-1] + [[K[n + 1][1]['lat'], K[n + 1][1]['lon']]]
            plan_legs.append(dict(mode=mode, ok=legs[leg_at(gi)][1], pts=[[round(q[0], 6), round(q[1], 6)] for q in seg]))
        plan_pts.append(dict(lat=round(k['lat'], 6), lon=round(k['lon'], 6), kind=k['kind'], name=k.get('name', ''), h=k.get('h'), grade=k.get('grade'), ref=k.get('ref'),
                             mode=mode if n + 1 < len(K) else ('trail' if trail else 'terrain')))
    nolink = sum(1 for pts, ok, mode in legs if trail and not ok)
    step = max(1, len(G) // 300)
    order = sorted([(m, dict(name=c['name'], grade=c['grade'], h=c['h'], note=c['note'], ref=c['id'], crossed=c['name'] not in uturn, trail=(('both' if hv[0] and hv[1] else 'one' if hv[0] or hv[1] else 'none') if trail else None))) for c, m, hv in zip(chain, marks[1:-1], halves)] +
                   [(x['i'], dict(name=x['name'], grade=x['grade'], h=x['h'], note=x['note'], ref=x['id'], crossed=True, extra=True)) for x in extra], key=lambda x: x[0])
    return dict(km=round(total / 1000, 1), up=int(up), dn=int(dn), nolink=nolink, legs=len(legs), uturn=uturn, over=over, banned=banned,
                passes=[x[1] for x in order],
                camps=[dict(h=int(E[i]), km=round(D[i] / 1000, 1), name=cname.get(i, '')) for i in camps],
                line=[[round(q[0], 5), round(q[1], 5)] for q in G[::step]] + [[round(G[-1][0], 5), round(G[-1][1], 5)]],
                plan=dict(pts=plan_pts, legs=plan_legs))


def propose(start, finish, km, quota=None, days=None, trail=True, kind='', exclude=(), must=(), ban=(), k=4, progress=None):
    """quota — {категория: (от, до)}; must — перевалы, через которые маршрут идёт обязательно.
    progress(**поля) — ход работы для показа на карте: этап, кандидаты, перебираемые цепочки, готовые варианты. → {ok, variants | msg}."""
    rep = progress or (lambda **kw: None)
    busy0 = trackfix.BUSY[0]
    quota = {g: (max(0, min(6, int(a))), max(0, min(6, int(b)))) for g, (a, b) in (quota or {'1А': (1, 2), '1Б': (0, 1)}).items() if g in GRADES}
    quota = {g: (a, max(a, b)) for g, (a, b) in quota.items()}
    must = [str(x) for x in must][:6]
    if not any(b for a, b in quota.values()) and not must: return dict(ok=False, msg='Укажи хотя бы одну категорию перевалов с «до» больше нуля — или выбери перевалы на карте.')
    if sum(a for a, b in quota.values()) > 6: return dict(ok=False, msg='Больше шести перевалов «Нитка» пока не подбирает — уменьши «от».')
    target = float(km) * 1000; budget = target / (DETOUR if trail else 1.05)
    if dist(start, finish) > budget * 0.95:
        return dict(ok=False, msg=f'Старт и финиш дальше друг от друга, чем позволяет длина. Прибавь километров — хотя бы {int(dist(start, finish) * (DETOUR if trail else 1.05) / 1000 * 1.2)}.')
    rep(stage='Ищу перевалы каталога в пределах длины', pct=3)
    C, far = candidates(start, finish, budget, quota, set(must))
    lost = set(must) - {c['id'] for c in C} - {f['id'] for f in far}
    if lost: return dict(ok=False, msg='Часть обязательных перевалов не нашлась в каталоге перевалов (может, это вершина) — убери их из обязательных.')
    rep(stage=f'Нашёл {len(C)} перевалов-кандидатов — смотрю их седловины по рельефу', pct=6, cands=[[round(c['p'][0], 5), round(c['p'][1], 5), c['grade']] for c in C])
    # перевал — переход из долины в долину: у каждого кандидата ищем по рельефу обе долины (вход и выход)
    with ThreadPoolExecutor(6) as ex0:
        for c, sd in zip(C, ex0.map(lambda c: terrain.saddle(c['p'], c['h']), C)): c['sides'] = sd
    # все перевалы района (любой категории) — чтобы узнать их на готовой нитке «по пути»
    la0_, la1_ = min(start[0], finish[0]) - budget / 2 / 110540, max(start[0], finish[0]) + budget / 2 / 110540
    kk = math.cos(math.radians(start[0])); lo0_, lo1_ = min(start[1], finish[1]) - budget / 2 / (111320 * kk), max(start[1], finish[1]) + budget / 2 / (111320 * kk)
    allp = []
    for c in suggest.catalog():
        if c.get('is_summit'): continue
        it_ = _cat_item(c)
        if it_ and la0_ <= it_['p'][0] <= la1_ and lo0_ <= it_['p'][1] <= lo1_: allp.append(it_)
    # нельзя: перевалы категорий, у которых «до» = 0 (их не просили), и отмеченные «не через него» — даже по пути
    forbid = [it_ for it_ in allp if it_['id'] not in set(must) and (it_['id'] in set(ban) or (it_['grade'] in GRADES and quota.get(it_['grade'], (0, 0))[1] <= 0))]
    rep(stage=f'Перебираю цепочки из {len(C)} перевалов', pct=10)
    if far: return dict(ok=False, msg=f'{", ".join(f["name"] for f in far)} — слишком далеко для {int(km)} км от старта и финиша. Прибавь километров или убери его из обязательных.')
    if not C: return dict(ok=False, msg=f'В этом районе в пределах {int(km)} км нет перевалов нужных категорий из каталога. Попробуй другие категории, больше километров или сдвинь старт.')
    have = {g: sum(1 for c in C if c['grade'] == g) for g in quota}
    short = [f'{g}: нужно от {a}, есть {have[g]}' for g, (a, b) in quota.items() if a > have[g]]
    if short: return dict(ok=False, msg='В пределах этой длины не хватает перевалов: ' + '; '.join(short) + '. Прибавь километров или уменьши «от».')
    tried = [0]
    def on_depth(depth, nmax, n, seqs):
        tried[0] += n
        rep(stage=f'Перебираю цепочки из {depth} {"перевала" if depth == 1 else "перевалов"}: проверено {tried[0]}', pct=8 + int(30 * depth / nmax),
            sketch=[[[round(start[0], 5), round(start[1], 5)]] + [[round(C2[j]['p'][0], 5), round(C2[j]['p'][1], 5)] for j in sq] + [[round(finish[0], 5), round(finish[1], 5)]] for sq in seqs])
    C2 = [c for c in C if c['id'] not in (set(exclude) - set(must)) or c['id'] in set(must)]
    found, C = search(start, finish, budget, C, quota, must, set(exclude) - set(must), on_depth)
    if not found: return dict(ok=False, msg=f'Не сложилось ни одной цепочки на {int(km)} км с такими перевалами. Попробуй больше километров, меньше «от» или другие обязательные.' + (' Часть перевалов уже показана — «Ещё варианты» ищет только новые.' if exclude else ''))
    picks = diverse(found, C, k + 5, must); dropped = []
    sites = []
    if days and days > 1:      # стоянки из OpenStreetMap в охвате цепочек — ночёвки встанут на них, где получится
        rep(stage='Ищу стоянки и приюты по OpenStreetMap', pct=40)
        P_ = [start, finish] + [C[j]['p'] for _, sq, _ in picks for j, _o in sq]
        try: sites = suggest.osm_camps(min(p[0] for p in P_) - 0.03, max(p[0] for p in P_) + 0.03, min(p[1] for p in P_) - 0.04, max(p[1] for p in P_) + 0.04)
        except Exception: sites = []
    lines = []
    with ThreadPoolExecutor(6) as ex:
        V = []
        for i, (sc, seq, tot) in enumerate(picks):
            chain = []
            for j, o in seq:
                c = dict(C[j]); sd = c.get('sides')
                c['sin'], c['sout'] = ((sd[0], sd[1]) if o == 0 else (sd[1], sd[0])) if sd else (c['p'], c['p'])
                chain.append(c)
            trying = [list(start)] + [q for c in chain for q in ([c['sin'], c['p'], c['sout']] if c.get('sides') else [c['p']])] + [list(finish)]
            rep(stage=f'Прокладываю вариант {i + 1} из {len(picks)}: тропы, подходы и переходы через седловины', pct=40 + int(58 * i / len(picks)), sketch=[],
                trying=[[round(q[0], 5), round(q[1], 5)] for q in trying])
            try: v = build(start, finish, chain, trail, kind, days, ex, sites, allp, quota, forbid)
            except RuntimeError: continue      # высоты не скачались — вариант пропускаем
            if v['banned']: dropped.append(v['banned']); continue      # прошёл через перевал, которого не просили, — не показываем
            V.append(v); lines.append(v['line']); rep(built=list(lines))
            if len(V) >= k + 1: break
    if not V and dropped: return dict(ok=False, msg=f'Все найденные варианты проходят через перевалы, которых ты не просил ({", ".join(sorted({n for d in dropped for n in d})[:4])}), — обойти их тропами и по рельефу не вышло. Разреши эти категории, прибавь километров или сдвинь старт.')
    if not V: return dict(ok=False, msg='Не получилось посчитать варианты — высоты или тропы сейчас недоступны. Попробуй ещё раз через минуту.')
    V.sort(key=lambda v: abs(v['km'] * 1000 - target) / target + 0.15 * v['nolink'] / max(1, v['legs']) + 0.25 * len(v['over']) + 0.3 * len(v['uturn']))
    U = []
    for v in V:      # разные перевалы, а путь один и тот же (перевалы рядом на одной нитке) — это не другой вариант
        if any(abs(v['km'] - u['km']) < 0.5 and abs(v['up'] - u['up']) < 60 for u in U): continue
        U.append(v)
    V = U[:k]
    for v in V: v['delta'] = round(v['km'] - float(km), 1)
    busy = trackfix.BUSY[0] > busy0
    return dict(ok=True, variants=V, cands=len(C), busy=busy,
                warn='Сервис троп (BRouter) был перегружен — часть отрезков могла пойти по рельефу, хотя тропа там есть. Подбери ещё раз через минуту — то, что уже нашлось, запомнено.' if busy else '')
