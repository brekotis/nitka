"""«Нитка» — веб-сервис карт и профилей для отчётов о походах. Только стандартная библиотека.

python3 -m server.app            (NITKA_PORT=8080, NITKA_DATA=./data, NITKA_CODE=<кодовое слово клуба, необязательно>)
"""
import gzip, hashlib, hmac, io, json, mimetypes, os, queue, re, secrets, shutil, sqlite3, subprocess, sys, threading, time, urllib.parse, urllib.request, zipfile, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
DATA = os.path.abspath(os.environ.get('NITKA_DATA') or os.path.join(ROOT, 'data'))
os.makedirs(DATA, exist_ok=True)
os.environ.setdefault('NITKA_CACHE', os.path.join(DATA, 'cache'))
sys.path.insert(0, ROOT)
from engine import classify, importers, mapkit, model, suggest          # noqa: E402
from engine import sheets as sheets_mod, chrono, trackfix, planner          # noqa: E402

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
      CREATE TABLE IF NOT EXISTS members(project_id TEXT, user_id INTEGER, role TEXT, added REAL, PRIMARY KEY(project_id, user_id));
      CREATE INDEX IF NOT EXISTS members_user ON members(user_id);
      CREATE TABLE IF NOT EXISTS shares(token TEXT PRIMARY KEY, project_id TEXT, role TEXT, created REAL);
      CREATE INDEX IF NOT EXISTS shares_project ON shares(project_id);
      CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY, project_id TEXT, user_id INTEGER, ts REAL, t0 REAL, kind TEXT, what TEXT, km REAL, data BLOB);
      CREATE INDEX IF NOT EXISTS revisions_project ON revisions(project_id, id);
    ''')
    if 'by' not in [r[1] for r in c.execute('PRAGMA table_info(projects)')]:
        c.execute('ALTER TABLE projects ADD COLUMN by INTEGER')       # кто последним менял поход
    cols = [r[1] for r in c.execute('PRAGMA table_info(users)')]
    if 'origin' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN origin TEXT')        # «откуда вы» — турклуб, город и т. п.
    if 'news_seen' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN news_seen INTEGER')  # номер последней новости, которую человек видел
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

def save_project(pid, uid, proj, create=False, why=None, kind='auto', old=None):
    """Сохранить поход (права проверены раньше) и записать в журнал. Возвращает версию — время сохранения."""
    proj['meta']['title'] = (proj['meta'].get('title') or '').strip()[:120] or 'Без названия'
    now = time.time(); js = json.dumps(proj, ensure_ascii=False, separators=(',', ':')); card = card_of(proj)
    c = db(); prev = None
    if create: c.execute('INSERT INTO projects(id,user_id,title,json,card,created,updated,by) VALUES(?,?,?,?,?,?,?,?)', (pid, uid, proj['meta']['title'], js, json.dumps(card, ensure_ascii=False), now, now, uid))
    else:
        prev = c.execute('SELECT json, updated, by, user_id FROM projects WHERE id=?', (pid,)).fetchone()
        c.execute('UPDATE projects SET title=?, json=?, card=?, updated=?, by=? WHERE id=?', (proj['meta']['title'], js, json.dumps(card, ensure_ascii=False), now, uid, pid))
    c.commit()
    try: journal(pid, uid, prev, old, proj, js, card.get('km'), why or ('поход создан' if create else None), 'base' if create else kind, now)
    except Exception:
        import traceback; traceback.print_exc()      # журнал не должен ломать сохранение
    return rev_of(now)

def rev_of(t): return f'{t:.6f}'

# ---------------------------------------------------------------- общий доступ
RANK = {'view': 1, 'edit': 2, 'owner': 3}

def access(pid, uid):
    """Что человек может с походом: 'owner', 'edit', 'view' или None."""
    if not uid: return None
    r = db().execute('SELECT user_id FROM projects WHERE id=?', (pid,)).fetchone()
    if not r: return None
    if r['user_id'] == uid: return 'owner'
    m = db().execute('SELECT role FROM members WHERE project_id=? AND user_id=?', (pid, uid)).fetchone()
    return m['role'] if m else None

def share_of(token):
    """Ссылка «поделиться» → (поход, права) или (None, None)."""
    if not token or not re.fullmatch(r'[A-Za-z0-9_\-]{10,40}', str(token)): return None, None
    r = db().execute('SELECT project_id, role FROM shares WHERE token=?', (token,)).fetchone()
    return (r['project_id'], r['role']) if r else (None, None)

def user_name(uid):
    r = db().execute('SELECT name FROM users WHERE id=?', (uid,)).fetchone() if uid else None
    return r['name'] if r else ''

def share_state(pid, role):
    c = db(); own = c.execute('SELECT p.user_id, u.name FROM projects p LEFT JOIN users u ON u.id=p.user_id WHERE p.id=?', (pid,)).fetchone()
    links = {r['role']: r['token'] for r in c.execute('SELECT token, role FROM shares WHERE project_id=?', (pid,))}
    mem = [dict(id=r['user_id'], name=r['name'] or '?', role=r['role'], added=r['added']) for r in
           c.execute('SELECT m.user_id, m.role, m.added, u.name FROM members m LEFT JOIN users u ON u.id=m.user_id WHERE m.project_id=? ORDER BY m.added', (pid,))]
    return dict(links=links, members=mem, owner=own['name'] if own else '', manage=role == 'owner')

# ---------------------------------------------------------------- журнал изменений: версии похода, к любой можно вернуться
REV_KEEP = 100                     # версий на поход
REV_GAP, REV_SPAN = 600, 3600      # подряд идущие мелкие правки одного человека — одна версия (пауза < 10 мин, не дольше часа)
META_WORDS = {'area': 'район', 'kind': 'вид похода', 'start_date': 'дата начала', 'kcoef': 'коэффициент км', 'mode': 'план или отчёт',
              'tz': 'часовой пояс', 'radial_one_way': 'радиалки', 'category': 'категория', 'note': 'заметка'}
PART_WORDS = {'bands': 'участки пути', 'sheets': 'настройки карт', 'variants': 'варианты', 'custom_types': 'свои типы точек',
              'sources': 'файлы', 'frames': 'рамки листов', 'chrono': 'хронометраж', 'plan': 'нарисованный маршрут'}

def pack(js): return zlib.compress(js.encode('utf-8'), 6)
def unpack(b): return json.loads(zlib.decompress(b).decode('utf-8'))

def describe(a, b):
    """Что поменялось между двумя состояниями похода — короткими словами для журнала."""
    out = []
    ma, mb = a.get('meta', {}), b.get('meta', {})
    ch = [k for k in set(ma) | set(mb) if ma.get(k) != mb.get(k)]
    if 'title' in ch: out.append(f'название: «{mb.get("title", "")}»')
    for w in sorted({META_WORDS.get(k, 'настройки похода') for k in ch if k != 'title'}): out.append(w)
    pa = {p['id']: p for p in a.get('pieces', [])}; pb = {p['id']: p for p in b.get('pieces', [])}
    add = [i for i in pb if i not in pa]; rem = [i for i in pa if i not in pb]
    if add or rem: out.append('треки: ' + ' '.join(x for x in (f'+{len(add)}' if add else '', f'−{len(rem)}' if rem else '') if x))
    geo = [pb[i].get('name', '') for i in pb if i in pa and pa[i].get('pts') != pb[i].get('pts')]
    if geo: out.append('правка трека ' + ', '.join(f'«{n}»' for n in geo[:2]) + (f' и ещё {len(geo) - 2}' if len(geo) > 2 else ''))
    if any(any(pa[i].get(k) != pb[i].get(k) for k in ('day', 'order', 'role', 'var', 'shape', 'once', 'name')) for i in pb if i in pa): out.append('дни и куски')
    nz = lambda q: {k: v for k, v in q.items() if v not in (None, '', [], {})}      # пустое поле и его отсутствие — одно и то же
    qa = {q.get('id'): nz(q) for q in a.get('points', [])}; qb = {q.get('id'): nz(q) for q in b.get('points', [])}
    na = sum(1 for i in qb if i not in qa); nr = sum(1 for i in qa if i not in qb); nc = sum(1 for i in qb if i in qa and qa[i] != qb[i])
    if na or nr or nc: out.append('точки: ' + ', '.join(x for x in (f'+{na}' if na else '', f'−{nr}' if nr else '', f'изменено {nc}' if nc else '') if x))
    for k in sorted(set(a) | set(b)):
        if k in ('meta', 'pieces', 'points') or a.get(k) == b.get(k): continue
        w = PART_WORDS.get(k, 'прочее')
        if w not in out: out.append(w)
    return out

def journal(pid, uid, prev, old, new, js, km, why, kind, now):
    c = db()
    last = c.execute('SELECT id, user_id, ts, t0, kind FROM revisions WHERE project_id=? ORDER BY id DESC LIMIT 1', (pid,)).fetchone()
    if kind == 'base':
        c.execute('INSERT INTO revisions(project_id,user_id,ts,t0,kind,what,km,data) VALUES(?,?,?,?,?,?,?,?)', (pid, uid, now, now, 'base', json.dumps([why]), km, pack(js))); c.commit(); return
    if old is None: old = json.loads(prev['json']) if prev else {}
    if not last and prev:      # поход старше журнала: его прежнее состояние — первая версия
        oc = card_of(old)
        c.execute('INSERT INTO revisions(project_id,user_id,ts,t0,kind,what,km,data) VALUES(?,?,?,?,?,?,?,?)',
                  (pid, prev['by'] or prev['user_id'], prev['updated'], prev['updated'], 'base', json.dumps(['как было до журнала']), oc.get('km'), pack(prev['json'])))
        last = c.execute('SELECT id, user_id, ts, t0, kind FROM revisions WHERE project_id=? ORDER BY id DESC LIMIT 1', (pid,)).fetchone()
    if kind == 'auto' and last and last['kind'] == 'auto' and last['user_id'] == uid and now - last['ts'] < REV_GAP and now - last['t0'] < REV_SPAN:
        b = c.execute('SELECT data FROM revisions WHERE project_id=? AND id<? ORDER BY id DESC LIMIT 1', (pid, last['id'])).fetchone()
        what = describe(unpack(b['data']) if b else old, new)
        if not what: c.execute('DELETE FROM revisions WHERE id=?', (last['id'],))      # правки вернулись к прежнему — версии нет
        else: c.execute('UPDATE revisions SET ts=?, what=?, km=?, data=? WHERE id=?', (now, json.dumps(what, ensure_ascii=False), km, pack(js), last['id']))
    else:
        what = ([why] if why else []) + (describe(old, new) if kind != 'restore' else [])
        if not what: return
        c.execute('INSERT INTO revisions(project_id,user_id,ts,t0,kind,what,km,data) VALUES(?,?,?,?,?,?,?,?)',
                  (pid, uid, now, now, kind, json.dumps(what, ensure_ascii=False), km, pack(js)))
    c.execute('DELETE FROM revisions WHERE project_id=? AND id NOT IN (SELECT id FROM revisions WHERE project_id=? ORDER BY id DESC LIMIT ?)', (pid, pid, REV_KEEP))
    c.commit()

def changelog():
    """Что нового (web/changelog.json, новые сверху) — перечитывается, когда файл меняется."""
    p = os.path.join(WEB, 'changelog.json')
    try:
        mt = os.path.getmtime(p)
        if _CL.get('mt') != mt:
            with open(p, encoding='utf-8') as f: _CL.update(mt=mt, items=json.load(f))
    except (OSError, ValueError): return []
    return _CL.get('items', [])
_CL = {}

def dist_ok(a, b): return classify.dist(tuple(a[:2]), tuple(b[:2]))

def _local_date(ts, tz):
    return time.strftime('%Y-%m-%d', time.gmtime(ts + tz * 3600))

def for_client(proj):
    """Проект для браузера: у кусков — что «Нитка» видит в них как в радиалке (форма и км). В базу это не пишется."""
    out = dict(proj); pcs = []
    tz = classify.trip_tz(proj, next((p['pts'][0][1] for p in proj.get('pieces', []) if p.get('pts')), None))
    for p in proj.get('pieces', []):
        ts = classify.piece_times(p)
        if any(k in p for k in ('tm', 't0', 'orig', 'hist', 'man', 'okseg')):
            p = dict({k: v for k, v in p.items() if k not in ('tm', 't0', 'orig', 'hist', 'man', 'okseg')}, edited='orig' in p, undo=len(p.get('hist') or []))
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
        if k in ('region', 'overview', 'work', 'days', 'profile', 'photos', 'logo', 'times'): sheets[k] = bool(v)
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
    st['docx'] = st.get('state') == 'done' and os.path.exists(os.path.join(render_dir(pid), 'grafik_dvizheniya.docx'))
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

GEO_CACHE, GEO_LOCK, GEO_LAST = {}, threading.Lock(), [0.0]

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
        if getattr(self, '_rev', None): self.send_header('X-Nitka-Rev', self._rev)      # версия похода — браузер шлёт её обратно при сохранении
        for k, v in h.items(): self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD': self.wfile.write(body)

    def body(self):
        if self._body is not None: return self._body
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY: self.close_connection = True; raise Err(413, 'Файл больше 45 МБ — разбейте его на части или заархивируйте.')
        self._body = self.rfile.read(n) if n else b''
        return self._body

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

    def project(self, pid, need=None):
        """Поход и права на него: владелец, участник (правка/просмотр) или ссылка для просмотра (?s=…, без входа)."""
        if not re.fullmatch(r'[0-9a-f]{12}', pid): raise Err(404, 'Нет такого похода.')
        need = need or ('view' if self.command in ('GET', 'HEAD') else 'edit')
        uid = self.uid()
        if uid and not db().execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone(): uid = None
        role = access(pid, uid)
        if not role and share_of(self._q.get('s'))[0] == pid: role = 'view'      # ссылка даёт смотреть; править — только войдя
        if not role:
            if not uid: raise Err(401, 'Нужно войти.')
            raise Err(404, 'Нет такого похода.')
        if RANK[role] < RANK[need]: raise Err(403, 'Этот поход тебе открыли только для просмотра.' if role == 'view' else 'Это может только владелец похода.')
        row = db().execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        self._uid, self._role, self._rev = uid, role, rev_of(row['updated'])
        return uid, json.loads(row['json']), row

    def save(self, pid, proj, why=None, kind=None, old=None):
        self._rev = save_project(pid, self._uid, proj, why=why, kind=kind or ('op' if why else 'auto'), old=old)

    def handle_one(self):
        self._body = None; self._rev = None; self._q = {}
        try:
            u = urllib.parse.urlsplit(self.path); path = urllib.parse.unquote(u.path); q = dict(urllib.parse.parse_qsl(u.query)); self._q = q
            self.route(self.command if self.command != 'HEAD' else 'GET', path, q)
        except Err as e:
            self.send(e.code, dict(error=e.msg))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            import traceback; traceback.print_exc()
            try: self.send(500, dict(error='Что-то сломалось на сервере. Попробуйте ещё раз.'))
            except Exception: pass
        finally:
            # тело, которое обработчик не прочитал (ошибка, пустой {}), иначе склеится со следующим запросом в том же соединении
            if self._body is None and not self.close_connection:
                try:
                    n = int(self.headers.get('Content-Length') or 0)
                    if 0 < n <= MAX_BODY: self.rfile.read(n)
                    elif n: self.close_connection = True
                except Exception: self.close_connection = True
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
            v = int(max(os.path.getmtime(os.path.join(WEB, n)) for n in ('app.js', 'editor.js', 'planner.js', 'style.css', 'index.html')))
            data = data.replace(b'__V__', str(v).encode())
        self.send(200, data, ctype, cache='no-store' if rel == 'index.html' else ('public, max-age=31536000' if 'v' in q else 'public, max-age=604800'))

    def api(self, m, a, q):
        if a == ['me']:
            u = self.uid(); c = db(); r = c.execute('SELECT * FROM users WHERE id=?', (u,)).fetchone() if u else None
            news = []
            if r:
                if not r['seen'] or time.time() - r['seen'] > 600: c.execute('UPDATE users SET seen=? WHERE id=?', (time.time(), r['id'])); c.commit()      # последний заход
                news = [x for x in changelog() if x['n'] > (r['news_seen'] or 0)]
            return self.send(200, dict(user=dict(name=r['name'], origin=r['origin'] or '', admin=r['key'] in ADMINS) if r else None, need_code=bool(CODE), keep_days=KEEP_DAYS, news=news))
        if a == ['news-seen'] and m == 'POST':      # «понятно» — новости до этой включительно больше не показываются
            uid = self.need_user(); last = max((x['n'] for x in changelog()), default=0)
            db().execute('UPDATE users SET news_seen=? WHERE id=?', (last, uid)); db().commit()
            return self.send(200, dict(ok=True))
        if a == ['login'] and m == 'POST':
            d = self.json(); name = str(d.get('name') or '')
            key, shown = user_key(name)
            if len(key.split(':', 1)[1]) < 2: raise Err(400, 'Напишите имя, ник или телефон — хотя бы две буквы.')
            if len(shown) > 40: raise Err(400, 'Слишком длинно — хватит имени или телефона.')
            c = db(); r = c.execute('SELECT * FROM users WHERE key=?', (key,)).fetchone()
            if CODE and not hmac.compare_digest(str(d.get('code') or '').strip().lower(), CODE.lower()):
                raise Err(403, 'Кодовое слово клуба не подошло.')
            if not r:
                c.execute('INSERT INTO users(key,name,created,seen,news_seen) VALUES(?,?,?,?,?)', (key, shown, time.time(), time.time(), max((x['n'] for x in changelog()), default=0))); c.commit()
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
            rows = db().execute('''SELECT p.id, p.title, p.card, p.created, p.updated, 'owner' AS role, NULL AS owner,
                                     (SELECT COUNT(*) FROM members m WHERE m.project_id=p.id) AS nmem FROM projects p WHERE p.user_id=?
                                   UNION ALL
                                   SELECT p.id, p.title, p.card, p.created, p.updated, m.role, u.name, 0 FROM members m JOIN projects p ON p.id=m.project_id
                                     LEFT JOIN users u ON u.id=p.user_id WHERE m.user_id=?
                                   ORDER BY updated DESC''', (uid, uid)).fetchall()
            out = []
            for r in rows:
                if '"geo"' not in (r['card'] or ''):          # карточка старого образца — пересчитываем один раз
                    pj = db().execute('SELECT json FROM projects WHERE id=?', (r['id'],)).fetchone()
                    card = json.dumps(card_of(json.loads(pj['json'])), ensure_ascii=False)
                    db().execute('UPDATE projects SET card=? WHERE id=?', (card, r['id'])); db().commit()
                    r = dict(r, card=card)
                st = build_status(r['id'])
                out.append(dict(id=r['id'], title=r['title'], updated=r['updated'], card=json.loads(r['card'] or '{}'), role=r['role'], owner=r['owner'], members=r['nmem'],
                                build=dict(state=st.get('state'), expires=st.get('expires'), n=sum(1 for s in st.get('sheets', []) if s.get('state') == 'ok'))))
            return self.send(200, dict(projects=out))
        if len(a) >= 2 and a[0] == 's':      # ссылка «поделиться»
            pid, role = share_of(a[1])
            if not pid: raise Err(404, 'Ссылка больше не работает — попроси новую у того, кто ею поделился.')
            pr = db().execute('SELECT p.title, p.card, u.name FROM projects p LEFT JOIN users u ON u.id=p.user_id WHERE p.id=?', (pid,)).fetchone()
            if not pr: raise Err(404, 'Этого похода больше нет.')
            uid = self.uid(); have = access(pid, uid) if uid else None
            if len(a) == 2 and m == 'GET':
                return self.send(200, dict(pid=pid, role=role, title=pr['title'], owner=pr['name'] or '', card=json.loads(pr['card'] or '{}'), have=have))
            if a[2:] == ['join'] and m == 'POST':
                uid = self.need_user(); have = access(pid, uid)
                if have and RANK[have] >= RANK[role]: return self.send(200, dict(pid=pid, role=have))
                db().execute('INSERT INTO members(project_id,user_id,role,added) VALUES(?,?,?,?) ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role',
                             (pid, uid, role, time.time())); db().commit()
                return self.send(200, dict(pid=pid, role=role))
            raise Err(404, 'Нет такого адреса.')
        if a == ['catalog'] and m == 'GET':      # перевалы и вершины каталога в кадре — для планировщика
            self.need_user()
            try: la0, lo0, la1, lo1 = [float(x) for x in q['bbox'].split(',')]
            except (KeyError, ValueError): raise Err(400, 'Нужен охват карты.')
            if (la1 - la0) * (lo1 - lo0) > 6: return self.send(200, dict(items=[], wide=True))
            try: cat = suggest.catalog()
            except RuntimeError as e: raise Err(503, str(e))
            out = []
            for c in cat:
                try: la, lo = float(c['latlon'][0]), float(c['latlon'][1])
                except Exception: continue
                if not (la0 <= la <= la1 and lo0 <= lo <= lo1): continue
                summit = bool(c.get('is_summit')); raw = str(c.get('name') or '').strip()
                try: h = int(float(c.get('elevation'))) if c.get('elevation') else None
                except Exception: h = None
                out.append(dict(id=str(c.get('id') or f'{la:.5f},{lo:.5f}'), kind='peak' if summit else 'pass', name=raw,
                                title=(('в. ' if summit else 'пер. ') + raw) if raw else ('вершина' if summit else 'перевал'),
                                grade=suggest.GR.get(str(c.get('grade_eng') or '').lower()), h=h, lat=round(la, 6), lon=round(lo, 6),
                                note='; '.join(x for x in [str(c.get('connects') or '').strip(), ('склоны: ' + str(c['slopes']).strip()) if c.get('slopes') else ''] if x)[:200],
                                alt=str(c.get('altnames') or '')[:80]))
                if len(out) >= 1500: break
            return self.send(200, dict(items=out), cache='private, max-age=3600')
        if a == ['geocode'] and m == 'GET':      # «куда идём?» — поиск места по OpenStreetMap (Nominatim: не чаще раза в секунду)
            self.need_user(); text = re.sub(r'\s+', ' ', q.get('q') or '').strip()[:120]
            if len(text) < 2: return self.send(200, dict(items=[]))
            if text.lower() in GEO_CACHE: return self.send(200, dict(items=GEO_CACHE[text.lower()]))
            with GEO_LOCK:
                wait = 1.05 - (time.time() - GEO_LAST[0])
                if wait > 0: time.sleep(wait)
                GEO_LAST[0] = time.time()
                try:
                    url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(dict(q=text, format='jsonv2', limit=7, **{'accept-language': 'ru'}))
                    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': mapkit.UA}), timeout=12) as r: res = json.loads(r.read())
                except Exception: raise Err(503, 'Поиск мест сейчас не отвечает — найди место на карте сам.')
            items = []
            for x in res:
                try: bb = [float(v) for v in x.get('boundingbox') or []]
                except ValueError: bb = []
                items.append(dict(name=x.get('display_name', '')[:160], lat=float(x['lat']), lon=float(x['lon']), kind=x.get('type') or '', bbox=bb if len(bb) == 4 else None))
            if len(GEO_CACHE) > 2000: GEO_CACHE.clear()
            GEO_CACHE[text.lower()] = items
            return self.send(200, dict(items=items))
        if a == ['elev'] and m == 'POST':      # высоты по рельефу — профиль в планировщике
            self.need_user()
            try: pts = [[float(x[0]), float(x[1])] for x in (self.json().get('pts') or [])][:8000]
            except (TypeError, ValueError, IndexError): raise Err(400, 'Неверные точки.')
            if not pts: return self.send(200, dict(ele=[]))
            try: return self.send(200, dict(ele=planner.elevations(pts)))
            except RuntimeError as e: raise Err(503, str(e))
        if a == ['projects'] and m == 'POST':
            uid = self.need_user(); d = self.json()
            if db().execute('SELECT COUNT(*) FROM projects WHERE user_id=?', (uid,)).fetchone()[0] >= 300: raise Err(400, 'Слишком много походов — удалите ненужные.')
            pid = secrets.token_hex(6); proj = classify.new_project(str(d.get('title') or '').strip()[:120])
            if d.get('kind'): proj['meta']['kind'] = str(d['kind'])[:30]
            if proj['meta']['kind'] == 'горный': proj['meta']['kcoef'] = 1.2
            save_project(pid, uid, proj, create=True)
            return self.send(200, dict(id=pid))
        if len(a) >= 2 and a[0] == 'projects':
            pid = a[1]; sub = a[2] if len(a) > 2 else ''
            need = ('view' if m in ('GET', 'HEAD') and sub not in ('share', 'history', 'route') else 'owner' if sub == 'share' and m == 'POST'
                    else 'view' if (not sub and m == 'DELETE') or sub == 'copy' else 'edit')
            uid, proj, row = self.project(pid, need); role = self._role
            if not sub and m == 'GET':
                shared = role != 'owner' or bool(db().execute('SELECT 1 FROM members WHERE project_id=? LIMIT 1', (pid,)).fetchone())
                return self.send(200, dict(project=for_client(proj), build=build_status(pid), updated=row['updated'], rev=self._rev, role=role,
                                           owner=user_name(row['user_id']), shared=shared))
            if sub == 'rev' and m == 'GET':      # не поменял ли поход кто-то ещё (общий поход открыт у нескольких)
                return self.send(200, dict(rev=self._rev, by=user_name(row['by']), mine=row['by'] == uid))
            if not sub and m == 'PUT':
                want = self.headers.get('X-Nitka-Rev')
                if want and want != self._rev:      # пока человек правил, поход поменяли — его старая копия затёрла бы чужие правки
                    who = user_name(row['by'])
                    return self.send(409, dict(conflict=True, by=who, error=('Поход только что поменяли в другой вкладке' if row['by'] == uid else f'Поход только что поменял(а) {who}')
                                               + ' — твоя последняя правка не сохранилась. Показываю свежую версию, повтори её.'))
                old = json.loads(row['json'])
                proj = merge_client(proj, self.json()); self.save(pid, proj, old=old)
                return self.send(200, dict(ok=True, card=card_of(proj), rev=self._rev))
            if not sub and m == 'DELETE':
                if role != 'owner':      # участник убирает чужой поход из своего списка
                    db().execute('DELETE FROM members WHERE project_id=? AND user_id=?', (pid, uid)); db().commit()
                    return self.send(200, dict(ok=True, left=True))
                c = db()
                for t in ('projects', 'members', 'shares', 'revisions'): c.execute(f'DELETE FROM {t} WHERE {"id" if t == "projects" else "project_id"}=?', (pid,))
                c.commit()
                if pid not in ACTIVE: shutil.rmtree(render_dir(pid), ignore_errors=True)
                return self.send(200, dict(ok=True))
            if sub == 'copy' and m == 'POST':
                if not uid: raise Err(401, 'Нужно войти.')
                nid = secrets.token_hex(6); proj['meta']['title'] = (proj['meta'].get('title') or '') + ' (копия)'
                save_project(nid, uid, proj, create=True, why='копия похода'); return self.send(200, dict(id=nid))
            if sub == 'share' and m == 'GET':
                return self.send(200, share_state(pid, role))
            if sub == 'share' and m == 'POST':      # только владелец: ссылки и участники
                d = self.json(); c = db(); act = d.get('action')
                if act == 'link':
                    r_ = d.get('role') if d.get('role') in ('view', 'edit') else 'view'; op = d.get('op')
                    if op in ('off', 'reset'): c.execute('DELETE FROM shares WHERE project_id=? AND role=?', (pid, r_))
                    if op in ('on', 'reset') and not c.execute('SELECT 1 FROM shares WHERE project_id=? AND role=?', (pid, r_)).fetchone():
                        c.execute('INSERT INTO shares(token,project_id,role,created) VALUES(?,?,?,?)', (secrets.token_urlsafe(12), pid, r_, time.time()))
                elif act == 'member':
                    try: who = int(d.get('user'))
                    except (TypeError, ValueError): raise Err(400, 'Не понял, кого.')
                    if d.get('role') == 'remove': c.execute('DELETE FROM members WHERE project_id=? AND user_id=?', (pid, who))
                    elif d.get('role') in ('view', 'edit'): c.execute('UPDATE members SET role=? WHERE project_id=? AND user_id=?', (d['role'], pid, who))
                else: raise Err(400, 'Неизвестное действие.')
                c.commit(); return self.send(200, share_state(pid, role))
            if sub == 'history' and m == 'GET' and len(a) == 3:      # журнал: версии похода
                rows = db().execute('''SELECT r.id, r.ts, r.t0, r.kind, r.what, r.km, r.user_id, u.name FROM revisions r LEFT JOIN users u ON u.id=r.user_id
                                       WHERE r.project_id=? ORDER BY r.id DESC LIMIT ?''', (pid, REV_KEEP)).fetchall()
                return self.send(200, dict(items=[dict(id=r['id'], ts=r['ts'], t0=r['t0'], kind=r['kind'], what=json.loads(r['what'] or '[]'), km=r['km'],
                                                       who=r['name'] or '', me=r['user_id'] == uid) for r in rows], keep=REV_KEEP))
            if sub == 'history' and m == 'GET' and len(a) == 4:      # версия — на карте: нитки и точки
                r = db().execute('SELECT data FROM revisions WHERE id=? AND project_id=?', (a[3], pid)).fetchone()
                if not r: raise Err(404, 'Этой версии уже нет.')
                v = unpack(r['data'])
                lines = [dict(role=p.get('role'), day=p.get('day'), pts=[[round(q[0], 5), round(q[1], 5)] for q in p['pts'][::max(1, len(p['pts']) // 400)]])
                         for p in v.get('pieces', []) if p.get('role') != 'skip' and p.get('pts')]
                return self.send(200, dict(lines=lines, points=[dict(lat=q['lat'], lon=q['lon'], type=q.get('type'), name=q.get('name', '')) for q in v.get('points', []) if q.get('type') in ('camp', 'pass', 'peak')]))
            if sub == 'history' and m == 'POST':      # вернуть поход к версии — сам возврат тоже версия, его можно отменить
                try: rid = int(self.json().get('id'))
                except (TypeError, ValueError): raise Err(400, 'Не указана версия.')
                r = db().execute('SELECT data, ts FROM revisions WHERE id=? AND project_id=?', (rid, pid)).fetchone()
                if not r: raise Err(404, 'Этой версии уже нет.')
                v = unpack(r['data']); old = json.loads(row['json'])
                self.save(pid, v, why=f'возврат к версии от @{int(r["ts"])}', kind='restore', old=old)
                return self.send(200, dict(project=for_client(v), rev=self._rev, msg='Поход вернулся к выбранной версии. Передумал — верни следующую версию в журнале.'))
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
                self.save(pid, proj, why=f'загружено: {name}')
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
                self.save(pid, proj, why=msg.lower() if len(msg) < 60 else 'варианты маршрута')
                return self.send(200, dict(project=for_client(proj), msg=msg))
            if sub == 'plan' and m == 'POST':      # маршрут из планировщика → куски по дням, ночёвки, перевалы
                r = planner.apply(proj, self.json().get('plan') or {})
                if r['ok']: self.save(pid, proj, why='маршрут из планировщика')
                return self.send(200, dict(project=for_client(proj), **r))
            if sub == 'day-radial' and m == 'POST':      # весь день — радиальный выход (куски склеиваются в один)
                try: day = int(self.json().get('day'))
                except (TypeError, ValueError): raise Err(400, 'Не указан день.')
                r = classify.day_radial(proj, day)
                if r['ok']: self.save(pid, proj, why=f'день {day} — радиалка')
                return self.send(200, dict(project=for_client(proj), **r))
            if sub == 'check' and m == 'GET':      # что не так с треком: выбросы, разрывы
                return self.send(200, dict(items=trackfix.check(proj)))
            if sub == 'track-edit' and m == 'POST':      # вырезать выброс, перерисовать участок, отменить, вернуть как было; из редактора — кусок целиком
                d = self.json()
                act = d.get('action')
                r = (trackfix.replace(proj, d) if act == 'replace' else trackfix.replace_many(proj, d) if act == 'replace_many'
                     else trackfix.accept(proj, d) if act == 'ok'
                     else trackfix.untangle(proj, d.get('piece'), d.get('i0'), d.get('i1'), d.get('piece2')) if act == 'untangle' else trackfix.edit(proj, d))
                TE = {'replace': 'редактор трека', 'replace_many': 'редактор трека', 'ok': '«это не ошибка» в проверке трека', 'untangle': 'спрямлены клубки',
                      'cut': 'вырезан выброс', 'redraw': 'перерисован участок', 'undo': 'отменена правка трека', 'reset': 'трек — как при загрузке'}
                if r['ok']: self.save(pid, proj, why=TE.get(act, 'правка трека'))
                return self.send(200, dict(project=for_client(proj), **r))
            if sub == 'route' and m == 'GET':      # путь по тропам между двумя точками (BRouter) — для перерисовки и вариантов
                try: a = [float(x) for x in q['a'].split(',')]; b = [float(x) for x in q['b'].split(',')]
                except (KeyError, ValueError): raise Err(400, 'Нужны точки a и b.')
                if dist_ok(a, b) > 30000: raise Err(400, 'Слишком далеко для одного отрезка — поставь промежуточные точки.')
                pts = trackfix.route(a, b, proj['meta'].get('kind') or '')
                return self.send(200, dict(ok=bool(pts), pts=pts or [], msg='' if pts else 'Тропу не нашёл — соединю прямой.'))
            if sub == 'export.gpx' and m == 'GET':      # чистый GPX с прореживанием
                try: step = max(0, min(200, int(q.get('step') or 0)))
                except ValueError: step = 0
                data = trackfix.export_gpx(proj, step, 'one' if q.get('split') == 'one' else 'day').encode('utf-8')
                title = re.sub(r'[\\/:*?"<>|]+', ' ', proj['meta'].get('title') or 'Поход').strip()
                return self.send(200, data, 'application/gpx+xml', headers={'Content-Disposition': "attachment; filename*=UTF-8''" + urllib.parse.quote(f'{title}{" — каждые " + str(step) + " м" if step else ""}.gpx')})
            if sub == 'chrono' and m == 'POST':      # хронометраж → записи на треке (ещё не точки: турист проверяет и принимает)
                text = str(self.json().get('text') or '')[:60000]
                return self.send(200, chrono.place(proj, text))
            if sub == 'source-delete' and m == 'POST':
                classify.remove_source(proj, str(self.json().get('id') or '')); self.save(pid, proj, why='убран файл')
                return self.send(200, dict(project=for_client(proj)))
            if sub == 'cut' and m == 'POST':
                d = self.json()
                try: lat, lon = float(d['lat']), float(d['lon'])
                except (KeyError, TypeError, ValueError): raise Err(400, 'Не понял, где резать.')
                r = classify.cut_piece(proj, str(d.get('piece') or ''), lat, lon)
                if r['ok']: self.save(pid, proj, why='нитка разрезана')
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
                if n: self.save(pid, proj, why='куски по ходу маршрута')
                return self.send(200, dict(project=for_client(proj), changed=n))
            if sub == 'split-by-camps' and m == 'POST':
                r = classify.split_by_camps(proj)
                if r['ok']: self.save(pid, proj, why='нитка разрезана по ночёвкам')
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
        uid = self.uid(); now = time.time()
        if not uid or not db().execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone():
            if not share_of((q or {}).get('s'))[0]: raise Err(401, 'Нужно войти.')
            uid = 's:' + q['s']      # смотрят по ссылке — свой счётчик запросов
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
        if not re.fullmatch(r'[A-Za-z0-9_\-]+(\.prev|\.thumb)?\.(png|jpg|pdf|docx)', name): raise Err(404, 'нет')
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
            nice = f'{title} — карты.pdf' if name == 'karty.pdf' else f'{title} — график движения.docx' if name == 'grafik_dvizheniya.docx' else f'{title} — {name}'
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
            if os.path.exists(os.path.join(d, 'grafik_dvizheniya.docx')): z.write(os.path.join(d, 'grafik_dvizheniya.docx'), 'График движения.docx')
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
