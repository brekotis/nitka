"""«Нитка» — веб-сервис карт и профилей для отчётов о походах. Только стандартная библиотека.

python3 -m server.app            (NITKA_PORT=8080, NITKA_DATA=./data, NITKA_CODE=<кодовое слово клуба, необязательно>)
"""
import gzip, hashlib, hmac, io, json, mimetypes, os, queue, re, secrets, shutil, sqlite3, subprocess, sys, threading, time, urllib.parse, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
DATA = os.path.abspath(os.environ.get('NITKA_DATA') or os.path.join(ROOT, 'data'))
os.makedirs(DATA, exist_ok=True)
os.environ.setdefault('NITKA_CACHE', os.path.join(DATA, 'cache'))
sys.path.insert(0, ROOT)
from engine import classify, importers, mapkit, model, suggest          # noqa: E402
from engine import sheets as sheets_mod          # noqa: E402

WEB = os.path.join(ROOT, 'web')
RENDERS = os.path.join(DATA, 'renders')
PORT = int(os.environ.get('NITKA_PORT') or 8080)
HOST = os.environ.get('NITKA_HOST') or '127.0.0.1'
CODE = (os.environ.get('NITKA_CODE') or '').strip()
KEEP_DAYS = float(os.environ.get('NITKA_KEEP_DAYS') or 7)
ADMINS = {x.strip().lower() for x in (os.environ.get('NITKA_ADMINS') or 'name:brekotis').split(',') if x.strip()}      # кому видна страница «Кто пользуется»
ADMIN_TRIES = {}
MAX_BODY = 45 * 1024 * 1024
TILE_LAYERS = {'otm': 17, 'gsh100': 15, 'gsh050': 15, 'esri': 17, 'topo500': 14, 'topo1000': 13}      # gsh100 — сводный слой «Генштаб» (см. topo_tile)
TILE_CAP = 6 * 1024 ** 3
TILE_RATE = {}
BLANK_PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360606060000000050001a5f645400000000049454e44ae426082')
SCALES = (10000, 25000, 50000, 100000, 200000, 300000, 500000, 1000000, 2000000, 5000000)

def _secret():
    p = os.path.join(DATA, 'secret.key')
    if not os.path.exists(p):
        with open(p, 'w') as f: f.write(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return open(p).read().strip().encode()
SECRET = _secret()

# ---------------------------------------------------------------- база
_local = threading.local()
def db():
    c = getattr(_local, 'c', None)
    if c is None:
        c = _local.c = sqlite3.connect(os.path.join(DATA, 'nitka.db'), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
    return c

def init_db():
    c = db()
    c.executescript('''
      CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, key TEXT UNIQUE, name TEXT, created REAL, seen REAL);
      CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, user_id INTEGER, title TEXT, json TEXT, card TEXT, created REAL, updated REAL);
      CREATE INDEX IF NOT EXISTS projects_user ON projects(user_id, updated);
      CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    ''')
    if 'origin' not in [r[1] for r in c.execute('PRAGMA table_info(users)')]:
        c.execute('ALTER TABLE users ADD COLUMN origin TEXT')        # «откуда вы» — турклуб, город и т. п.
    c.commit()

def user_key(name):
    name = re.sub(r'\s+', ' ', name or '').strip()
    digits = re.sub(r'\D', '', name)
    if len(digits) >= 10 and len(digits) >= len(re.sub(r'[\s()+\-]', '', name)) - 1:
        if len(digits) == 11 and digits[0] == '8': digits = '7' + digits[1:]
        if len(digits) == 10: digits = '7' + digits
        return 'tel:' + digits, '+' + digits
    return 'name:' + name.lower().lstrip('@'), name.lstrip('@')

def sign(uid):
    return f'{uid}.' + hmac.new(SECRET, str(uid).encode(), hashlib.sha256).hexdigest()[:32]

def adm_token(uid):
    return hmac.new(SECRET, f'adm:{uid}'.encode(), hashlib.sha256).hexdigest()[:40]

def code_hash(code, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', code.encode(), bytes.fromhex(salt), 120000).hex()

def setting(key, value=None):
    c = db()
    if value is None:
        r = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return r['value'] if r else None
    c.execute('INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value)); c.commit()

def unsign(tok):
    try:
        uid, sig = tok.split('.', 1)
        if hmac.compare_digest(sign(int(uid)), tok): return int(uid)
    except Exception: pass
    return None

# ---------------------------------------------------------------- проекты
def card_of(proj):
    """Короткая карточка похода для списка: длина, дни, даты, силуэт нитки."""
    meta = proj.get('meta', {})
    pcs = [p for p in proj.get('pieces', []) if p.get('role') in ('main', 'radial') and p.get('pts')]
    lines = [p['pts'] for p in sorted(pcs, key=lambda p: (p.get('day') or 999, p.get('order', 0)))]
    km = sum(p.get('km') or 0 for p in pcs if p.get('role') == 'main' or meta.get('radial_one_way', True)) * float(meta.get('kcoef') or 1)
    days = sorted({p['day'] for p in pcs if p.get('day')})
    thumb = []
    allp = [q for l in lines for q in l]
    if allp:
        la0, la1 = min(q[0] for q in allp), max(q[0] for q in allp); lo0, lo1 = min(q[1] for q in allp), max(q[1] for q in allp)
        import math
        kx = math.cos(math.radians((la0 + la1) / 2)); w = max((lo1 - lo0) * kx, 1e-6); h = max(la1 - la0, 1e-6); s = min(200 / w, 100 / h)
        step = max(1, len(allp) // 220)
        for l in lines:
            pts = l[::step] + ([l[-1]] if (len(l) - 1) % step else [])
            thumb.append([[round(((q[1] - lo0) * kx - w / 2) * s + 100, 1), round((h / 2 - (q[0] - la0)) * s + 50, 1)] for q in pts])
    geo = []; bbox = None
    if allp:
        gstep = max(1, len(allp) // 160)
        geo = [[[round(q[0], 5), round(q[1], 5)] for q in (l[::gstep] + ([l[-1]] if (len(l) - 1) % gstep else []))] for l in lines]      # нитка в настоящих координатах — для карты «где ты ходил»
        bbox = [round(la0, 5), round(lo0, 5), round(la1, 5), round(lo1, 5)]
    c = dict(geo=geo, bbox=bbox, area=meta.get('area') or '', kind=meta.get('kind') or '', start=meta.get('start_date'), km=round(km, 1), ndays=len(days),
             last=max(days) if days else 0, pieces=len(pcs), points=sum(1 for p in proj.get('points', []) if p.get('type') != 'skip'), thumb=thumb)
    return c

def load_project(pid, uid):
    r = db().execute('SELECT * FROM projects WHERE id=? AND user_id=?', (pid, uid)).fetchone()
    return (json.loads(r['json']), r) if r else (None, None)

def save_project(pid, uid, proj, create=False):
    proj['meta']['title'] = (proj['meta'].get('title') or '').strip()[:120] or 'Без названия'
    now = time.time(); js = json.dumps(proj, ensure_ascii=False, separators=(',', ':')); card = json.dumps(card_of(proj), ensure_ascii=False)
    c = db()
    if create: c.execute('INSERT INTO projects(id,user_id,title,json,card,created,updated) VALUES(?,?,?,?,?,?,?)', (pid, uid, proj['meta']['title'], js, card, now, now))
    else: c.execute('UPDATE projects SET title=?, json=?, card=?, updated=? WHERE id=? AND user_id=?', (proj['meta']['title'], js, card, now, pid, uid))
    c.commit()

def _local_date(ts, tz):
    return time.strftime('%Y-%m-%d', time.gmtime(ts + tz * 3600))

def for_client(proj):
    """Проект для браузера: у кусков — что «Нитка» видит в них как в радиалке (форма и км). В базу это не пишется."""
    out = dict(proj); pcs = []
    tz = classify.trip_tz(proj, next((p['pts'][0][1] for p in proj.get('pieces', []) if p.get('pts')), None))
    for p in proj.get('pieces', []):
        ts = classify.piece_times(p)
        if 'tm' in p or 't0' in p: p = {k: v for k, v in p.items() if k not in ('tm', 't0')}
        if ts:
            ti = classify.time_info(p['pts'], ts)
            if ti: p = dict(p, tinfo=dict(start=classify.hm(ti['start'], tz), end=classify.hm(ti['end'], tz), move=ti['move'], date=_local_date(ti['start'], tz),
                                       stops=[[la, lo, classify.hm(a, tz), round((b - a) / 60)] for la, lo, a, b in ti['stops']]))
        if p.get('role') == 'radial' or (p.get('role') == 'main' and len(p.get('pts', [])) > 2):
            try:
                r = [(q[0], q[1]) for q in p['pts']]
                if p.get('role') == 'radial' or model.dist(r[0], r[-1]) < max(400.0, 0.05 * (p.get('km') or 0) * 1000):
                    p = dict(p, rinfo=model.radial_summary(r))
            except Exception: pass
        pcs.append(p)
    out['pieces'] = pcs
    return out

def merge_client(stored, incoming):
    """Клиент присылает проект без геометрии треков — точки ниток берутся из сохранённого."""
    geo = {p['id']: p for p in stored.get('pieces', [])}
    out = []
    for p in incoming.get('pieces', []):
        s = geo.get(p.get('id'))
        if not s: continue
        day = p.get('day')
        try: day = int(day) if day not in (None, '', 0) else None
        except Exception: day = None
        if day is not None and not 1 <= day <= 400: day = None
        s = {k: v for k, v in s.items() if k not in ('shape', 'once')}
        if p.get('shape') in ('back', 'loop', 'lolli'): s['shape'] = p['shape']      # форма радиалки, выбранная вручную
        if isinstance(p.get('once'), bool): s['once'] = p['once']      # обратный путь этой радиалки: в одну сторону или нет (иначе — как во всём походе)
        role = p.get('role') if p.get('role') in ('main', 'radial', 'skip', 'alt', 'exit') else 'main'
        s.pop('var', None)
        if role in classify.VAR_ROLES:      # кусок варианта: без дня, привязан к варианту
            day = None; s['var'] = str(p.get('var') or '')[:16]
        out.append(dict(s, name=str(p.get('name') or s['name'])[:120], day=day, role=role,
                        order=p.get('order', s.get('order', 0))))
    pts = []
    for q in incoming.get('points', [])[:5000]:
        try:
            q = dict(q); q['lat'] = float(q['lat']); q['lon'] = float(q['lon'])
            if not (-90 <= q['lat'] <= 90 and -180 <= q['lon'] <= 180): continue
            q['id'] = str(q.get('id') or classify.uid())[:16]; q['type'] = str(q.get('type') or 'mark')[:20]; q['name'] = str(q.get('name') or '')[:120]
            for k in ('h', 'day', 'after_day', 'nights'):
                v = q.get(k)
                try: q[k] = int(v) if v not in (None, '') else None
                except Exception: q[k] = None
            if q.get('date') and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(q['date'])): q['date'] = None
            if q.get('nums') is not None: q['nums'] = [int(x) for x in q['nums'] if str(x).lstrip('-').isdigit()][:80]
            pts.append(q)
        except Exception: continue
    meta = dict(stored.get('meta', {}))
    im = incoming.get('meta', {})
    for k in ('title', 'area', 'kind', 'category', 'note'):
        if k in im: meta[k] = str(im[k] or '')[:200]
    if 'start_date' in im: meta['start_date'] = im['start_date'] if im['start_date'] and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(im['start_date'])) else None
    if 'kcoef' in im:
        try: meta['kcoef'] = min(3.0, max(1.0, float(im['kcoef'])))
        except Exception: pass
    if 'radial_one_way' in im: meta['radial_one_way'] = bool(im['radial_one_way'])
    if 'no_passes' in im: meta['no_passes'] = bool(im['no_passes'])
    if 'tz' in im:
        try: meta['tz'] = max(-12.0, min(14.0, float(im['tz']))) if im['tz'] not in (None, '') else None
        except (TypeError, ValueError): pass      # по каталогу перевалов уже искали — у нитки ничего нет
    if im.get('elev') in ('auto', 'dem', 'track'): meta['elev'] = im['elev']
    sheets = dict(stored.get('sheets', {}))
    for k, v in (incoming.get('sheets') or {}).items():
        if k in ('region', 'overview', 'work', 'days', 'profile', 'photos', 'logo'): sheets[k] = bool(v)
        elif k == 'work_scale' and v in (50000, 100000, 200000): sheets[k] = v
        elif k == 'scheme' and v in ('auto', 'sheet', 'overview', 'none'): sheets[k] = v
        elif k == 'base' and v in ('gsh', 'otm'): sheets[k] = v
        elif k == 'adjust' and isinstance(v, dict):
            adj = {}
            for sid, a in list(v.items())[:200]:
                if not re.fullmatch(r'region|overview|scheme|work-\d{1,3}|day-\d{1,3}', str(sid)) or not isinstance(a, dict): continue
                try:
                    o = {}
                    if a.get('lat') is not None and a.get('lon') is not None and -90 <= float(a['lat']) <= 90 and -180 <= float(a['lon']) <= 180:
                        o['lat'] = round(float(a['lat']), 6); o['lon'] = round(float(a['lon']), 6)
                    if a.get('scale') and int(a['scale']) in SCALES: o['scale'] = int(a['scale'])
                    if o: adj[sid] = o
                except (TypeError, ValueError): continue
            sheets['adjust'] = adj
    bands = []
    for b in (incoming.get('bands') or [])[:300]:
        try:
            a = [round(float(b['a'][0]), 6), round(float(b['a'][1]), 6)]; e = [round(float(b['b'][0]), 6), round(float(b['b'][1]), 6)]
        except (KeyError, TypeError, ValueError, IndexError): continue
        kind = str(b.get('kind') or '')
        o = dict(id=str(b.get('id') or classify.uid())[:16], kind=kind if kind in classify.BAND else 'custom', a=a, b=e)
        if o['kind'] == 'custom':
            o['title'] = str(b.get('title') or 'особый участок')[:60]
            o['color'] = b.get('color') if re.fullmatch(r'#[0-9a-fA-F]{6}', str(b.get('color') or '')) else '#E6007A'
        bands.append(o)
    cts = []
    for c in (incoming.get('custom_types') or [])[:40]:
        cid = str(c.get('id') or '')
        if not re.fullmatch(r'c_[0-9a-f]{4,12}', cid): cid = 'c_' + classify.uid()
        col = c.get('color') if re.fullmatch(r'#[0-9a-fA-F]{6}', str(c.get('color') or '')) else '#555555'
        cts.append(dict(id=cid, title=str(c.get('title') or 'Своя точка')[:60], letter=str(c.get('letter') or '•')[:2], color=col))
    # варианты маршрута: название, пояснение, тип; пустые (без кусков) отпадают, у осиротевших кусков — свой вариант
    vs = []; known = {}
    for v in (incoming.get('variants') if 'variants' in incoming else stored.get('variants')) or []:
        try:
            vid = str(v.get('id') or '')[:16]; kind = v.get('kind') if v.get('kind') in classify.VAR_ROLES else 'alt'
            if not vid or vid in known: continue
            known[vid] = dict(id=vid, kind=kind, title=str(v.get('title') or 'вариант')[:80], note=str(v.get('note') or '')[:300])
        except AttributeError: continue
    for p in out:
        if p['role'] not in classify.VAR_ROLES: continue
        v = known.get(p.get('var'))
        if not v:
            v = known[p['var']] = dict(id=p.get('var') or classify.uid(), kind=p['role'], title=classify.variant_title(p['name']), note='')
            p['var'] = v['id']
        p['role'] = v['kind']      # тип задаётся у варианта целиком
    used = {p.get('var') for p in out if p['role'] in classify.VAR_ROLES}
    vs = [v for v in known.values() if v['id'] in used]
    if 'mode' in im and im['mode'] in ('plan', 'report', ''): meta['mode'] = im['mode'] or None
    return dict(stored, meta=meta, pieces=out, points=pts, sheets=sheets, custom_types=cts, bands=bands, variants=vs)

# ---------------------------------------------------------------- сборка в фоне
JOBS = queue.Queue()
ACTIVE = {}            # pid → 'queued' | 'running'
ORDER = []
_jl = threading.Lock()

def render_dir(pid): return os.path.join(RENDERS, pid)

def enqueue(pid, proj, only=None):
    with _jl:
        if pid in ACTIVE: return False
        ACTIVE[pid] = 'queued'; ORDER.append(pid)
    out = render_dir(pid); os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'project.json'), 'w', encoding='utf-8') as f: json.dump(proj, f, ensure_ascii=False)
    st = dict(state='queued', stage='В очереди', done=0, total=0, sheets=[], error=None)
    with open(os.path.join(out, 'status.json'), 'w', encoding='utf-8') as f: json.dump(st, f, ensure_ascii=False)
    JOBS.put((pid, only))
    return True

def worker():
    while True:
        pid, only = JOBS.get()
        out = render_dir(pid)
        with _jl: ACTIVE[pid] = 'running'
        try:
            cmd = [sys.executable, '-m', 'engine.cli', 'build', os.path.join(out, 'project.json'), out]
            if only and os.path.exists(os.path.join(out, 'sheets.json')): cmd += ['--only', ','.join(only)]
            with open(os.path.join(out, 'build.log'), 'w') as log:
                r = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=45 * 60)
            st = read_json(os.path.join(out, 'status.json')) or {}
            if st.get('state') not in ('done', 'error'):
                st.update(state='error', error='Сборка оборвалась (код %s). Попробуйте ещё раз.' % r.returncode, finished=time.time())
                with open(os.path.join(out, 'status.json'), 'w', encoding='utf-8') as f: json.dump(st, f, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            with open(os.path.join(out, 'status.json'), 'w', encoding='utf-8') as f:
                json.dump(dict(state='error', error='Сборка заняла больше 45 минут и была остановлена.', sheets=[], finished=time.time()), f, ensure_ascii=False)
        except Exception as e:
            with open(os.path.join(out, 'status.json'), 'w', encoding='utf-8') as f:
                json.dump(dict(state='error', error=str(e)[:300], sheets=[], finished=time.time()), f, ensure_ascii=False)
        finally:
            with _jl:
                ACTIVE.pop(pid, None)
                if pid in ORDER: ORDER.remove(pid)

def read_json(p):
    for _ in range(3):
        try:
            with open(p, encoding='utf-8') as f: return json.load(f)
        except FileNotFoundError: return None
        except Exception: time.sleep(0.05)
    return None

def build_status(pid):
    st = read_json(os.path.join(render_dir(pid), 'status.json')) or dict(state='none', sheets=[])
    with _jl:
        a = ACTIVE.get(pid)
        if a == 'queued': st['queue'] = ORDER.index(pid) if pid in ORDER else 0
    if not a and st.get('state') in ('queued', 'running'):
        st.update(state='error', error='Сборка прервалась из-за перезапуска сервера — запустите ещё раз.')
    if st.get('finished'): st['expires'] = st['finished'] + KEEP_DAYS * 86400
    st['keep_days'] = KEEP_DAYS
    return st

def janitor():
    while True:
        try:
            now = time.time()
            if os.path.isdir(RENDERS):
                for pid in os.listdir(RENDERS):
                    d = os.path.join(RENDERS, pid)
                    if pid in ACTIVE or not os.path.isdir(d): continue
                    st = os.path.join(d, 'status.json')
                    if now - (os.path.getmtime(st) if os.path.exists(st) else os.path.getmtime(d)) > KEEP_DAYS * 86400:
                        shutil.rmtree(d, ignore_errors=True)
            try:
                if suggest.refresh(7): print('westra catalogue refreshed', flush=True)      # каталог перевалов — раз в неделю
            except Exception as e:
                print('westra refresh:', e, file=sys.stderr)
            td = os.path.join(os.environ['NITKA_CACHE'], 'tiles'); files = []; total = 0
            for base, _, names in os.walk(td):
                for n in names:
                    p = os.path.join(base, n)
                    try: s = os.stat(p)
                    except OSError: continue
                    files.append((s.st_atime, s.st_size, p)); total += s.st_size
            if total > TILE_CAP:
                files.sort()
                for _, size, p in files:
                    try: os.remove(p)
                    except OSError: pass
                    total -= size
                    if total < TILE_CAP * 0.7: break
        except Exception as e:
            print('janitor:', e, file=sys.stderr)
        time.sleep(3600)

# ---------------------------------------------------------------- HTTP
class Err(Exception):
    def __init__(self, code, msg): self.code = code; self.msg = msg

class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'nitka'

    def log_message(self, fmt, *a):
        if os.environ.get('NITKA_LOG'): sys.stderr.write('%s %s\n' % (self.address_string(), fmt % a))

    # ---- helpers
    def send(self, code, body=b'', ctype='application/json; charset=utf-8', headers=None, cache=None):
        if isinstance(body, (dict, list)): body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str): body = body.encode()
        h = dict(headers or {})
        if len(body) > 1400 and 'gzip' in (self.headers.get('Accept-Encoding') or '') and ctype.split(';')[0] in ('application/json', 'text/html', 'text/css', 'application/javascript', 'image/svg+xml', 'text/javascript'):
            body = gzip.compress(body, 5); h['Content-Encoding'] = 'gzip'
        self.send_response(code)
        self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache or 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in h.items(): self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD': self.wfile.write(body)

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY: raise Err(413, 'Файл больше 45 МБ — разбейте его на части или заархивируйте.')
        return self.rfile.read(n) if n else b''

    def json(self):
        try: return json.loads(self.body() or b'{}')
        except Exception: raise Err(400, 'Не понял запрос.')

    def uid(self):
        for part in (self.headers.get('Cookie') or '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == 'nitka': return unsign(v)
        return None

    def cookie(self, name):
        for part in (self.headers.get('Cookie') or '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == name: return v
        return ''

    def need_admin(self, unlocked=True):
        uid = self.need_user(); r = db().execute('SELECT key FROM users WHERE id=?', (uid,)).fetchone()
        if not r or r['key'] not in ADMINS: raise Err(404, 'Нет такого адреса.')
        if unlocked and not hmac.compare_digest(self.cookie('nitka_adm'), adm_token(uid)): raise Err(403, 'Нужен код администратора.')
        return uid

    def need_user(self):
        u = self.uid()
        if not u or not db().execute('SELECT 1 FROM users WHERE id=?', (u,)).fetchone(): raise Err(401, 'Нужно войти.')
        return u

    def project(self, pid):
        if not re.fullmatch(r'[0-9a-f]{12}', pid): raise Err(404, 'Нет такого похода.')
        uid = self.need_user(); proj, row = load_project(pid, uid)
        if not proj: raise Err(404, 'Нет такого похода.')
        return uid, proj, row

    def handle_one(self):
        try:
            u = urllib.parse.urlsplit(self.path); path = urllib.parse.unquote(u.path); q = dict(urllib.parse.parse_qsl(u.query))
            self.route(self.command if self.command != 'HEAD' else 'GET', path, q)
        except Err as e:
            self.send(e.code, dict(error=e.msg))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            import traceback; traceback.print_exc()
            try: self.send(500, dict(error='Что-то сломалось на сервере. Попробуйте ещё раз.'))
            except Exception: pass
    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = handle_one

    # ---- маршруты
    def route(self, m, path, q):
        if path.startswith('/api/'): return self.api(m, path[5:].strip('/').split('/'), q)
        if path.startswith('/tiles/') and m == 'GET': return self.tile(path[7:].split('/'), q)
        if path.startswith('/files/') and m == 'GET': return self.file(path[7:].split('/'), q)
        if m != 'GET': raise Err(405, 'Так нельзя.')
        rel = 'index.html' if path in ('/', '') or not path.startswith('/static/') else path[8:]
        p = os.path.normpath(os.path.join(WEB, rel))
        if not p.startswith(WEB) or not os.path.isfile(p): raise Err(404, 'Не найдено.')
        ctype = mimetypes.guess_type(p)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript',): ctype += '; charset=utf-8'
        with open(p, 'rb') as f: data = f.read()
        if rel == 'index.html':
            v = int(max(os.path.getmtime(os.path.join(WEB, n)) for n in ('app.js', 'style.css', 'index.html')))
            data = data.replace(b'__V__', str(v).encode())
        self.send(200, data, ctype, cache='no-store' if rel == 'index.html' else ('public, max-age=31536000' if 'v' in q else 'public, max-age=604800'))

    def api(self, m, a, q):
        if a == ['me']:
            u = self.uid(); r = db().execute('SELECT * FROM users WHERE id=?', (u,)).fetchone() if u else None
            return self.send(200, dict(user=dict(name=r['name'], origin=r['origin'] or '', admin=r['key'] in ADMINS) if r else None, need_code=bool(CODE), keep_days=KEEP_DAYS))
        if a == ['login'] and m == 'POST':
            d = self.json(); name = str(d.get('name') or '')
            key, shown = user_key(name)
            if len(key.split(':', 1)[1]) < 2: raise Err(400, 'Напишите имя, ник или телефон — хотя бы две буквы.')
            if len(shown) > 40: raise Err(400, 'Слишком длинно — хватит имени или телефона.')
            c = db(); r = c.execute('SELECT * FROM users WHERE key=?', (key,)).fetchone()
            if CODE and not hmac.compare_digest(str(d.get('code') or '').strip().lower(), CODE.lower()):
                raise Err(403, 'Кодовое слово клуба не подошло.')
            if not r:
                c.execute('INSERT INTO users(key,name,created,seen) VALUES(?,?,?,?)', (key, shown, time.time(), time.time())); c.commit()
                r = c.execute('SELECT * FROM users WHERE key=?', (key,)).fetchone(); new = True
            else:
                c.execute('UPDATE users SET seen=? WHERE id=?', (time.time(), r['id'])); c.commit(); new = False
            n = c.execute('SELECT COUNT(*) FROM projects WHERE user_id=?', (r['id'],)).fetchone()[0]
            return self.send(200, dict(user=dict(name=r['name'], origin=r['origin'] or '', admin=r['key'] in ADMINS), new=new, projects=n),
                             headers={'Set-Cookie': f'nitka={sign(r["id"])}; Path=/; Max-Age=63072000; HttpOnly; SameSite=Lax'
                                      + ('; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else '')})
        if a == ['admin', 'state'] and m == 'GET':
            uid = self.need_admin(unlocked=False)
            return self.send(200, dict(code_set=bool(setting('admin_code')), unlocked=hmac.compare_digest(self.cookie('nitka_adm'), adm_token(uid))))
        if a == ['admin', 'code'] and m == 'POST':
            uid = self.need_admin(unlocked=False); code = str(self.json().get('code') or '').strip(); now = time.time()
            t = [x for x in ADMIN_TRIES.get(uid, []) if now - x < 600]
            if len(t) >= 6: raise Err(429, 'Слишком много попыток — подожди десять минут.')
            saved = setting('admin_code')
            if not saved:
                if len(code) < 4: raise Err(400, 'Код — хотя бы четыре символа.')
                setting('admin_code', code_hash(code))
            elif not hmac.compare_digest(code_hash(code, saved.split(':')[0]), saved):
                ADMIN_TRIES[uid] = t + [now]; raise Err(403, 'Код не подошёл.')
            ADMIN_TRIES.pop(uid, None)
            return self.send(200, dict(ok=True), headers={'Set-Cookie': f'nitka_adm={adm_token(uid)}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax'
                                                          + ('; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else '')})
        if a == ['admin', 'users'] and m == 'GET':
            self.need_admin()
            out = []
            for u in db().execute('SELECT * FROM users ORDER BY created DESC').fetchall():
                ps = []
                for pr in db().execute('SELECT id, title, card, updated FROM projects WHERE user_id=? ORDER BY updated DESC', (u['id'],)).fetchall():
                    k = json.loads(pr['card'] or '{}'); st = build_status(pr['id'])
                    ps.append(dict(title=pr['title'], ndays=k.get('ndays', 0), km=k.get('km', 0), points=k.get('points', 0), kind=k.get('kind', ''), updated=pr['updated'],
                                   built=sum(1 for x in st.get('sheets', []) if x.get('state') == 'ok'), state=st.get('state')))
                out.append(dict(name=u['name'], by='телефон' if u['key'].startswith('tel:') else 'имя', origin=u['origin'] or '', created=u['created'], seen=u['seen'], projects=ps))
            return self.send(200, dict(users=out))
        if a == ['profile'] and m == 'POST':
            uid = self.need_user(); origin = re.sub(r'\s+', ' ', str(self.json().get('origin') or '')).strip()[:80]
            if len(origin) < 2: raise Err(400, 'Напиши пару слов: турклуб, город или «сам по себе».')
            db().execute('UPDATE users SET origin=? WHERE id=?', (origin, uid)); db().commit()
            return self.send(200, dict(ok=True, origin=origin))
        if a == ['logout'] and m == 'POST':
            return self.send(200, dict(ok=True), headers={'Set-Cookie': 'nitka=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'})
        if a == ['types']:
            return self.send(200, dict(types=[dict(t, color=('#%02x%02x%02x' % tuple(t['color'])) if t.get('color') else None) for t in classify.TYPES],
                                       grades=classify.GRADES, obj=list(classify.OBJ_TYPES), grade_col={g: '#%02x%02x%02x' % classify.GRADE_COL[g] for g in classify.GRADES},
                                       bands=[dict(id=b['id'], title=b['title'], group=b['group'], color='#%02x%02x%02x' % tuple(b['color'][:3]), hatch=bool(b.get('hatch'))) for b in classify.BANDS]),
                             cache='public, max-age=300')
        if a == ['projects'] and m == 'GET':
            uid = self.need_user()
            rows = db().execute('SELECT id,title,card,created,updated FROM projects WHERE user_id=? ORDER BY updated DESC', (uid,)).fetchall()
            out = []
            for r in rows:
                if '"geo"' not in (r['card'] or ''):          # карточка старого образца — пересчитываем один раз
                    pj = db().execute('SELECT json FROM projects WHERE id=?', (r['id'],)).fetchone()
                    card = json.dumps(card_of(json.loads(pj['json'])), ensure_ascii=False)
                    db().execute('UPDATE projects SET card=? WHERE id=?', (card, r['id'])); db().commit()
                    r = dict(r, card=card)
                st = build_status(r['id'])
                out.append(dict(id=r['id'], title=r['title'], updated=r['updated'], card=json.loads(r['card'] or '{}'),
                                build=dict(state=st.get('state'), expires=st.get('expires'), n=sum(1 for s in st.get('sheets', []) if s.get('state') == 'ok'))))
            return self.send(200, dict(projects=out))
        if a == ['projects'] and m == 'POST':
            uid = self.need_user(); d = self.json()
            if db().execute('SELECT COUNT(*) FROM projects WHERE user_id=?', (uid,)).fetchone()[0] >= 300: raise Err(400, 'Слишком много походов — удалите ненужные.')
            pid = secrets.token_hex(6); proj = classify.new_project(str(d.get('title') or '').strip()[:120])
            if d.get('kind'): proj['meta']['kind'] = str(d['kind'])[:30]
            if proj['meta']['kind'] == 'горный': proj['meta']['kcoef'] = 1.2
            save_project(pid, uid, proj, create=True)
            return self.send(200, dict(id=pid))
        if len(a) >= 2 and a[0] == 'projects':
            uid, proj, row = self.project(a[1]); pid = a[1]; sub = a[2] if len(a) > 2 else ''
            if not sub and m == 'GET': return self.send(200, dict(project=for_client(proj), build=build_status(pid), updated=row['updated']))
            if not sub and m == 'PUT':
                proj = merge_client(proj, self.json()); save_project(pid, uid, proj)
                return self.send(200, dict(ok=True, card=card_of(proj)))
            if not sub and m == 'DELETE':
                db().execute('DELETE FROM projects WHERE id=? AND user_id=?', (pid, uid)); db().commit()
                if pid not in ACTIVE: shutil.rmtree(render_dir(pid), ignore_errors=True)
                return self.send(200, dict(ok=True))
            if sub == 'copy' and m == 'POST':
                nid = secrets.token_hex(6); proj['meta']['title'] = (proj['meta'].get('title') or '') + ' (копия)'
                save_project(nid, uid, proj, create=True); return self.send(200, dict(id=nid))
            if sub == 'import' and m == 'POST':
                if 'json' in (self.headers.get('Content-Type') or ''):
                    link = str(self.json().get('link') or '').strip()
                    if not re.match(r'^https?://', link) and not re.search(r'nkt[klp]=', link): raise Err(400, 'Это не похоже на ссылку. Нужна ссылка nakarte.me с треком.')
                    try: layers = importers.parse_nakarte_link(link)
                    except importers.ImportError_ as e: raise Err(400, str(e))
                    name = 'ссылка nakarte'
                else:
                    name = (q.get('name') or 'трек')[:120]; data = self.body()
                    if not data: raise Err(400, 'Файл пустой.')
                    try: layers = importers.parse_file(name, data)
                    except importers.ImportError_ as e: raise Err(400, str(e))
                if not any(L['segs'] or L['wpts'] for L in layers): raise Err(400, f'В «{name}» не нашлось ни треков, ни точек.')
                if sum(len(s) for L in layers for s in L['segs']) > 1500000: raise Err(400, 'Слишком большой трек.')
                as_role = q.get('as') if q.get('as') in classify.VAR_ROLES else 'main'      # «Что загружаю»: основной маршрут или вариант
                try: tzq = float(q['tz']) if 'tz' in q else None      # пояс браузера: им считается время, если у похода его ещё нет
                except ValueError: tzq = None
                info = classify.merge_import(proj, layers, name, as_role, tzq)
                save_project(pid, uid, proj)
                return self.send(200, dict(project=for_client(proj), info=info))
            if sub == 'variants' and m == 'POST':      # варианты маршрута: сделать из кусков, нарисовать, объединить, отделить
                d = self.json(); act = d.get('action'); kind = d.get('kind') if d.get('kind') in classify.VAR_ROLES else 'alt'
                pcs = {p['id']: p for p in proj.get('pieces', [])}
                if act == 'make':
                    ids = [str(x) for x in (d.get('pieces') or [])][:200]
                    sel = sorted([pcs[i] for i in ids if i in pcs], key=lambda p: p.get('order', 0))
                    if not sel: raise Err(400, 'Не выбраны куски нитки.')
                    made = classify.group_variants(proj, sel, kind)
                    msg = f"{'Запасной вариант' if kind == 'alt' else 'Аварийный выход'}" + (f' — {len(made)} шт.: куски не стыкуются друг с другом' if len(made) > 1 else ' готов')
                elif act == 'draw':
                    try: pts = [[round(float(a), 6), round(float(b), 6)] for a, b in (d.get('pts') or [])][:2000]
                    except (TypeError, ValueError): raise Err(400, 'Неверные точки.')
                    if len(pts) < 2: raise Err(400, 'Нужно хотя бы две точки.')
                    p = dict(id=classify.uid(), src='drawn', name='нарисовано на карте', pts=pts, day=None, role=kind,
                             order=len(proj['pieces']), km=round(classify.length_km(pts), 2))
                    proj['pieces'].append(p)
                    made = classify.group_variants(proj, [p], kind, title=str(d.get('title') or ('запасной вариант' if kind == 'alt' else 'аварийный выход'))[:80])
                    msg = f"Нарисовано {p['km']:.1f} км".replace('.', ',')
                elif act == 'split':
                    p = pcs.get(str(d.get('piece') or ''))
                    if not p or p.get('role') not in classify.VAR_ROLES: raise Err(400, 'Это не кусок варианта.')
                    made = classify.group_variants(proj, [p], p['role'], title=classify.variant_title(p['name'])); msg = 'Кусок стал отдельным вариантом'
                elif act == 'join':
                    a, b = str(d.get('var') or ''), str(d.get('into') or '')
                    vb = next((v for v in proj.get('variants', []) if v['id'] == b), None)
                    if not vb or a == b: raise Err(400, 'Нет такого варианта.')
                    for p in proj['pieces']:
                        if p.get('var') == a: p['var'] = b; p['role'] = vb['kind']
                    msg = 'Варианты объединены'
                elif act == 'unmake':
                    a = str(d.get('var') or '')
                    for p in proj['pieces']:
                        if p.get('var') == a: p['role'] = 'main'; p.pop('var', None)
                    msg = 'Куски вернулись в нитку без дня'
                else: raise Err(400, 'Неизвестное действие.')
                used = {p.get('var') for p in proj['pieces'] if p.get('role') in classify.VAR_ROLES}
                proj['variants'] = [v for v in proj.get('variants', []) if v['id'] in used]
                save_project(pid, uid, proj)
                return self.send(200, dict(project=for_client(proj), msg=msg))
            if sub == 'source-delete' and m == 'POST':
                classify.remove_source(proj, str(self.json().get('id') or '')); save_project(pid, uid, proj)
                return self.send(200, dict(project=for_client(proj)))
            if sub == 'cut' and m == 'POST':
                d = self.json()
                try: lat, lon = float(d['lat']), float(d['lon'])
                except (KeyError, TypeError, ValueError): raise Err(400, 'Не понял, где резать.')
                r = classify.cut_piece(proj, str(d.get('piece') or ''), lat, lon)
                if r['ok']: save_project(pid, uid, proj)
                return self.send(200, dict(project=for_client(proj), **r))
            if sub == 'suggest' and m == 'POST':
                what = str(self.json().get('what') or '')
                try:
                    if what == 'camps': return self.send(200, dict(items=suggest.camps(proj)))
                    if what == 'passes': return self.send(200, dict(items=suggest.passes(proj)))
                except RuntimeError as e: raise Err(503, str(e))
                raise Err(400, 'Не понял, что подсказать.')
            if sub == 'chain-order' and m == 'POST':
                n = classify.chain_order(proj)
                if n: save_project(pid, uid, proj)
                return self.send(200, dict(project=for_client(proj), changed=n))
            if sub == 'split-by-camps' and m == 'POST':
                r = classify.split_by_camps(proj)
                if r['ok']: save_project(pid, uid, proj)
                return self.send(200, dict(project=for_client(proj), **r))
            if sub == 'build' and m == 'POST':
                d = self.json()
                if not any(p.get('role') in ('main', 'radial') for p in proj.get('pieces', [])): raise Err(400, 'Сначала загрузите трек основного маршрута.')
                only = [s for s in (d.get('only') or []) if re.fullmatch(r'[a-z]+(-\d+)?', str(s))] or None
                mine = {r['id'] for r in db().execute('SELECT id FROM projects WHERE user_id=?', (uid,))}
                if sum(1 for x in list(ACTIVE) if x in mine) >= 3: raise Err(429, 'Уже собираются три твоих похода — дождись, пока закончат.')
                if not enqueue(pid, proj, only): raise Err(409, 'Этот поход уже собирается.')
                return self.send(200, dict(ok=True, build=build_status(pid)))
            if sub == 'status' and m == 'GET': return self.send(200, build_status(pid))
            if sub == 'sheet-counts' and m == 'GET':      # сколько рабочих листов даст каждый масштаб — для кнопок на шаге «Карты»
                try: return self.send(200, dict(counts=sheets_mod.work_counts(model.Trip(proj))))
                except ValueError: return self.send(200, dict(counts={}))
            if sub == 'zip' and m == 'GET': return self.zip(pid, proj, q.get('fmt') or 'png')
        raise Err(404, 'Нет такого адреса.')

    def tile(self, a, q=None):
        try: layer, z, x, y = a[0], int(a[1]), int(a[2]), int(a[3].split('.')[0])
        except Exception: raise Err(404, 'нет')
        if layer not in TILE_LAYERS or not 3 <= z <= TILE_LAYERS[layer]: raise Err(404, 'нет')
        uid = self.need_user(); now = time.time()
        w = TILE_RATE.get(uid)
        if not w or now - w[0] > 600: w = TILE_RATE[uid] = [now, 0]
        w[1] += 1
        if w[1] > 6000: raise Err(429, 'Слишком много запросов карты — подождите несколько минут.')
        if layer == 'gsh100': return self.topo_tile(z, x, y)
        # Браузер спрашивает тайл до четырёх раз: без r, потом r=1, 2, 3 — через 5, 15 и 30 секунд (см. tiles() в app.js).
        # r=oz — временная замена на время ожидания: увеличенный кусок более мелкого зума из кэша.
        r = (q or {}).get('r', '')
        if r == 'oz':
            up = self.overzoom(layer, z, x, y)
            return self.send(200, up, 'image/png', cache='no-store') if up else self.send(404, b'', 'image/png', cache='no-store')
        # OpenTopoMap рисует крупный зум по запросу: «тайла нет» у него значит «ещё не готов» — на повторах спрашиваем его заново
        data = mapkit.fetch_tile(layer, z, x, y, timeout=12, tries=2, fresh=bool(r) and layer == 'otm')
        if data == b'' and layer == 'otm':
            if r != '3': return self.send(503, b'', 'image/png', cache='no-store')      # браузер спросит ещё раз
            up = self.overzoom(layer, z, x, y)      # три повтора не помогли — оставляем замену, но ненадолго: при следующем просмотре спросим снова
            return self.send(200, up or BLANK_PNG, 'image/png', cache='public, max-age=300')
        if data == b'': return self.send(200, BLANK_PNG, 'image/png', cache='public, max-age=86400')       # тайла нет вовсе
        if data is None:
            # сервер карты не ответил: просим браузер спросить ещё раз, после последнего повтора — увеличенный кусок более мелкого зума
            up = self.overzoom(layer, z, x, y) if r == '3' else None
            if up: return self.send(200, up, 'image/png', cache='no-store')
            return self.send(503, b'', 'image/png', cache='no-store')
        self.send(200, data, 'image/jpeg' if data[:2] == b'\xff\xd8' else 'image/png', cache='public, max-age=2592000')

    def topo_tile(self, z, x, y):
        """«Генштаб» в редакторе: в каждом месте — первая топокарта, которая там есть (Генштаб, потом ГГЦ), как и при сборке листов."""
        from PIL import Image
        chain = mapkit.topo_chain(z, 50 if z >= 14 else 100 if z >= 9 else 200, max_up=3)
        acc = None; failed = False
        for L, lz in chain:
            d = 2 ** (z - lz)
            data = mapkit.fetch_tile(L, lz, x // d, y // d, timeout=12, tries=2)
            if data is None: failed = True; continue
            if not data: continue
            im = Image.open(io.BytesIO(data)).convert('RGBA')
            if d > 1:
                c = 256 // d; im = im.crop(((x % d) * c, (y % d) * c, (x % d + 1) * c, (y % d + 1) * c)).resize((256, 256), Image.BICUBIC)
            if acc is None and d == 1 and im.getchannel('A').getextrema()[0] == 255:
                return self.send(200, data, 'image/png', cache='public, max-age=2592000')      # целый тайл нужного зума — отдаём как есть
            acc = im if acc is None else Image.alpha_composite(im, acc)
            if acc.getchannel('A').getextrema()[0] == 255: break
        if acc is None:
            if failed: return self.send(503, b'', 'image/png', cache='no-store')      # браузер спросит ещё раз
            return self.send(200, BLANK_PNG, 'image/png', cache='public, max-age=86400')      # топокарт здесь нет
        b = io.BytesIO(); acc.save(b, 'PNG')
        return self.send(200, b.getvalue(), 'image/png', cache='no-store' if failed else 'public, max-age=86400')

    def overzoom(self, layer, z, x, y):
        from PIL import Image
        for up in (1, 2, 3):
            if z - up < 3: break
            f = 2 ** up; path = os.path.join(os.environ['NITKA_CACHE'], 'tiles', layer, str(z - up), str(x // f), f'{y // f}.png')
            if not os.path.exists(path) or os.path.getsize(path) == 0: continue      # только из кэша: лишних запросов наружу не делаем
            try:
                im = Image.open(path).convert('RGB'); c = 256 // f
                im = im.crop(((x % f) * c, (y % f) * c, (x % f + 1) * c, (y % f + 1) * c)).resize((256, 256), Image.BICUBIC)
                b = io.BytesIO(); im.save(b, 'PNG'); return b.getvalue()
            except Exception: continue
        return None

    def file(self, a, q):
        if len(a) != 2: raise Err(404, 'нет')
        pid, name = a
        uid, proj, row = self.project(pid)
        if not re.fullmatch(r'[A-Za-z0-9_\-]+(\.prev|\.thumb)?\.(png|jpg|pdf)', name): raise Err(404, 'нет')
        d = render_dir(pid); p = os.path.join(d, name)
        want_jpg = name.endswith('.jpg') and not name.endswith(('.prev.jpg', '.thumb.jpg'))
        if want_jpg:
            src = p[:-4] + '.png'
            if not os.path.exists(src): raise Err(404, 'Файл уже удалён — соберите карты заново.')
            if not os.path.exists(p) or os.path.getmtime(p) < os.path.getmtime(src):
                from PIL import Image
                Image.MAX_IMAGE_PIXELS = None
                Image.open(src).convert('RGB').save(p, 'JPEG', quality=93, subsampling=0, dpi=(300, 300), optimize=True)
        if not os.path.exists(p): raise Err(404, 'Файл уже удалён — соберите карты заново.')
        h = {}
        if q.get('dl'):
            title = re.sub(r'[\\/:*?"<>|]+', ' ', proj['meta'].get('title') or 'Поход').strip()
            nice = f'{title} — {name}' if name != 'karty.pdf' else f'{title} — карты.pdf'
            h['Content-Disposition'] = "attachment; filename*=UTF-8''" + urllib.parse.quote(nice)
        with open(p, 'rb') as f: data = f.read()
        self.send(200, data, mimetypes.guess_type(name)[0] or 'application/octet-stream', headers=h, cache='private, max-age=31536000' if q.get('v') else 'no-cache')

    def zip(self, pid, proj, fmt):
        d = render_dir(pid); sheets = read_json(os.path.join(d, 'sheets.json')) or []
        ok = [s for s in sheets if s.get('state') == 'ok' and os.path.exists(os.path.join(d, s['file']))]
        if not ok: raise Err(404, 'Карты ещё не собраны или уже удалены.')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as z:
            for s in ok:
                src = os.path.join(d, s['file'])
                if fmt == 'jpg':
                    from PIL import Image
                    Image.MAX_IMAGE_PIXELS = None
                    b = io.BytesIO(); Image.open(src).convert('RGB').save(b, 'JPEG', quality=93, subsampling=0, dpi=(300, 300)); z.writestr(s['file'][:-4] + '.jpg', b.getvalue())
                else: z.write(src, s['file'])
            if os.path.exists(os.path.join(d, 'karty.pdf')): z.write(os.path.join(d, 'karty.pdf'), 'Все карты.pdf')
        title = re.sub(r'[\\/:*?"<>|]+', ' ', proj['meta'].get('title') or 'Поход').strip()
        self.send(200, buf.getvalue(), 'application/zip', headers={'Content-Disposition': "attachment; filename*=UTF-8''" + urllib.parse.quote(f'{title} — карты ({fmt}).zip')})


def resume_interrupted():
    """Сборки, оборванные перезапуском сервера меньше 15 минут назад, запускаются заново сами."""
    if not os.path.isdir(RENDERS): return
    for pid in os.listdir(RENDERS):
        try:
            p = os.path.join(RENDERS, pid, 'status.json'); st = read_json(p) or {}
            if st.get('state') not in ('running', 'queued') or time.time() - os.path.getmtime(p) > 900: continue
            r = db().execute('SELECT json FROM projects WHERE id=?', (pid,)).fetchone()
            if r and enqueue(pid, json.loads(r['json'])): print('resumed build', pid, flush=True)
        except Exception as e:
            print('resume:', pid, e, file=sys.stderr)

def main():
    init_db()
    resume_interrupted()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), H); srv.daemon_threads = True
    print(f'nitka: http://{HOST}:{PORT}  data={DATA}  code={"on" if CODE else "off"}', flush=True)
    srv.serve_forever()

if __name__ == '__main__':
    main()
