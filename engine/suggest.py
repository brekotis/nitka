"""Подсказки по треку: ночёвки в концах дней и перевалы/вершины по маршруту из каталога Вестры (тот же, что на nakarte.me).

Ничего не добавляет само — возвращает списки, человек выбирает.
"""
import gzip, json, math, os, re, time, urllib.request
from datetime import date, timedelta
from . import mapkit
from .classify import OBJ_TYPES, dist

WESTRA_URL = 'https://nakarte.me/westraPasses/westra_passes.json'
GR = {'1a': '1А', '1b': '1Б', '2a': '2А', '2b': '2Б', '3a': '3А', '3b': '3Б', 'nograde': 'н/к'}
_cat = None

def _path(): return os.path.join(mapkit.CACHE, 'westra_passes.json')

def refresh(max_age_days=7):
    """Скачивает каталог, если его нет или он старше max_age_days. Сбой сети старый файл не портит."""
    path = _path()
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_days * 86400: return False
    req = urllib.request.Request(WESTRA_URL, headers={'User-Agent': mapkit.UA})
    with urllib.request.urlopen(req, timeout=60) as r: data = r.read()
    if data[:2] == b'\x1f\x8b': data = gzip.decompress(data)          # nakarte отдаёт файл сжатым, о чём бы ни просили
    if len(json.loads(data)) < 1000: raise ValueError('каталог подозрительно короткий')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'wb') as f: f.write(data)
    os.replace(path + '.tmp', path)
    return True

def catalog():
    """Каталог перевалов с диска (сервер обновляет его в фоне раз в неделю); если файла ещё нет — качаем сейчас."""
    global _cat
    path = _path()
    if not os.path.exists(path):
        try: refresh()
        except Exception: raise RuntimeError('Каталог перевалов сейчас недоступен — попробуйте позже.')
    mt = os.path.getmtime(path)
    if _cat is None or _cat[0] != mt:
        with open(path, encoding='utf-8') as f: _cat = (mt, json.load(f))
    return _cat[1]

def info():
    path = _path()
    return dict(n=len(catalog()), updated=os.path.getmtime(path)) if os.path.exists(path) else None

def day_chains(proj):
    """{день: [(lat, lon), ...]} — нитка дня по порядку кусков; без разбивки по дням всё считается днём 1."""
    pcs = [p for p in sorted(proj.get('pieces', []), key=lambda p: p.get('order', 0)) if p.get('role') in ('main', 'radial') and len(p.get('pts', [])) > 1]
    any_day = any(p.get('day') for p in pcs); out = {}
    for p in pcs:
        d = p.get('day') if any_day else 1
        if not d: continue
        out.setdefault(int(d), []).extend((q[0], q[1]) for q in p['pts'])
    return dict(sorted(out.items()))

def _seg_dist(p, a, b, k):
    """Расстояние (м) от точки p до отрезка ab; k — cos широты."""
    ax, ay = (a[1] - p[1]) * k, a[0] - p[0]; bx, by = (b[1] - p[1]) * k, b[0] - p[0]
    dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
    return math.hypot(ax + dx * t, ay + dy * t) * 111195.0

def passes(proj, radius=400.0, sure=150.0):
    """Перевалы и вершины каталога у нитки: [{type, name, grade, h, lat, lon, day, dist, sure, note}] по ходу маршрута."""
    chains = day_chains(proj)
    if not chains: return []
    allp = [q for pts in chains.values() for q in pts]
    la0, la1 = min(q[0] for q in allp) - 0.006, max(q[0] for q in allp) + 0.006
    lo0, lo1 = min(q[1] for q in allp) - 0.009, max(q[1] for q in allp) + 0.009
    have = [(q['lat'], q['lon']) for q in proj.get('points', []) if q.get('type') in OBJ_TYPES]
    out = []
    for c in catalog():
        try: la, lo = float(c['latlon'][0]), float(c['latlon'][1])
        except Exception: continue
        if not (la0 <= la <= la1 and lo0 <= lo <= lo1): continue
        k = math.cos(math.radians(la)); best = None
        for d, pts in chains.items():
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                if min(abs(a[0] - la), abs(b[0] - la)) > 0.02 or min(abs(a[1] - lo), abs(b[1] - lo)) > 0.03: continue
                dd = _seg_dist((la, lo), a, b, k)
                if best is None or dd < best[0] - 1e-6: best = (dd, d, i)
        if not best or best[0] > radius: continue
        if any(dist(h, (la, lo)) < 300 for h in have): continue
        summit = bool(c.get('is_summit'))
        raw = str(c.get('name') or '').strip()
        name = (('в. ' if summit else 'пер. ') + raw) if raw else ('вершина' if summit else 'перевал')
        try: h = int(float(c.get('elevation'))) if c.get('elevation') else None
        except Exception: h = None
        note = '; '.join(x for x in [str(c.get('connects') or '').strip(), ('склоны: ' + str(c['slopes']).strip()) if c.get('slopes') else ''] if x)
        out.append(dict(type='peak' if summit else 'pass', name=name, grade=GR.get(str(c.get('grade_eng') or '').lower()), h=h, lat=round(la, 6), lon=round(lo, 6),
                        day=best[1], i=best[2], dist=int(round(best[0])), sure=best[0] <= sure, note=note[:240], alt=(c.get('altnames') or '')[:80]))
    out.sort(key=lambda o: (o['day'], o['i']))
    for o in out: o.pop('i')
    return out

def camps(proj):
    """Ночёвки в концах дней: [{lat, lon, date|None, after_day, nights, note}] — там, где ночёвка ещё не отмечена."""
    pcs = [p for p in sorted(proj.get('pieces', []), key=lambda p: p.get('order', 0)) if p.get('role') in ('main', 'radial') and p.get('day') and len(p.get('pts', [])) > 1]
    days = {}
    for p in pcs: days.setdefault(int(p['day']), dict(main=[], rad=[]))['rad' if p.get('role') == 'radial' else 'main'].append(p['pts'])
    ds = sorted(days)
    if len(ds) < 2: return []
    start = date.fromisoformat(proj['meta']['start_date']) if proj.get('meta', {}).get('start_date') else None
    def ends(d):
        D = days[d]
        if D['main']: return tuple(D['main'][0][0][:2]), tuple(D['main'][-1][-1][:2])
        return tuple(D['rad'][0][0][:2]), tuple(D['rad'][0][0][:2])       # только радиалка: вышли и вернулись
    have = [q for q in proj.get('points', []) if q.get('type') == 'camp']
    sites = [(q['lat'], q['lon']) for q in proj.get('points', []) if q.get('type') in ('camp', 'campsite')]      # уже отмеченные стоянки любого вида
    out = []
    for d, nxt in zip(ds, ds[1:]):
        _, e = ends(d); s2, _ = ends(nxt)
        gap = dist(e, s2)
        pt = e if gap > 300 else ((e[0] + s2[0]) / 2, (e[1] + s2[1]) / 2)
        dt = (start + timedelta(days=d - 1)).isoformat() if start else None
        if any(dist((q['lat'], q['lon']), pt) < 400 and (not dt or not q.get('date') or q['date'] == dt) for q in have): continue
        if dt and any(q.get('date') == dt for q in have): continue
        if any(dist(sp, pt) < 400 for sp in sites): continue
        note = f'конец дня {d}' + (f' и начало дня {nxt}' if gap <= 300 else f'; день {nxt} начинается в {gap / 1000:.1f} км отсюда — возможно, был переезд'.replace('.', ','))
        out.append(dict(lat=round(pt[0], 6), lon=round(pt[1], 6), date=dt, after_day=d, nights=nxt - d, note=note))
    return out
