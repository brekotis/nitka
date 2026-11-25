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


def candidates(start, finish, budget, gmin, gmax):
    lo, hi = GRADES.index(gmin), GRADES.index(gmax)
    out = []
    for c in suggest.catalog():
        if c.get('is_summit'): continue
        g = suggest.GR.get(str(c.get('grade_eng') or '').lower())
        if g not in GRADES or not lo <= GRADES.index(g) <= hi: continue
        try: p = (float(c['latlon'][0]), float(c['latlon'][1]))
        except Exception: continue
        if dist(start, p) + dist(p, finish) > budget * 1.05: continue
        try: h = int(float(c.get('elevation'))) if c.get('elevation') else None
        except Exception: h = None
        raw = str(c.get('name') or '').strip()
        nm = ('пер. ' + raw) if raw and not raw.replace(' ', '').isdigit() else f'перевал {raw or h or ""}'.strip()      # безымянные в каталоге записаны высотой
        out.append(dict(id=str(c.get('id') or f'{p[0]:.5f},{p[1]:.5f}'), p=p, grade=g, h=h, name=nm,
                        note=str(c.get('connects') or '').strip()[:160]))
    return out


def search(start, finish, budget, C, nmin, nmax, exclude=()):
    """Лучевой поиск цепочек перевалов. → [(оценка, [индексы кандидатов], прямая длина)] лучшие сверху."""
    C = [c for c in C if c['id'] not in exclude]
    if not C: return [], C
    U_TURN, PEN = 130.0, 0.35      # пришли к перевалу и ушли той же долиной — это не «через перевал»
    states = [((), 0.0, start, 0.0)]; done = []
    for depth in range(1, nmax + 1):
        scored = []
        for seq, L, last, pen in states:
            prev = (C[seq[-2]]['p'] if len(seq) > 1 else start) if seq else None
            for j, c in enumerate(C):
                if j in seq or any(dist(C[k]['p'], c['p']) < MIN_GAP for k in seq): continue
                L2 = L + dist(last, c['p'])
                if L2 + dist(c['p'], finish) > budget * 1.15: continue
                p2 = pen + (PEN if prev is not None and _angle(prev, last, c['p']) > U_TURN else 0.0)
                tot = L2 + dist(c['p'], finish)
                end_pen = PEN if _angle(last, c['p'], finish) > U_TURN and dist(c['p'], finish) > 500 else 0.0
                scored.append((abs(tot - budget) / budget + p2, seq + (j,), L2, c['p'], p2, tot, end_pen))
        scored.sort(key=lambda x: x[0])
        scored = scored[:BEAM]
        states = [(x[1], x[2], x[3], x[4]) for x in scored]
        if depth >= nmin:
            for sc, seq, L2, p, pen, tot, end_pen in scored:
                if abs(tot - budget) / budget < 0.35: done.append((sc + end_pen, list(seq), tot))
        if not states: break
    done.sort(key=lambda x: x[0])
    return done, C


def diverse(found, C, k):
    pick = []
    for sc, seq, tot in found:
        ids = {C[j]['id'] for j in seq}
        if any(len(ids & s) / max(1, min(len(ids), len(s))) > 0.5 for _, s in pick): continue
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


def build(start, finish, chain, trail, kind, days, ex):
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
    camps = []
    if days and days > 1:
        day = total / days; passes = [D[m] for m in marks[1:-1]]
        for k in range(1, days):
            t = day * k; lo_, hi_ = t - 0.15 * day, t + 0.15 * day
            best = None
            for i in range(len(G)):
                if not lo_ <= D[i] <= hi_ or any(abs(D[i] - pd) < 1000 for pd in passes): continue
                if best is None or E[i] < E[best]: best = i
            if best is None: best = min(range(len(G)), key=lambda i: abs(D[i] - t))
            camps.append(best)
    # ключевые точки по ходу: старт, перевалы и ночёвки вперемешку по месту на нитке, финиш
    K = [(marks[i], keys[i]) for i in range(len(keys))] + [(i, dict(lat=round(G[i][0], 6), lon=round(G[i][1], 6), kind='camp', name='', h=int(E[i]))) for i in camps]
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
                camps=[dict(h=int(E[i]), km=round(D[i] / 1000, 1)) for i in camps],
                line=[[round(q[0], 5), round(q[1], 5)] for q in G[::step]] + [[round(G[-1][0], 5), round(G[-1][1], 5)]],
                plan=dict(pts=plan_pts, legs=plan_legs))


def propose(start, finish, km, gmin='1А', gmax='1Б', nmin=1, nmax=3, days=None, trail=True, kind='', exclude=(), k=4):
    """→ {ok, variants | msg}."""
    if gmin not in GRADES or gmax not in GRADES or GRADES.index(gmin) > GRADES.index(gmax): return dict(ok=False, msg='Категории перевалов: «от» не может быть сложнее «до».')
    nmin, nmax = max(1, min(int(nmin), 6)), max(1, min(int(nmax), 6)); nmax = max(nmin, nmax)
    target = float(km) * 1000; budget = target / (DETOUR if trail else 1.05)
    if dist(start, finish) > budget * 0.95:
        return dict(ok=False, msg=f'Старт и финиш дальше друг от друга, чем позволяет длина. Прибавь километров — хотя бы {int(dist(start, finish) * (DETOUR if trail else 1.05) / 1000 * 1.2)}.')
    C = candidates(start, finish, budget, gmin, gmax)
    if not C: return dict(ok=False, msg=f'В этом районе в пределах {int(km)} км нет перевалов {gmin}–{gmax} из каталога. Попробуй другие категории, больше километров или сдвинь старт.')
    found, C = search(start, finish, budget, C, nmin, nmax, set(exclude))
    if not found: return dict(ok=False, msg=f'Не сложилось ни одной цепочки из {nmin}–{nmax} перевалов на {int(km)} км. Попробуй меньше перевалов или больше километров.' + (' Часть перевалов уже показана — «Ещё варианты» ищет только новые.' if exclude else ''))
    picks = diverse(found, C, k + 2)
    with ThreadPoolExecutor(6) as ex:
        V = []
        for sc, seq, tot in picks:
            try: V.append(build(start, finish, [C[j] for j in seq], trail, kind, days, ex))
            except RuntimeError: continue      # высоты не скачались — вариант пропускаем
    if not V: return dict(ok=False, msg='Не получилось посчитать варианты — высоты или тропы сейчас недоступны. Попробуй ещё раз через минуту.')
    V.sort(key=lambda v: abs(v['km'] * 1000 - target) / target + 0.15 * v['nolink'] / max(1, v['legs']))
    U = []
    for v in V:      # разные перевалы, а путь один и тот же (перевалы рядом на одной нитке) — это не другой вариант
        if any(abs(v['km'] - u['km']) < 0.5 and abs(v['up'] - u['up']) < 60 for u in U): continue
        U.append(v)
    V = U[:k]
    for v in V: v['delta'] = round(v['km'] - float(km), 1)
    return dict(ok=True, variants=V, cands=len(C))
