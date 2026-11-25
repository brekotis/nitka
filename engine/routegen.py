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
from . import dem, suggest, terrain

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


def search(start, finish, budget, C, quota, must=(), exclude=(), on_depth=None):
    """Лучевой поиск цепочек перевалов с квотами по категориям: для каждой — сколько «от» и «до».
    Обязательные перевалы входят в каждую цепочку. → [(оценка, [индексы кандидатов], прямая длина)] лучшие сверху."""
    must = set(must)
    C = [c for c in C if c['id'] not in exclude or c['id'] in must]
    if not C: return [], C
    G = sorted(quota); gi = {g: i for i, g in enumerate(G)}
    lo = [quota[g][0] for g in G]; hi = [quota[g][1] for g in G]
    mi = {j for j, c in enumerate(C) if c['id'] in must}
    need_min = sum(lo)
    nmax = max(1, min(6, max(sum(hi), len(mi))))
    U_TURN, PEN = 130.0, 0.35      # пришли к перевалу и ушли той же долиной — это не «через перевал»
    zero = tuple(0 for _ in G)
    states = [((), 0.0, start, 0.0, zero)]; done = []
    for depth in range(1, nmax + 1):
        scored = []
        for seq, L, last, pen, cnt in states:
            prev = (C[seq[-2]]['p'] if len(seq) > 1 else start) if seq else None
            for j, c in enumerate(C):
                if j in seq or any(dist(C[k]['p'], c['p']) < MIN_GAP for k in seq): continue
                g = c['grade']; k_ = gi.get(g)
                if j not in mi and (k_ is None or cnt[k_] >= hi[k_]): continue      # этой категории уже достаточно
                cnt2 = cnt if k_ is None else cnt[:k_] + (cnt[k_] + 1,) + cnt[k_ + 1:]
                # хватит ли оставшихся мест на недобранные «от» и обязательные
                left = nmax - depth
                lack = sum(max(0, lo[i] - cnt2[i]) for i in range(len(G))) + len(mi - set(seq + (j,)))
                if lack > left: continue
                L2 = L + dist(last, c['p'])
                if L2 + dist(c['p'], finish) > budget * 1.15: continue
                p2 = pen + (PEN if prev is not None and _angle(prev, last, c['p']) > U_TURN else 0.0)
                tot = L2 + dist(c['p'], finish)
                end_pen = PEN if _angle(last, c['p'], finish) > U_TURN and dist(c['p'], finish) > 500 else 0.0
                scored.append((abs(tot - budget) / budget + p2 + 0.12 * lack, seq + (j,), L2, c['p'], p2, tot, end_pen, cnt2, lack))
        scored.sort(key=lambda x: x[0])
        tried = len(scored)
        scored = scored[:BEAM]
        states = [(x[1], x[2], x[3], x[4], x[7]) for x in scored]
        if on_depth: on_depth(depth, nmax, tried, [x[1] for x in scored[:10]])      # что сейчас перебирается — показать на карте
        for sc, seq, L2, p, pen, tot, end_pen, cnt2, lack in scored:
            if lack == 0 and len(seq) >= max(1, need_min) and abs(tot - budget) / budget < 0.35:
                done.append((abs(tot - budget) / budget + pen + end_pen, list(seq), tot))
        if not states: break
    done.sort(key=lambda x: x[0])
    return done, C


def diverse(found, C, k, must=()):
    """Непохожие цепочки: обязательные перевалы есть у всех — сравниваем остальные."""
    pick = []
    for sc, seq, tot in found:
        ids = {C[j]['id'] for j in seq} - set(must)
        if any(ids and s and len(ids & s) / min(len(ids), len(s)) > 0.5 for _, s in pick): continue
        if any(not ids and not s for _, s in pick): continue
        pick.append(((sc, seq, tot), ids))
        if len(pick) >= k: break
    return [p[0] for p in pick]


def _leg(a, b, trail, kind):
    """Отрезок по тропам с подходами по рельефу; без троп — весь по рельефу (не прямой через гребни)."""
    key = (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5), kind, trail)
    with _RC_LOCK: hit = _RC.get(key)
    if hit: return hit
    if trail:
        pts, ok, mode, _ = terrain.trail_leg(a, b, kind)
    else:
        pts, info = terrain.path(a, b); ok, mode = True, 'terrain'
        if not pts: pts, mode = [[a[0], a[1]], [b[0], b[1]]], 'line'
    res = (pts, ok, mode)
    with _RC_LOCK:
        if len(_RC) > 5000: _RC.clear()
        _RC[key] = res
    return res


def build(start, finish, chain, trail, kind, days, ex, sites=()):
    """Цепочка перевалов → настоящий маршрут: отрезки, км, набор, ночёвки. → вариант с планом для планировщика."""
    keys = [dict(lat=start[0], lon=start[1], kind='via', name='старт')] + \
           [dict(lat=c['p'][0], lon=c['p'][1], kind='pass', name=c['name'], h=c['h'], grade=c['grade'], ref=c['id']) for c in chain] + \
           [dict(lat=finish[0], lon=finish[1], kind='via', name='финиш')]
    pairs = [((keys[i]['lat'], keys[i]['lon']), (keys[i + 1]['lat'], keys[i + 1]['lon'])) for i in range(len(keys) - 1)]
    legs = list(ex.map(lambda ab: _leg(ab[0], ab[1], trail, kind), pairs))
    # вся нитка подряд, с метками ключевых точек
    G = [list(pairs[0][0])]; marks = [0]
    for pts, ok, mode in legs:
        seg = pts[1:]
        for q in seg:
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
    # ночёвки: у границы дня — самая низкая точка в окне ±15 % дневного перехода, не ближе 1 км к перевалу
    camps, cname = [], {}
    if days and days > 1:
        day = total / days; passes = [D[m] for m in marks[1:-1]]
        # стоянки OpenStreetMap у нитки (до 400 м): ближайший к ней номер точки
        near = []
        for la, lo, nm, kd in sites:
            k = math.cos(math.radians(la)); best = None
            for i in range(0, len(G), 2):
                if abs(G[i][0] - la) > 0.005 or abs(G[i][1] - lo) > 0.007: continue
                d = math.hypot((G[i][0] - la) * 110540, (G[i][1] - lo) * 111320 * k)
                if best is None or d < best[0]: best = (d, i)
            if best and best[0] < 400: near.append((best[1], nm if nm and kd in nm.lower() else f'{kd} «{nm}»' if nm else kd))
        for k in range(1, days):
            t = day * k
            # сначала — известная стоянка в пределах ±25 % дневного перехода, ближе к границе дня
            ok_site = [(abs(D[i] - t), i, nm) for i, nm in near if abs(D[i] - t) <= 0.25 * day and not any(abs(D[i] - pd) < 1000 for pd in passes) and i not in camps]
            if ok_site:
                _, i, nm = min(ok_site); camps.append(i); cname[i] = nm; continue
            # нет — самая низкая точка в окне ±15 % (долина, а не гребень)
            lo_, hi_ = t - 0.15 * day, t + 0.15 * day
            best = None
            for i in range(len(G)):
                if not lo_ <= D[i] <= hi_ or any(abs(D[i] - pd) < 1000 for pd in passes): continue
                if best is None or E[i] < E[best]: best = i
            if best is None: best = min(range(len(G)), key=lambda i: abs(D[i] - t))
            camps.append(best)
        camps.sort()
    # ключевые точки по ходу: старт, перевалы и ночёвки вперемешку по месту на нитке, финиш
    K = [(marks[i], keys[i]) for i in range(len(keys))] + [(i, dict(lat=round(G[i][0], 6), lon=round(G[i][1], 6), kind='camp', name=cname.get(i, ''), h=int(E[i]))) for i in camps]
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
            plan_legs.append(dict(mode=mode, ok=legs[leg_at(gi)][1], pts=[[round(q[0], 6), round(q[1], 6)] for q in (G[gi:gj + 1] if gj > gi else [G[gi], G[gj]])]))
        plan_pts.append(dict(lat=round(k['lat'], 6), lon=round(k['lon'], 6), kind=k['kind'], name=k.get('name', ''), h=k.get('h'), grade=k.get('grade'), ref=k.get('ref'),
                             mode=mode if n + 1 < len(K) else ('trail' if trail else 'terrain')))
    nolink = sum(1 for pts, ok, mode in legs if trail and not ok)
    step = max(1, len(G) // 300)
    return dict(km=round(total / 1000, 1), up=int(up), dn=int(dn), nolink=nolink, legs=len(legs),
                passes=[dict(name=c['name'], grade=c['grade'], h=c['h'], note=c['note'], ref=c['id']) for c in chain],
                camps=[dict(h=int(E[i]), km=round(D[i] / 1000, 1), name=cname.get(i, '')) for i in camps],
                line=[[round(q[0], 5), round(q[1], 5)] for q in G[::step]] + [[round(G[-1][0], 5), round(G[-1][1], 5)]],
                plan=dict(pts=plan_pts, legs=plan_legs))


def propose(start, finish, km, quota=None, days=None, trail=True, kind='', exclude=(), must=(), k=4, progress=None):
    """quota — {категория: (от, до)}; must — перевалы, через которые маршрут идёт обязательно.
    progress(**поля) — ход работы для показа на карте: этап, кандидаты, перебираемые цепочки, готовые варианты. → {ok, variants | msg}."""
    rep = progress or (lambda **kw: None)
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
    rep(stage=f'Нашёл {len(C)} перевалов-кандидатов — перебираю цепочки', pct=8, cands=[[round(c['p'][0], 5), round(c['p'][1], 5), c['grade']] for c in C])
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
    picks = diverse(found, C, k + 2, must)
    sites = []
    if days and days > 1:      # стоянки из OpenStreetMap в охвате цепочек — ночёвки встанут на них, где получится
        rep(stage='Ищу стоянки и приюты по OpenStreetMap', pct=40)
        P_ = [start, finish] + [C[j]['p'] for _, sq, _ in picks for j in sq]
        try: sites = suggest.osm_camps(min(p[0] for p in P_) - 0.03, max(p[0] for p in P_) + 0.03, min(p[1] for p in P_) - 0.04, max(p[1] for p in P_) + 0.04)
        except Exception: sites = []
    lines = []
    with ThreadPoolExecutor(6) as ex:
        V = []
        for i, (sc, seq, tot) in enumerate(picks):
            rep(stage=f'Прокладываю вариант {i + 1} из {len(picks)} по тропам и рельефу', pct=40 + int(58 * i / len(picks)), sketch=[], trying=[[round(start[0], 5), round(start[1], 5)]] + [[round(C[j]['p'][0], 5), round(C[j]['p'][1], 5)] for j in seq] + [[round(finish[0], 5), round(finish[1], 5)]])
            try: v = build(start, finish, [C[j] for j in seq], trail, kind, days, ex, sites)
            except RuntimeError: continue      # высоты не скачались — вариант пропускаем
            V.append(v); lines.append(v['line']); rep(built=list(lines))
    if not V: return dict(ok=False, msg='Не получилось посчитать варианты — высоты или тропы сейчас недоступны. Попробуй ещё раз через минуту.')
    V.sort(key=lambda v: abs(v['km'] * 1000 - target) / target + 0.15 * v['nolink'] / max(1, v['legs']))
    U = []
    for v in V:      # разные перевалы, а путь один и тот же (перевалы рядом на одной нитке) — это не другой вариант
        if any(abs(v['km'] - u['km']) < 0.5 and abs(v['up'] - u['up']) < 60 for u in U): continue
        U.append(v)
    V = U[:k]
    for v in V: v['delta'] = round(v['km'] - float(km), 1)
    return dict(ok=True, variants=V, cands=len(C))
