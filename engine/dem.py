"""ЦМР: тайлы Terrarium (AWS Open Data, SRTM/Copernicus ~30 м). Выборка высот и отмывка рельефа."""
import math, os, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image
from . import mapkit

URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
_cache = {}
_lock = threading.Lock()
MAX_MEM_TILES = 900

def _tile(z, x, y):
    k = (z, x, y)
    if k in _cache: return _cache[k]
    path = os.path.join(mapkit.CACHE, 'tiles', 'terrarium', str(z), str(x), f'{y}.png')
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        req = urllib.request.Request(URL.format(z=z, x=x, y=y), headers={'User-Agent': mapkit.UA})
        data = None
        for _ in range(4):
            try:
                with urllib.request.urlopen(req, timeout=40) as r: data = r.read(); break
            except Exception: data = None
        if data is None: raise RuntimeError(f'не удалось скачать высоты (тайл {k})')
        tmp = path + f'.{threading.get_ident()}.tmp'
        with open(tmp, 'wb') as f: f.write(data)
        os.replace(tmp, path)
    a = np.asarray(Image.open(path).convert('RGB'), dtype=np.float32)
    e = a[:, :, 0] * 256 + a[:, :, 1] + a[:, :, 2] / 256 - 32768
    with _lock:
        if len(_cache) > MAX_MEM_TILES: _cache.clear()
        _cache[k] = e
    return e

def _txy(lat, lon, z):
    n = 2 ** z
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n

def grid(la0, la1, lo0, lo1, z=13):
    """Мозаика высот на охват; возвращает (массив, x0, y0) — x0,y0 в пикселях мира на зуме z."""
    xa, ya = _txy(la1, lo0, z); xb, yb = _txy(la0, lo1, z)
    tx0, ty0, tx1, ty1 = int(xa), int(ya), int(xb), int(yb)
    jobs = [(x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]
    with ThreadPoolExecutor(8) as ex: tiles = dict(zip(jobs, ex.map(lambda j: _tile(z, *j), jobs)))
    G = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), dtype=np.float32)
    for (x, y), t in tiles.items(): G[(y - ty0) * 256:(y - ty0 + 1) * 256, (x - tx0) * 256:(x - tx0 + 1) * 256] = t
    return G, tx0 * 256, ty0 * 256

class Sampler:
    """Высота в точке (билинейно). Тайлы подгружаются только вдоль нитки — память не зависит от размера района."""
    def __init__(self, pts=None, z=13):
        self.z = z
        if pts: self.prefetch(pts)

    def _need(self, lat, lon):
        x, y = _txy(lat, lon, self.z); x = x * 256 - 0.5; y = y * 256 - 0.5
        j, i = int(math.floor(x)), int(math.floor(y))
        return {(j // 256, i // 256), ((j + 1) // 256, i // 256), (j // 256, (i + 1) // 256), ((j + 1) // 256, (i + 1) // 256)}

    def prefetch(self, pts):
        need = set()
        for p in pts: need |= self._need(p[0], p[1])
        need = [k for k in need if (self.z, *k) not in _cache]
        if need:
            with ThreadPoolExecutor(8) as ex: list(ex.map(lambda k: _tile(self.z, *k), need))

    def _px(self, j, i):
        return _tile(self.z, j // 256, i // 256)[i % 256, j % 256]

    def __call__(self, lat, lon):
        x, y = _txy(lat, lon, self.z); x = x * 256 - 0.5; y = y * 256 - 0.5
        i, j = int(math.floor(y)), int(math.floor(x)); fy, fx = y - i, x - j
        return float(self._px(j, i) * (1 - fx) * (1 - fy) + self._px(j + 1, i) * fx * (1 - fy)
                     + self._px(j, i + 1) * (1 - fx) * fy + self._px(j + 1, i + 1) * fx * fy)

def hillshade(view, z=12, az=315, alt=45, zf=1.6):
    """Отмывка рельефа на поле карты view (PIL 'L', 255 — свет)."""
    la0, la1, lo0, lo1 = view.bounds()
    G, x0, y0 = grid(la0 - 0.01, la1 + 0.01, lo0 - 0.01, lo1 + 0.01, z)
    res = 2 * math.pi * 6378137.0 / (256 * 2 ** z) * math.cos(math.radians(view.lat0))
    gy, gx = np.gradient(G, res)
    slope = np.arctan(zf * np.hypot(gx, gy)); aspect = np.arctan2(gy, -gx)
    a = math.radians(360 - az + 90); h = math.radians(alt)
    hs = np.sin(h) * np.cos(slope) + np.cos(h) * np.sin(slope) * np.cos(a - aspect)
    img = Image.fromarray(np.clip(hs * 255, 0, 255).astype(np.uint8))
    n = 2 ** z
    def wx(lon): return (lon + 180) / 360 * n * 256 - x0
    def wy(lat): return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n * 256 - y0
    box = (wx(lo0), wy(la1), wx(lo1), wy(la0))
    return img.resize((view.W, view.H), Image.BICUBIC, box=box)
