"""Чтение треков и точек: GPX, KML/KMZ, GeoJSON, ZIP, ссылки и данные nakarte.me (nktk v1–4, nktl).

Все читалки возвращают список «слоёв»:
    dict(name='5 августа', segs=[[(lat, lon, ele|None, time|None), ...], ...], wpts=[dict(name, lat, lon, ele, sym, desc)])
"""
import base64, io, json, math, re, urllib.parse, urllib.request, zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

UA = 'nitka-hiking-report-maps/1.0'
MAX_UNZIP = 150 * 1024 * 1024
ARC = ((1 << 24) - 1) / 360


class ImportError_(Exception):
    pass


# ---------------------------------------------------------------- nakarte: nktk
def _b64(s):
    s = s.strip().replace(' ', '+')
    s += '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)

def _varint(b, i):
    r = 0; sh = 0
    while True:
        c = b[i]; i += 1; r |= (c & 0x7f) << sh; sh += 7
        if c < 0x80: return r, i

def _pb(b):
    i = 0; out = []
    while i < len(b):
        k, i = _varint(b, i); f = k >> 3; t = k & 7
        if t == 0: v, i = _varint(b, i)
        elif t == 2:
            l, i = _varint(b, i); v = b[i:i + l]; i += l
        elif t == 5: v = b[i:i + 4]; i += 4
        elif t == 1: v = b[i:i + 8]; i += 8
        else: raise ImportError_('повреждённые данные трека nakarte')
        out.append((f, t, v))
    return out

def _zz(n): return (n >> 1) ^ -(n & 1)

def _packed(v):
    out = []; i = 0
    while i < len(v):
        n, i = _varint(v, i); out.append(_zz(n))
    return out

def _nktk_v4(b):
    name = ''; segs = []; wpts = []; shown = True
    for f, t, v in _pb(b):
        if f == 1:
            for f2, t2, v2 in _pb(v):
                if f2 == 2: shown = bool(v2)
        if f == 2:
            for f2, t2, v2 in _pb(v):
                if f2 == 1: name = v2.decode('utf-8', 'replace')
                elif f2 == 2:
                    lats = []; lons = []
                    for f3, t3, v3 in _pb(v2):
                        if f3 == 1: lats = _packed(v3) if t3 == 2 else lats + [_zz(v3)]
                        if f3 == 2: lons = _packed(v3) if t3 == 2 else lons + [_zz(v3)]
                    la = lo = 0; pts = []
                    for a, o in zip(lats, lons):
                        la += a; lo += o; pts.append((round(la / ARC, 6), round(lo / ARC, 6), None, None))
                    if pts: segs.append(pts)
                elif f2 == 3:
                    mla = mlo = 0; raw = []
                    for f3, t3, v3 in _pb(v2):
                        if f3 == 1: mla = _zz(v3)
                        elif f3 == 2: mlo = _zz(v3)
                        elif f3 == 3:
                            w = {}
                            for f4, t4, v4 in _pb(v3): w[f4] = v4
                            raw.append(w)
                    for w in raw:
                        wpts.append(dict(name=(w.get(3, b'') or b'').decode('utf-8', 'replace'),
                                         lat=round((_zz(w.get(1, 0)) + mla) / ARC, 6), lon=round((_zz(w.get(2, 0)) + mlo) / ARC, 6)))
    return dict(name=name, segs=segs, wpts=wpts)

class _Packed:
    """Старый упакованный формат nakarte (версии 0–3)."""
    def __init__(self, b): self.b = b; self.i = 0
    def num(self):
        b = self.b; i = self.i
        if i >= len(b): raise EOFError
        x = b[i]
        if x < 128: self.i += 1; return x - 64
        n = x & 0x7f; x = b[i + 1]
        if x < 128: self.i += 2; return (n | x << 7) - 8192
        n |= (x & 0x7f) << 7; x = b[i + 2]
        if x < 128: self.i += 3; return (n | x << 14) - 1048576
        n |= (x & 0x7f) << 14; x = b[i + 3]
        self.i += 4; return (n | x << 21) - 268435456
    def s(self, n):
        v = self.b[self.i:self.i + n]; self.i += n; return v.decode('utf-8', 'replace')

def _nktk_old(b, version):
    r = _Packed(b); name = ''; segs = []; wpts = []
    try:
        name = r.s(r.num())
        for _ in range(r.num()):
            seg = []; x = y = 0
            for _ in range(r.num()):
                x += r.num(); y += r.num(); seg.append((round(y / ARC, 6), round(x / ARC, 6), None, None))
            if seg: segs.append(seg)
        r.num(); r.num()
        if version >= 3: r.num()
        if version >= 2:
            n = r.num()
            if n: mx = r.num(); my = r.num()
            for _ in range(n):
                wn = r.s(r.num()); r.num()
                x = r.num() + mx; y = r.num() + my
                wpts.append(dict(name=wn, lat=round(y / ARC, 6), lon=round(x / ARC, 6)))
    except (EOFError, IndexError):
        pass
    return dict(name=name or 'Трек из ссылки', segs=segs, wpts=wpts)

def parse_nktk(text):
    """Последовательность фрагментов nktk, разделённых '/'."""
    out = []
    for frag in text.strip().split('/'):
        if not frag.strip(): continue
        try: b = _b64(frag)
        except Exception: raise ImportError_('не удалось прочитать трек из ссылки nakarte')
        if not b: continue
        ver = b[0] - 64
        if ver == 4: out.append(_nktk_v4(b[1:]))
        elif ver in (1, 2, 3): out.append(_nktk_old(b[1:], ver))
        else: raise ImportError_('неизвестная версия трека nakarte')
    return out

def _fetch(url, limit=40 * 1024 * 1024):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read(limit + 1)
    if len(data) > limit: raise ImportError_('файл по ссылке слишком большой')
    return data

def parse_nakarte_link(url):
    """Ссылка nakarte.me: nktk (трек в ссылке), nktl (короткая ссылка), nktp (точка)."""
    url = url.strip()
    frag = url.split('#', 1)[1] if '#' in url else url.split('?', 1)[-1]
    params = {}
    for part in frag.split('&'):
        if '=' in part:
            k, v = part.split('=', 1); params.setdefault(k, []).append(v)
    layers = []
    for v in params.get('nktk', []):
        layers += parse_nktk(v)
    for v in params.get('nktl', []):
        for tid in v.split('/'):
            if not re.fullmatch(r'[A-Za-z0-9_\-]{6,64}', tid): raise ImportError_('странный идентификатор трека в ссылке')
            try: data = _fetch(f'https://tracks.nakarte.me/track/{tid}')
            except Exception: raise ImportError_('nakarte.me не отдал трек по короткой ссылке — попробуйте позже или скачайте GPX')
            layers += parse_nktk(data.decode('latin-1'))
    for v in params.get('nktp', []):
        a = [urllib.parse.unquote(x) for x in v.split('/')]
        try: layers.append(dict(name='Точка из ссылки', segs=[], wpts=[dict(name=(a[2] if len(a) > 2 else 'Точка'), lat=float(a[0]), lon=float(a[1]))]))
        except Exception: pass
    if not layers:
        if 'nktu' in params or 'nktj' in params:
            raise ImportError_('в ссылке трек лежит на стороннем сайте — скачайте его файлом (GPX/KML) и загрузите сюда')
        raise ImportError_('в ссылке нет трека: нужна ссылка с nktl= или nktk= (в nakarte: «Треки» → значок ссылки у трека или «Скопировать ссылку на все треки»)')
    return layers


# ---------------------------------------------------------------- GPX / KML / GeoJSON
def _tag(e): return e.tag.rsplit('}', 1)[-1]
def _child(e, name):
    for c in e:
        if _tag(c) == name: return c
def _text(e, name):
    c = _child(e, name)
    return (c.text or '').strip() if c is not None else ''
def _flt(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception: return None
def _time(v):
    if not v: return None
    try:
        return datetime.fromisoformat(v.strip().replace('Z', '+00:00')).astimezone(timezone.utc).timestamp()
    except Exception: return None

def parse_gpx(data, fname='трек.gpx'):
    try: root = ET.fromstring(data)
    except ET.ParseError: raise ImportError_(f'«{fname}» — не похоже на GPX (файл повреждён?)')
    wpts = []; layers = []
    for e in root:
        t = _tag(e)
        if t == 'wpt':
            la, lo = _flt(e.get('lat')), _flt(e.get('lon'))
            if la is None or lo is None: continue
            wpts.append(dict(name=_text(e, 'name'), lat=la, lon=lo, ele=_flt(_text(e, 'ele')), sym=_text(e, 'sym') or _text(e, 'type'),
                             desc=_text(e, 'desc') or _text(e, 'cmt'), time=_time(_text(e, 'time'))))
        elif t in ('trk', 'rte'):
            name = _text(e, 'name'); segs = []
            groups = [c for c in e if _tag(c) == 'trkseg'] if t == 'trk' else [e]
            for g in groups:
                seg = []
                for p in g:
                    if _tag(p) not in ('trkpt', 'rtept'): continue
                    la, lo = _flt(p.get('lat')), _flt(p.get('lon'))
                    if la is None or lo is None: continue
                    seg.append((la, lo, _flt(_text(p, 'ele')), _time(_text(p, 'time'))))
                if len(seg) > 1: segs.append(seg)
            if segs: layers.append(dict(name=name, segs=segs, wpts=[]))
    base = re.sub(r'\.gpx$', '', fname, flags=re.I)
    for L in layers:
        if not L['name']: L['name'] = base
    if len(layers) == 1 and not _has_day_hint(layers[0]['name']) and _has_day_hint(base): layers[0]['name'] = base
    if wpts:
        if layers and len(layers) == 1: layers[0]['wpts'] = wpts
        else: layers.append(dict(name=base, segs=[], wpts=wpts))
    return layers

def _has_day_hint(name):
    return bool(re.search(r'\d', name or ''))

def _kml_coords(text):
    out = []
    for tok in (text or '').split():
        a = tok.split(',')
        if len(a) >= 2:
            lo, la = _flt(a[0]), _flt(a[1])
            if la is not None and lo is not None: out.append((la, lo, _flt(a[2]) if len(a) > 2 else None, None))
    return out

def parse_kml(data, fname='трек.kml'):
    try: root = ET.fromstring(data)
    except ET.ParseError: raise ImportError_(f'«{fname}» — не похоже на KML')
    layers = {}
    base = re.sub(r'\.km[lz]$', '', fname, flags=re.I)
    def layer(name):
        return layers.setdefault(name, dict(name=name, segs=[], wpts=[]))
    def walk(e, folder):
        for c in e:
            t = _tag(c)
            if t in ('Folder', 'Document'):
                walk(c, _text(c, 'name') or folder)
            elif t == 'Placemark':
                name = _text(c, 'name'); lines = []; pts = []
                for g in c.iter():
                    gt = _tag(g)
                    if gt == 'LineString' or gt == 'LinearRing':
                        cc = _kml_coords(_text(g, 'coordinates'))
                        if len(cc) > 1: lines.append(cc)
                    elif gt == 'Point':
                        cc = _kml_coords(_text(g, 'coordinates'))
                        if cc: pts.append(cc[0])
                    elif gt == 'Track':
                        whens = [(_time(x.text)) for x in g if _tag(x) == 'when']
                        seg = []
                        for k, x in enumerate([x for x in g if _tag(x) == 'coord']):
                            a = (x.text or '').split()
                            if len(a) >= 2:
                                lo, la = _flt(a[0]), _flt(a[1])
                                if la is not None and lo is not None:
                                    seg.append((la, lo, _flt(a[2]) if len(a) > 2 else None, whens[k] if k < len(whens) else None))
                        if len(seg) > 1: lines.append(seg)
                if lines: layer(name or folder or base)['segs'] += lines
                for p in pts: layer(folder or base)['wpts'].append(dict(name=name, lat=p[0], lon=p[1], ele=p[2], desc=_text(c, 'description')))
    walk(root, '')
    return [L for L in layers.values() if L['segs'] or L['wpts']]

def parse_geojson(data, fname='трек.geojson'):
    try: g = json.loads(data)
    except Exception: raise ImportError_(f'«{fname}» — не похоже на GeoJSON')
    base = re.sub(r'\.(geo)?json$', '', fname, flags=re.I)
    feats = g.get('features') if g.get('type') == 'FeatureCollection' else [g]
    layers = []; wl = dict(name=base, segs=[], wpts=[])
    def P(c): return (c[1], c[0], c[2] if len(c) > 2 else None, None)
    for f in feats or []:
        geom = f.get('geometry') or (f if f.get('coordinates') else None)
        if not geom: continue
        name = str((f.get('properties') or {}).get('name') or '')
        t = geom.get('type'); c = geom.get('coordinates')
        try:
            if t == 'LineString': layers.append(dict(name=name or base, segs=[[P(x) for x in c]], wpts=[]))
            elif t == 'MultiLineString': layers.append(dict(name=name or base, segs=[[P(x) for x in s] for s in c], wpts=[]))
            elif t == 'Point': wl['wpts'].append(dict(name=name, lat=c[1], lon=c[0], ele=c[2] if len(c) > 2 else None))
        except Exception: continue
    if wl['wpts']: layers.append(wl)
    return layers

def parse_file(name, data, _depth=0):
    """Определяет формат по содержимому и расширению."""
    low = name.lower()
    head = data[:600].lstrip(b'\xef\xbb\xbf \r\n\t')
    if data[:2] == b'PK':
        if _depth > 1: return []
        out = []
        try: z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile: raise ImportError_(f'«{name}» — архив повреждён')
        infos = [i for i in z.infolist() if not i.is_dir()]
        if sum(i.file_size for i in infos) > MAX_UNZIP or len(infos) > 400: raise ImportError_(f'«{name}» — слишком большой архив')
        infos.sort(key=lambda i: _natkey(i.filename))
        for i in infos:
            n = i.filename
            try: n = n.encode('cp437').decode('cp866') if not i.flag_bits & 0x800 else n
            except Exception: pass
            if not re.search(r'\.(gpx|kml|kmz|geojson|json|nktk|zip)$', n, re.I): continue
            try: out += parse_file(n.rsplit('/', 1)[-1], z.read(i), _depth + 1)
            except ImportError_: continue
        if not out: raise ImportError_(f'в архиве «{name}» не нашлось треков (GPX, KML, GeoJSON)')
        return out
    if head.startswith(b'<'):
        if b'<gpx' in data[:4000].lower(): return parse_gpx(data, name)
        if b'<kml' in data[:4000].lower() or low.endswith('.kml'): return parse_kml(data, name)
        raise ImportError_(f'«{name}» — XML, но не GPX и не KML')
    if head.startswith(b'{'): return parse_geojson(data, name)
    if low.endswith('.nktk') or re.fullmatch(rb'[A-Za-z0-9_\-=/\s]+', data[:2000] or b'!'):
        try: return parse_nktk(data.decode('latin-1'))
        except ImportError_:
            if low.endswith('.nktk'): raise
    raise ImportError_(f'«{name}» — формат не поддерживается (нужен GPX, KML/KMZ, GeoJSON, ZIP или ссылка nakarte)')

def _natkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


# ---------------------------------------------------------------- упрощение плотных GPS-треков
def simplify(pts, tol_m=3.0, keep_idx=()):
    """Дуглас–Пекер; точки (lat, lon, ele, time). keep_idx — точки, которые остаются всегда (начало и конец остановок)."""
    n = len(pts)
    if n < 300: return list(pts)
    k = math.cos(math.radians(pts[0][0])); mx = 111320 * k; my = 110540
    X = [(p[1] * mx, p[0] * my) for p in pts]
    keep = [False] * n; keep[0] = keep[-1] = True
    for i in keep_idx:
        if 0 <= i < n: keep[i] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2: continue
        ax, ay = X[a]; bx, by = X[b]; dx, dy = bx - ax, by - ay; L = math.hypot(dx, dy)
        best = -1.0; bi = -1
        for i in range(a + 1, b):
            px, py = X[i]
            d = abs(dx * (ay - py) - dy * (ax - px)) / L if L > 0 else math.hypot(px - ax, py - ay)
            if d > best: best = d; bi = i
        if best > tol_m:
            keep[bi] = True; stack.append((a, bi)); stack.append((bi, b))
    return [p for p, f in zip(pts, keep) if f]
