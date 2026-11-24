"""Поход как данные: дни, нитка, ночёвки, перевалы, высотные профили, километраж.

Trip(project) — всё, что раньше было зашито в kdata.py конкретного похода.
Километраж: длина нитки по карте × kcoef. Радиальные выходы — в одну сторону (если radial_one_way).
"""
import math
from datetime import date, timedelta
from . import dem
from .classify import TYPE, OBJ_TYPES, GRADES, GRADE_COL, BAND, dist

STEP = 25.0        # шаг выборки по нитке, м
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

def there_back(r):
    """Радиальный выход: (путь туда, путь обратно). Кольцо — обратного пути нет; нарисованный в одну сторону —
    обратно тем же путём (если конец трека уже вернулся на свою же нитку — от этой точки)."""
    if dist(r[0], r[-1]) < 150: return list(r), []
    acc = 0.0; far = len(r) - 1
    for i in range(len(r) - 1, 0, -1):
        acc += dist(r[i], r[i - 1])
        if acc > 300: far = i - 1; break
    i = min(range(far + 1), key=lambda t: dist(r[t], r[-1]))
    back = r[i::-1] if dist(r[i], r[-1]) < 150 else r[::-1]
    return list(r), list(back)


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
        pieces = [p for p in sorted(proj.get('pieces', []), key=lambda p: p.get('order', 0)) if p.get('role') != 'skip' and len(p.get('pts', [])) > 1]
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
            D = days.setdefault(int(d), dict(mains=[], rad=[]))
            (D['rad'] if p.get('role') == 'radial' else D['mains']).append(pts)
        self.DAYS = dict(sorted(days.items()))
        if not self.DAYS: raise ValueError('В проекте нет треков — загрузите GPX или ссылку nakarte.')
        for d, D in self.DAYS.items():
            if not D['mains'] and len(D['rad']) == 1 and dist(D['rad'][0][0], D['rad'][0][-1]) > 1500:
                D['mains'] = D['rad']; D['rad'] = []          # «радиалка» без возврата и без основной нитки — обычный переход
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
        back_flag = 1 if self.one_way else 0
        ins = []
        for r in D['rad']:
            out, back = there_back(r)
            rr = [(p, 0) for p in out] + [(p, back_flag) for p in back]
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
        return 'track' if pts and sum(1 for p in pts if p in self._ele) >= 0.9 * len(pts) else 'dem'

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
                       zmin=int(self.profile(d)['zmin']), zmax=int(self.profile(d)['zmax']), radial=bool(self.DAYS[d]['rad']),
                       objects=[self.obj_label(self.OBJ[m['oi']]) for m in self.profile(d)['marks'] if m.get('oi') is not None and m['first']],
                       key_objects=[self.obj_label(self.OBJ[m['oi']]) for m in self.profile(d)['marks'] if m.get('oi') is not None and m['first'] and self.OBJ[m['oi']]['key']]) for d in self.DAYS],
            rest=sorted(self.REST),
            camps=[dict(lab=c['lab'], date=c['date'], h=self.camp_height(c), name=c['name'], after=c['after']) for c in self.CAMPS],
            objects=[dict(num=o['num'], title=o['title'], grade=o['grade'], h=o['h'], kind=o['kind'], days=o['days'], key=o['key']) for o in self.OBJ_ORDERED],
            transfers=[dict(a=a, b=b, km=round(dist(p, q) / 1000, 1)) for a, b, p, q in self.TRANSFERS],
            bands=[dict(day=b['day'], title=b['title'], km=round(b['km'], 1)) for b in self.BANDS])
