"""Поход как данные: дни, нитка, ночёвки, перевалы, высотные профили, километраж.

Trip(project) — всё, что раньше было зашито в kdata.py конкретного похода.
Километраж: длина нитки по карте × kcoef. Радиальные выходы — в одну сторону (если radial_one_way).
"""
import math
from datetime import date, timedelta
from . import dem
from .classify import TYPE, OBJ_TYPES, GRADES, GRADE_COL, BAND, dist, piece_times, time_info, trip_tz, hm, dur

STEP = 25.0        # шаг выборки по нитке, м
DENSE_M = 80.0     # медианный шаг трека, до которого его высотам верим (записанный GPS-трек); реже — нарисованный маршрут
GAP = 400.0        # разрыв между кусками одного дня, с которого это уже «переезд», м
PIN_M = 100        # привязка к высоте из подписи — если нитка прошла ближе 100 м

def _lp(p):
    return p.get('label_pos') if p.get('label_pos') in ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW') else None

def near(p, pts):
    return min(range(len(pts)), key=lambda i: dist(pts[i], p))

def hexcol(s, default=(70, 70, 70)):
    try:
        s = s.lstrip('#'); return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception: return default

def _seg_dist(p, a, b):
    """Расстояние от точки до отрезка, м (локальная плоская проекция)."""
    k = math.cos(math.radians(p[0])); mx, my = 111320 * k, 110540
    ax, ay = (a[1] - p[1]) * mx, (a[0] - p[0]) * my; bx, by = (b[1] - p[1]) * mx, (b[0] - p[0]) * my
    dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
    return math.hypot(ax + t * dx, ay + t * dy)

def _turn(r, lo=0, hi=None):
    """Точка разворота: самая дальняя от начала (в пределах [lo, hi])."""
    hi = len(r) - 1 if hi is None else hi
    return max(range(lo, hi + 1), key=lambda i: dist(r[0], r[i]))

def _shared_tail(r, c, near_m=70.0, skip_m=300.0, miss_m=150.0):
    """Обратный путь по своим же следам: с какой точки конец радиалки идёт по её началу.
    Возвращает (js, ip): js — первая точка общего «хвоста», ip — точка на пути туда, где хвост к нему подходит.
    Точку сравниваем только с тем, что пройдено раньше неё больше чем на skip_m, иначе любая точка «рядом» с соседней."""
    n = len(r)
    k0 = math.cos(math.radians(r[0][0])); mx, my = 111320 * k0, 110540; cell = 120.0
    grid = {}
    for i in range(n - 1):
        (ya, xa), (yb, xb) = r[i], r[i + 1]
        for cx in range(int(min(xa, xb) * mx // cell), int(max(xa, xb) * mx // cell) + 1):
            for cy in range(int(min(ya, yb) * my // cell), int(max(ya, yb) * my // cell) + 1):
                grid.setdefault((cx, cy), []).append(i)
    js, ip = n, None; miss = 0.0; j = n - 1
    while j > 0:
        lim = c[j] - skip_m; best = None; bi = None
        cx, cy = int(r[j][1] * mx // cell), int(r[j][0] * my // cell)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for i in grid.get((gx, gy), ()):
                    if c[i + 1] > lim: continue
                    d = _seg_dist(r[j], r[i], r[i + 1])
                    if best is None or d < best: best, bi = d, i
        if best is not None and best < near_m:
            js, ip = j, bi; miss = 0.0
        else:
            miss += c[j + 1] - c[j] if j + 1 < n else 0.0
            if miss > miss_m or lim <= 0: break
        j -= 1
    return js, ip

def _end_on_path(r):
    """Нарисованная радиалка кончается на своём же пути туда (подход + кольцо): индекс этой точки на пути, иначе None."""
    acc = 0.0; far = len(r) - 1
    for i in range(len(r) - 1, 0, -1):
        acc += dist(r[i], r[i - 1])
        if acc > 300: far = i - 1; break
    i = min(range(far + 1), key=lambda t: dist(r[t], r[-1]))
    return i if i > 0 and dist(r[i], r[-1]) < 150 else None

def radial_info(r, shape='auto'):
    """Форма радиального выхода по геометрии и выбору туриста (shape: auto | back | loop | lolli).
    drawn    — нарисован в одну сторону (конец далеко от начала): обратно тем же путём;
    recorded — записан целиком туда и обратно: делится в точке разворота;
    loop     — кольцо: обратного пути нет;
    lolli    — подход туда-обратно по одной тропе и кольцо на конце: обратный путь — только подход.
    Возвращает dict(form, split, auto): split — индекс, с которого начинается обратный путь (для recorded/lolli)."""
    n = len(r)
    if n < 3: return dict(form='drawn', split=None, auto='drawn')
    c = [0.0]
    for a, b in zip(r, r[1:]): c.append(c[-1] + dist(a, b))
    L = c[-1]
    closed = dist(r[0], r[-1]) < max(400.0, 0.05 * L)
    auto = 'drawn'; split = {}
    if closed:
        js, ip = _shared_tail(r, c)
        back = L - c[js] if js < n else 0.0
        if back < max(200.0, 0.05 * L): auto = 'loop'
        else:
            ring = c[js] - c[ip]
            k = _turn(r, ip, js) if ip is not None else _turn(r)
            split['back'] = k
            if ring < max(300.0, 0.1 * L): auto = 'recorded'
            else: auto = 'lolli'; split['lolli'] = js
        split.setdefault('back', _turn(r))
    else:
        i = _end_on_path(r)
        if i is not None: auto = 'lolli'; split['lolli'] = i; split['drawn_lolli'] = True
    want = shape if shape in ('back', 'loop', 'lolli') else None
    if want == 'loop': form = 'loop'
    elif want == 'back': form = 'recorded' if closed and 0 < split.get('back', 0) < n - 1 else 'drawn'
    elif want == 'lolli': form = 'lolli' if 'lolli' in split else ('loop' if closed else 'drawn')
    else: form = auto
    sp = split.get('lolli') if form == 'lolli' else split.get('back') if form == 'recorded' else None
    return dict(form=form, split=sp, auto=auto, drawn_lolli=form == 'lolli' and split.get('drawn_lolli', False))

class SegIndex:
    """Ближайший отрезок среди линий: сетка ~200 м, чтобы не перебирать всю нитку для каждой точки."""
    def __init__(self, lines, cell=200.0):
        lat0 = next((l[0][0] for l in lines if l), 0.0)
        self.mx = 111320 * math.cos(math.radians(lat0)); self.my = 110540; self.cell = cell; self.g = {}; self.segs = []
        for li, l in enumerate(lines):
            for i in range(len(l) - 1):
                a, b = l[i], l[i + 1]; k = len(self.segs); self.segs.append((a, b, li, i))
                for cx in range(int(min(a[1], b[1]) * self.mx // cell), int(max(a[1], b[1]) * self.mx // cell) + 1):
                    for cy in range(int(min(a[0], b[0]) * self.my // cell), int(max(a[0], b[0]) * self.my // cell) + 1):
                        self.g.setdefault((cx, cy), []).append(k)

    def near(self, p, reach=200.0):
        """(расстояние, номер линии, номер отрезка в ней) до ближайшего отрезка не дальше reach (иначе расстояние — бесконечность)."""
        r = int(math.ceil(reach / self.cell)); cx, cy = int(p[1] * self.mx // self.cell), int(p[0] * self.my // self.cell)
        best = (float('inf'), None, None); seen = set()
        for gx in range(cx - r, cx + r + 1):
            for gy in range(cy - r, cy + r + 1):
                for k in self.g.get((gx, gy), ()):
                    if k in seen: continue
                    seen.add(k); a, b, li, i = self.segs[k]; d = _seg_dist(p, a, b)
                    if d < best[0]: best = (d, li, i)
        return best

def radial_form(r, shape='auto'):
    return radial_info(r, shape)['form']

def there_back(r, shape='auto'):
    """Радиальный выход: (путь туда, путь обратно, форма). Кольцо — обратного пути нет; записанный целиком и
    «подход + кольцо» — делятся там, где начинается обратный путь по своим следам; нарисованный в одну сторону —
    обратно тем же путём (если конец трека уже вернулся на свою же нитку — от этой точки)."""
    info = radial_info(r, shape); form = info['form']
    if form == 'loop': return list(r), [], form
    if form == 'lolli' and info['drawn_lolli']:      # нарисовано: подход, кольцо и снова точка на подходе — обратно по подходу
        k = info['split']; return list(r), list(r[k::-1]), form
    if form in ('recorded', 'lolli'):
        k = info['split']; return list(r[:k + 1]), list(r[k + 1:]), form
    return list(r), list(r[::-1]), form

def radial_summary(r):
    """Для экрана «Дни»: что «Нитка» видит в радиалке и сколько км в каждой форме — (путь туда, обратный путь), км."""
    def km(pts): return round(sum(dist(a, b) for a, b in zip(pts, pts[1:])) / 1000, 2)
    info = radial_info(r); opts = {}
    for sh in ('back', 'loop', 'lolli'):
        out, back, form = there_back(r, sh)
        if sh == 'lolli' and form != 'lolli': continue
        opts[sh] = [km(out), km(back) if form != 'drawn' else km(back)]
    return dict(auto=info['auto'], opts=opts)


class Trip:
    def __init__(self, proj):
        self.proj = proj; meta = proj.get('meta', {})
        self.meta = meta
        self.kcoef = float(meta.get('kcoef') or 1.0)
        self.one_way = bool(meta.get('radial_one_way', True))
        self.start = date.fromisoformat(meta['start_date']) if meta.get('start_date') else None
        self._ele = {}
        self._prof = {}
        self._S = dem.Sampler(z=13)
        self.types = dict(TYPE)
        for c in proj.get('custom_types', []):
            self.types[c['id']] = dict(id=c['id'], title=c.get('title') or 'Своя точка', sym='letter', text=(c.get('letter') or '•')[:2], color=hexcol(c.get('color', '')), custom=True)

        # ------------------------------------------------ дни
        pieces = [p for p in sorted(proj.get('pieces', []), key=lambda p: p.get('order', 0)) if p.get('role') in ('main', 'radial', None) and len(p.get('pts', [])) > 1]
        if pieces and not any(p.get('day') for p in pieces):
            for p in pieces: p = p   # один трек без разбивки — один «день»
            daymap = {id(p): 1 for p in pieces}
        else:
            daymap = {id(p): p.get('day') for p in pieces}
        days = {}
        for p in pieces:
            d = daymap[id(p)]
            if not d: continue
            pts = []
            for q in p['pts']:
                t = (q[0], q[1])
                if pts and t == pts[-1]: continue
                pts.append(t)
                if len(q) > 2 and q[2] is not None: self._ele[t] = q[2]
            if len(pts) < 2: continue
            D = days.setdefault(int(d), dict(mains=[], rad=[], rshape=[]))
            if p.get('role') == 'radial':
                D['rad'].append(pts); D['rshape'].append(p.get('shape') or 'auto')
                D.setdefault('ronce', []).append(self.one_way if p.get('once') is None else bool(p['once']))
            else: D['mains'].append(pts)
        self.DAYS = dict(sorted(days.items()))
        if not self.DAYS: raise ValueError('В проекте нет треков — загрузите GPX или ссылку nakarte.')
        for d, D in self.DAYS.items():
            if not D['mains'] and len(D['rad']) == 1 and D['rshape'][0] == 'auto' and dist(D['rad'][0][0], D['rad'][0][-1]) > 1500:
                D['mains'] = D['rad']; D['rad'] = []; D['rshape'] = []; D['ronce'] = []          # «радиалка» без возврата и без основной нитки — обычный переход
            D.setdefault('ronce', [])
            D['rform'] = [radial_form(r, s) for r, s in zip(D['rad'], D['rshape'])]
            D['main'] = [p for m in D['mains'] for p in m]
        self.REST = set(range(1, max(self.DAYS) + 1)) - set(self.DAYS)
        self._path = {d: self._day_path(d) for d in self.DAYS}
        ds = list(self.DAYS)
        self.START = self._path[ds[0]][0][0]; self.FINISH = self._path[ds[-1]][-1][0]

        # разрывы нитки: между днями (переезд) и внутри дня
        self.TRANSFERS = []
        for a, b in zip(ds, ds[1:]):
            p, q = self._path[a][-1][0], self._path[b][0][0]
            if dist(p, q) > 1000: self.TRANSFERS.append((a, b, p, q))
        self.GAPS = []
        for d in ds:
            P = self._path[d]
            for i in range(1, len(P)):
                if P[i][1] == 2: self.GAPS.append((d, P[i - 1][0], P[i][0]))
        self.PARTS = []; cur = [ds[0]]
        for a, b in zip(ds, ds[1:]):
            if any(t[0] == a for t in self.TRANSFERS): self.PARTS.append(cur); cur = []
            cur.append(b)
        self.PARTS.append(cur)

        # ------------------------------------------------ точки
        pts = [p for p in proj.get('points', []) if p.get('type') in self.types and p.get('type') != 'skip']
        self.CAMPS = []
        for c in pts:
            if c['type'] != 'camp': continue
            nights = max(1, int(c.get('nights') or 1)); pt = (c['lat'], c['lon'])
            d0 = date.fromisoformat(c['date']) if c.get('date') else None
            if d0 and self.start: after = (d0 - self.start).days + 1
            elif c.get('after_day'): after = int(c['after_day'])
            else:
                best = min(self.DAYS, key=lambda d: dist(self._path[d][-1][0], pt))
                if dist(self._path[best][-1][0], pt) > 800: continue
                after = best
            if d0 is None and self.start: d0 = self.start + timedelta(days=after - 1)
            self.CAMPS.append(dict(pt=pt, after=after, before=after + nights, nights=nights, d0=d0, name=(c.get('name') or '').strip(), h=c.get('h'),
                                   on_map=c.get('on_map', True), on_profile=c.get('on_profile', True), lp=_lp(c)))
        self.CAMPS.sort(key=lambda c: (c['after'], c['d0'] or date.min))
        n = 1
        for c in self.CAMPS:
            k = c['nights']
            c['lab'] = f'Н{n}' if k == 1 else f'Н{n}–{n + k - 1}'
            if c['d0']:
                d1 = c['d0'] + timedelta(days=k - 1)
                c['date'] = f"{c['d0']:%d.%m}" if k == 1 else (f"{c['d0']:%d}–{d1:%d.%m}" if c['d0'].month == d1.month else f"{c['d0']:%d.%m}–{d1:%d.%m}")
            else: c['date'] = ''
            n += k
        self.OBJ = []; self.POI = []; self.PHOTOS = []
        for p in pts:
            t = self.types[p['type']]; pt = (p['lat'], p['lon'])
            if p['type'] == 'camp': continue
            if p['type'] == 'photo':
                if p.get('on_map', True) and p.get('day'): self.PHOTOS.append(dict(day=int(p['day']), lat=pt[0], lon=pt[1], nums=list(p.get('nums') or [])))
                continue
            if not (p.get('on_map', True) or p.get('on_profile')): continue
            name = (p.get('name') or '').strip() or t['title']
            grade = p.get('grade') if p.get('grade') in GRADE_COL else None
            if p['type'] in OBJ_TYPES:
                kind = p['type'] if p['type'] in ('pass', 'peak') else 'obst'
                self.OBJ.append(dict(kind=kind, type=p['type'], title=name, grade=grade, h=int(p['h']) if p.get('h') else None, pt=pt, key=bool(p.get('key')),
                                     on_map=p.get('on_map', True), on_profile=p.get('on_profile', True), lp=_lp(p)))
            else:
                self.POI.append(dict(type=p['type'], sym=t['sym'], text=t.get('text'), color=t.get('color') or (70, 70, 70), title=name, legend=t['title'], pt=pt,
                                     on_map=p.get('on_map', True), on_profile=bool(p.get('on_profile')), h=int(p['h']) if p.get('h') else None, lp=_lp(p)))

        # порядковые номера объектов — по ходу маршрута
        order = []
        for d in self.DAYS:
            for m in self.profile(d)['marks']:
                if m.get('oi') is not None and m['oi'] not in order: order.append(m['oi'])
        order += [i for i in range(len(self.OBJ)) if i not in order]
        for num, oi in enumerate(order, 1): self.OBJ[oi]['num'] = num
        for oi, o in enumerate(self.OBJ):
            o['days'] = [d for d in self.DAYS if any(m.get('oi') == oi for m in self.profile(d)['marks'])]
        self.OBJ_ORDERED = [self.OBJ[i] for i in order]

        # ------------------------------------------------ участки с особым характером пути
        self.BANDS = []
        for b in proj.get('bands', []):
            try: a = (float(b['a'][0]), float(b['a'][1])); e = (float(b['b'][0]), float(b['b'][1]))
            except (KeyError, TypeError, ValueError, IndexError): continue
            st = BAND.get(b.get('kind'))
            if st: title, color, hatch = st['title'], st['color'], st.get('hatch')
            else: title, color, hatch = (b.get('title') or 'особый участок').strip()[:60], hexcol(b.get('color', ''), (230, 0, 120)) + (110,), None
            best = None
            for d in self.DAYS:
                P = self.profile(d); pts = P['pts']
                i = near(a, pts); j = near(e, pts); cost = dist(a, pts[i]) + dist(e, pts[j])
                if dist(a, pts[i]) < 400 and dist(e, pts[j]) < 400 and (best is None or cost < best[0]): best = (cost, d, min(i, j), max(i, j))
            if not best or best[3] - best[2] < 1: continue
            _, d, i, j = best; P = self.profile(d)
            self.BANDS.append(dict(day=d, kind=b.get('kind') if st else 'custom:' + title, title=title, color=color, hatch=hatch, pts=P['pts'][i:j + 1],
                                   x0=P['x'][i], x1=P['x'][j], km=P['c'][j] - P['c'][i]))
        self.BANDS.sort(key=lambda b: (b['day'], b['x0']))
        self._variants(proj)
        # время по дням — у кусков, записанных с временем (часы, навигатор)
        self.TIMES = {}
        for p in sorted(proj.get('pieces', []), key=lambda p: p.get('order', 0)):
            if p.get('role') in ('main', 'radial') and p.get('day') and int(p['day']) in self.DAYS:
                ts = piece_times(p)
                if ts:
                    ti = time_info(p['pts'], ts)
                    if ti: self.TIMES.setdefault(int(p['day']), []).append(ti)
        allp = self.all_pts()
        self.tz = trip_tz(proj, allp[0][1] if allp else None)

    def day_time(self, d):
        """Выход, приход, чистое ходовое время и привалы дня (по всем его кускам с временем)."""
        T = self.TIMES.get(d)
        if not T: return None
        a = min(t['start'] for t in T); b = max(t['end'] for t in T); move = sum(t['move'] for t in T)
        stops = [s for t in T for s in t['stops']]
        return dict(start=a, end=b, move=move, stops=stops, pause=sum(s[3] - s[2] for s in stops))

    # ---------------------------------------------------------------- запасные варианты и аварийные выходы
    def _variants(self, proj):
        """VARS: варианты маршрута. Где отходят от нитки и куда ведут, км, набор, перевалы на них; на картах рисуется
        только та часть, что отошла от основной нитки дальше 100 м (иначе вариант ложится поверх неё)."""
        self.VARS = []
        defs = {v.get('id'): v for v in proj.get('variants', []) if isinstance(v, dict)}
        groups = {}
        for p in sorted(proj.get('pieces', []), key=lambda p: p.get('order', 0)):
            if p.get('role') not in ('alt', 'exit') or len(p.get('pts', [])) < 2: continue
            pts = []
            for q in p['pts']:
                t = (q[0], q[1])
                if not pts or t != pts[-1]: pts.append(t)
            if len(pts) > 1: groups.setdefault(p.get('var') or p['id'], dict(kind=p['role'], lines=[]))['lines'].append(pts)
        if not groups: return
        lines, lday = [], []
        for d in self.DAYS:
            for l in self.day_lines(d): lines.append(l); lday.append(d)
        idx = SegIndex(lines)
        pos = {}                                # положение точки на маршруте — чтобы нумеровать варианты по ходу
        acc = 0
        for li, l in enumerate(lines): pos[li] = acc; acc += len(l)
        towns = [o for o in self.POI if o['type'] in ('town', 'transport', 'exit', 'house')]
        mountain = self._mountain()
        for vid, g in groups.items():
            v = defs.get(vid) or {}; kind = v.get('kind') if v.get('kind') in ('alt', 'exit') else g['kind']; ls = g['lines']
            shown = []
            for l in ls:
                run = []; prev = None
                for q in l:
                    if idx.near(q, 120)[0] > 100:
                        if not run and prev is not None: run.append(prev)
                        run.append(q)
                    else:
                        if run: run.append(q); shown.append(run) if len(run) > 1 else None; run = []
                    prev = q
                if len(run) > 1: shown.append(run)
            a, e = ls[0][0], ls[-1][-1]
            da, la, ia = idx.near(a, 1000); de, le, _ = idx.near(e, 1000)
            frm = lday[la] if la is not None else None; to = lday[le] if le is not None else None
            tgt = min(towns, key=lambda o: dist(o['pt'], e), default=None)
            target = tgt['title'] if tgt and dist(tgt['pt'], e) < 3000 else None
            L = sum(dist(x, y) for l in ls for x, y in zip(l, l[1:]))
            up = dn = 0.0
            for l in ls:                        # набор и сброс — по модели рельефа, как у нарисованной нитки
                dense = []
                for x, y in zip(l, l[1:]):
                    n = max(1, int(math.ceil(dist(x, y) / STEP)))
                    dense += [(x[0] + (y[0] - x[0]) * k / n, x[1] + (y[1] - x[1]) * k / n) for k in range(n)]
                dense.append(l[-1]); self._S.prefetch(dense)
                z = [self._S(*q) for q in dense]; h = 2 if mountain else 4
                z = [sum(z[max(0, i - h):i + h + 1]) / len(z[max(0, i - h):i + h + 1]) for i in range(len(z))]
                ref = z[0]
                for vv in z[1:]:
                    if vv - ref >= 5: up += vv - ref; ref = vv
                    elif ref - vv >= 5: dn += ref - vv; ref = vv
            self.VARS.append(dict(id=vid, kind=kind, title=(v.get('title') or '').strip() or 'вариант', note=(v.get('note') or '').strip(), lines=ls, shown=shown,
                                  km=L / 1000 * self.kcoef, up=up, dn=dn, frm=frm, to=to, target=target, joins=de < 300,
                                  order=(pos[la] + ia if la is not None else 1e9), objects=[]))
        n = {'alt': 0, 'exit': 0}
        for V in sorted(self.VARS, key=lambda V: (V['order'], V['title'])):
            n[V['kind']] += 1; V['num'] = n[V['kind']]; V['lab'] = ('З' if V['kind'] == 'alt' else 'А') + str(V['num'])
        self.VARS.sort(key=lambda V: (V['kind'] != 'alt', V['num']))
        vidx = [SegIndex(V['lines']) for V in self.VARS]
        for o in self.OBJ:                       # перевал на варианте, а не на основной нитке, — принадлежит варианту
            o['vars'] = []
            if o['days']: continue
            for V, vi in zip(self.VARS, vidx):
                if vi.near(o['pt'], 200)[0] < 150: o['vars'].append(V['lab']); V['objects'].append(self.obj_label(o))

    def var_route(self, V):
        """«от дня 9 до дня 11», «с дня 7 → Кировск»."""
        a = f"от дня {V['frm']}" if V['frm'] else 'отдельно от нитки'
        if V['kind'] == 'exit' or not V['joins']:
            b = V['target'] or ('к нитке, день ' + str(V['to']) if V['joins'] and V['to'] else '')
            return f"{a} → {b}" if b else a
        return f"{a} до дня {V['to']}" if V['to'] and V['to'] != V['frm'] else (f"день {V['frm']}" if V['frm'] else a)

    @property
    def plan(self):
        """Поход ещё впереди — карты подписываются как план (можно переключить вручную)."""
        m = self.meta.get('mode')
        if m in ('plan', 'report'): return m == 'plan'
        return bool(self.start and self.start > date.today())

    # ---------------------------------------------------------------- даты
    def date_obj(self, d):
        return self.start + timedelta(days=d - 1) if self.start else None
    def date(self, d):
        o = self.date_obj(d)
        return f'{o:%d.%m}' if o else ''
    def year(self):
        return self.start.year if self.start else None
    def period(self):
        if not self.start: return ''
        a, b = self.date_obj(min(self.DAYS)), self.date_obj(max(self.DAYS))
        return f'{a:%d.%m}–{b:%d.%m}.{b.year}' if a.year == b.year else f'{a:%d.%m.%Y}–{b:%d.%m.%Y}'
    def camp_after(self, d): return next((c for c in self.CAMPS if c['after'] == d), None)
    def camp_before(self, d): return next((c for c in self.CAMPS if c['before'] == d), None)

    # ---------------------------------------------------------------- геометрия дня
    def day_lines(self, d):
        D = self.DAYS[d]
        return list(D['mains']) + D['rad']
    def day_pts(self, d):
        return [p for s in self.day_lines(d) for p in s]
    def all_pts(self):
        return [p for d in self.DAYS for p in self.day_pts(d)]

    def _day_path(self, d):
        """Путь дня по порядку движения: [(точка, флаг)]; флаг 1 — обратный путь радиалки (в километраж не входит),
        2 — точка после разрыва (переезд внутри дня). Радиалка от места ночёвки — перед основной ниткой, иначе вклеена в неё."""
        D = self.DAYS[d]; path = []
        for k, m in enumerate(D['mains']):
            for i, p in enumerate(m):
                path.append((p, 2 if (i == 0 and k > 0 and dist(D['mains'][k - 1][-1], p) > GAP) else 0))
        ins = []
        for r, shape, once in zip(D['rad'], D['rshape'], D['ronce']):
            out, back, form = there_back(r, shape)
            rr = [(p, 0) for p in out] + [(p, 1 if once else 0) for p in back]
            if not D['mains']:
                if path and dist(path[-1][0], rr[0][0]) > GAP: rr[0] = (rr[0][0], 2)
                path += rr; continue
            if dist(r[0], path[0][0]) < 250: path = rr + path; continue
            ins.append((near(r[0], [p for p, _ in path]), rr))
        for i, r in sorted(ins, key=lambda t: -t[0]): path[i + 1:i + 1] = r
        return path

    def _densify(self, path, step=STEP):
        out = [path[0][0]]; fl = [0]; el = [self._ele.get(path[0][0])]
        for (a, _), (b, f) in zip(path, path[1:]):
            ea, eb = self._ele.get(a), self._ele.get(b)
            if f == 2:
                out.append(b); fl.append(2); el.append(eb); continue
            n = max(1, int(math.ceil(dist(a, b) / step)))
            for k in range(1, n + 1):
                out.append((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)); fl.append(f)
                el.append(ea + (eb - ea) * k / n if ea is not None and eb is not None else None)
        return out, fl, el

    def _visits(self, o, pts, x):
        """Прохождения объекта ниткой: подход ближе 150 м (у самого начала/конца дня — ближе 60 м); подходы ближе 0,5 км по нитке сливаются."""
        dd = [dist(p, o['pt']) for p in pts]; runs = []; i = 0; n = len(pts)
        while i < n:
            if dd[i] < 150:
                j = i
                while j + 1 < n and dd[j + 1] < 150: j += 1
                k = min(range(i, j + 1), key=lambda t: dd[t])
                edge = x[k] < 0.1 or x[-1] - x[k] < 0.1
                if dd[k] <= 60 or not edge:
                    if runs and x[k] - x[runs[-1]] < 0.5:
                        if dd[k] < dd[runs[-1]]: runs[-1] = k
                    else: runs.append(k)
                i = j + 1
            else: i += 1
        return [(k, dd[k]) for k in runs]

    def elev_source(self, d=None):
        mode = self.meta.get('elev') or 'auto'
        if mode in ('dem', 'track'): return mode
        pts = [p for dd in ([d] if d else self.DAYS) for p in self.day_pts(dd)]
        if not pts or sum(1 for p in pts if p in self._ele) < 0.9 * len(pts): return 'dem'
        # высоты есть, но трек может быть нарисован в планировщике: точки через 200–500 м, высоты подставлены по вершинам —
        # подъёмы и спуски между ними пропадают. Записанный GPS-трек густой (шаг 10–35 м, после прореживания тоже)
        gaps = sorted(dist(a, b) for a, b in zip(pts, pts[1:]))
        return 'track' if gaps and gaps[len(gaps) // 2] <= DENSE_M else 'dem'

    def profile(self, d):
        """Профиль дня: x (км пути), c (километраж), z (м), точки, отметки объектов, статистика."""
        if d in self._prof: return self._prof[d]
        pts, fl, el = self._densify(self._path[d])
        n = len(pts)
        x = [0.0]; ckm = [0.0]
        for i in range(1, n):
            s = 0.0 if fl[i] == 2 else dist(pts[i - 1], pts[i]) / 1000
            x.append(x[-1] + s); ckm.append(ckm[-1] + (0.0 if fl[i] == 1 else s))
        use_track = self.elev_source(d) == 'track'
        if not use_track or any(e is None for e in el): self._S.prefetch(pts)
        z = [el[i] if (use_track and el[i] is not None) else self._S(*pts[i]) for i in range(n)]
        # сглаживание: в горах — лёгкое (±50 м по нитке); на равнине модель рельефа шумит кронами леса сильнее,
        # чем меняется сам рельеф, поэтому окно шире (до ±200 м)
        amp = max(z) - min(z)
        h = 2 if (use_track or self._mountain() or amp >= 300) else (4 if amp >= 120 else 8)
        z = [sum(z[max(0, i - h):i + h + 1]) / len(z[max(0, i - h):i + h + 1]) for i in range(n)]
        ret = [f == 1 for f in fl]
        marks = []; anchors = []; cam = set()
        cb, ca = self.camp_before(d), self.camp_after(d)
        if cb and cb['h'] and dist(cb['pt'], pts[0]) < 500: anchors.append((0, cb['h'] - z[0], 'camp', cb['h'])); cam.add(0)
        if ca and ca['h'] and dist(ca['pt'], pts[-1]) < 500: anchors.append((n - 1, ca['h'] - z[n - 1], 'camp', ca['h'])); cam.add(n - 1)
        if 0 in cam:                               # возвращение с радиального выхода на ту же ночёвку
            for i in range(1, n - 1):
                if ret[i] and not ret[i + 1] and dist(pts[i], cb['pt']) < 150:
                    anchors.append((i, cb['h'] - z[i], 'camp', cb['h'])); cam.add(i)
        for oi, o in enumerate(self.OBJ):
            for vi, (k, dk) in enumerate(self._visits(o, pts, x)):
                pin = bool(o['h']) and dk <= PIN_M and not any(abs(x[k] - x[kc]) < 0.15 for kc in cam)
                if pin: anchors.append((k, o['h'] - z[k], o['kind'], o['h']))
                marks.append(dict(oi=oi, k=k, first=vi == 0, pin=pin))
        for pi, o in enumerate(self.POI):
            if not o['on_profile']: continue
            for vi, (k, dk) in enumerate(self._visits(o, pts, x)):
                if vi == 0: marks.append(dict(oi=None, pi=pi, k=k, first=True, pin=False))
        if anchors:
            # поправка к высотам: в точках привязки — точно, рядом — плавно, дальше 600 м по нитке затухает;
            # в самой точке перевала/вершины профиль не выше её высоты
            R = 0.6; z2 = []
            for i in range(n):
                num = den = tap = 0.0; cap = None; exact = None
                near_camp = any(abs(x[i] - x[kc]) < 0.1 for kc in cam)
                for k, dz, kind, hh in anchors:
                    dx = abs(x[i] - x[k])
                    if dx < R:
                        tap = max(tap, 0.5 * (1 + math.cos(math.pi * dx / R)))
                        if dx < 1e-9: exact = dz
                        w = 1.0 / (dx * dx + 1e-6); num += w * dz; den += w
                        if kind in ('pass', 'peak') and dx < 0.15 and not near_camp:
                            c = hh + 1500 * max(0.0, dx - 0.05); cap = c if cap is None else min(cap, c)
                v = z[i] + (exact if exact is not None else (tap * num / den if den else 0.0))
                z2.append(min(v, cap) if cap is not None else v)
            z = z2
        up = dn = 0.0; ref = z[0]
        for i in range(1, n):                      # набор/сброс с порогом 5 м; через разрыв (переезд) не считается
            if fl[i] == 2: ref = z[i]; continue
            v = z[i]
            if v - ref >= 5: up += v - ref; ref = v
            elif ref - v >= 5: dn += ref - v; ref = v
        K = self.kcoef
        x = [v * K for v in x]; ckm = [v * K for v in ckm]
        for m in marks: m['x'] = x[m['k']]; m['z'] = z[m['k']]
        marks.sort(key=lambda m: m['x'])
        self._prof[d] = dict(day=d, x=x, c=ckm, ret=ret, gap=[f == 2 for f in fl], gapx=[x[i] for i in range(n) if fl[i] == 2], z=z, pts=pts, marks=marks, L=ckm[-1], Lpath=x[-1], up=up, dn=dn,
                             zmin=min(z), zmax=max(z), src='track' if use_track else 'dem')
        return self._prof[d]

    def _mountain(self):
        """Горный ли поход в целом (перепад высот по всей нитке от 600 м): тогда сглаживание везде лёгкое, как в горах."""
        if not hasattr(self, '_mnt'):
            pts = self.all_pts()[::3]
            self._S.prefetch(pts); zz = [self._S(*p) for p in pts]
            self._mnt = (max(zz) - min(zz)) >= 600 if zz else False
        return self._mnt

    def camp_height(self, c):
        """Высота ночёвки: заданная руками, иначе по профилю дня, который на ней закончился (или начался); до 10 м."""
        if c['h']: return int(c['h'])
        if c['after'] in self.DAYS and dist(self._path[c['after']][-1][0], c['pt']) < 800: z = self.profile(c['after'])['z'][-1]
        elif c['before'] in self.DAYS and dist(self._path[c['before']][0][0], c['pt']) < 800: z = self.profile(c['before'])['z'][0]
        else:
            self._S.prefetch([c['pt']]); z = self._S(*c['pt'])
        return int(round(z / 10.0)) * 10

    # ---------------------------------------------------------------- подписи объектов
    @staticmethod
    def obj_sub(o, unit=True):
        parts = ([o['grade']] if o['grade'] else []) + ([f"{o['h']} м" if unit else str(o['h'])] if o['h'] else [])
        return ' · '.join(parts)
    @classmethod
    def obj_label(cls, o):
        s = cls.obj_sub(o, unit=False)
        return o['title'] + (', ' + s.replace(' · ', ', ') if s else '')

    def totals(self):
        P = [self.profile(d) for d in self.DAYS]
        return dict(L=sum(p['L'] for p in P), up=sum(p['up'] for p in P), dn=sum(p['dn'] for p in P))

    def summary(self):
        """Сводка для интерфейса: дни, ночёвки, объекты."""
        t = self.totals()
        return dict(
            km=round(t['L'], 1), up=int(round(t['up'])), dn=int(round(t['dn'])), kcoef=self.kcoef, period=self.period(), elev=self.elev_source(),
            days=[dict(n=d, date=self.date(d), km=round(self.profile(d)['L'], 1), up=int(round(self.profile(d)['up'])), dn=int(round(self.profile(d)['dn'])),
                       **({} if not self.day_time(d) else dict(t_start=hm(self.day_time(d)['start'], self.tz), t_end=hm(self.day_time(d)['end'], self.tz),
                          move=dur(self.day_time(d)['move']), n_stops=len(self.day_time(d)['stops']), pause=dur(self.day_time(d)['pause']))),
                       zmin=int(self.profile(d)['zmin']), zmax=int(self.profile(d)['zmax']), radial=bool(self.DAYS[d]['rad']),
                       objects=[self.obj_label(self.OBJ[m['oi']]) for m in self.profile(d)['marks'] if m.get('oi') is not None and m['first']],
                       key_objects=[self.obj_label(self.OBJ[m['oi']]) for m in self.profile(d)['marks'] if m.get('oi') is not None and m['first'] and self.OBJ[m['oi']]['key']]) for d in self.DAYS],
            rest=sorted(self.REST),
            camps=[dict(lab=c['lab'], date=c['date'], h=self.camp_height(c), name=c['name'], after=c['after']) for c in self.CAMPS],
            objects=[dict(num=o['num'], title=o['title'], grade=o['grade'], h=o['h'], kind=o['kind'], days=o['days'], key=o['key'], vars=o.get('vars', [])) for o in self.OBJ_ORDERED],
            transfers=[dict(a=a, b=b, km=round(dist(p, q) / 1000, 1)) for a, b, p, q in self.TRANSFERS],
            bands=[dict(day=b['day'], title=b['title'], km=round(b['km'], 1)) for b in self.BANDS],
            variants=[dict(lab=V['lab'], kind=V['kind'], title=V['title'], note=V['note'], route=self.var_route(V), km=round(V['km'], 1),
                           up=int(round(V['up'])), dn=int(round(V['dn'])), objects=V['objects']) for V in self.VARS],
            plan=self.plan, tz=self.tz,
            move=dur(sum(self.day_time(d)['move'] for d in self.DAYS if self.day_time(d))) if self.TIMES else None)
