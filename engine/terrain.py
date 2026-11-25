"""Путь по рельефу — там, где тропы нет (подход к перевалу, ледник, гребень): самый быстрый для пешехода путь по модели высот.

Стоимость шага — время по формуле Тоблера (скорость падает с крутизной, вниз чуть быстрее, чем вверх), а склоны круче
STEEP° — почти непроходимы (в 25 раз дороже): путь обходит скалы и сбросы, идёт долиной и выходит на седловину.
Поиск — расслабление на сетке numpy (8 направлений) до сходимости; сетка ~28 м (зум 12), для длинных отрезков крупнее.
"""
import math
import numpy as np
from . import dem
from .classify import dist

STEEP = 35.0
MAX_CELLS = 260000


def _ll(px, py, z):
    n = 256 * 2 ** z
    lon = px / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / n))))
    return lat, lon


def _px(lat, lon, z):
    x, y = dem._txy(lat, lon, z)
    return x * 256, y * 256


def _simplify(pts, tol=12.0):
    if len(pts) < 3: return pts
    keep = [0, len(pts) - 1]; st = [(0, len(pts) - 1)]
    k = math.cos(math.radians(pts[0][0]))
    X = [p[1] * 111320 * k for p in pts]; Y = [p[0] * 110540 for p in pts]
    while st:
        a, b = st.pop(); dx, dy = X[b] - X[a], Y[b] - Y[a]; D = math.hypot(dx, dy); best, bi = -1, None
        for i in range(a + 1, b):
            d = abs(dx * (Y[a] - Y[i]) - dy * (X[a] - X[i])) / D if D else math.hypot(X[i] - X[a], Y[i] - Y[a])
            if d > best: best, bi = d, i
        if bi is not None and best > tol: keep.append(bi); st += [(a, bi), (bi, b)]
    return [pts[i] for i in sorted(set(keep))]


def path(a, b, pad=0.35, avoid=()):
    """Путь по рельефу от a до b ([lat, lon]) → (точки, {'km', 'maxdeg'}) или (None, None), если не вышло.
    avoid — [(lat, lon, радиус м)]: места, через которые идти нельзя (в 60 раз дороже)."""
    span = dist(a, b)
    if span < 60: return [list(a), list(b)], dict(km=round(span / 1000, 2), maxdeg=0)
    m = max(800.0, span * pad)      # запас вокруг: путь может обходить гребень
    la0, la1 = min(a[0], b[0]) - m / 110540, max(a[0], b[0]) + m / 110540
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    lo0, lo1 = min(a[1], b[1]) - m / (111320 * k), max(a[1], b[1]) + m / (111320 * k)
    z = 12
    while z > 9:
        x0, y0 = _px(la1, lo0, z); x1, y1 = _px(la0, lo1, z)
        if (x1 - x0) * (y1 - y0) <= MAX_CELLS: break
        z -= 1
    try: G, gx, gy = dem.grid(la0, la1, lo0, lo1, z)
    except Exception: return None, None
    x0, y0 = _px(la1, lo0, z); x1, y1 = _px(la0, lo1, z)
    c0, r0, c1, r1 = int(x0 - gx), int(y0 - gy), int(x1 - gx) + 1, int(y1 - gy) + 1
    E = G[r0:r1, c0:c1].astype(np.float64)
    H, W = E.shape
    if H < 3 or W < 3: return None, None
    E = (E + np.roll(E, 1, 0) + np.roll(E, -1, 0) + np.roll(E, 1, 1) + np.roll(E, -1, 1)) / 5      # шум модели рельефа — не скалы
    cell = 40075016.7 * k / (256 * 2 ** z)      # метров в пикселе на этой широте
    INF = 1e18
    dirs = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
    cost = []
    for dy, dx in dirs:      # время шага из соседа (y-dy, x-dx) в клетку (y, x)
        h = cell * math.hypot(dy, dx)
        Eprev = np.full_like(E, np.nan)
        ys, yd = (slice(dy, None), slice(0, H - dy)) if dy >= 0 else (slice(0, H + dy), slice(-dy, None))
        xs, xd = (slice(dx, None), slice(0, W - dx)) if dx >= 0 else (slice(0, W + dx), slice(-dx, None))
        Eprev[ys, xs] = E[yd, xd]
        s = (E - Eprev) / h
        v = 6.0 * np.exp(-3.5 * np.abs(s + 0.05))      # км/ч, Тоблер
        t = h / (v * 1000 / 3600)
        t = np.where(np.degrees(np.arctan(np.abs(s))) > STEEP, t * 25, t)
        cost.append(np.where(np.isnan(t), INF, t))
    if avoid:      # запрещённые седловины — почти непроходимы
        yy, xx = np.mgrid[0:H, 0:W]; mask = np.zeros((H, W), dtype=bool)
        for la, lo, r in avoid:
            px, py = _px(la, lo, z); cx, cy = px - gx - c0, py - gy - r0; rr = r / cell
            if -rr <= cx <= W + rr and -rr <= cy <= H + rr: mask |= (xx - cx) ** 2 + (yy - cy) ** 2 <= rr * rr
        if mask.any(): cost = [np.where(mask, t * 60, t) for t in cost]
    ra, ca = _px(a[0], a[1], z); rb, cb = _px(b[0], b[1], z)
    sa = (min(H - 1, max(0, int(ca - gy) - r0)), min(W - 1, max(0, int(ra - gx) - c0)))
    sb = (min(H - 1, max(0, int(cb - gy) - r0)), min(W - 1, max(0, int(rb - gx) - c0)))
    A = np.full((H, W), INF); A[sa] = 0.0; P = np.full((H, W), -1, dtype=np.int8)
    for it in range(4 * (H + W)):
        best = A.copy(); arg = P.copy()
        for d, (dy, dx) in enumerate(dirs):
            sh = np.full_like(A, INF)
            ys, yd = (slice(dy, None), slice(0, H - dy)) if dy >= 0 else (slice(0, H + dy), slice(-dy, None))
            xs, xd = (slice(dx, None), slice(0, W - dx)) if dx >= 0 else (slice(0, W + dx), slice(-dx, None))
            sh[ys, xs] = A[yd, xd]
            cand = sh + cost[d]
            better = cand < best - 1e-9
            best = np.where(better, cand, best); arg = np.where(better, d, arg)
        if np.array_equal(best, A): break
        A, P = best, arg
    if A[sb] >= INF / 2: return None, None
    cells = [sb]; y, x = sb
    for _ in range(H * W):
        if (y, x) == sa: break
        d = P[y, x]
        if d < 0: break
        dy, dx = dirs[d]; y, x = y - dy, x - dx; cells.append((y, x))
    cells.reverse()
    pts = [list(_ll(gx + c0 + x + 0.5, gy + r0 + y + 0.5, z)) for y, x in cells]
    pts = [list(a)] + pts[1:-1] + [list(b)]
    pts = [[round(p[0], 6), round(p[1], 6)] for p in _simplify(pts)]
    return pts, dict(km=round(sum(dist(p, q) for p, q in zip(pts, pts[1:])) / 1000, 2), maxdeg=max_slope(pts))


_SADDLE = {}

def saddle(p, h=None):
    """Седловина перевала по рельефу: две долины по разные стороны гребня.
    Вокруг перевала на кругах 400–900 м ищется самое низкое направление (первая долина) и самое низкое в противоположной
    стороне (вторая); гребень — направления поперёк, они выше седловины. Из каждой стороны точка спускается по направлению,
    пока не станет на 250 м ниже перевала (до 2,5 км) — это «вход» и «выход».
    → (сторона_1, сторона_2, высота седловины) или None, если по рельефу это не седловина (плато, гребень, неточные координаты)."""
    key = (round(p[0], 5), round(p[1], 5))
    if key in _SADDLE: return _SADDLE[key]
    k = math.cos(math.radians(p[0]))
    at = lambda ang, r: (p[0] + r * math.cos(math.radians(ang)) / 110540, p[1] + r * math.sin(math.radians(ang)) / (111320 * k))
    angs = list(range(0, 360, 10)); rad = (400, 650, 900)
    probe = [at(a_, r) for a_ in angs for r in rad] + [at(a_, r) for a_ in angs for r in range(1000, 2600, 150)] + [tuple(p)]
    try:
        S = dem.Sampler(probe, z=12)
        # точка перевала в каталоге бывает неточной: седловина — самая высокая из низких точек рядом не ищем, берём рельеф в 120 м
        e0 = max(S(*at(a_, 120)) for a_ in range(0, 360, 45)) if h is None else None
        e0 = S(p[0], p[1]) if e0 is None else min(e0, S(p[0], p[1]) + 60)
        ring = {a_: sum(S(*at(a_, r)) for r in rad) / len(rad) for a_ in angs}
    except Exception:
        _SADDLE[key] = None; return None
    a1 = min(angs, key=lambda a_: ring[a_])
    opp = [a_ for a_ in angs if abs(((a_ - a1 + 180) % 360) - 180) >= 120]
    a2 = min(opp, key=lambda a_: ring[a_])
    side = [a_ for a_ in angs if 60 <= abs(((a_ - a1 + 180) % 360) - 180) <= 120]
    ridge = max(ring[a_] for a_ in side) if side else e0
    if not (ring[a1] < e0 - 50 and ring[a2] < e0 - 50 and ridge > min(ring[a1], ring[a2]) + 80):
        _SADDLE[key] = None; return None
    def down(ang):
        last = at(ang, 900)
        for r in range(1000, 2600, 150):
            q = at(ang, r); last = q
            if S(*q) <= e0 - 250: break
        return [round(last[0], 6), round(last[1], 6)]
    res = (down(a1), down(a2), int(round(e0)))
    _SADDLE[key] = res
    return res


def crosses(G, E, D, i, win=350.0, drop=80.0):
    """Нитка переваливает в точке i: это местный максимум высоты (±350 м по пути), а до и после (через ~1,2 км) — ниже на 80+ м."""
    n = len(G); lo = hi = i
    while lo > 0 and D[i] - D[lo] < win: lo -= 1
    while hi < n - 1 and D[hi] - D[i] < win: hi += 1
    top = max(E[lo:hi + 1])
    if E[i] < top - 60: return False
    a = b = i
    while a > 0 and D[i] - D[a] < 1200: a -= 1
    while b < n - 1 and D[b] - D[i] < 1200: b += 1
    return min(E[lo:i + 1] + [E[a]]) < top - drop and min(E[i:hi + 1] + [E[b]]) < top - drop and a < i < b


def trail_leg(a, b, kind='', approach=250.0, avoid=()):
    """Отрезок по тропам (BRouter), а где тропа не доходит до точки — подход по рельефу (к перевалу от последней тропы).
    Тропы нет вовсе или BRouter уводит в обход через полрайона — весь отрезок по рельефу.
    → (точки, ok — нашлась ли тропа, 'trail' | 'terrain', maxdeg)"""
    from . import trackfix
    st = dist(a, b)
    near = [x for x in avoid if dist((x[0], x[1]), a) + dist((x[0], x[1]), b) < st + 4000][:40]      # только у этого отрезка
    pts = trackfix.route(a, b, kind, near) if st < 40000 else None
    if pts and len(pts) > 1:
        L = sum(dist(p, q) for p, q in zip(pts, pts[1:]))
        if st > 1000 and L > 2.5 * st: pts = None
    if not pts:
        tp, info = path(a, b, avoid=near)
        if tp: return tp, False, 'terrain', info['maxdeg']
        return [list(a), list(b)], False, 'line', max_slope([list(a), list(b)])
    out = [list(q) for q in pts]
    if dist(a, out[0]) > approach:
        tp, _ = path(a, out[0], avoid=near); out = (tp[:-1] if tp else [list(a)]) + out
    else: out[0] = list(a)
    if dist(out[-1], b) > approach:
        tp, _ = path(out[-1], b, avoid=near); out = out + (tp[1:] if tp else [list(b)])
    else: out[-1] = list(b)
    return out, True, 'trail', None


def max_slope(pts, win=60.0):
    """Наибольшая крутизна по нитке, градусы (по рельефу, на отрезках от 60 м)."""
    if len(pts) < 2: return 0
    G = [pts[0]]
    for q in pts[1:]:
        p = G[-1]; n = int(dist(p, q) // 30)
        for j in range(1, n + 1): G.append([p[0] + (q[0] - p[0]) * j / (n + 1), p[1] + (q[1] - p[1]) * j / (n + 1)])
        G.append(q)
    try:
        S = dem.Sampler(G, z=12); E = [S(q[0], q[1]) for q in G]
    except Exception: return None
    D = [0.0]
    for i in range(1, len(G)): D.append(D[-1] + dist(G[i - 1], G[i]))
    best, j = 0.0, 0
    for i in range(len(G)):
        while j < len(G) and D[j] - D[i] < win: j += 1
        if j >= len(G): break
        best = max(best, math.degrees(math.atan(abs(E[j] - E[i]) / (D[j] - D[i]))))
    return int(round(best))
