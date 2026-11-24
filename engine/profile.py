"""Высотный профиль: заливка по дням, подписи ВСЕХ ночёвок (№, дата, высота) и перевалов/вершин
(название, категория трудности, высота). Подписи расставляются автоматически и не перекрываются.

draw(img, box, segs, marks, camps, ...) рисует профиль в прямоугольнике box листа img и возвращает новый img.
  segs  — куски нитки по дням: dict(day, x=[км], z=[м], color, lines=['день 5', '10.08', '9,1 км'])
  marks — объекты на нитке: dict(x, z, kind='pass'|'peak'|'obst', title, grade, h, label=True|False)
  camps — ночёвки/старт/финиш: dict(x, z, kind='camp'|'start'|'finish', lab='Н6', text='11.08 · 3700 м')
"""
import math
from PIL import Image, ImageDraw
from .mapkit import font, mm, INK, rot_text

GRID = (208, 212, 218)
BROWN = (95, 40, 10)
CAMP_INK = (20, 45, 110)
TENT = (255, 205, 0)

# ---------------------------------------------------------------- текстовые блоки
def _text_img(runs_lines, halo=3, gap=1.12):
    """runs_lines: строки, каждая — список отрезков (text, font, fill, chip_fill|None). Возвращает RGBA-картинку."""
    tmp = ImageDraw.Draw(Image.new('RGBA', (8, 8)))
    sizes = []; pad = halo + 2
    for runs in runs_lines:
        w = 0; h = 0
        for t, f, fill, chip in runs:
            tw = tmp.textlength(t, font=f) + (f.size * 0.7 if chip else 0)
            w += tw; h = max(h, f.size)
        sizes.append((w, h))
    W = int(max(w for w, _ in sizes)) + 2 * pad
    H = int(sum(h * gap for _, h in sizes)) + 2 * pad
    im = Image.new('RGBA', (W, H), (255, 255, 255, 0)); d = ImageDraw.Draw(im)
    y = pad
    for runs, (w, h) in zip(runs_lines, sizes):
        x = pad; cy = y + h * gap / 2
        for t, f, fill, chip in runs:
            tw = tmp.textlength(t, font=f)
            if chip:
                cw = tw + f.size * 0.7
                d.rounded_rectangle((x, cy - f.size * 0.62, x + cw - f.size * 0.1, cy + f.size * 0.62), radius=int(f.size * 0.28), fill=chip)
                d.text((x + (cw - f.size * 0.1) / 2, cy), t, font=f, fill=(255, 255, 255), anchor='mm')
                x += cw
            else:
                d.text((x, cy), t, font=f, fill=fill, anchor='lm', stroke_width=halo, stroke_fill=(255, 255, 255))
                x += tw
        y += h * gap
    return im

def mark_block(m, pt, grade_col, two_lines=True):
    fb = font(pt, True, cond=True); fr = font(pt, cond=True)
    sub = []
    if m.get('grade'): sub.append((m['grade'], font(pt - 0.4, True, cond=True), None, grade_col.get(m['grade'], grade_col[None])))
    if m.get('h'): sub.append(((' ' if m.get('grade') else '') + f"{m['h']} м", fr, INK, None))
    title = [(m['title'], fb, (190, 0, 0) if m['kind'] == 'obst' else (m.get('color') if m['kind'] == 'poi' and m.get('color') else BROWN), None)]
    if not sub: return _text_img([title])
    return _text_img([title, sub]) if two_lines else _text_img([title + [('  ', fr, INK, None)] + sub])

def camp_block(c, pt):
    fb = font(pt, True, cond=True); fr = font(pt, cond=True)
    col = {'camp': CAMP_INK, 'start': (0, 115, 50), 'finish': INK}[c['kind']]
    return _text_img([[(c['lab'], fb, col, None), (' · ' + c['text'], fr, col, None)]])

def _hit(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

def _spread(xs, ws, lo, hi, gap=0.0):
    """Раздвигает центры подписей (ширины ws) до отсутствия перекрытий при минимальном смещении."""
    n = len(xs)
    order = sorted(range(n), key=lambda i: xs[i])
    p = [min(max(xs[i], lo + ws[i] / 2), hi - ws[i] / 2) for i in order]; w = [ws[i] for i in order]
    for _ in range(600):
        moved = False
        for i in range(n - 1):
            ov = (p[i] + w[i] / 2 + gap) - (p[i + 1] - w[i + 1] / 2)
            if ov > 0.5:
                p[i] -= ov / 2; p[i + 1] += ov / 2; moved = True
        for i in range(n):
            c = min(max(p[i], lo + w[i] / 2), hi - w[i] / 2)
            if c != p[i]: p[i] = c; moved = True
        if not moved: break
    out = list(xs)
    for i, k in enumerate(order): out[k] = p[i]
    return out

# ---------------------------------------------------------------- профиль
def draw(img, box, segs, marks, camps, *, grade_col, orient='v', title=None, note=None, ylim=None, ystep=None,
         xstep=None, day_strip=True, pt=6.0, x_label='км от начала маршрута', bounds=True, head_min=0, dry=False, bands=None, vex_max=None):
    x0, y0, w, h = box
    X0 = segs[0]['x'][0]; X1 = segs[-1]['x'][-1]
    zmin = min(min(s['z']) for s in segs); zmax = max(max(s['z']) for s in segs)
    L = x0 + mm(11.5); R = x0 + w - mm(3.5)
    T = y0 + (mm(4.6) if title else 0) + (mm(3.4) if note else 0) + mm(1.0)
    strip_h = mm(12.0) if day_strip else 0
    B = y0 + h - mm(4.6) - strip_h - mm(3.4)
    fx = lambda v: L + (R - L) * (v - X0) / (X1 - X0)
    lo = ylim[0] if ylim else math.floor((zmin - 60) / 100) * 100
    zhi = ylim[1] if ylim else zmax
    if vex_max:
        # равнину не вытягиваем в «горы»: вертикальный масштаб крупнее горизонтального не больше чем в vex_max раз
        need = (B - T - max(mm(6), head_min)) * (X1 - X0) * 1000 / ((R - L) * vex_max)
        if zhi - lo < need: zhi = lo + need
    if not ystep:
        span = zhi - lo; ystep = 100 if span <= 700 else (200 if span <= 1500 else 500)

    # все подписи — единый набор блоков: объекты (перевалы, вершины, препятствия) и ночёвки
    items = [dict(x=m['x'], z=m['z'], img=mark_block(m, pt, grade_col), col=(120, 90, 70)) for m in marks if m.get('label', True)]
    items += [dict(x=c['x'], z=c['z'], img=camp_block(c, pt), col=(90, 110, 160), camp=True) for c in camps]
    if orient == 'v':
        for it in items: it['img'] = it['img'].transpose(Image.ROTATE_90)
    items.sort(key=lambda it: (it['x'], 0 if it.get('camp') else 1))

    def layout(head):
        fy = lambda v: B - (B - T - head) * (v - lo) / (zhi - lo)
        cx = []; cy = []
        for s in segs:
            cx += [fx(v) for v in s['x']]; cy += [fy(v) for v in s['z']]
        def top(xa, xb):          # самая высокая точка кривой на отрезке (минимальный y)
            ys = [yy for xx, yy in zip(cx, cy) if xa <= xx <= xb]
            return min(ys) if ys else B
        if orient == 'v':
            lx = _spread([fx(it['x']) for it in items], [it['img'].width for it in items], L + mm(0.4), R - mm(0.4), gap=mm(0.15))
        else:
            lx = [min(max(fx(it['x']), L + it['img'].width / 2 + 2), R - it['img'].width / 2 - 2) for it in items]
        placed = []
        for it, cxl in zip(items, lx):
            b = it['img']; py = fy(it['z'])
            by = min(top(cxl - b.width / 2 - 3, cxl + b.width / 2 + 3), py) - mm(2.6) - b.height
            r = [cxl - b.width / 2, by, cxl + b.width / 2, by + b.height]
            for _ in range(20):
                hit = next((o for o in placed if _hit(r, o)), None)
                if not hit: break
                r[1] = hit[1] - mm(0.4) - b.height; r[3] = r[1] + b.height
            placed.append(tuple(r))
        over = T + mm(0.8) - min(r[1] for r in placed) if placed else 0
        return fy, placed, over

    head = max(mm(6), head_min)
    for _ in range(8):
        fy, placed, over = layout(head)
        if over <= 0: break
        head = min(head + over + 2, (B - T) * 0.62)
    hi_eff = lo + (zhi - lo) * (B - T) / (B - T - head)
    grid_top = math.ceil(zhi / ystep) * ystep
    if dry: return img, dict(head=head, zhi=zhi)

    d = ImageDraw.Draw(img)
    fa = font(5.8, cond=True)
    if title: d.text((x0, y0), title, font=font(7.4, True, cond=True), fill=INK, anchor='la')
    if note: d.text((x0, y0 + (mm(4.2) if title else 0)), note, font=font(5.8, cond=True), fill=(80, 80, 80), anchor='la')
    # сетка
    d.rectangle((L, T, R, B), fill=(255, 255, 255))
    v = math.ceil(lo / ystep) * ystep
    while v <= min(hi_eff, grid_top) + 1e-6:
        y = fy(v)
        if y >= T - 1:
            d.line((L, y, R, y), fill=GRID, width=2)
            d.text((L - mm(1), y), str(int(v)), font=fa, fill=INK, anchor='rm')
        v += ystep
    # ось X подписана километражем (c): на обратном пути радиалок он не растёт, делений там нет
    xs = [v for s in segs for v in s['x']]; cs = [v for s in segs for v in s.get('c', s['x'])]
    C0, C1 = cs[0], cs[-1]
    if not xstep:
        span = C1 - C0; xstep = 1 if span <= 14 else (2 if span <= 30 else (5 if span <= 90 else 10))
    v = math.ceil(C0 / xstep - 1e-9) * xstep
    while v <= C1 + 1e-6:
        i = next((j for j, cv in enumerate(cs) if cv >= v - 1e-9), len(cs) - 1)
        xv = xs[i] if i == 0 or cs[i] == cs[i - 1] else xs[i - 1] + (xs[i] - xs[i - 1]) * (v - cs[i - 1]) / (cs[i] - cs[i - 1])
        d.line((fx(xv), T, fx(xv), B), fill=(232, 234, 238), width=2)
        d.line((fx(xv), B, fx(xv), B + mm(1)), fill=INK, width=2)
        d.text((fx(xv), B + mm(1.3)), f'{v:g}', font=fa, fill=INK, anchor='ma'); v += xstep
    rot_text(img, 'высота, м', fa, (x0 + mm(1.7), (T + B) / 2), 90)
    # заливка и линия по дням
    lay = Image.new('RGBA', (int(R - L) + 4, int(B - T) + 4), (0, 0, 0, 0)); ld = ImageDraw.Draw(lay)
    ox, oy = int(L) - 2, int(T) - 2
    for s in segs:
        poly = [(fx(s['x'][0]) - ox, B - oy)] + [(fx(a) - ox, fy(b) - oy) for a, b in zip(s['x'], s['z'])] + [(fx(s['x'][-1]) - ox, B - oy)]
        ld.polygon(poly, fill=s['color'] + (52,))
    veil = []                                   # обратный путь радиальных выходов — бледнее
    for s in segs:
        fl = s.get('ret'); i = 0
        while fl and i < len(fl):
            if fl[i]:
                j = i
                while j + 1 < len(fl) and fl[j + 1]: j += 1
                xa, xb = fx(s['x'][max(i - 1, 0)]), fx(s['x'][j])
                ld.rectangle((xa - ox, 0, xb - ox, lay.height), fill=(255, 255, 255, 0))
                tint = Image.new('RGBA', lay.size, (0, 0, 0, 0))
                ImageDraw.Draw(tint).polygon([(xa - ox, B - oy)] + [(fx(a) - ox, fy(b) - oy) for a, b in zip(s['x'][max(i - 1, 0):j + 1], s['z'][max(i - 1, 0):j + 1])] + [(xb - ox, B - oy)], fill=s['color'] + (20,))
                lay.alpha_composite(tint); ld = ImageDraw.Draw(lay)
                veil.append((xa, xb)); i = j + 1
            else: i += 1
    reg = img.crop((ox, oy, ox + lay.width, oy + lay.height)).convert('RGBA')
    img.paste(Image.alpha_composite(reg, lay).convert('RGB'), (ox, oy))
    d = ImageDraw.Draw(img)
    for xa, xb, col in bands or []:               # характер пути — полоска вдоль оси расстояний
        xa, xb = max(L, fx(xa)), min(R, fx(xb))
        if xb - xa >= 2: d.rectangle((xa, B - mm(1.5), xb, B), fill=col, outline=tuple(int(c * 0.6) for c in col), width=1)
    for s in segs:
        d.line([(fx(a), fy(b)) for a, b in zip(s['x'], s['z'])], fill=s['color'], width=int(mm(0.5)), joint='curve')
    # границы дней
    if bounds:
        for s in segs[1:]:
            xb = fx(s['x'][0]); yb = fy(s['z'][0])
            yy = yb
            while yy < B:
                d.line((xb, yy, xb, min(B, yy + mm(0.9))), fill=(110, 110, 110), width=2); yy += mm(1.7)
    # переезды: между днями и внутри дня (нитка прерывается, километры не идут)
    fj = font(5.0, cond=True, italic=True)
    jumps = [s['x'][0] for s in segs[1:] if s.get('transfer')] + [gx for s in segs for gx in s.get('gapx', [])]
    for gx in jumps:
        xb = fx(gx)
        for yy in range(int(T) + 4, int(B) - 2, int(mm(1.3))):
            d.line((xb, yy, xb, min(B, yy + mm(0.7))), fill=(90, 90, 90), width=2)
        rot_text(img, 'переезд', fj, (xb + mm(1.3), B - mm(6.5)), 90, fill=(80, 80, 80))
    d = ImageDraw.Draw(img)
    d.rectangle((L, T, R, B), outline=INK, width=2)
    fv = font(5.2, cond=True, italic=True)
    for xa, xb in veil:
        for yy in range(int(T) + 4, int(B) - 2, int(mm(1.5))):
            d.line((xa, yy, xa, min(B, yy + mm(0.6))), fill=(150, 150, 150), width=2); d.line((xb, yy, xb, min(B, yy + mm(0.6))), fill=(150, 150, 150), width=2)
        if xb - xa > d.textlength('обратно', font=fv) + 6: d.text(((xa + xb) / 2, B - mm(1.6)), 'обратно', font=fv, fill=(90, 90, 90), anchor='mm')
    d.text((R, B + mm(4.3)), x_label, font=fa, fill=(70, 70, 70), anchor='ra')
    # полоса дней
    if day_strip:
        ys = B + mm(8.0)
        for s in segs:
            xa, xb = fx(s['x'][0]) + 2, fx(s['x'][-1]) - 2
            tint = tuple(int(c * 0.16 + 255 * 0.84) for c in s['color'])
            d.rounded_rectangle((xa, ys, xb, ys + strip_h - mm(0.6)), radius=int(mm(0.8)), fill=tint, outline=s['color'], width=2)
            lines = s.get('lines', []); lh = mm(2.75); yy = ys + mm(0.35)
            for i, t in enumerate(lines):
                f = font(5.7 if i else 6.0, i == 0, cond=True)
                if d.textlength(t, font=f) <= xb - xa - 4:
                    d.text(((xa + xb) / 2, yy + lh / 2), t, font=f, fill=s['color'] if i == 0 else INK, anchor='mm')
                yy += lh
    # отметки объектов на кривой
    for m in marks:
        px, py = fx(m['x']), fy(m['z']); col = grade_col.get(m.get('grade'), grade_col[None]); r = mm(0.95)
        if not m.get('label', True): r = mm(0.6)
        if m['kind'] == 'peak':
            d.polygon([(px, py - r * 1.5), (px + r * 1.3, py + r * 0.8), (px - r * 1.3, py + r * 0.8)], fill=col, outline=(255, 255, 255))
        elif m['kind'] == 'obst':
            d.polygon([(px, py - r * 1.3), (px + r * 1.3, py), (px, py + r * 1.3), (px - r * 1.3, py)], fill=(200, 0, 0), outline=(255, 255, 255))
        elif m['kind'] == 'poi':
            pc = m.get('color') or (70, 70, 70)
            d.rectangle((px - r * 0.9, py - r * 0.9, px + r * 0.9, py + r * 0.9), fill=pc, outline=(255, 255, 255), width=2)
        else:
            d.ellipse((px - r, py - r, px + r, py + r), fill=col, outline=(255, 255, 255), width=2)
    # выноски и подписи
    for it, r in zip(items, placed):
        px, py = fx(it['x']), fy(it['z'])
        d.line((px, py - mm(1.3), (r[0] + r[2]) / 2, r[3] - 3), fill=it['col'], width=2)
    back = Image.new('RGBA', img.size, (0, 0, 0, 0)); bd = ImageDraw.Draw(back)      # полупрозрачные подложки под подписи
    for it, r in zip(items, placed):
        bd.rounded_rectangle((r[0] + 2, r[1] + 2, r[2] - 2, r[3] - 2), radius=int(mm(0.6)), fill=(255, 255, 255, 205))
    bb = back.getbbox()
    if bb: img.paste(Image.alpha_composite(img.crop(bb).convert('RGBA'), back.crop(bb)).convert('RGB'), bb[:2])
    for it, r in zip(items, placed):
        img.paste(it['img'], (int(r[0]), int(r[1])), it['img'])
    d = ImageDraw.Draw(img)
    # знаки ночёвок на кривой
    for c in camps:
        px, py = fx(c['x']), fy(c['z']); s = mm(1.45)
        if c['kind'] == 'camp':
            d.polygon([(px, py - s * 1.15), (px + s, py + s * 0.7), (px - s, py + s * 0.7)], fill=TENT, outline=INK, width=2)
            d.line((px, py - s * 0.25, px, py + s * 0.7), fill=INK, width=2)
        elif c['kind'] == 'start':
            d.ellipse((px - s, py - s, px + s, py + s), fill=(0, 150, 60), outline=(255, 255, 255), width=2)
        else:
            d.rectangle((px - s * 0.85, py - s * 0.85, px + s * 0.85, py + s * 0.85), fill=INK, outline=(255, 255, 255), width=2)
    # вертикальное преувеличение
    vex = ((B - T - head) / (zhi - lo)) / ((R - L) / ((X1 - X0) * 1000))
    return img, dict(vex=vex, L=L, R=R, T=T, B=B, head=head)
