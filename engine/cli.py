"""Сборка листов проекта в отдельном процессе.

python3 -m engine.cli build <project.json> <out_dir> [--only id,id]
Пишет <out_dir>/status.json после каждого листа; в конце — summary.json и общий PDF.
"""
import io, json, os, sys, time, traceback
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

def _write(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)

def previews(png):
    im = Image.open(png).convert('RGB')
    for suffix, w, q in (('.prev.jpg', 1500, 84), ('.thumb.jpg', 520, 80)):
        t = im.copy(); t.thumbnail((w, w * 3), Image.LANCZOS); t.save(png[:-4] + suffix, 'JPEG', quality=q, optimize=True)
    return im.size

def make_pdf(out, sheets, title):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    path = os.path.join(out, 'karty.pdf')
    c = canvas.Canvas(path, pagesize=A4); c.setTitle(f'{title} — карты'); c.setAuthor('')
    n = 0
    for i, s in enumerate(sheets):
        f = os.path.join(out, s['file'])
        if not os.path.exists(f): continue
        im = Image.open(f).convert('RGB')
        buf = io.BytesIO(); im.save(buf, 'JPEG', quality=92, subsampling=0, optimize=True); buf.seek(0)
        w, h = im.width / 300 * 72, im.height / 300 * 72          # масштаб 100 %
        pw, ph = landscape(A4) if w > A4[0] else A4
        k = min(1.0, pw / w, ph / h)
        c.setPageSize((pw, ph))
        c.drawImage(ImageReader(buf), (pw - w * k) / 2, (ph - h * k) / 2, w * k, h * k)
        c.bookmarkPage(f'm{i}'); c.addOutlineEntry(s['title'], f'm{i}', 0)
        c.showPage(); n += 1
    if n: c.showOutline(); c.save()
    return path if n else None

def build(proj_path, out, only=None):
    from .model import Trip
    from .sheets import Builder
    os.makedirs(out, exist_ok=True)
    st_path = os.path.join(out, 'status.json')
    status = dict(state='running', stage='Читаю треки и считаю высоты', done=0, total=0, sheets=[], started=time.time(), error=None)
    _write(st_path, status)
    try:
        proj = json.load(open(proj_path, encoding='utf-8'))
        T = Trip(proj)
        summ = T.summary()
        _write(os.path.join(out, 'summary.json'), summ)
        B = Builder(T, out, proj.get('sheets') or {})
        plan = B.plan()
        old = {}
        if only:
            try: old = {s['id']: s for s in json.load(open(os.path.join(out, 'sheets.json'), encoding='utf-8'))}
            except Exception: old = {}
        todo = [p for p in plan if not only or p['id'] in only]
        status.update(total=len(todo), sheets=[dict(p, state='wait') for p in plan], summary=summ)
        for s in status['sheets']:
            if only and s['id'] not in only and s['id'] in old and os.path.exists(os.path.join(out, s['file'])): s.update(old[s['id']])
        _write(st_path, status)
        for p in todo:
            s = next(x for x in status['sheets'] if x['id'] == p['id'])
            s['state'] = 'run'; status['stage'] = p['title']; _write(st_path, status)
            t = time.time()
            try:
                r = B.render(p['id'])
                px = previews(r['file'])
                s.update(state='ok', w_cm=round(r['w_cm'], 1), h_cm=round(r['h_cm'], 1), px=px, note=r.get('note', ''), frame=r.get('frame'), size=os.path.getsize(r['file']), sec=round(time.time() - t, 1), ver=int(time.time()))
            except Exception as e:
                traceback.print_exc()
                s.update(state='err', error=str(e)[:300])
            status['done'] += 1; _write(st_path, status)
        ok = [s for s in status['sheets'] if s.get('state') == 'ok']
        status['stage'] = 'Собираю общий PDF'; _write(st_path, status)
        try: status['pdf'] = bool(make_pdf(out, ok, proj.get('meta', {}).get('title') or 'Поход'))
        except Exception: traceback.print_exc(); status['pdf'] = False
        _write(os.path.join(out, 'sheets.json'), status['sheets'])
        status.update(state='done' if ok else 'error', stage='Готово', finished=time.time(), error=None if ok else 'Ни один лист не собрался')
    except Exception as e:
        traceback.print_exc()
        status.update(state='error', error=str(e)[:500], finished=time.time())
    _write(st_path, status)
    return status

if __name__ == '__main__':
    a = sys.argv[1:]
    if a and a[0] == 'build':
        only = None
        if '--only' in a: only = set(a[a.index('--only') + 1].split(','))
        st = build(a[1], a[2], only)
        print(st['state'], st.get('error') or '', [(s['id'], s.get('state'), s.get('sec')) for s in st['sheets']])
        sys.exit(0 if st['state'] == 'done' else 1)
    print(__doc__)
