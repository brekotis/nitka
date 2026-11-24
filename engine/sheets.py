"""Листы отчёта: район, обзорная карта, рабочая карта 1:100 000, карты дней с профилем, высотный профиль маршрута.

Builder(trip, out_dir, opts).plan() → список листов; .render(id) → файл PNG 300 dpi.
"""
import math, os
from PIL import Image, ImageDraw, ImageChops
from . import mapkit as mk, profile as prof
from .mapkit import font, mm, INK, View, Overlay, Sheet, g_track, g_dash, g_sym, g_band
from .model import GRADE_COL
from .classify import GRADES, dist

VEX_MAX = 60            # предел вертикального растяжения профиля: равнина должна выглядеть равниной
DAYC = [(226, 0, 26), (122, 26, 170)]          # нечётные / чётные дни
GREY = (95, 95, 95)
BROWN = (110, 40, 5)
BAR = {10000: (0.5, 0.1), 25000: (1, 0.25), 50000: (2, 0.5), 100000: (5, 1), 200000: (10, 2), 300000: (15, 5), 500000: (20, 5),
       1000000: (50, 10), 2000000: (100, 20), 2500000: (100, 25), 5000000: (200, 50)}

def dcol(d): return DAYC[(d - 1) % 2]
def km(v, nd=1): return f'{v:.{nd}f}'.replace('.', ',')
def num(v): return f'{int(round(v)):,}'.replace(',', ' ')
def plural(n, one, few, many):
    n = abs(int(n)); a, b = n % 10, n % 100
    return one if a == 1 and b != 11 else (few if 2 <= a <= 4 and not 12 <= b <= 14 else many)
def bbox(pts):
    la = [p[0] for p in pts]; lo = [p[1] for p in pts]
    return min(la), max(la), min(lo), max(lo)
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'web', 'img', 'logo.png')
def stamp_logo(path, h_mm=12.0):
    """Логотип клуба в правом нижнем углу листа. Если угол занят (легенда, подписи) — лист удлиняется на полоску под логотип."""
    if not os.path.exists(LOGO): return None
    img = Image.open(path).convert('RGB'); lg = Image.open(LOGO).convert('RGBA')
    h = int(mm(h_mm)); w = int(lg.width * h / lg.height); lg = lg.resize((w, h), Image.LANCZOS)
    m = int(mm(2.2)); W, H = img.size
    corner = img.crop((W - w - 3 * m, H - h - 2 * m, W, H))
    busy = ImageChops.difference(corner, Image.new('RGB', corner.size, (255, 255, 255))).convert('L').point(lambda v: 255 if v > 14 else 0).getbbox()
    if busy:
        ext = Image.new('RGB', (W, H + h + m), (255, 255, 255)); ext.paste(img, (0, 0)); img = ext; H = img.height
    img.paste(lg, (W - w - m, H - h - m), lg)
    img.save(path, dpi=(mk.DPI, mk.DPI), optimize=True)
    return img.width / mk.DPI * 2.54, img.height / mk.DPI * 2.54

def scale_txt(sc): return f'1:{sc:,}'.replace(',', ' ')
def per_cm(sc): return f'в 1 сантиметре {sc // 100} м' if sc < 100000 else f'в 1 сантиметре {sc / 100000:g} км'.replace('.', ',')


class Builder:
    def __init__(self, trip, out, opts=None):
        self.T = trip; self.out = out; self.o = dict(opts or {})
        os.makedirs(out, exist_ok=True)
        T = trip
        self.base = self.o.get('base') or 'otm'
        self.ALL = T.all_pts()
        la0, la1, lo0, lo1 = bbox(self.ALL)
        self.KX = 111.32 * math.cos(math.radians((la0 + la1) / 2)); self.KY = 111.13
        self.area = (T.meta.get('area') or T.meta.get('title') or '').strip()
        self.year = T.year()
        self.tot = T.totals()
        self.has_rad = any(D['rad'] and D['mains'] for D in T.DAYS.values()) and T.one_way
        k = T.kcoef
        kx = f' × {km(k)}' if abs(k - 1) > 1e-6 else ''
        rad = 'радиальные выходы — в одну сторону' if self.has_rad else ''
        self.kx = kx
        self.KM_NOTE = 'Километраж — длина нитки по карте' + kx + (' (коэффициент для горного рельефа)' if kx else '') + ('; ' + rad if rad else '') + '.'
        self.KM_NOTE_SHORT = 'Километраж — длина нитки по карте' + kx + ('; ' + rad if rad else '') + '.'
        pinned = any(m.get('pin') for d in T.DAYS for m in T.profile(d)['marks'])
        if T.elev_source() == 'track':
            self.PROF_NOTE = 'Высоты — по записи трека (GPS/барометр), слегка сглажены' + ('; в точках перевалов и вершин привязаны к высотам из подписей' if pinned else '') + '. '
        else:
            self.PROF_NOTE = 'Высоты — по цифровой модели рельефа (SRTM/Copernicus, ≈30 м)' + (' с привязкой к высотам перевалов и вершин' if pinned else '') + '; высоты ночёвок округлены до 10 м. '
        self.PROF_NOTE += 'Расстояния — длина нитки по треку группы' + kx + '.'
        self.RAD_NOTE = 'Радиальные выходы входят в километраж в одну сторону: обратный путь на графике показан бледнее, километры на нём не считаются.'
        self.n_pass = sum(1 for o in T.OBJ if o['kind'] == 'pass'); pk = [o for o in T.OBJ if o['kind'] == 'peak']; self.n_peak = sum(1 for o in pk if o['grade']) or len(pk)
        self.grades = [g for g in GRADES if any(o['grade'] == g for o in T.OBJ)]

    # ---------------------------------------------------------------- стиль основы
    def style(self, sc):
        if self.base == 'otm' or sc >= 1000000:
            z = {10000: 17, 25000: 16, 50000: 15, 100000: 14, 200000: 13, 300000: 12, 500000: 11, 1000000: 10, 2000000: 9, 2500000: 8, 5000000: 7}[sc]
            return dict(layers=['otm'], z=z, src='Основа: OpenTopoMap (© OpenStreetMap, SRTM).', lighten=0.14 if sc < 1000000 else 0.22, shade=0.0, shade_z=12)
        tab = {
            10000: ([('gsh050', 15), ('gsh100', 14), ('topo500', 14), ('otm', 15)], 17, '1:50 000', 13),
            25000: ([('gsh050', 15), ('gsh100', 14), ('topo500', 14), ('otm', 15)], 16, '1:50 000', 13),
            50000: (['gsh050', ('gsh100', 14), ('topo500', 14), 'otm'], 15, '1:50 000', 13),
            100000: (['gsh100', ('topo1000', 13), 'otm'], 14, '1:100 000', 12),
            200000: (['gsh100', ('topo1000', 12), 'otm'], 13, '1:100 000', 12),
            300000: (['gsh100', ('topo1000', 12), 'otm'], 12, '1:100 000', 11),
            500000: (['ggc500', ('ggc1000', 11), 'otm'], 12, '1:500 000', 10),
        }
        layers, z, name, sz = tab[sc]
        return dict(layers=layers, z=z, src=f'Основа: топокарта Генштаба {name} (nakarte.me), отмывка рельефа — по ЦМР.', lighten=0.16 if sc <= 100000 else 0.22,
                    shade=0.34 if sc <= 100000 else 0.38, shade_z=sz)

    def basemap(self, v, sc):
        s = self.style(sc)
        img = v.basemap(s['layers'], s['z'], lighten=s['lighten'], shade=s['shade'], shade_z=s['shade_z'])
        use = getattr(v, 'layer_use', {}); tot = sum(use.values()) or 1
        names = [L if isinstance(L, str) else L[0] for L in s['layers']]
        if len(names) > 1 and names[-1] == 'otm' and use.get(len(names) - 1, 0) > 0.6 * tot:
            # топокарт Генштаба на этот район нет — весь поход рисуем на OpenTopoMap, без двойной отмывки
            self.base = 'otm'; s = self.style(sc)
            img = v.basemap(s['layers'], s['z'], lighten=s['lighten'], shade=s['shade'], shade_z=s['shade_z'])
        elif len(names) > 1 and use.get(0, 0) < 0.5 * tot and 'Генштаба' in s['src']:
            s = dict(s, src='Основа: топокарты Генштаба разных масштабов (nakarte.me), отмывка рельефа — по ЦМР.')
        return img, s

    # ---------------------------------------------------------------- план листов
    def plan(self):
        T = self.T; o = self.o; P = []
        if o.get('region', True): P.append(dict(id='region', title='Район похода', file='00_rayon.png'))
        if o.get('overview', True): P.append(dict(id='overview', title='Обзорная карта', file='01_obzornaya_karta.png'))
        if o.get('profile', True): P.append(dict(id='profile', title='Высотный профиль маршрута', file='02_profil_marshruta.png'))
        if o.get('work', True):
            n = len(self.chunks())
            for k in range(1, n + 1): P.append(dict(id=f'work-{k}', title=f'Рабочая карта, лист {k} из {n}', file=f'03_rabochaya_list_{k}.png'))
        if o.get('days', True):
            for d in T.DAYS:
                dt = T.date(d)
                P.append(dict(id=f'day-{d}', title=f'День {d}' + (f' · {dt}' if dt else ''), file=f'den_{d:02d}' + (f'_{dt.replace(".", "-")}' if dt else '') + '.png'))
        return P

    def render(self, sid):
        if sid == 'region': return self.region()
        if sid == 'overview': return self.overview()
        if sid == 'profile': return self.full_profile()
        if sid.startswith('work-'): return self.sheet(int(sid[5:]))
        if sid.startswith('day-'): return self.day(int(sid[4:]))
        raise KeyError(sid)

    def _file(self, sid):
        return os.path.join(self.out, next(p['file'] for p in self.plan() if p['id'] == sid))

    # ---------------------------------------------------------------- данные для профиля
    def prof_inputs(self, days, x_off=0.0, c_off=0.0):
        """x — положение на профиле (путь), c — километраж (обратный путь радиалок не считается)."""
        T = self.T; segs, marks, camps = [], [], []; off = x_off; coff = c_off
        for d in days:
            P = T.profile(d)
            lines = [f'день {d}'] + ([T.date(d)] if T.date(d) else []) + [km(P['L']) + ' км', f"↑{P['up']:.0f} ↓{P['dn']:.0f}"]
            segs.append(dict(day=d, x=[off + v for v in P['x']], c=[coff + v for v in P['c']], ret=P['ret'], z=P['z'], color=dcol(d), lines=lines,
                             gapx=[off + v for v in P['gapx']], transfer=any(t[1] == d for t in T.TRANSFERS)))
            for m in P['marks']:
                if m.get('oi') is not None:
                    o = T.OBJ[m['oi']]
                    if not o['on_profile']: continue
                    marks.append(dict(x=off + m['x'], z=m['z'], kind=o['kind'], title=o['title'], grade=o['grade'], h=o['h'], oi=m['oi'], label=m['first']))
                else:
                    o = T.POI[m['pi']]
                    marks.append(dict(x=off + m['x'], z=m['z'], kind='poi', title=o['title'], grade=None, h=o['h'], oi=('p', m['pi']), label=True, color=o['color']))
            off += P['Lpath']; coff += P['L']
        seen = {}
        for m in sorted(marks, key=lambda m: m['x']):   # один и тот же объект в одной точке (конец дня = начало следующего) — одна подпись
            if m['label'] and m['oi'] in seen and m['x'] - seen[m['oi']] < 0.3: m['label'] = False
            if m['label']: seen[m['oi']] = m['x']
        first = days[0]; c = T.camp_before(first); s0 = segs[0]
        if c and c['on_profile'] and dist(c['pt'], T.profile(first)['pts'][0]) < 500:
            camps.append(dict(x=s0['x'][0], z=s0['z'][0], kind='camp', lab=c['lab'], text=self._camp_prof_text(c)))
        else:
            t = ' · '.join(x for x in [T.date(first), f"{int(round(s0['z'][0] / 10)) * 10} м"] if x)
            camps.append(dict(x=s0['x'][0], z=s0['z'][0], kind='start', lab='Старт' if first == min(T.DAYS) else 'Выход', text=t))
        for d, s in zip(days, segs):
            c = T.camp_after(d)
            if c and dist(c['pt'], T.profile(d)['pts'][-1]) < 800:
                if c['on_profile']: camps.append(dict(x=s['x'][-1], z=s['z'][-1], kind='camp', lab=c['lab'], text=self._camp_prof_text(c)))
            elif d == max(T.DAYS):
                t = ' · '.join(x for x in [T.date(d), f"{int(round(s['z'][-1] / 10)) * 10} м"] if x)
                camps.append(dict(x=s['x'][-1], z=s['z'][-1], kind='finish', lab='Финиш', text=t))
        self._pbands = []; off = x_off          # участки с особым характером пути — полоской вдоль оси
        for d in days:
            for b in T.BANDS:
                if b['day'] == d: self._pbands.append((off + b['x0'], off + b['x1'], b['color'][:3]))
            off += T.profile(d)['Lpath']
        return segs, marks, camps

    def _camp_prof_text(self, c):
        return ' · '.join(x for x in [c['date'], f"{self.T.camp_height(c)} м"] if x) + (' · днёвка' if c['nights'] > 1 else '')

    def grade_legend(self, d, x, y, pt=5.8):
        """Строка условных знаков профиля."""
        T = self.T; f = font(pt, cond=True); fb = font(pt - 0.4, True, cond=True)
        def txt(t, gap=1.2):
            nonlocal x
            d.text((x, y), t, font=f, fill=INK, anchor='lm'); x += d.textlength(t, font=f) + mm(gap)
        s = mm(1.45); r = mm(0.95)
        if T.CAMPS:
            d.polygon([(x + s, y - s * 1.15), (x + 2 * s, y + s * 0.7), (x, y + s * 0.7)], fill=prof.TENT, outline=INK, width=2); x += 2 * s + mm(1)
            txt('ночёвка: №, дата, высота', 4)
        if self.n_pass:
            d.ellipse((x, y - r, x + 2 * r, y + r), fill=GRADE_COL[self.grades[-1] if self.grades else None], outline=(255, 255, 255)); x += 2 * r + mm(1)
            txt('перевал', 2.5)
        if self.n_peak:
            d.polygon([(x + r * 1.3, y - r * 1.5), (x + r * 2.6, y + r * 0.8), (x, y + r * 0.8)], fill=GRADE_COL[self.grades[-1] if self.grades else None]); x += r * 2.6 + mm(1)
            txt('вершина', 2.5)
        if any(o['kind'] == 'obst' for o in T.OBJ):
            d.polygon([(x + r * 1.3, y - r * 1.3), (x + r * 2.6, y), (x + r * 1.3, y + r * 1.3), (x, y)], fill=(200, 0, 0)); x += r * 2.6 + mm(1)
            txt('препятствие', 4)
        if self.grades:
            txt('категория трудности:', 1.2)
            for g in self.grades:
                tw = d.textlength(g, font=fb) + fb.size * 0.6
                d.rounded_rectangle((x, y - fb.size * 0.62, x + tw, y + fb.size * 0.62), radius=int(fb.size * 0.28), fill=GRADE_COL[g])
                d.text((x + tw / 2, y), g, font=fb, fill=(255, 255, 255), anchor='mm'); x += tw + mm(1.2)
        return x

    def headline(self):
        """«175,3 км (с коэфф. 1,2) · 15 перевалов (до 2Б), 2 вершины»"""
        T = self.T; parts = [f'{km(self.tot["L"])} км' + (f' (с коэфф. {km(T.kcoef)})' if self.kx else '')]
        ob = []
        top = self.grades[-1] if self.grades else None
        if self.n_pass: ob.append(f'{self.n_pass} {plural(self.n_pass, "перевал", "перевала", "перевалов")}' + (f' (до {top})' if top else ''))
        if self.n_peak: ob.append(f'{self.n_peak} {plural(self.n_peak, "вершина", "вершины", "вершин")}')
        if ob: parts.append(', '.join(ob))
        return parts

    # --------------------------------------------------------------------------- высотный профиль всего маршрута
    def profile_rows(self):
        """Строки общего профиля: части маршрута между переездами, длинные — режутся по ~100 км.
        Короткий кусок между переездами (день-два) отдельной строкой не идёт — он остаётся в соседней, переезд помечается."""
        T = self.T; Lof = lambda part: sum(T.profile(d)['Lpath'] for d in part)
        parts = [list(p) for p in T.PARTS]; tot = Lof([d for p in parts for d in p]); i = 0
        while len(parts) > 1 and i < len(parts):
            if Lof(parts[i]) < max(25.0, 0.15 * tot):
                if i > 0: parts[i - 1] += parts.pop(i); i -= 1
                else: parts[1] = parts[0] + parts[1]; parts.pop(0)
                continue
            i += 1
        rows = []
        for part in parts:
            L = Lof(part)
            n = max(1, int(math.ceil(L / 110.0)))
            if n == 1 or len(part) < 2: rows.append(part); continue
            cur = []; acc = 0.0; target = L / n
            for d in part:
                cur.append(d); acc += T.profile(d)['Lpath']
                if acc >= target * 0.92 and d != part[-1] and len(rows) < 40:
                    rows.append(cur); cur = []; acc = 0.0
            if cur: rows.append(cur)
        return rows

    def full_profile(self):
        T = self.T; DPI = mk.DPI
        rows = self.profile_rows()
        W_CM, PH = 26.6, 8.2          # лист 26,6 см — альбомная A4
        if len(rows) == 1: PH = 10.5
        notes = 2 + (1 if self.has_rad else 0)
        W = int(W_CM / 2.54 * DPI); H = int((1.65 + len(rows) * (PH + 0.45) + 0.5 + notes * 0.31 + 0.25) / 2.54 * DPI)
        x0 = int(mm(6)); box = (x0, 0, W - 2 * x0, int(PH / 2.54 * DPI))
        img = Image.new('RGB', (W, H), (255, 255, 255)); d = ImageDraw.Draw(img)
        title = ' · '.join(x for x in ['Высотный профиль маршрута', self.area, T.period()] if x)
        pt = 11
        while d.textlength(title, font=font(pt, True)) > W - mm(6) and pt > 7.5: pt -= 0.25
        d.text((W / 2, mm(1.0)), title, font=font(pt, True), fill=INK, anchor='ma')
        top = max((o for o in T.OBJ if o['h']), key=lambda o: o['h'], default=None)
        zmax_all = max(T.profile(dd)['zmax'] for dd in T.DAYS)
        hp = self.headline()
        sub = [hp[0], f'набор {num(self.tot["up"])} м', f'сброс {num(self.tot["dn"])} м'] + hp[1:]
        sub.append(f'высшая точка — {top["title"]}, {top["h"]} м' if top and top['h'] >= zmax_all - 60 else f'высшая точка — {num(zmax_all)} м')
        nd = len(T.DAYS); nn = sum(c['nights'] for c in T.CAMPS)
        sub.append(f'{nd} {plural(nd, "ходовой день", "ходовых дня", "ходовых дней")}' + (f', {nn} {plural(nn, "ночёвка", "ночёвки", "ночёвок")}' if nn else ''))
        sub = ' · '.join(sub); sp = 7.6
        while d.textlength(sub, font=font(sp, cond=True)) > W - mm(6) and sp > 5.6: sp -= 0.2
        d.text((W / 2, mm(6.4)), sub, font=font(sp, cond=True), fill=(60, 60, 60), anchor='ma')
        zmin_all = min(T.profile(dd)['zmin'] for dd in T.DAYS)
        span = zmax_all - zmin_all
        ystep = 50 if span <= 350 else (100 if span <= 800 else (200 if span <= 1600 else 500))
        zlo = math.floor((zmin_all - ystep * 0.2) / ystep) * ystep
        y = int(1.65 / 2.54 * DPI); off = coff = 0.0; vex = []; head = 0; data = []; ztop = zmax_all
        xl = 'км от начала маршрута' + (f' (длина по карте{self.kx}' + ('; радиальные выходы — в одну сторону' if self.has_rad else '') + ')' if self.kx or self.has_rad else '')
        for k, days in enumerate(rows, 1):
            segs, marks, camps = self.prof_inputs(days, off, coff)
            L = sum(T.profile(dd)['L'] for dd in days); up = sum(T.profile(dd)['up'] for dd in days); dn = sum(T.profile(dd)['dn'] for dd in days)
            dts = f' ({T.date(days[0])}–{T.date(days[-1])})' if T.date(days[0]) and len(days) > 1 else ''
            t = (f'Часть {k} · ' + (f'дни {days[0]}–{days[-1]}' if len(days) > 1 else f'день {days[0]}') + dts + f' · {km(L)} км · набор {num(up)} м · сброс {num(dn)} м'
                 if len(rows) > 1 else None)
            kw = dict(grade_col=GRADE_COL, orient='v', title=t, ylim=(zlo, zmax_all), ystep=ystep, pt=6.0, x_label=xl, bands=list(self._pbands), vex_max=VEX_MAX)
            dry = prof.draw(img, box, segs, marks, camps, dry=True, **kw)[1]
            head = max(head, dry['head']); ztop = max(ztop, dry['zhi'])   # единый вертикальный масштаб частей
            data.append((segs, marks, camps, kw)); off += sum(T.profile(dd)['Lpath'] for dd in days); coff += L
        span2 = ztop - zlo; ystep = 50 if span2 <= 350 else (100 if span2 <= 800 else (200 if span2 <= 1600 else 500))
        for segs, marks, camps, kw in data:
            kw.update(ylim=(zlo, ztop), ystep=ystep)
            img, info = prof.draw(img, (x0, y, box[2], box[3]), segs, marks, camps, head_min=head, **kw)
            vex.append(info['vex'] / T.kcoef); y += int((PH + 0.45) / 2.54 * DPI)
        d = ImageDraw.Draw(img)
        lx = self.grade_legend(d, x0 + mm(1), y + mm(1.5)) + mm(3); fl = font(5.8, cond=True); seen = []
        for b in T.BANDS:                       # полоски вдоль оси — характер пути
            if b['kind'] in seen: continue
            seen.append(b['kind']); tw = d.textlength(b['title'], font=fl)
            if lx + mm(7) + tw > W - x0: break
            d.rectangle((lx, y + mm(0.7), lx + mm(5), y + mm(2.3)), fill=b['color'][:3], outline=(90, 90, 90))
            d.text((lx + mm(6), y + mm(1.5)), b['title'], font=fl, fill=INK, anchor='lm'); lx += mm(8.5) + tw
        d.text((x0 + mm(1), y + mm(4.9)), self.PROF_NOTE, font=font(5.8, cond=True), fill=(80, 80, 80), anchor='la')
        vt = (', '.join(f'часть {i} — в {km(v)} раза' for i, v in enumerate(vex, 1)) if len(vex) > 1 else f'в {km(vex[0])} раза')
        d.text((x0 + mm(1), y + mm(7.9)), 'Вертикальный масштаб крупнее горизонтального: ' + vt
               + '. На полосе под графиком — день, дата, длина перехода, набор ↑ и сброс ↓ высоты, м.', font=font(5.8, cond=True), fill=(80, 80, 80), anchor='la')
        if self.has_rad: d.text((x0 + mm(1), y + mm(10.9)), self.RAD_NOTE, font=font(5.8, cond=True), fill=(80, 80, 80), anchor='la')
        p = self._file('profile'); img.save(p, dpi=(DPI, DPI), optimize=True)
        size = (self.o.get('logo', True) and stamp_logo(p)) or (img.width / DPI * 2.54, img.height / DPI * 2.54)
        return dict(file=p, w_cm=size[0], h_cm=size[1], note='')

    # ---------------------------------------------------------------- отрисовка нитки на карте
    def obj_sym(self, ov, o, size=3.0):
        col = GRADE_COL[o['grade']]
        if o['kind'] == 'pass': ov.sym('pass', *o['pt'], size, color=col)
        elif o['kind'] == 'peak': ov.sym('peak', *o['pt'], size, color=col if o['grade'] else (95, 60, 30))
        elif o['type'] == 'prizhim': ov.sym('prizhim', *o['pt'], size + 0.4)
        elif o['type'] == 'ford': ov.sym('obst', *o['pt'], size, text='≈')
        else: ov.sym('obst', *o['pt'], size + (0.2 if o['grade'] and o['grade'] != 'н/к' else 0), text=o['grade'] if o['grade'] and o['grade'] != 'н/к' else '!')

    def draw_route(self, ov, *, focus=None, w=0.75, arrows_every=22, pt=6.3, day_labels=True, numbers=False, camp_dates=True,
                   camp_heights=False, km_marks=False, pois='label', photos=False, photo_radius=150):
        T = self.T; v = ov.v
        pref = lambda o: [o['lp']] if o.get('lp') else None      # сторона подписи, заданная руками
        # подпись перевала/вершины — цветом категории трудности (чуть темнее знака, чтобы читалась на карте)
        lab_col = lambda o: tuple(int(c * 0.82) for c in GRADE_COL[o['grade']]) if o['grade'] else (BROWN if o['kind'] != 'obst' else (190, 0, 0))
        for b in T.BANDS:                                  # участки с особым характером пути — полосой под ниткой
            if focus and b['day'] != focus: continue
            if any(v.inside(*p, pad=40) for p in b['pts'][::4] + b['pts'][-1:]): ov.band(b['pts'], b['color'], 4.0 if v.scale <= 100000 else 2.8, b['hatch'])
        for d in T.DAYS:                                   # соседние дни — серым
            if focus and d != focus:
                for s in T.day_lines(d): ov.line(s, (120, 120, 120), 0.45, casing=(255, 255, 255), casing_mm=0.2, dash=(1.6, 0.8))
        for a, b, p, q in T.TRANSFERS:
            if not focus or focus in (a, b): ov.line([p, q], GREY, 0.45, casing=(255, 255, 255), casing_mm=0.2, dash=(0.7, 0.8))
        for d, p, q in T.GAPS:
            if not focus or focus == d: ov.line([p, q], GREY, 0.45, casing=(255, 255, 255), casing_mm=0.2, dash=(0.7, 0.8))
        for d in T.DAYS:
            if focus and d != focus: continue
            D = T.DAYS[d]
            for r in D['rad']:
                if D['mains'] and T.one_way: ov.line(r, dcol(d), w * 0.8, casing=(255, 255, 255), casing_mm=0.3, dash=(1.8, 0.9))
                else: ov.line(r, dcol(d), w, casing=(255, 255, 255), casing_mm=0.35)
            for m in D['mains']: ov.line(m, dcol(d), w, casing=(255, 255, 255), casing_mm=0.35)
            for m in D['mains'] or D['rad']: ov.arrows(m, dcol(d), arrows_every, size_mm=1.9)
        if km_marks and focus:
            P = T.profile(focus); step = 1 if P['L'] <= 12 else (2 if P['L'] <= 30 else 5); k = step
            while k < P['L'] - 0.25:
                i = next(t for t in range(len(P['c'])) if P['c'][t] >= k)
                ov.sym('km', *P['pts'][i], 2.7, color=dcol(focus), text=str(k)); k += step
        objs = [o for o in T.OBJ if o['on_map'] and v.inside(*o['pt'])]
        for o in objs: self.obj_sym(ov, o, 3.0)
        first_d, last_d = min(T.DAYS), max(T.DAYS)
        start_free = not (T.camp_before(first_d) and dist(T.camp_before(first_d)['pt'], T.START) < 500)
        fin_free = not (T.camp_after(last_d) and dist(T.camp_after(last_d)['pt'], T.FINISH) < 300)
        if v.inside(*T.START) and start_free: ov.sym('start', *T.START, 3.4)
        if v.inside(*T.FINISH) and fin_free: ov.sym('finish', *T.FINISH, 3.0)
        for a, b, p, q in T.TRANSFERS:
            if v.inside(*q) and (not focus or focus == b): ov.sym('start', *q, 3.2)
        pl = [o for o in T.POI if o['on_map'] and v.inside(*o['pt'])] if pois else []
        for o in pl: ov.sym(o['sym'], *o['pt'], 3.0 if o['sym'] != 'town' else 2.2, color=o['color'] if o['sym'] in ('letter', 'peak') else None, text=o['text'])
        camps = [c for c in T.CAMPS if c['on_map'] and v.inside(*c['pt'])]
        for c in camps: ov.sym('camp', *c['pt'], 3.6)
        clusters = []
        if photos and T.PHOTOS:
            clusters = [c for c in mk.cluster_photos([p for p in T.PHOTOS if not focus or p['day'] == focus], photo_radius) if v.inside(c['lat'], c['lon'])]
            for c in clusters: ov.sym('photo', c['lat'], c['lon'], 3.0)
        # подписи: сначала объекты текущего дня и ночёвки, затем остальное
        def camp_text(c, full=True):
            t = c['lab'] + (f" · {c['date']}" if camp_dates and c['date'] else '')
            if full and c['name'] and not numbers: t += '\n' + c['name']
            if camp_heights: t += f"\n{T.camp_height(c)} м" + (' · днёвка' if c['nights'] > 1 else '')
            return t
        first = lambda o: 0 if (not focus or focus in o['days']) else 1
        if v.inside(*T.FINISH) and fin_free: ov.label(*T.FINISH, ('ФИНИШ ' + T.date(last_d)).strip(), pt, bold=True, force=True)
        if v.inside(*T.START) and start_free and (not focus or focus == first_d): ov.label(*T.START, ('СТАРТ ' + T.date(first_d)).strip(), pt, bold=True, color=(0, 120, 50), force=True)
        for a, b, p, q in T.TRANSFERS:
            if v.inside(*q) and (not focus or focus == b): ov.label(*q, ('выход ' + T.date(b)).strip() + '\n(после переезда)', pt - 0.3, bold=True, color=(0, 120, 50))
        for c in camps:
            mine = not focus or focus in (c['after'], c['before'])
            if mine: ov.label(*c['pt'], camp_text(c), pt, bold=True, color=prof.CAMP_INK, force=True, prefer=pref(c))
        for o in sorted(objs, key=first):
            mine = first(o) == 0
            if numbers:
                ov.label(*o['pt'], str(o['num']), pt + 0.4, bold=True, color=(255, 255, 255), halo=None, bg=GRADE_COL[o['grade']] if o['kind'] != 'obst' else (200, 0, 0),
                         gap_mm=1.3, force=True, leader=True, prefer=pref(o))
            elif mine:
                sub = T.obj_sub(o)
                ov.label(*o['pt'], o['title'] + ('\n' + sub if sub else ''), pt, bold=True, color=lab_col(o), force=True, prefer=pref(o))
            else:
                ov.label(*o['pt'], o['title'], pt - 0.6, color=lab_col(o), italic=True, prefer=pref(o))
        for c in camps:
            if focus and focus not in (c['after'], c['before']): ov.label(*c['pt'], c['lab'], pt - 0.6, color=prof.CAMP_INK, prefer=pref(c))
        if pois == 'label':
            for o in pl:
                col = o['color'] if o['sym'] in ('letter', 'water', 'plan', 'exit') else INK
                ov.label(*o['pt'], o['title'], pt - 0.5, color=tuple(int(c * 0.8) for c in col), bold=o['type'] in ('town', 'exit'), prefer=pref(o))
        elif pois == 'towns':
            for o in pl:
                if o['type'] == 'town': ov.label(*o['pt'], o['title'], pt, bold=True, leader=False)
        if day_labels:
            for d in T.DAYS:
                if focus and d != focus: continue
                s = T.DAYS[d]['main'] or T.day_pts(d)
                ins = [q for q in s if v.inside(*q, pad=-60)]
                if len(ins) < 5: continue
                # точка подписи — середина нитки дня; если там тесно от знаков, сдвигаем вдоль нитки
                busy = [v.px(*o['pt']) for o in objs] + [v.px(*c['pt']) for c in camps]
                def free(q): x, y = v.px(*q); return min([math.hypot(x - a, y - b_) for a, b_ in busy] or [1e9])
                cand = [ins[len(ins) * k // 12] for k in (6, 5, 7, 4, 8, 3, 9)]
                q = next((c for c in cand if free(c) > mm(7)), max(cand, key=free))
                ov.label(*q, f'день {d}' + (f' · {T.date(d)}' if T.date(d) else ''), pt, color=dcol(d), bold=True, italic=True, leader=False, gap_mm=2.0)
        for c in clusters: ov.label(c['lat'], c['lon'], c['label'], pt - 0.6, color=mk.PHOTO, gap_mm=1.0, force=True)

    def g_grades(self, d, x, y):
        fb = font(4.3, True, cond=True); gs = self.grades
        w = mm(7.6) / len(gs); xx = x - mm(3.8)
        for g in gs:
            d.rounded_rectangle((xx, y - mm(1.05), xx + w - mm(0.25), y + mm(1.05)), radius=int(mm(0.3)), fill=GRADE_COL[g])
            d.text((xx + (w - mm(0.25)) / 2, y), g, font=fb, fill=(255, 255, 255), anchor='mm'); xx += w

    def legend_items(self, extra=None, numbers=False, photos=False, view=None, pois=True, focus=None):
        T = self.T; items = []
        gcol = GRADE_COL[self.grades[-1] if self.grades else None]
        if len(T.DAYS) > 1:
            items += [(g_track(DAYC[0]), 'нитка, нечётные дни; ▶ направление'), (g_track(DAYC[1]), 'нитка, чётные дни')]
        else: items.append((g_track(DAYC[0]), 'нитка маршрута; ▶ направление'))
        if self.has_rad: items.append((g_dash(DAYC[0], 0.6), 'радиальный выход'))
        if T.TRANSFERS or T.GAPS: items.append((g_dash(GREY, 0.45), 'переезд (без трека)'))
        if T.CAMPS: items.append((g_sym('camp', size=3.6), 'ночёвка: №, дата' + ('' if numbers else ', высота')))
        items.append((lambda d, x, y: (g_sym('start', size=3.0)(d, x - mm(1.8), y), g_sym('finish', size=2.8)(d, x + mm(2.2), y)),
                      'старт' + (', выход после переезда' if T.TRANSFERS else '') + ' / финиш'))
        if self.n_pass: items.append((g_sym('pass', size=3.0, color=gcol), 'перевал' + (' (№ — см. список)' if numbers else ': категория, высота, м')))
        if self.n_peak: items.append((g_sym('peak', size=3.0, color=gcol), 'вершина на маршруте'))
        kinds = {o['type'] for o in T.OBJ}
        if 'obst' in kinds: items.append((g_sym('obst', '!', size=3.0), 'локальное препятствие'))
        if 'ford' in kinds: items.append((g_sym('obst', '≈', size=3.0), 'переправа, брод'))
        if 'prizhim' in kinds: items.append((g_sym('prizhim', size=3.4), 'прижим'))
        if len(self.grades) > 1: items.append((self.g_grades, 'цвет знака — категория трудности'))
        if pois:
            seen = []
            for o in T.POI:
                if not o['on_map'] or o['type'] in seen: continue
                if view is not None and not any(view.inside(*q['pt']) for q in T.POI if q['type'] == o['type'] and q['on_map']): continue
                seen.append(o['type'])
                items.append((g_sym(o['sym'], o['text'], size=3.0 if o['sym'] != 'town' else 2.2, color=o['color'] if o['sym'] in ('letter', 'peak') else None), o['legend'].lower() if not T.types[o['type']].get('custom') else o['legend']))
        if photos and T.PHOTOS: items.append((g_sym('photo', size=3.0), 'место фото «день-номер»'))
        seen = []
        for b in T.BANDS:
            if b['kind'] in seen or (focus and b['day'] != focus): continue
            if view is not None and not any(view.inside(*p) for p in b['pts'][::4]): continue
            seen.append(b['kind']); items.append((g_band(b['color'], b['hatch']), b['title']))
        return items + (extra or [])

    def put_legend(self, sh, x, y, items, cols, pt=5.8):
        rows = max(1, int(math.ceil(len(items) / cols)))
        sh.legend(x, y, items, (sh.frame_box[2] - x) / mm(1) / cols, rows, pt=pt)
        return y + rows * mm(3.55)

    def foot(self, sh, sc, src, extra_lines=()):
        km_, tick = BAR[sc]
        y = sh.frame_box[3] + mm(7.5); x = sh.frame_box[0]
        sh.scalebar(x + mm(1), y, km_, tick, 'Масштаб ' + scale_txt(sc))
        L = km_ * 1000 / (sc * 0.0254 / mk.DPI)
        tx = x + mm(3) + L + mm(6)
        sh.d.text((tx, y - mm(1.6)), per_cm(sc), font=font(6.3, cond=True), fill=INK, anchor='la')
        f = font(5.8, cond=True); room = sh.frame_box[2] - mm(9) - tx; lines = []
        what = ', '.join(['Нитка'] + (['ночёвки'] if self.T.CAMPS else []) + (['перевалы'] if self.n_pass else [])) + ' — трек группы.'
        for t in [src, what, *extra_lines, self.KM_NOTE]:
            if t is self.KM_NOTE and sh.d.textlength(t, font=f) > room: t = self.KM_NOTE_SHORT
            if lines and sh.d.textlength(lines[-1] + ' ' + t, font=f) <= room: lines[-1] += ' ' + t; continue
            while sh.d.textlength(t, font=f) > room and '. ' in t:      # перенос по предложениям
                a, b = t.split('. ', 1); lines.append(a + '.'); t = b
            lines.append(t)
        for i, t in enumerate(lines): sh.d.text((tx, y + mm(1.5) + i * mm(2.9)), t, font=f, fill=(70, 70, 70), anchor='la')
        sh.north(sh.frame_box[2] - mm(4), y + mm(2.2))
        return max(y + mm(8.5), y + mm(1.5) + len(lines) * mm(2.9) + mm(1.2))

    def _done(self, sh, sid, note=''):
        p = self._file(sid); sh.save(p); v = sh.v
        size = (self.o.get('logo', True) and stamp_logo(p)) or sh.size_cm
        frame = dict(lat=round(v.lat0, 6), lon=round(v.lon0, 6), scale=v.scale, fw=round(v.W / mk.DPI * 2.54, 2), fh=round(v.H / mk.DPI * 2.54, 2), manual=bool(self.adj(sid)))
        return dict(file=p, w_cm=size[0], h_cm=size[1], note=note + (' · рамка вручную' if self.adj(sid) else ''), frame=frame)

    def adj(self, sid):
        """Ручная правка рамки листа: dict(lat, lon, scale) — всё необязательно."""
        a = (self.o.get('adjust') or {}).get(sid) or {}
        out = {}
        try:
            if a.get('lat') is not None and a.get('lon') is not None: out['center'] = (float(a['lat']), float(a['lon']))
            if a.get('scale') and int(a['scale']) in BAR: out['scale'] = int(a['scale'])
        except (TypeError, ValueError): pass
        return out

    # --------------------------------------------------------------------------- рабочая карта
    def work_field(self):
        sc = int(self.o.get('work_scale') or 100000)
        return sc, 15.3, 18.4

    def chunks(self):
        """Нарезка нитки на листы: жадно по ходу маршрута, с полем 0,8 см."""
        if hasattr(self, '_chunks'): return self._chunks
        T = self.T; sc, FW, FH = self.work_field(); f = sc / 100000.0
        SEQ = [(d, p) for d in T.DAYS for s in T.day_lines(d) for p in s]
        out = []; i = 0
        while i < len(SEQ):
            j = i; a0 = a1 = SEQ[i][1][0]; b0 = b1 = SEQ[i][1][1]
            while j + 1 < len(SEQ):
                la, lo = SEQ[j + 1][1]
                na0, na1, nb0, nb1 = min(a0, la), max(a1, la), min(b0, lo), max(b1, lo)
                if (nb1 - nb0) * self.KX > (FW - 1.6) * f or (na1 - na0) * self.KY > (FH - 1.6) * f: break
                a0, a1, b0, b1 = na0, na1, nb0, nb1; j += 1
            out.append(((a0 + a1) / 2, (b0 + b1) / 2, sorted({SEQ[k][0] for k in range(i, j + 1)})))
            if j + 1 >= len(SEQ): break
            i = j + 1
        self._chunks = out
        return out

    def sheet_views(self):
        sc, FW, FH = self.work_field()
        return [(View(self.adj(f'work-{k}').get('center') or (c[0], c[1]), sc, FW, FH), c[2]) for k, c in enumerate(self.chunks(), 1)]

    def sheet(self, k):
        T = self.T; sc, FW, FH = self.work_field()
        views = self.sheet_views(); v, ds = views[k - 1]
        base, st = self.basemap(v, sc)
        ov = Overlay(v)
        for j, (nv, _) in enumerate(views, 1):
            if j in (k - 1, k + 1):
                a0, a1, b0, b1 = nv.bounds()
                ov.line([(a1, b0), (a1, b1), (a0, b1), (a0, b0), (a1, b0)], (40, 40, 40), 0.3, dash=(2.2, 1.4))
        self.draw_route(ov, camp_heights=True, photos=self.o.get('photos', True) and sc <= 100000, photo_radius=250 * sc / 100000)
        dts = lambda a, b: f' ({T.date(a)}–{T.date(b)})' if T.date(a) and a != b else (f' ({T.date(a)})' if T.date(a) else '')
        dd = f'дни {ds[0]}–{ds[-1]}' + dts(ds[0], ds[-1]) if len(ds) > 1 else f'день {ds[0]}' + dts(ds[0], ds[0])
        title = 'Рабочая карта · ' + (f'лист {k} из {len(views)} · ' if len(views) > 1 else '') + dd
        gs = {50000: (1, 1), 100000: (2, 3)}.get(sc, (None, None))
        sh = Sheet(v, base, ov.render(), title, None, 16.5, FH + 12, lat_step=gs[0] and gs[0] / 60, lon_step=gs[1] and gs[1] / 60)
        grid = f'Сетка географическая (WGS-84): {gs[0]}′ по широте, {gs[1]}′ по долготе.' if gs[0] else 'Сетка географическая (WGS-84).'
        y = self.foot(sh, sc, st['src'], [grid])
        extra = [(g_dash((40, 40, 40), 0.3), 'граница соседнего листа')] if len(views) > 1 else []
        self.put_legend(sh, sh.frame_box[0], y + mm(1.5), self.legend_items(extra, photos=self.o.get('photos', True), view=v), 3)
        return self._done(sh, f'work-{k}')

    # --------------------------------------------------------------------------- обзорная
    def overview_plan(self):
        la0, la1, lo0, lo1 = bbox(self.ALL)
        w = (lo1 - lo0) * self.KX; h = (la1 - la0) * self.KY
        best = None; a = self.adj('overview')
        if a.get('scale'):
            # масштаб задан руками: альбомный лист, высота поля — сколько нужно нитке (в разумных пределах)
            sc = a['scale']; fh = min(max(h * 1e5 / sc + 2.6, 9.0), 13.5)
            return sc, True, 25.0, round(fh, 1), 26.6, a.get('center') or ((la0 + la1) / 2, (lo0 + lo1) / 2)
        for land, FW, FHmax, PW in ((True, 25.0, 13.5, 26.6), (False, 16.9, 20.5, 18.0)):
            for sc in (50000, 100000, 200000, 300000, 500000, 1000000, 2000000, 5000000):
                fw, fh = w * 1e5 / sc + 2.2, h * 1e5 / sc + 2.6
                if fw <= FW and fh <= FHmax:
                    cand = (sc, 0 if land else 1, land, FW, max(fh, 9.0 if land else 11.0), PW)
                    if best is None or cand[:2] < best[:2]: best = cand
                    break
        if best is None: best = (5000000, 0, True, 25.0, 13.0, 26.6)
        sc, _, land, FW, FH, PW = best
        return sc, land, FW, round(FH, 1), PW, a.get('center') or ((la0 + la1) / 2, (lo0 + lo1) / 2)

    def overview(self):
        T = self.T
        sc, land, FW, FH, PW, center = self.overview_plan()
        v = View(center, sc, FW, FH)
        base, st = self.basemap(v, sc)
        ov = Overlay(v)
        wsc = self.work_field()[0]
        scheme = self.o.get('work', True) and sc > wsc and len(self.chunks()) > 1
        if scheme:
            for k, (sv, _) in enumerate(self.sheet_views(), 1):
                a0, a1, b0, b1 = sv.bounds()
                ov.line([(a1, b0), (a1, b1), (a0, b1), (a0, b0), (a1, b0)], (0, 60, 160), 0.4, dash=(2.2, 1.2))
                ov.label(a1, b0, f'лист {k}', 6.6, color=(0, 60, 160), bold=True, leader=False, gap_mm=0.6, prefer=['SE', 'NE', 'E'], force=True)
        numbers = len([o for o in T.OBJ if o['on_map']]) > 6
        self.draw_route(ov, w=0.8, arrows_every=14, pt=6.2, numbers=numbers, camp_dates=not numbers, day_labels=True, pois='towns')
        hp = self.headline()
        sub = ' · '.join(x for x in [T.period(), *hp, f'схема листов рабочей карты {scale_txt(wsc)}' if scheme else ''] if x)
        gs = {100000: (2, 3), 200000: (3, 5), 300000: (5, 10), 500000: (10, 15)}.get(sc, (None, None))
        sh = Sheet(v, base, ov.render(), ' · '.join(x for x in ['Обзорная карта маршрута', self.area] if x), sub, PW, FH + 16, top_cm=1.5, lat_step=gs[0] and gs[0] / 60, lon_step=gs[1] and gs[1] / 60)
        y = self.foot(sh, sc, st['src'])
        x = sh.frame_box[0]; d = sh.d
        extra = [(lambda d, x, y: d.rectangle((x - mm(3.5), y - mm(1.3), x + mm(3.5), y + mm(1.3)), outline=(0, 60, 160), width=int(mm(0.35))), f'лист рабочей карты {scale_txt(wsc)}')] if scheme else []
        cols = 4 if land else 3
        yy = self.put_legend(sh, x, y + mm(2), self.legend_items(extra, numbers=numbers, view=v), cols, pt=6.0) + mm(3.5)
        if numbers:      # список перевалов и вершин по номерам
            objs = [o for o in T.OBJ_ORDERED if o['on_map']]
            d.text((x, yy), 'Перевалы, вершины и препятствия по ходу маршрута (№ на карте — название, категория, высота, день):', font=font(6.6, True, cond=True), fill=INK, anchor='la')
            yy += mm(4.2); cols = 4 if land else 2; rows = int(math.ceil(len(objs) / cols)); colw = (sh.frame_box[2] - x) / cols; f = font(6.0, cond=True); fb = font(5.4, True, cond=True)
            for i, o in enumerate(objs):
                c, r = divmod(i, rows); xx = x + c * colw; yr = yy + r * mm(3.5)
                col = GRADE_COL[o['grade']] if o['kind'] != 'obst' else (200, 0, 0)
                d.rounded_rectangle((xx, yr - mm(1.3), xx + mm(4.2), yr + mm(1.3)), radius=int(mm(0.5)), fill=col)
                d.text((xx + mm(2.1), yr), str(o['num']), font=fb, fill=(255, 255, 255), anchor='mm')
                t = T.obj_label(o) + ((' · ' + ('дни ' if len(o['days']) > 1 else 'день ') + ', '.join(map(str, o['days']))) if o['days'] else '')
                while d.textlength(t, font=f) > colw - mm(6.5) and len(t) > 8: t = t[:-2].rstrip() + '…'
                d.text((xx + mm(5.4), yr), t, font=font(6.0, True, cond=True) if o['key'] else f, fill=INK, anchor='lm')
            yy += rows * mm(3.5) + mm(1.5)
        if T.CAMPS:
            t = 'Ночёвки: ' + ';  '.join(c['lab'] + ' — ' + ', '.join(z for z in [c['date'], c['name'], f"{T.camp_height(c)} м"] if z) for c in T.CAMPS if c['on_map'])
            words = t.split(' '); line = ''; f2 = font(5.9, cond=True)
            for wd in words + ['\n']:
                if wd == '\n' or d.textlength(line + ' ' + wd, font=f2) > sh.frame_box[2] - x:
                    d.text((x, yy), line, font=f2, fill=prof.CAMP_INK, anchor='la'); yy += mm(3.1); line = wd
                else: line = (line + ' ' + wd).strip()
        return self._done(sh, 'overview', note=f'масштаб {scale_txt(sc)}')

    # --------------------------------------------------------------------------- район
    def region(self):
        T = self.T
        la0, la1, lo0, lo1 = bbox(self.ALL)
        FW, FH = 16.7, 10.5
        w = (lo1 - lo0) * self.KX; h = (la1 - la0) * self.KY
        a = self.adj('region')
        sc = a.get('scale') or next((s for s in (1000000, 2000000, 5000000) if w * 1e5 / s < FW * 0.6 and h * 1e5 / s < FH * 0.6), 5000000)
        v = View(a.get('center') or ((la0 + la1) / 2, (lo0 + lo1) / 2), sc, FW, FH)
        base, st = self.basemap(v, sc)
        ov = Overlay(v)
        for a, b, p, q in T.TRANSFERS: ov.line([p, q], (120, 0, 0), 0.4, dash=(0.7, 0.7))
        for d in T.DAYS:
            for s in T.day_lines(d): ov.line(s, (220, 0, 0), 0.7, casing=(255, 255, 255), casing_mm=0.2)
        for o in T.POI:
            if o['type'] == 'town' and v.inside(*o['pt']):
                ov.sym('town', *o['pt'], 2.4); ov.label(*o['pt'], o['title'], 7.5, bold=True, leader=False, prefer=['S', 'SE', 'SW', 'E', 'W'])
        ov.label(la1 + (la1 - la0) * 0.06 + 0.004 * sc / 1e6, (lo0 + lo1) / 2, 'район похода', 8.5, color=(200, 0, 0), bold=True, prefer=['N'], leader=False, gap_mm=1, force=True)
        sh = Sheet(v, base, ov.render(), ': '.join(x for x in ['Район похода', self.area] if x), None, 18.0, FH + 5, top_cm=1.0)
        y = sh.frame_box[3] + mm(7.5); x = sh.frame_box[0]
        km_, tick = BAR[sc]
        L = sh.scalebar(x + mm(1), y, km_, tick, 'Масштаб ' + scale_txt(sc))
        sh.d.text((x + mm(12) + L, y + mm(1)), st['src'], font=font(5.8, cond=True), fill=(70, 70, 70), anchor='la')
        return self._done(sh, 'region', note=f'масштаб {scale_txt(sc)}')

    # --------------------------------------------------------------------------- карты дней с профилем
    PAGE_W = 18.0
    def day_plan(self, d):
        a0, a1, b0, b1 = bbox(self.T.day_pts(d))
        w = (b1 - b0) * self.KX + 1.1; h = (a1 - a0) * self.KY + 1.1
        a = self.adj(f'day-{d}'); center = a.get('center') or ((a0 + a1) / 2, (b0 + b1) / 2)
        if a.get('scale'):
            return a['scale'], min(max(h * 1e5 / a['scale'], 8.0), 13.6), center
        if a.get('center'):       # рамку сдвинули, масштаб — автоматический
            for sc in (25000, 50000, 100000, 200000, 500000, 1000000):
                if w * 1e5 / sc <= self.PAGE_W - 1.1 and h * 1e5 / sc <= 13.6: return sc, max(h * 1e5 / sc, 8.0), center
        for sc in (25000, 50000, 100000, 200000, 500000, 1000000):
            fw, fh = w * 1e5 / sc, h * 1e5 / sc
            if fw <= self.PAGE_W - 1.1 and fh <= 13.6:
                return sc, max(fh, 8.0), ((a0 + a1) / 2, (b0 + b1) / 2)
        return 2000000, 8.0, ((a0 + a1) / 2, (b0 + b1) / 2)

    def day_chain(self, d):
        """«Н6 → пер. Восьми (2А, 3513) → Н7»."""
        T = self.T; P = T.profile(d); parts = []
        c = T.camp_before(d)
        from_camp = bool(c and dist(c['pt'], P['pts'][0]) < 500)
        parts.append(c['lab'] if from_camp else ('старт' if d == min(T.DAYS) else 'выход'))
        marks = [m for m in P['marks'] if m.get('oi') is not None and m['first'] and T.OBJ[m['oi']]['on_map']]
        for m in marks:
            o = T.OBJ[m['oi']]
            if m['x'] < 0.15 and from_camp: parts[0] += f" ({o['title']})"; continue
            if P['Lpath'] - m['x'] < 0.15: continue
            s = T.obj_sub(o, unit=False).replace(' · ', ', ')
            parts.append(o['title'] + (f' ({s})' if s else ''))
        c = T.camp_after(d); end = c['lab'] if c and dist(c['pt'], P['pts'][-1]) < 800 else ('финиш' if d == max(T.DAYS) else 'конец дня')
        last = [T.OBJ[m['oi']] for m in marks if P['Lpath'] - m['x'] < 0.15]
        if last:
            s = T.obj_sub(last[0], unit=False).replace(' · ', ', ')
            end += f" ({last[0]['title']}" + (f', {s}' if s else '') + ')'
        return ' → '.join(parts + [end])

    def day(self, d):
        T = self.T
        sc, fh, center = self.day_plan(d)
        v = View(center, sc, self.PAGE_W - 1.1, fh)
        base, st = self.basemap(v, sc)
        ov = Overlay(v)
        photos = self.o.get('photos', True)
        self.draw_route(ov, focus=d, camp_heights=True, km_marks=True, day_labels=False, arrows_every=26, photos=photos, photo_radius=150 * sc / 50000)
        P = T.profile(d)
        dt = T.date(d) + (f'.{T.date_obj(d).year}' if T.date(d) else '')
        title = ' · '.join(x for x in [f'День {d}', dt, f'{km(P["L"])} км', f'набор {P["up"]:.0f} м', f'сброс {P["dn"]:.0f} м'] if x)
        gs = {25000: (0.5, 0.5), 50000: (1, 1), 100000: (2, 3)}.get(sc, (None, None))
        sh = Sheet(v, base, ov.render(), title, self.day_chain(d), self.PAGE_W, fh + 15.5, lat_step=gs[0] and gs[0] / 60, lon_step=gs[1] and gs[1] / 60, top_cm=1.5)
        y = self.foot(sh, sc, st['src'])
        x = sh.frame_box[0]
        segs, marks, camps = self.prof_inputs([d])
        box = (x, int(y + mm(3.0)), sh.frame_box[2] - x, int(mm(58)))
        rad_here = bool(T.DAYS[d]['rad'] and T.DAYS[d]['mains']) and T.one_way
        xl = 'км от начала дня' + (f', длина по карте{self.kx}' if self.kx else '') + ' (номера в кружках на карте)' + ('; радиальный выход — в одну сторону' if rad_here else '')
        sh.img, info = prof.draw(sh.img, box, segs, marks, camps, grade_col=GRADE_COL, orient='h',
                                 title=f'Профиль высот · день {d} · высоты {P["zmin"]:.0f}–{P["zmax"]:.0f} м', day_strip=False, x_label=xl, pt=6.0, bands=list(self._pbands), vex_max=VEX_MAX)
        sh.d = ImageDraw.Draw(sh.img)
        yl = box[1] + box[3] + mm(1.0)
        extra = [(g_sym('km', '3', size=2.7, color=dcol(d)), 'километры от начала дня' + (f' ({self.kx.strip()})' if self.kx else ''))]
        if len(T.DAYS) > 1: extra.append((g_dash((120, 120, 120), 0.45), 'нитка соседних дней'))
        self.put_legend(sh, x, yl, self.legend_items(extra, photos=photos and any(p['day'] == d for p in T.PHOTOS), view=v, focus=d), 3, pt=5.6)
        return self._done(sh, f'day-{d}', note=f'масштаб {scale_txt(sc)}')
