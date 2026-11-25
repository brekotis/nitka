"""Рендер карт для отчёта: тайловая основа + векторные слои + зарамочное оформление."""
import math, os, io, json, time, bisect, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get('NITKA_CACHE') or os.path.join(HERE, '..', 'data', 'cache')
UA = 'nitka-hiking-report-maps/1.0'
R = 6378137.0
DPI = 300
SS = 2  # supersampling for vector overlay

FONT_DIR = os.path.join(HERE, '..', 'fonts') + os.sep
_fonts = {}
def font(pt, bold=False, cond=False, italic=False, scale=1):
    name = 'DejaVuSans'
    if cond: name += 'Condensed'
    if bold and italic: name += '-BoldOblique'
    elif bold: name += '-Bold'
    elif italic: name += '-Oblique'
    k = (name, max(1, int(round(pt / 72 * DPI * scale))))
    if k not in _fonts: _fonts[k] = ImageFont.truetype(FONT_DIR + name + '.ttf', k[1])
    return _fonts[k]

def mm(v, scale=1):
    return v / 25.4 * DPI * scale

# ---------------------------------------------------------------- projection
def merc(lat, lon):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

def unmerc(x, y):
    return math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2), math.degrees(x / R)

LAYERS = {
    'ggc2000': ('https://a.tiles.nakarte.me/ggc2000/{z}/{x}/{y}', True),
    'ggc1000': ('https://a.tiles.nakarte.me/ggc1000/{z}/{x}/{y}', True),
    'ggc500': ('https://a.tiles.nakarte.me/ggc500/{z}/{x}/{y}', True),
    'ggc250': ('https://a.tiles.nakarte.me/ggc250/{z}/{x}/{y}', True),
    'gsh050': ('https://a.tiles.nakarte.me/new_gsh_050k/{z}/{x}/{y}', True),   # новый Генштаб 1:50 000
    'gsh100': ('https://a.tiles.nakarte.me/new_gsh_100k/{z}/{x}/{y}', True),   # новый Генштаб 1:100 000
    'topo500': ('https://a.tiles.nakarte.me/topo500/{z}/{x}/{y}', True),
    'topo1000': ('https://a.tiles.nakarte.me/topo1000/{z}/{x}/{y}', True),
    'topo001m': ('https://a.tiles.nakarte.me/topo001m/{z}/{x}/{y}', True),
    'otm': ('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', False),
    'esri': ('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', False),
}
_lock = threading.Lock()

# Топокарты на nakarte.me. Каждый слой покрывает только свои районы: «новый Генштаб» (gsh050/gsh100) — в основном Кавказ,
# topo500/topo1000 и ГГЦ (ggc*) — остальное, причём пятнами. Поэтому основа собирается из первого слоя, который в этом месте есть.
# Число в имени слоя nakarte — метров в сантиметре: 500 → 1:50 000, 1000 → 1:100 000, 2000 → 1:200 000.
TOPO_NAME = {'gsh050': 'Генштаба 1:50 000', 'topo500': 'Генштаба 1:50 000', 'ggc500': 'ГГЦ 1:50 000', 'ggc250': 'ГГЦ 1:25 000',
             'gsh100': 'Генштаба 1:100 000', 'topo1000': 'Генштаба 1:100 000', 'ggc1000': 'ГГЦ 1:100 000', 'ggc2000': 'ГГЦ 1:200 000',
             'topo001m': '1:1 000 000'}
TOPO_MAXZ = {'gsh050': 15, 'ggc250': 15, 'topo500': 14, 'ggc500': 14, 'gsh100': 14, 'topo1000': 13, 'ggc1000': 13, 'ggc2000': 12, 'topo001m': 10}
TOPO_ORDER = {
    50: ['gsh050', 'topo500', 'ggc500', 'ggc250', 'gsh100', 'topo1000', 'ggc1000', 'ggc2000', 'topo001m'],
    100: ['gsh100', 'topo1000', 'ggc1000', 'gsh050', 'topo500', 'ggc500', 'ggc250', 'ggc2000', 'topo001m'],
    200: ['ggc2000', 'gsh100', 'topo1000', 'ggc1000', 'topo001m'],
}

def topo_chain(z, prefer, max_up=2):
    """Цепочка слоёв [(слой, зум)] для основы зума z: сначала карты масштаба prefer (50 | 100 | 200 тыс.),
    потом остальные. Слой, который пришлось бы растягивать больше чем в 2**max_up раз, не берётся."""
    return [(L, min(z, TOPO_MAXZ[L])) for L in TOPO_ORDER[prefer] if z - min(z, TOPO_MAXZ[L]) <= max_up]

def get_tile(layer, z, x, y):
    path = os.path.join(CACHE, 'tiles', layer, str(z), str(x), f'{y}.png')
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return _rgb(Image.open(path))
    data = fetch_tile(layer, z, x, y)      # пустая пометка «тайла нет» разбирается там же (у неё есть срок годности)
    return _rgb(Image.open(io.BytesIO(data))) if data else None

def fetch_tile(layer, z, x, y, timeout=30, tries=3, fresh=False):
    """Скачивает тайл в кэш; возвращает байты, b'' — тайла точно нет, None — сервер карты не ответил (в кэш не пишется)."""
    path = os.path.join(CACHE, 'tiles', layer, str(z), str(x), f'{y}.png')
    if os.path.exists(path) and not (fresh and os.path.getsize(path) == 0):      # fresh — пометку «тайла нет» перепроверить
        # пометка «тайла нет» не вечна. OpenTopoMap рисует крупный зум по запросу: сначала отвечает 404,
        # а через несколько минут тайл уже есть — поэтому у него пометка живёт четверть часа, у остальных слоёв — две недели
        if os.path.getsize(path) or time.time() - os.path.getmtime(path) < (900 if layer == 'otm' else 14 * 86400):
            with open(path, 'rb') as f: return f.read()
    url, tms = LAYERS[layer]
    if not (0 <= x < 2 ** z and 0 <= y < 2 ** z): return b''
    yy = (2 ** z - 1 - y) if tms else y
    data = None
    for k in range(tries):
        req = urllib.request.Request(url.format(z=z, x=x, y=yy, s='abc'[(x + y + k) % 3]), headers={'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(); break
        except urllib.error.HTTPError as e:
            if e.code in (404, 204, 400): data = b''; break
        except Exception:
            pass
    if data is None: return None
    if data:
        try: Image.open(io.BytesIO(data)).load()
        except Exception: return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f'.{threading.get_ident()}.tmp'
    with open(tmp, 'wb') as f: f.write(data)
    os.replace(tmp, path)
    return data

def _rgb(im):
    """Тайл в RGBA; полностью пустой — None."""
    im = im.convert('RGBA')
    if im.split()[3].getextrema()[1] == 0:
        return None
    return im

class View:
    """Карта: центр, масштаб 1:scale, размер поля в см. px — координаты в пикселях поля при DPI."""
    def __init__(self, center, scale, w_cm, h_cm):
        self.lat0, self.lon0 = center
        self.scale = scale
        self.W = int(round(w_cm / 2.54 * DPI)); self.H = int(round(h_cm / 2.54 * DPI))
        g = scale * 0.0254 / DPI
        self.res = g / math.cos(math.radians(self.lat0))
        self.xc, self.yc = merc(self.lat0, self.lon0)
        self.xmin = self.xc - self.W / 2 * self.res
        self.ymax = self.yc + self.H / 2 * self.res

    def px(self, lat, lon, s=1):
        x, y = merc(lat, lon)
        return ((x - self.xmin) / self.res * s, (self.ymax - y) / self.res * s)

    def ll(self, px, py):
        return unmerc(self.xmin + px * self.res, self.ymax - py * self.res)

    def bounds(self):
        la1, lo0 = self.ll(0, 0); la0, lo1 = self.ll(self.W, self.H)
        return la0, la1, lo0, lo1

    def inside(self, lat, lon, pad=0):
        x, y = self.px(lat, lon)
        return -pad <= x <= self.W + pad and -pad <= y <= self.H + pad

    def _present(self, layers, z, tx0, ty0, tx1, ty1):
        """Отбрасывает слои, которых нет ни в одной из 25 проб по полю листа (последний слой цепочки остаётся всегда).
        Проба — те же тайлы, что пойдут в мозаику, так что запросы не лишние."""
        keep = []
        for i, L in enumerate(layers):
            lname, lz = (L, z) if isinstance(L, str) else L
            if i == len(layers) - 1 or lname not in TOPO_MAXZ: keep.append(L); continue
            d = 2 ** (z - lz)
            probe = {((tx0 + (tx1 - tx0) * a // 4) // d, (ty0 + (ty1 - ty0) * b // 4) // d) for a in range(5) for b in range(5)}
            with ThreadPoolExecutor(6) as ex:
                if any(im is not None for im in ex.map(lambda xy: get_tile(lname, lz, *xy), probe)): keep.append(L)
        self.layers_kept = [L if isinstance(L, str) else L[0] for L in keep]
        return keep

    def basemap(self, layers, z, lighten=0.12, fallback_layers=None, shade=0.0, shade_z=12):
        """layers: список слоёв по приоритету; при отсутствии тайла берётся следующий слой."""
        world = 256 * 2 ** z
        t = 2 * math.pi * R / world
        X0 = (self.xmin + math.pi * R) / t
        Y0 = (math.pi * R - self.ymax) / t
        X1 = X0 + self.W * self.res / t
        Y1 = Y0 + self.H * self.res / t
        tx0, ty0, tx1, ty1 = int(X0 // 256), int(Y0 // 256), int(X1 // 256), int(Y1 // 256)
        if len(layers) > 1:
            layers = self._present(layers, z, tx0, ty0, tx1, ty1)
        self.layer_names = [L if isinstance(L, str) else L[0] for L in layers]      # номера в layer_use — по этому списку
        mosaic = Image.new('RGB', ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (255, 255, 255))
        jobs = [(x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]
        missing = []; use = {}
        def job(xy):
            x, y = xy
            acc = None; used = None
            for li, L in enumerate(layers):
                lz = z if isinstance(L, str) else L[1]
                lname = L if isinstance(L, str) else L[0]
                if lz == z:
                    im = get_tile(lname, z, x, y)
                else:  # более мелкий зум: вырезаем и увеличиваем
                    d = 2 ** (z - lz)
                    im = get_tile(lname, lz, x // d, y // d)
                    if im is not None:
                        cs = 256 // d
                        im = im.crop(((x % d) * cs, (y % d) * cs, (x % d + 1) * cs, (y % d + 1) * cs)).resize((256, 256), Image.BICUBIC)
                if im is None: continue
                acc = im if acc is None else Image.alpha_composite(im, acc)
                if used is None: used = li
                if acc.split()[3].getextrema()[0] == 255: break
                used = max(used, li) if li else used
            if acc is None: return xy, None, None
            out = Image.new('RGBA', (256, 256), (255, 255, 255, 255))
            return xy, Image.alpha_composite(out, acc).convert('RGB'), used
        holes = []
        for attempt in range(2):
            with ThreadPoolExecutor(6) as ex:
                for (x, y), im, li in ex.map(job, jobs):
                    if im is not None:
                        mosaic.paste(im, ((x - tx0) * 256, (y - ty0) * 256))
                        use[li] = use.get(li, 0) + 1
                        if li: missing.append((x, y, li))
                    else: holes.append((x, y))
            if not holes or attempt: break
            jobs, holes = holes, []; time.sleep(3)      # сервер карты мог не ответить — ещё одна попытка для пустых мест
        self.holes = len(holes)
        self.fallback_tiles = missing
        self.layer_use = use
        crop = mosaic.crop((int(X0 - tx0 * 256), int(Y0 - ty0 * 256), int(math.ceil(X1 - tx0 * 256)), int(math.ceil(Y1 - ty0 * 256))))
        img = crop.resize((self.W, self.H), Image.LANCZOS)
        if lighten:
            img = Image.blend(img, Image.new('RGB', img.size, (255, 255, 255)), lighten)
        if shade:
            # отмывка рельефа по ЦМР: тени затемняют, освещённые склоны остаются как есть
            from . import dem
            from PIL import ImageChops
            hs = dem.hillshade(self, shade_z).filter(ImageFilter.GaussianBlur(1.2))
            lut = [int(255 * (1 - shade * max(0.0, (185 - v) / 185) ** 1.15)) for v in range(256)]
            m = hs.point(lut)
            img = ImageChops.multiply(img, Image.merge('RGB', (m, m, m)))
        return img


# ---------------------------------------------------------------- geometry helpers
def dist(a, b):
    return 6371000 * math.hypot(math.radians(b[0] - a[0]), math.radians(b[1] - a[1]) * math.cos(math.radians(a[0])))

def cluster_photos(photos, radius_m):
    """Объединяет близкие фототочки одного дня в один знак с диапазоном номеров."""
    out = []
    for p in sorted(photos, key=lambda p: (p['day'], min(p['nums']) if p['nums'] else 0)):
        for c in out:
            if c['day'] == p['day'] and dist((c['lat'], c['lon']), (p['lat'], p['lon'])) < radius_m:
                c['nums'] += p['nums']; c['members'].append(p); break
        else:
            out.append(dict(day=p['day'], lat=p['lat'], lon=p['lon'], nums=list(p['nums']), members=[p]))
    for c in out:
        c['lat'] = sum(m['lat'] for m in c['members']) / len(c['members'])
        c['lon'] = sum(m['lon'] for m in c['members']) / len(c['members'])
        c['label'] = photo_label(c['day'], c['nums'])
    return out

def photo_label(day, nums):
    nums = sorted(set(nums))
    if not nums: return f'{day}'
    runs = []; a = b = nums[0]
    for n in nums[1:]:
        if n == b + 1: b = n
        else: runs.append((a, b)); a = b = n
    runs.append((a, b))
    parts = [f'{a}' if a == b else (f'{a},{b}' if b == a + 1 else f'{a}…{b}') for a, b in runs]
    return f'{day}-' + ','.join(parts)


# ---------------------------------------------------------------- overlay drawing
DAY_COLORS = [(226, 0, 26), (122, 26, 170)]  # нечётные / чётные дни
INK = (25, 25, 25)
PHOTO = (0, 70, 160)
WATER = (0, 120, 215)
HAZ = (200, 0, 0)

class Overlay:
    def __init__(self, view):
        self.v = view
        self.s = SS
        self.img = Image.new('RGBA', (view.W * SS, view.H * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.occupied = []  # прямоугольники подписей/знаков (в 1x px)
        self.trackmask = Image.new('L', (view.W, view.H), 0)
        self.tm = ImageDraw.Draw(self.trackmask)

    def P(self, lat, lon):
        return self.v.px(lat, lon, self.s)

    def line(self, pts, color, w_mm, casing=None, casing_mm=0, dash=None):
        P = [self.P(*p) for p in pts]
        if len(P) < 2: return
        if casing:
            self._poly(P, casing, mm(w_mm + 2 * casing_mm, self.s))
        if dash:
            self._dashed(P, color, mm(w_mm, self.s), mm(dash[0], self.s), mm(dash[1], self.s))
        else:
            self._poly(P, color, mm(w_mm, self.s))
        self.tm.line([(x / self.s, y / self.s) for x, y in P], fill=255, width=max(1, int(mm(w_mm + 0.6))))

    def _poly(self, P, color, w):
        w = max(1, int(round(w)))
        self.d.line(P, fill=color, width=w, joint='curve')
        r = w / 2
        for x, y in (P[0], P[-1]):
            self.d.ellipse((x - r, y - r, x + r, y + r), fill=color)

    def _dashed(self, P, color, w, on, off):
        acc = 0; draw = True; seg = [P[0]]
        for a, b in zip(P, P[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1]); pos = 0
            while L - pos > 1e-6:
                lim = on if draw else off
                step = min(lim - acc, L - pos)
                pos += step; acc += step
                pt = (a[0] + (b[0] - a[0]) * pos / L, a[1] + (b[1] - a[1]) * pos / L)
                if draw: seg.append(pt)
                if acc >= lim - 1e-6:
                    if draw and len(seg) > 1: self.d.line(seg, fill=color, width=max(1, int(w)))
                    draw = not draw; acc = 0; seg = [pt]
        if draw and len(seg) > 1: self.d.line(seg, fill=color, width=max(1, int(w)))

    def band(self, pts, color, w_mm, hatch=None):
        """Полупрозрачная полоса под треком (участок с особым характером пути)."""
        layer = Image.new('RGBA', self.img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        P = [self.P(*p) for p in pts]
        w = int(mm(w_mm, self.s))
        d.line(P, fill=color, width=w, joint='curve')
        for x, y in (P[0], P[-1]):
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=color)
        if hatch:
            mask = layer.split()[3].point(lambda a: 255 if a > 0 else 0)
            hl = Image.new('RGBA', self.img.size, (0, 0, 0, 0)); hd = ImageDraw.Draw(hl)
            st = int(mm(1.1, self.s)); W, H = self.img.size
            xs = [p[0] for p in P]; ys = [p[1] for p in P]
            x0, x1, y0, y1 = int(min(xs)) - w, int(max(xs)) + w, int(min(ys)) - w, int(max(ys)) + w
            for k in range(x0 - (y1 - y0), x1 + st, st):
                hd.line((k, y1, k + (y1 - y0), y0), fill=hatch, width=max(1, int(mm(0.25, self.s))))
            empty = Image.new('RGBA', self.img.size, (0, 0, 0, 0))
            layer = Image.alpha_composite(layer, Image.composite(hl, empty, mask))
        self.img.alpha_composite(layer)

    def arrows(self, pts, color, every_mm, size_mm=2.0, offset_mm=None):
        P = [self.P(*p) for p in pts]
        step = mm(every_mm, self.s); acc = (offset_mm and mm(offset_mm, self.s)) or step / 2
        for a, b in zip(P, P[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L == 0: continue
            pos = acc
            while pos < L:
                x = a[0] + (b[0] - a[0]) * pos / L; y = a[1] + (b[1] - a[1]) * pos / L
                # направление сглаженное по окну
                self._arrow(x, y, self._dir(P, a, b), color, mm(size_mm, self.s))
                pos += step
            acc = pos - L

    def _dir(self, P, a, b):
        return math.atan2(b[1] - a[1], b[0] - a[0])

    def _arrow(self, x, y, ang, color, s):
        pts = [(s, 0), (-s * 0.8, s * 0.75), (-s * 0.35, 0), (-s * 0.8, -s * 0.75)]
        c, sn = math.cos(ang), math.sin(ang)
        Q = [(x + px * c - py * sn, y + px * sn + py * c) for px, py in pts]
        self.d.polygon(Q, fill=color, outline=(255, 255, 255), width=max(1, int(s * 0.18)))

    # --- symbols ---------------------------------------------------------
    def sym(self, kind, lat, lon, size_mm=3.2, color=None, text=None):
        x, y = self.P(lat, lon); s = mm(size_mm, self.s) / 2; d = self.d
        o = max(1, int(mm(0.3, self.s)))
        if kind == 'camp':
            d.polygon([(x, y - s * 1.1), (x + s, y + s * 0.75), (x - s, y + s * 0.75)], fill=(255, 210, 0), outline=INK, width=o)
            d.line([(x, y - s * 0.2), (x, y + s * 0.75)], fill=INK, width=o)
        elif kind == 'start':
            d.ellipse((x - s, y - s, x + s, y + s), fill=(0, 150, 60), outline=(255, 255, 255), width=o)
            self._ctext(x, y, 'С', font(6.5, True, scale=self.s), (255, 255, 255))
        elif kind == 'finish':
            d.rectangle((x - s, y - s, x + s, y + s), fill=(255, 255, 255), outline=INK, width=o)
            q = s / 2
            for i in range(4):
                for j in range(4):
                    if (i + j) % 2 == 0:
                        d.rectangle((x - s + i * q, y - s + j * q, x - s + (i + 1) * q, y - s + (j + 1) * q), fill=INK)
        elif kind == 'photo':
            r = s * 0.42
            d.ellipse((x - r, y - r, x + r, y + r), fill=PHOTO, outline=(255, 255, 255), width=max(1, int(o * 0.7)))
        elif kind == 'pass':
            w = max(2, int(mm(0.42, self.s)))
            col = color or (150, 40, 0)
            r = s * 1.15
            d.ellipse((x - r * 1.25, y - r * 1.25, x + r * 1.25, y + r * 1.25), fill=(255, 255, 255, 200))
            d.arc((x - r * 0.35 - 2 * r, y - r, x - r * 0.35, y + r), -55, 55, fill=col, width=w)
            d.arc((x + r * 0.35, y - r, x + r * 0.35 + 2 * r, y + r), 125, 235, fill=col, width=w)
        elif kind == 'obst':
            d.polygon([(x, y - s * 1.2), (x + s * 1.2, y), (x, y + s * 1.2), (x - s * 1.2, y)], fill=HAZ, outline=(255, 255, 255), width=o)
            if text: self._ctext(x, y, text, font(5.2, True, cond=True, scale=self.s), (255, 255, 255))
        elif kind == 'prizhim':
            w = max(2, int(mm(0.5, self.s)))
            Q = [(x - s * 1.3 + i * s * 0.65, y + (s * 0.6 if i % 2 else -s * 0.6)) for i in range(5)]
            d.line(Q, fill=(255, 255, 255), width=w * 3)
            d.line(Q, fill=HAZ, width=w)
        elif kind == 'water':
            r = s * 0.62
            d.polygon([(x, y - r * 1.9), (x + r * 0.85, y - r * 0.2), (x - r * 0.85, y - r * 0.2)], fill=WATER)
            d.ellipse((x - r, y - r * 0.9, x + r, y + r * 1.0), fill=WATER, outline=(255, 255, 255), width=max(1, int(o * 0.6)))
        elif kind in ('shop', 'drop', 'gate', 'house', 'cave', 'mon'):
            letter = {'shop': 'М', 'drop': 'З', 'gate': 'В', 'house': 'К', 'cave': 'П', 'mon': '★'}[kind]
            col = {'shop': (0, 110, 70), 'drop': (190, 90, 0), 'gate': (90, 90, 90), 'house': (90, 60, 20), 'cave': (60, 60, 60), 'mon': (70, 70, 70)}[kind]
            d.rectangle((x - s * 0.75, y - s * 0.75, x + s * 0.75, y + s * 0.75), fill=col, outline=(255, 255, 255), width=o)
            self._ctext(x, y, letter, font(5.5, True, scale=self.s), (255, 255, 255))
        elif kind == 'letter':
            d.rectangle((x - s * 0.75, y - s * 0.75, x + s * 0.75, y + s * 0.75), fill=color or (70, 70, 70), outline=(255, 255, 255), width=o)
            if text: self._ctext(x, y, text[:2], font(5.5 if len(text) < 2 else 4.6, True, cond=len(text) > 1, scale=self.s), (255, 255, 255))
        elif kind == 'station':
            r = s * 0.9
            d.ellipse((x - r, y - r, x + r, y + r), fill=(0, 140, 90), outline=(255, 255, 255), width=o)
            if text: self._ctext(x, y, text, font(5.0, True, cond=True, scale=self.s), (255, 255, 255))
        elif kind == 'exit':
            d.ellipse((x - s, y - s, x + s, y + s), fill=(255, 255, 255), outline=HAZ, width=int(o * 2))
            self._ctext(x, y, text or 'А', font(6, True, scale=self.s), HAZ)
        elif kind == 'num':
            d.ellipse((x - s, y - s, x + s, y + s), fill=(20, 40, 120), outline=(255, 255, 255), width=o)
            self._ctext(x, y, text, font(6.2 if len(text) < 2 else 5.4, True, cond=True, scale=self.s), (255, 255, 255))
        elif kind == 'town':
            d.ellipse((x - s * 0.6, y - s * 0.6, x + s * 0.6, y + s * 0.6), fill=(255, 255, 255), outline=INK, width=int(o * 1.5))
        elif kind == 'peak':
            d.polygon([(x, y - s * 0.95), (x + s * 0.95, y + s * 0.7), (x - s * 0.95, y + s * 0.7)], fill=color or (120, 60, 20), outline=(255, 255, 255), width=o)
        elif kind == 'km':
            r = s * 0.62
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255), outline=color or INK, width=max(1, int(o * 0.8)))
            if text: self._ctext(x, y, text, font(4.3 if len(text) < 2 else 3.7, True, cond=True, scale=self.s), color or INK)
        elif kind == 'badge':
            f = font(5.6, True, cond=True, scale=self.s); tw = d.textlength(text, font=f)
            hw = max(s * 0.95, tw / 2 + s * 0.35)
            d.rounded_rectangle((x - hw, y - s * 0.8, x + hw, y + s * 0.8), radius=int(s * 0.45), fill=color or (150, 40, 0), outline=(255, 255, 255), width=o)
            self._ctext(x, y, text, f, (255, 255, 255))
        elif kind == 'plan':
            d.polygon([(x, y - s * 1.1), (x + s, y + s * 0.75), (x - s, y + s * 0.75)], fill=(255, 255, 255), outline=(0, 90, 200), width=int(o * 1.6))
        self.occupy(x / self.s - s / self.s, y / self.s - s / self.s, x / self.s + s / self.s, y / self.s + s / self.s)

    def _ctext(self, x, y, t, f, fill, **kw):
        self.d.text((x, y), t, font=f, fill=fill, anchor='mm', **kw)

    def occupy(self, x0, y0, x1, y1):
        self.occupied.append((x0, y0, x1, y1))

    def label(self, lat, lon, text, pt=6.5, color=INK, bold=False, cond=True, italic=False, halo=(255, 255, 255),
              gap_mm=1.6, prefer=None, force=False, leader=True, bg=None):
        """Подпись с автоматическим выбором положения (8 позиций × 3 отступа)."""
        x, y = self.v.px(lat, lon)
        f = font(pt, bold, cond, italic, scale=self.s)
        lines = text.split('\n')
        tb = [self.d.textbbox((0, 0), l, font=f) for l in lines]
        tw = max(b[2] - b[0] for b in tb) / self.s; lh = (f.size * 1.12) / self.s; th = lh * len(lines)
        hw = tw / 2; hh = th / 2
        best = None
        order = prefer or ['E', 'W', 'NE', 'SE', 'NW', 'SW', 'N', 'S']
        for gi, g in enumerate((gap_mm, gap_mm * 2.2, gap_mm * 4.0)):
            gp = mm(g)
            for k, pos in enumerate(order):
                dx = {'E': 1, 'W': -1, 'N': 0, 'S': 0, 'NE': 0.75, 'SE': 0.75, 'NW': -0.75, 'SW': -0.75}[pos]
                dy = {'E': 0, 'W': 0, 'N': -1, 'S': 1, 'NE': -0.75, 'SE': 0.75, 'NW': -0.75, 'SW': 0.75}[pos]
                cx = x + dx * (gp + hw); cy = y + dy * (gp + hh)
                r = (cx - hw - 2, cy - hh - 1, cx + hw + 2, cy + hh + 1)
                cost = gi * 3 + k * 0.3
                if r[0] < 4 or r[1] < 4 or r[2] > self.v.W - 4 or r[3] > self.v.H - 4: cost += 1000
                for o in self.occupied:
                    ix = max(0, min(r[2], o[2]) - max(r[0], o[0])); iy = max(0, min(r[3], o[3]) - max(r[1], o[1]))
                    cost += ix * iy * 0.6
                crop = self.trackmask.crop(tuple(int(v) for v in r))
                cost += sum(crop.histogram()[1:]) * 0.04
                if best is None or cost < best[0]: best = (cost, cx, cy, r, gi)
        cost, cx, cy, r, gi = best
        if cost > 1000 and not force: return False
        if gi > 0 and leader:
            # выноска к точке
            ax, ay = min(max(x, r[0]), r[2]), min(max(y, r[1]), r[3])
            self.d.line([(x * self.s, y * self.s), (ax * self.s, ay * self.s)], fill=color, width=max(1, int(mm(0.18, self.s))))
        if bg:
            self.d.rounded_rectangle((r[0] * self.s, r[1] * self.s, r[2] * self.s, r[3] * self.s), radius=int(mm(0.6, self.s)), fill=bg, outline=color, width=max(1, int(mm(0.2, self.s))))
        sw = int(mm(0.35, self.s)) if halo else 0
        for i, l in enumerate(lines):
            ly = (cy - hh + lh * (i + 0.5)) * self.s
            self.d.text((cx * self.s, ly), l, font=f, fill=color, anchor='mm', stroke_width=sw, stroke_fill=halo)
        self.occupy(*r)
        return True

    def render(self):
        return self.img.resize((self.v.W, self.v.H), Image.LANCZOS)


# ---------------------------------------------------------------- frame & sheet
def rot_text(img, text, f, center, angle, fill=INK):
    b = f.getbbox(text)
    t = Image.new('RGBA', (b[2] + 8, b[3] + 8), (255, 255, 255, 0))
    ImageDraw.Draw(t).text((4, 4), text, font=f, fill=fill)
    t = t.rotate(angle, expand=True, resample=Image.BICUBIC)
    img.paste(t, (int(center[0] - t.width / 2), int(center[1] - t.height / 2)), t)

def nice_step(span_deg, target_lines):
    cands = [1/60, 2/60, 3/60, 5/60, 10/60, 15/60, 20/60, 30/60, 1]
    for c in cands:
        if span_deg / c <= target_lines: return c
    return 1

def fmt_deg(v, step):
    sec = int(round(v * 3600))
    d, rem = divmod(sec, 3600); m, s = divmod(rem, 60)
    if round(step * 3600) % 60 == 0:
        return f'{d}°{m:02d}′'
    return f'{d}°{m:02d}′{s:02d}″'

class Sheet:
    """Лист: заголовок, поле карты в рамке с градусной сеткой, нижний блок."""
    def __init__(self, view, base, overlay_img, title, subtitle, w_cm, h_cm, margin_cm=0.62, top_cm=1.0, lat_step=None, lon_step=None):
        self.v = view
        self.W = int(round(w_cm / 2.54 * DPI)); self.H = int(round(h_cm / 2.54 * DPI))
        self.img = Image.new('RGB', (self.W, self.H), (255, 255, 255))
        self.d = ImageDraw.Draw(self.img)
        self.mx = int(round(margin_cm / 2.54 * DPI))
        self.oy = int(round(top_cm / 2.54 * DPI))
        self.ox = (self.W - view.W) // 2
        field = base.copy().convert('RGBA')
        # сетка
        la0, la1, lo0, lo1 = view.bounds()
        self.lat_step = lat_step or nice_step(la1 - la0, 8)
        self.lon_step = lon_step or nice_step(lo1 - lo0, 6)
        gl = Image.new('RGBA', field.size, (0, 0, 0, 0)); gd = ImageDraw.Draw(gl)
        self.glat = [k * self.lat_step for k in range(int(math.ceil(la0 / self.lat_step)), int(math.floor(la1 / self.lat_step)) + 1)]
        self.glon = [k * self.lon_step for k in range(int(math.ceil(lo0 / self.lon_step)), int(math.floor(lo1 / self.lon_step)) + 1)]
        gw = max(1, int(mm(0.18)))
        for la in self.glat:
            y = view.px(la, view.lon0)[1]; gd.line((0, y, view.W, y), fill=(20, 40, 90, 150), width=gw)
        for lo in self.glon:
            x = view.px(view.lat0, lo)[0]; gd.line((x, 0, x, view.H), fill=(20, 40, 90, 150), width=gw)
        field = Image.alpha_composite(field, gl)
        field = Image.alpha_composite(field, overlay_img)
        self.img.paste(field.convert('RGB'), (self.ox, self.oy))
        self._frame(la0, la1, lo0, lo1)
        # заголовок
        pt = 10.5
        while self.d.textlength(title, font=font(pt, True)) > self.W - mm(4) and pt > 7: pt -= 0.25
        self.d.text((self.W / 2, mm(1.2)), title, font=font(pt, True), fill=INK, anchor='ma')
        if subtitle:
            sp = 7.5
            while self.d.textlength(subtitle, font=font(sp, cond=True)) > self.W - mm(6) and sp > 5.6: sp -= 0.2
            self.d.text((self.W / 2, mm(6.4)), subtitle, font=font(sp, cond=True), fill=(60, 60, 60), anchor='ma')
        self.bottom = self.oy + view.H + int(mm(4.2))

    def _frame(self, la0, la1, lo0, lo1):
        v = self.v; ox, oy = self.ox, self.oy; d = self.d
        d.rectangle((ox - 1, oy - 1, ox + v.W, oy + v.H), outline=INK, width=max(1, int(mm(0.2))))
        # минутная «линейка» в рамке
        b = int(mm(1.1)); o = int(mm(0.6))
        x0, y0, x1, y1 = ox - o - b, oy - o - b, ox + v.W + o + b, oy + v.H + o + b
        d.rectangle((x0, y0, x1, y1), outline=INK, width=max(1, int(mm(0.35))))
        d.rectangle((x0 + b, y0 + b, x1 - b, y1 - b), outline=INK, width=max(1, int(mm(0.15))))
        minute = 1 / 60
        k0 = int(math.floor(la0 / minute)); k1 = int(math.ceil(la1 / minute))
        for k in range(k0, k1):
            if k % 2: continue
            ya = v.px(k * minute, v.lon0)[1] + oy; yb = v.px((k + 1) * minute, v.lon0)[1] + oy
            ya, yb = max(min(ya, yb), y0 + b), min(max(ya, yb), y1 - b)
            if yb > ya:
                d.rectangle((x0, ya, x0 + b, yb), fill=INK); d.rectangle((x1 - b, ya, x1, yb), fill=INK)
        k0 = int(math.floor(lo0 / minute)); k1 = int(math.ceil(lo1 / minute))
        for k in range(k0, k1):
            if k % 2: continue
            xa = v.px(v.lat0, k * minute)[0] + ox; xb = v.px(v.lat0, (k + 1) * minute)[0] + ox
            xa, xb = max(xa, x0 + b), min(xb, x1 - b)
            if xb > xa:
                d.rectangle((xa, y0, xb, y0 + b), fill=INK); d.rectangle((xa, y1 - b, xb, y1), fill=INK)
        f = font(6.3, cond=True)
        for la in self.glat:
            y = v.px(la, v.lon0)[1] + oy
            t = fmt_deg(la, self.lat_step)
            rot_text(self.img, t, f, (x0 - mm(1.7), y), 90)
            rot_text(self.img, t, f, (x1 + mm(1.7), y), -90)
        for lo in self.glon:
            x = v.px(v.lat0, lo)[0] + ox
            t = fmt_deg(lo, self.lon_step)
            d.text((x, y0 - mm(0.5)), t, font=f, fill=INK, anchor='md')
            d.text((x, y1 + mm(0.5)), t, font=f, fill=INK, anchor='ma')
        self.frame_box = (x0, y0, x1, y1)

    def scalebar(self, x, y, km_total, ticks_km, label):
        v = self.v
        px_per_km = 1000 / (v.scale * 0.0254 / DPI)
        d = self.d; h = mm(1.4)
        L = km_total * px_per_km
        n = int(round(km_total / ticks_km))
        for i in range(n):
            xa = x + i * ticks_km * px_per_km; xb = xa + ticks_km * px_per_km
            d.rectangle((xa, y, xb, y + h), fill=INK if i % 2 == 0 else (255, 255, 255), outline=INK, width=max(1, int(mm(0.15))))
        f = font(6.3, cond=True)
        for i in range(n + 1):
            val = i * ticks_km
            s = f'{val:g}'.replace('.', ',')
            d.text((x + i * ticks_km * px_per_km, y - mm(0.4)), s, font=f, fill=INK, anchor='md')
        d.text((x + L + mm(1.2), y + h / 2), 'км', font=f, fill=INK, anchor='lm')
        d.text((x, y + h + mm(1.2)), label, font=font(7, True, cond=True), fill=INK, anchor='la')
        return L

    def north(self, x, y):
        d = self.d; s = mm(3.2)
        d.polygon([(x, y - s), (x + s * 0.45, y + s * 0.55), (x, y + s * 0.25)], fill=INK)
        d.polygon([(x, y - s), (x - s * 0.45, y + s * 0.55), (x, y + s * 0.25)], fill=(255, 255, 255), outline=INK)
        d.text((x, y - s - mm(0.6)), 'С', font=font(7.5, True), fill=INK, anchor='md')

    def legend(self, x, y, items, col_w_mm, rows, pt=6.3):
        """items: list of (draw_fn(draw,x,y), text)."""
        d = self.d; rh = mm(3.55)
        for i, (fn, text) in enumerate(items):
            c, r = divmod(i, rows)
            xx = x + c * mm(col_w_mm); yy = y + r * rh
            fn(d, xx + mm(4), yy + rh / 2)
            d.text((xx + mm(9.0), yy + rh / 2), text, font=font(pt, cond=True), fill=INK, anchor='lm')

    def save(self, path):
        # обрезка пустого низа листа
        bg = Image.new('RGB', self.img.size, (255, 255, 255))
        from PIL import ImageChops
        bb = ImageChops.difference(self.img, bg).getbbox()
        img = self.img.crop((0, 0, self.W, min(self.H, bb[3] + int(mm(3))))) if bb else self.img
        img.save(path, dpi=(DPI, DPI), optimize=True)
        self.size_cm = (img.width / DPI * 2.54, img.height / DPI * 2.54)


# legend glyphs (рисуются прямо на листе, 1x)
def g_track(color, casing=True, arrow=True, w=0.8):
    def f(d, x, y):
        if casing: d.line((x - mm(3.5), y, x + mm(3.5), y), fill=(255, 255, 255), width=int(mm(w + 0.7)))
        d.line((x - mm(3.5), y, x + mm(3.5), y), fill=color, width=max(1, int(mm(w))))
        if arrow:
            s = mm(1.0); d.polygon([(x + s, y), (x - s * 0.8, y - s * 0.75), (x - s * 0.35, y), (x - s * 0.8, y + s * 0.75)], fill=color, outline=(255, 255, 255))
    return f

ALT_COL, EXIT_RED, EXIT_INK = (31, 111, 209), (226, 0, 26), (17, 24, 39)      # запасной вариант, аварийный выход

def g_exit():
    """Аварийный выход: чёрно-красный штрих."""
    def f(d, x, y):
        d.line((x - mm(3.5), y, x + mm(3.5), y), fill=(255, 255, 255), width=int(mm(1.0)))
        d.line((x - mm(3.5), y, x + mm(3.5), y), fill=EXIT_RED, width=int(mm(0.5)))
        for k in range(3):
            xa = x - mm(3.5) + k * mm(2.6); d.line((xa, y, xa + mm(1.3), y), fill=EXIT_INK, width=int(mm(0.5)))
    return f

def g_frame(color):
    """Рамка листа рабочей карты с номером в углу."""
    def f(d, x, y):
        d.rectangle((x - mm(3.5), y - mm(1.5), x + mm(3.5), y + mm(1.5)), outline=color, width=max(1, int(mm(0.3))))
        d.rectangle((x - mm(3.5), y - mm(1.5), x - mm(1.9), y + mm(0.1)), fill=color)
    return f

def g_dash(color, w=0.6):
    def f(d, x, y):
        for k in range(3):
            xa = x - mm(3.5) + k * mm(2.6); d.line((xa, y, xa + mm(1.6), y), fill=color, width=int(mm(w)))
    return f

def g_band(color, hatch=None):
    def f(d, x, y):
        d.rectangle((x - mm(3.5), y - mm(1.15), x + mm(3.5), y + mm(1.15)), fill=color[:3] if len(color) == 3 else blend_white(color))
        if hatch:
            for k in range(-2, 3):
                xa = x + k * mm(1.1); d.line((xa - mm(1.1), y + mm(1.15), xa + mm(1.1), y - mm(1.15)), fill=hatch[:3], width=max(1, int(mm(0.25))))
        d.line((x - mm(3.5), y, x + mm(3.5), y), fill=DAY_COLORS[0], width=int(mm(0.6)))
    return f

def blend_white(c):
    a = c[3] / 255
    return tuple(int(c[i] * a + 255 * (1 - a)) for i in range(3))

def g_sym(kind, text=None, size=3.2, color=None):
    def f(d, x, y):
        ov = _GlyphOverlay(d)
        ov.sym(kind, x, y, size, text, color)
    return f

class _GlyphOverlay:
    """Рисование условных знаков в легенде тем же кодом, что и на карте (1x)."""
    def __init__(self, d): self.d = d
    def sym(self, kind, x, y, size_mm, text, color=None):
        class FakeV:
            def px(s, la, lo, sc=1): return (lo, la)
            W = H = 10 ** 6
        o = Overlay.__new__(Overlay)
        o.v = FakeV(); o.s = 1; o.d = self.d; o.occupied = []
        o.sym(kind, y, x, size_mm, color=color, text=text)
