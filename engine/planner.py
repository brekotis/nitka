"""Планировщик: маршрут, нарисованный по ключевым точкам (по тропам или напрямую), → куски по дням, ночёвки, перевалы.

План хранится в походе (proj['plan']): ключевые точки и геометрия отрезков между ними — так его можно открыть и поправить.
Куски и точки, сделанные из плана, помечены src='plan': при новом «Готово» они заменяются, остальное в походе не трогается.
"""
import math
from .classify import dist, length_km, uid
from . import dem

KINDS = ('via', 'camp', 'pass', 'peak')
MAX_PTS, MAX_LEG = 400, 20000
STEP_M = 50.0        # прямой отрезок дробится — высоты между точками берутся по рельефу


def densify(pts, step=STEP_M):
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        d = dist(a, b); n = int(d // step)
        for k in range(1, n + 1):
            f = k / (n + 1); out.append([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f])
        out.append(b)
    return out


def clean(plan):
    """Проверка того, что прислал браузер: ключевые точки и отрезки между ними."""
    K = []
    for p in (plan.get('pts') or [])[:MAX_PTS]:
        try: lat, lon = float(p['lat']), float(p['lon'])
        except (KeyError, TypeError, ValueError): continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180): continue
        kind = p.get('kind') if p.get('kind') in KINDS else 'via'
        try: h = int(p['h']) if p.get('h') not in (None, '') else None
        except (TypeError, ValueError): h = None
        K.append(dict(lat=round(lat, 6), lon=round(lon, 6), kind=kind, name=str(p.get('name') or '')[:80], h=h,
                      grade=str(p.get('grade') or '')[:6] or None, ref=str(p.get('ref') or '')[:40] or None,
                      mode=p.get('mode') if p.get('mode') in ('line', 'terrain') else 'trail'))
    try: tol = max(0, min(1000, int(plan.get('tol') or 0)))
    except (TypeError, ValueError): tol = 0
    legs = []
    for i, g in enumerate((plan.get('legs') or [])[:max(0, len(K) - 1)]):
        try: pts = [[round(float(q[0]), 6), round(float(q[1]), 6)] for q in (g.get('pts') or [])][:MAX_LEG]
        except (TypeError, ValueError, IndexError): pts = []
        a, b = [K[i]['lat'], K[i]['lon']], [K[i + 1]['lat'], K[i + 1]['lon']]
        if len(pts) < 2: pts = [a, b]
        # отрезок от точки до точки: подход к тропе и сход с неё — часть пути; но точка «примерно» (tol) — нитка остаётся на тропе
        t_ = tol if K[i]['mode'] == 'trail' else 0
        if dist(pts[0], a) <= t_: pass
        elif dist(pts[0], a) > 5: pts.insert(0, a)
        else: pts[0] = a
        if dist(pts[-1], b) <= t_: pass
        elif dist(pts[-1], b) > 5: pts.append(b)
        else: pts[-1] = b
        legs.append(dict(mode=K[i]['mode'], pts=pts, ok=bool(g.get('ok', True))))
    while len(legs) < len(K) - 1:
        i = len(legs); legs.append(dict(mode='line', pts=[[K[i]['lat'], K[i]['lon']], [K[i + 1]['lat'], K[i + 1]['lon']]], ok=True))
    return dict(pts=K, legs=legs, tol=tol)


def title(k):
    if not k: return ''
    n = k.get('name') or ''
    if k['kind'] == 'camp' and not n: return 'ночёвка'
    return n


def apply(proj, plan):
    """План → куски по дням (ночёвки делят маршрут), ночёвки, перевалы и вершины. Возвращает {ok, msg, days, km}."""
    P = clean(plan); K, legs = P['pts'], P['legs']
    if len(K) < 2: return dict(ok=False, msg='Нужно хотя бы две точки маршрута.')
    days = []; cur = [list(legs[0]['pts'][0])]; first = 0      # с допуском старт может быть на тропе, а не в самой точке
    for i, g in enumerate(legs):
        pts = densify(g['pts']) if g['mode'] != 'trail' else g['pts']
        cur.extend(pts[1:])
        last = i + 1 == len(K) - 1
        if K[i + 1]['kind'] == 'camp' or last:
            days.append((cur, first, i + 1)); cur = [pts[-1]]; first = i + 1
    allp = [q for d, _, _ in days for q in d]
    try:
        S = dem.Sampler(allp, z=13); ele = lambda q: round(S(q[0], q[1]), 1)
    except Exception: ele = None
    proj['pieces'] = [p for p in proj.get('pieces', []) if p.get('src') != 'plan']
    proj['points'] = [q for q in proj.get('points', []) if q.get('src') != 'plan']
    base_order = max([p.get('order', 0) for p in proj['pieces']] + [0]) + 1
    total = 0.0
    for n, (pts, a, b) in enumerate(days, 1):
        g = [[q[0], q[1]] + ([ele(q)] if ele else []) for q in pts]
        ta, tb = title(K[a]) or ('старт' if a == 0 else f'ночёвка {n - 1}'), title(K[b]) or ('финиш' if b == len(K) - 1 else f'ночёвка {n}')
        km = round(length_km(g), 2); total += km
        proj['pieces'].append(dict(id=uid(), src='plan', name=f'{ta} — {tb}', pts=g, day=n, role='main', order=base_order + n, km=km))
    day_of = {}
    for n, (_, a, b) in enumerate(days, 1):
        for i in range(a, b + 1): day_of.setdefault(i, n)
    for i, k in enumerate(K):
        h = k['h']
        if h is None and ele and k['kind'] in ('camp', 'pass', 'peak'):
            try: h = int(round(ele([k['lat'], k['lon']])))
            except Exception: h = None
        base = dict(id=uid(), src='plan', lat=k['lat'], lon=k['lon'], h=h, on_map=True)
        if k['kind'] == 'camp' and 0 < i < len(K) - 1:
            n = sum(1 for j in range(1, i + 1) if K[j]['kind'] == 'camp')
            proj['points'].append(dict(base, type='camp', name=k['name'], after_day=n, nights=1))
        elif k['kind'] in ('pass', 'peak'):
            nm = k['name'] or ('перевал' if k['kind'] == 'pass' else 'вершина')
            proj['points'].append(dict(base, type=k['kind'], name=nm, grade=k['grade'], day=day_of.get(i)))
    # перевалы каталога, через которые нарисованная нитка переваливает, но они не отмечены, — тоже в точки похода
    added = 0
    if ele:
        try:
            from . import routegen, suggest, terrain
            G = [[q[0], q[1]] for d, _, _ in days for q in d]
            E = [ele(q) for q in G]; D = [0.0]
            for i in range(1, len(G)): D.append(D[-1] + dist(G[i - 1], G[i]))
            have = {k['ref'] for k in K if k.get('ref')}
            la0, la1 = min(q[0] for q in G) - 0.01, max(q[0] for q in G) + 0.01; lo0, lo1 = min(q[1] for q in G) - 0.01, max(q[1] for q in G) + 0.01
            keyD = []
            for k in K:
                if k['kind'] in ('pass', 'peak'):
                    i = routegen._near_idx(G, (k['lat'], k['lon']), 300)
                    if i is not None: keyD.append(D[i])
            seen = []
            for c in suggest.catalog():
                if c.get('is_summit'): continue
                it = routegen._cat_item(c)
                if not it or it['id'] in have or not (la0 <= it['p'][0] <= la1 and lo0 <= it['p'][1] <= lo1): continue
                i = routegen._near_idx(G, it['p'])
                if i is None or not terrain.crosses(G, E, D, i) or any(abs(D[i] - d) < 800 for d in keyD + seen): continue
                seen.append(D[i])
                ends_, acc = [], 0.0
                for d_ in days: acc += length_km(d_[0]) * 1000; ends_.append(acc)
                dn = next((n for n, e_ in enumerate(ends_, 1) if D[i] <= e_ + 1), len(days))
                proj['points'].append(dict(id=uid(), src='plan', lat=round(it['p'][0], 6), lon=round(it['p'][1], 6), h=it['h'], on_map=True,
                                           type='pass', name=it['name'], grade=it['grade'], day=dn))
                added += 1
        except Exception: pass
    proj['plan'] = P
    proj.setdefault('meta', {})
    if not proj['meta'].get('mode'): proj['meta']['mode'] = 'plan'
    nd = len(days)
    return dict(ok=True, days=nd, km=round(total, 1),
                msg=f'Маршрут в походе: {nd} {"день" if nd % 10 == 1 and nd % 100 != 11 else "дня" if 2 <= nd % 10 <= 4 and not 12 <= nd % 100 <= 14 else "дней"}, '
                    f'{str(round(total, 1)).replace(".", ",")} км.' + (f' По пути нитка переваливает ещё через {added} {"перевал" if added % 10 == 1 and added % 100 != 11 else "перевала" if 2 <= added % 10 <= 4 and not 12 <= added % 100 <= 14 else "перевалов"} из каталога — они добавлены в точки.' if added else '') + ' Дальше — точки и карты, как обычно.')


def elevations(pts):
    """Высоты по рельефу для профиля в планировщике."""
    S = dem.Sampler(pts, z=13)
    return [round(S(q[0], q[1])) for q in pts]
