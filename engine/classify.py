"""Из «слоёв» импорта — в данные проекта: куски нитки по дням и точки с типами.

Тип точки угадывается по её названию и по названию папки/трека, где она лежала; всё можно поправить руками.
"""
import itertools, math, re, secrets
from datetime import date, datetime, timedelta, timezone
from .importers import simplify

# id, название, знак mapkit, буква/текст в знаке, цвет, группа
TYPES = [
    dict(id='camp', title='Ночёвка', sym='camp', group='Маршрут', hint='дата ночёвки — в названии: 05.08 или 16-17.08'),
    dict(id='pass', title='Перевал', sym='pass', group='Препятствия', graded=True),
    dict(id='peak', title='Вершина', sym='peak', group='Препятствия', graded=True),
    dict(id='obst', title='Препятствие', sym='obst', text='!', group='Препятствия', graded=True),
    dict(id='ford', title='Переправа, брод', sym='obst', text='≈', group='Препятствия', graded=True),
    dict(id='prizhim', title='Прижим', sym='prizhim', group='Препятствия'),
    dict(id='water', title='Вода: родник, ручей', sym='water', group='Полезное', color=(0, 120, 215)),
    dict(id='campsite', title='Место под стоянку', sym='plan', group='Полезное', color=(0, 90, 200)),
    dict(id='halt', title='Привал, обед', sym='letter', text='О', color=(150, 110, 40), group='Полезное'),
    dict(id='shop', title='Магазин', sym='letter', text='М', color=(0, 110, 70), group='Полезное'),
    dict(id='drop', title='Заброска', sym='letter', text='З', color=(190, 90, 0), group='Полезное'),
    dict(id='house', title='Приют, кордон, изба', sym='letter', text='К', color=(90, 60, 20), group='Полезное'),
    dict(id='transport', title='Транспорт: остановка, станция', sym='letter', text='Т', color=(30, 90, 160), group='Полезное'),
    dict(id='exit', title='Аварийный выход', sym='exit', text='А', group='Полезное', color=(200, 0, 0)),
    dict(id='sight', title='Достопримечательность', sym='letter', text='★', color=(120, 60, 150), group='Интересное'),
    dict(id='cave', title='Пещера', sym='letter', text='П', color=(60, 60, 60), group='Интересное'),
    dict(id='town', title='Населённый пункт', sym='town', group='Интересное'),
    dict(id='mark', title='Ориентир, заметка', sym='town', group='Интересное'),
    dict(id='photo', title='Фототочка', sym='photo', group='Интересное', color=(0, 70, 160), hint='название «день-номер»: 3-12 или 3-12,13'),
    dict(id='skip', title='Не показывать', sym=None, group='—'),
]
TYPE = {t['id']: t for t in TYPES}
OBJ_TYPES = ('pass', 'peak', 'obst', 'ford', 'prizhim')
GRADES = ['н/к', '1А', '1Б', '2А', '2Б', '3А', '3Б']
GRADE_COL = {'н/к': (95, 125, 95), '1А': (30, 140, 75), '1Б': (225, 135, 0), '2А': (208, 44, 36), '2Б': (135, 30, 125),
             '3А': (40, 40, 40), '3Б': (0, 0, 0), None: (95, 95, 95)}

# Участки с особым характером пути: полупрозрачная полоса под ниткой (цвет RGBA, штриховка — для самых трудных)
BANDS = [
    dict(id='nopath', title='движение без тропы', color=(255, 140, 0, 120), hatch=(150, 70, 0, 255), group='Лес и тропа'),
    dict(id='windfall', title='бурелом, завалы', color=(150, 90, 30, 125), hatch=(90, 45, 0, 255), group='Лес и тропа'),
    dict(id='overgrown', title='заросшая, теряющаяся тропа', color=(120, 160, 0, 115), group='Лес и тропа'),
    dict(id='bush', title='стланик, кустарник', color=(40, 130, 60, 120), hatch=(15, 80, 30, 255), group='Лес и тропа'),
    dict(id='swamp', title='болото, топкий участок', color=(60, 140, 120, 120), hatch=(20, 90, 80, 255), group='Лес и тропа'),
    dict(id='road', title='дорога', color=(110, 110, 110, 105), group='Лес и тропа'),
    dict(id='grass', title='крутой травянистый склон', color=(170, 200, 60, 125), group='Склоны'),
    dict(id='scree', title='осыпь', color=(230, 0, 120, 95), group='Склоны'),
    dict(id='kurum', title='курумник, крупные камни', color=(125, 125, 125, 130), hatch=(60, 60, 60, 255), group='Склоны'),
    dict(id='moraine', title='морена', color=(175, 140, 105, 135), group='Склоны'),
    dict(id='rock', title='скалы', color=(95, 60, 40, 135), hatch=(50, 25, 10, 255), group='Склоны'),
    dict(id='snow', title='снег, снежник', color=(110, 195, 255, 150), group='Снег и лёд'),
    dict(id='ice', title='лёд, открытый ледник', color=(0, 165, 230, 130), hatch=(0, 85, 160, 255), group='Снег и лёд'),
    dict(id='glacier', title='закрытый ледник, в связках', color=(70, 105, 225, 150), hatch=(25, 45, 150, 255), group='Снег и лёд'),
    dict(id='rope', title='перила, страховка', color=(225, 0, 0, 110), hatch=(140, 0, 0, 255), group='Техника и опасности'),
    dict(id='danger', title='камнеопасный, лавиноопасный участок', color=(255, 215, 0, 150), hatch=(200, 0, 0, 255), group='Техника и опасности'),
    dict(id='riverbed', title='по руслу, вдоль воды, броды', color=(0, 120, 215, 110), group='Вода'),
    dict(id='shore', title='берег, пляж, галька', color=(240, 200, 120, 140), group='Вода'),
]
BAND = {b['id']: b for b in BANDS}

MONTHS = dict(январ=1, феврал=2, март=3, апрел=4, мая=5, май=5, июн=6, июл=7, август=8, сентябр=9, октябр=10, ноябр=11, декабр=12)
_MON = '|'.join(MONTHS)

def uid(): return secrets.token_hex(4)

def dist(a, b):
    return 6371000 * math.hypot(math.radians(b[0] - a[0]), math.radians(b[1] - a[1]) * math.cos(math.radians(a[0])))

def length_km(pts):
    return sum(dist(a, b) for a, b in zip(pts, pts[1:])) / 1000

def _year(y, default):
    if y is None: return default
    y = int(y)
    return y + 2000 if y < 100 else y

# ---------------------------------------------------------------- названия треков
def piece_hint(name, year):
    """('date', date) | ('n', N — «день N») | ('lead', N — просто число в начале) | None по названию трека."""
    s = (name or '').lower()
    m = re.search(rf'(?<!\d)(\d{{1,2}})\s*(?:-?го)?\s+({_MON})[а-я]*(?:\s+(\d{{4}}))?', s)
    if m:
        mon = next(v for k, v in MONTHS.items() if m[2].startswith(k))
        try: return ('date', date(_year(m[3], year), mon, int(m[1])))
        except ValueError: pass
    m = re.search(r'(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?!\d)', s)
    if m and 1 <= int(m[2]) <= 12 and 1 <= int(m[1]) <= 31:
        try: return ('date', date(_year(m[3], year), int(m[2]), int(m[1])))
        except ValueError: pass
    m = re.search(r'(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)', s)
    if m:
        try: return ('date', date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError: pass
    m = re.search(r'(?:день|дн\.|day|д\.)\s*№?\s*(\d{1,2})(?!\d)', s) or re.search(r'(?<!\d)(\d{1,2})\s*(?:-?[йяе]|-?ый|-?ой)?\s*(?:день|дн\b|day)', s)
    if m and 1 <= int(m[1]) <= 90: return ('n', int(m[1]))
    m = re.match(r'\s*(\d{1,2})(?!\d)[\s._)\-]', s + ' ')
    if m and 1 <= int(m[1]) <= 60: return ('lead', int(m[1]))      # просто число в начале: номер дня ИЛИ число месяца
    return None

def is_radial_name(name): return bool(re.search(r'рад(иал|\.|\b)', (name or '').lower()))
# Варианты маршрута: запасной (alt) и аварийный выход (exit) — отдельные треки, в дни и километраж не входят
VAR_ROLES = ('alt', 'exit')
def variant_kind(name):
    n = (name or '').lower()
    if re.search(r'аварийн|авар\.|эвакуац|(^|[^а-яё])сход($|[^а-яё])|аварийк', n): return 'exit'
    if re.search(r'запасн|альтернат|вариант|варинат|обход', n) and not re.search(r'основн', n): return 'alt'
    return None

def variant_title(name):
    return re.sub(r'\s*·\s*часть\s*\d+$', '', re.sub(r'\.(gpx|kml|kmz|geojson|json)\b', '', name or '', flags=re.I)).strip(' -–·') or 'вариант'

def add_variant(proj, kind, title):
    v = dict(id=uid(), kind=kind, title=title[:80], note='')
    proj.setdefault('variants', []).append(v)
    return v

def day_radial(proj, day):
    """Весь день — радиальный выход: куски дня (по порядку) склеиваются в один, и форму — кольцо, туда-обратно,
    подход + кольцо — «Нитка» видит уже по целому выходу. Куски должны стыковаться (разрыв до 400 м)."""
    pcs = sorted([p for p in proj['pieces'] if p.get('day') == day and p.get('role') in ('main', 'radial') and len(p.get('pts', [])) > 1],
                 key=lambda p: p.get('order', 0))
    if not pcs: return dict(ok=False, msg='В этом дне нет нитки.')
    for a, b in zip(pcs, pcs[1:]):
        if dist(a['pts'][-1][:2], b['pts'][0][:2]) > 400:
            return dict(ok=False, msg=f'Куски дня {day} не стыкуются (между «{a["name"]}» и «{b["name"]}» больше 400 м). Поправь порядок кусков или отметь радиалкой только нужный кусок.')
    if len(pcs) == 1:
        pcs[0]['role'] = 'radial'; return dict(ok=True, msg=f'День {day} — радиальный выход.')
    pts = list(pcs[0]['pts']); tl = [piece_times(p) for p in pcs]; ts = list(tl[0]) if all(tl) else None
    for p, t in zip(pcs[1:], tl[1:]):
        k = 1 if dist(pts[-1][:2], p['pts'][0][:2]) < 1 else 0          # общая точка на стыке — один раз
        pts += p['pts'][k:]
        if ts is not None: ts += t[k:]
    new = dict(id=uid(), src=pcs[0].get('src'), name=f'день {day} — радиальный выход', pts=pts, day=day, role='radial', order=pcs[0].get('order', 0), km=round(length_km(pts), 2))
    set_times(new, ts)
    ids = {p['id'] for p in pcs}
    proj['pieces'] = [p for p in proj['pieces'] if p['id'] not in ids] + [new]
    return dict(ok=True, new=new['id'], msg=f'День {day} — один радиальный выход из {len(pcs)} кусков. Форму «Нитка» определила по нему целиком; если нужно, его можно снова разрезать.')

def group_variants(proj, pieces, kind, title=None):
    """Куски → варианты. Куски одного трека, которые стыкуются (конец одного у начала другого, до 300 м), — один вариант;
    несвязанные куски — разные варианты (у Паши «Запасные варианты» — шесть разных обходов одним треком)."""
    made = []; cur = None; last = None; base = None
    for p in pieces:
        b = title or variant_title(p['name'])
        if not (cur and b == base and last and dist(last, tuple(p['pts'][0][:2])) < 300):
            cur = add_variant(proj, kind, b); made.append(cur); base = b
        p['role'] = kind; p['var'] = cur['id']; p['day'] = None; p.pop('shape', None); p.pop('once', None)
        last = tuple(p['pts'][-1][:2])
    return made      # одинаковые названия различает номер варианта (З1, З2…) — он идёт по ходу маршрута

def is_summary_name(name): return bool(re.search(r'вес[ьъ]\s+маршрут|общий|полный трек|all\b|план\b|заявлен', (name or '').lower()))

# ---------------------------------------------------------------- названия точек
_GRADE = re.compile(r'(?<![А-Яа-яёA-Za-z0-9])(н\s?/\s?к|[1-3]\s?[АБAB]\*?)(?![А-Яа-яёA-Za-z0-9])', re.I)
_DATE = re.compile(r'(?<!\d)(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?!\d)\.?')
_DATE2 = re.compile(r'(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?\s*[-–—]\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?!\d)\.?')

def camp_date(raw, year=None):
    """Дата ночёвки из названия → (первая ночь, сколько ночей, остаток названия, дата стояла в начале) | None."""
    y0 = year or datetime.now().year
    try:
        m = _DATE2.search(raw)                    # «31.07 - 01.08»
        if m and 1 <= int(m[2]) <= 12 and 1 <= int(m[5]) <= 12:
            y1 = _year(m[6] or m[3], y0); d0 = date(_year(m[3], y1), int(m[2]), int(m[1])); d1 = date(y1, int(m[5]), int(m[4]))
            if d1 < d0 and not m[3]: d0 = d0.replace(year=d0.year - 1)
            if 0 <= (d1 - d0).days <= 15: return d0, (d1 - d0).days + 1, _DATE2.sub('', raw, count=1), m.start() == 0
        m = _DATE.search(raw)                     # «05.08», «16-17.08», «17.07.26»
        if m and 1 <= int(m[3]) <= 12 and 1 <= int(m[1]) <= 31:
            y = _year(m[4], y0); d0 = date(y, int(m[3]), int(m[1])); nights = 1
            if m[2]: nights = max(1, (date(y, int(m[3]), int(m[2])) - d0).days + 1)
            return d0, nights, _DATE.sub('', raw, count=1), m.start() == 0
    except ValueError:
        pass
    return None

_H = re.compile(r'(?<![\d.,])(\d{3,4})(?:\s*м\b\.?)?(?![\d])')

KEYWORDS = [  # порядок важен: первое совпадение
    ('skip', r'^\s*(старт|финиш|start|finish|начало|конец)\b'),
    ('prizhim', r'прижим'),
    ('ford', r'брод|переправ|навесн'),
    ('obst', r'ледопад|ригель|бергшрунд|каньон|завал|бурелом|осып|курум|обвал|опасн|препятств|камнепад|скал[ыь]|стенк'),
    ('water', r'родник|источник|вод[аы]\b|ручей|колодец|колонка|ключ\b'),
    ('shop', r'магазин|продукт'),
    ('drop', r'заброск'),
    ('cave', r'пещер|грот'),
    ('halt', r'обед|привал|перекус'),
    ('transport', r'автобус|станци|вокзал|остановк|паром|ж/?д\b|электричк|причал|такси'),
    ('house', r'кордон|приют|хижин|изба|зимовь|турбаз|база\b|балаган|кош\b|гостиниц|хостел'),
    ('exit', r'аварийн'),
    ('sight', r'памятник|часовн|церк|храм|монастыр|музей|водопад|смотров|обзорн|качел|крест|мемориал|башн|крепост|руин|маяк|вид\b'),
    ('campsite', r'стоянк|лагер|бивак|бивуак|место под'),
]

def classify_point(name, folder='', sym='', year=None):
    """→ dict(type, name, grade, h, date, nights, after_day, day, nums)."""
    raw = (name or '').strip(); low = raw.lower(); fl = (folder or '').lower()
    out = dict(type='skip', name=raw, grade=None, h=None, date=None, nights=1, after_day=None, day=None, nums=None)
    raw0 = raw
    f_camp = bool(re.search(r'ноч[её]в|стоянк|лагер|бивак|camp', fl))
    f_pass = bool(re.search(r'перевал|препятств|вершин|pass', fl))
    f_photo = bool(re.search(r'фото|photo|снимк', fl))
    fday = re.search(r'(?<!\d)(\d{1,2})\s*(?:-?[йяе])?\s*(?:день|дн)', fl) or re.search(r'(?:день|дн\.?)\s*(\d{1,2})', fl)
    fday = int(fday[1]) if fday else None

    # фототочки
    pm = re.match(r'^\s*(?:фото|ф\.?|photo)?\s*№?\s*(\d{1,3})\s*([-.,–]\s*[\d,\s\-–…]+)?\s*$', low)
    if pm and (f_photo or re.match(r'^\s*(фото|ф\.?\s*\d|photo)', low)):
        first = int(pm[1]); rest = (pm[2] or '').strip()
        nums = []
        if rest:
            for part in re.split(r',', rest.lstrip('-.,– ')):
                r = re.match(r'\s*(\d+)\s*[-–…]\s*(\d+)\s*$', part)
                if r and int(r[1]) <= int(r[2]) <= int(r[1]) + 60: nums += list(range(int(r[1]), int(r[2]) + 1))
                elif part.strip().isdigit(): nums.append(int(part))
            day = first
        else:
            day = fday; nums = [first]
        if fday: day = fday
        out.update(type='photo', day=day, nums=sorted(set(nums)), name=raw)
        return out

    if not raw or re.fullmatch(r'[\d\s]*', raw) or re.fullmatch(r'(wpt|точка|point|метка)?\s*\d*', low):
        return out

    # ночёвки: дата в названии («05.08», «16-17.08», «Лагерь 31.07 - 01.08», «Ночёвка 3»)
    dm = _DATE.search(raw)
    camp_word = re.search(r'ноч[её]в|^ноч\s?\d|\bм\.\s?н\.|^\s*н\s?\d', low)
    camp_kw = re.search(r'лагер|стоянк|бив[уа]+к|\bcamp', low)
    cd = camp_date(raw, year)
    if cd:
        d0, nights, rest, at_start = cd
        rl = rest.lower(); plain = not rest.strip(' ,.;:-–')
        other = any(re.search(pat, rl) for t, pat in KEYWORDS if t != 'campsite') or re.match(r'\s*(пер\.|перевал|гора|г\.|в\.|вершина|пик)', rl)
        pos = (_DATE2.search(raw) or _DATE.search(raw)).start(); glued = pos > 0 and raw[pos - 1].isalpha()      # «СТОЯНКА1.1» — не дата
        if f_camp or camp_word or (camp_kw and not glued) or plain or (at_start and not other):
            rest = re.sub(r'(?i)ноч[её]вка|м\.\s?н\.|лагерь|стоянка|бив[уа]+к', '', rest).strip(' ,.;:-–')
            hm = _H.search(rest)
            out.update(type='camp', date=d0.isoformat(), nights=nights, name=_H.sub('', rest).strip(' ,.;:-–м') if hm else rest, h=int(hm[1]) if hm and 100 <= int(hm[1]) <= 8900 else None)
            return out
    if camp_word:
        n = re.search(r'(\d{1,2})', low)
        out.update(type='camp', after_day=int(n[1]) if n else None, name='')
        return out

    # дата в начале названия не-ночёвки («13.07 обед») в подпись не идёт
    if dm and dm.start() == 0 and _DATE.sub('', raw, count=1).strip(' ,.;:-–'): raw = _DATE.sub('', raw, count=1).strip(' ,.;:-–'); low = raw.lower()
    # категория и высота
    g = _GRADE.search(raw); grade = None
    if g:
        grade = g[1].upper().replace(' ', '').replace('A', 'А').replace('B', 'Б')
        if grade.startswith('Н'): grade = 'н/к'
    body = _GRADE.sub(' ', raw) if g else raw
    hm = None
    for m in _H.finditer(body):
        if 100 <= int(m[1]) <= 8900: hm = m
    h = int(hm[1]) if hm else None
    base = (body[:hm.start()] + ' ' + body[hm.end():]) if hm else body
    base = re.sub(r'\s+', ' ', base).strip(' ,.;:-–()')
    bl = base.lower()
    keep = re.sub(r'\s+', ' ', body).strip(' ,.;:-–()')        # название с числами — для точек, у которых высоты нет («ж/д 122 км»)

    def title(prefix, pat):
        t = re.sub(pat, '', base, flags=re.I).strip(' ,.;:-–')
        return (prefix + t).strip() if t else prefix.strip()

    # «г. Осташков финиш 15.06» — это город и конец маршрута, а не гора: старт и финиш рисуются сами
    if re.search(r'(^|[^а-яё])(старт|финиш)([^а-яё]|$)', bl) and not re.search(r'автобус|станци|вокзал|остановк|паром|ж/?д|электричк|причал', bl) and not grade and not h:
        return out
    if re.match(r'^(пер\.|пер\s|перевал)', bl):
        out.update(type='pass', name=title('пер. ', r'^(перевал|пер\.?)\s*'), grade=grade, h=h); return out
    if re.match(r'^(гора|г\.|влк\.?|вулкан)\s', bl + ' '):
        out.update(type='peak', name=title('г. ', r'^(гора|г\.|влк\.?|вулкан)\s*'), grade=grade, h=h); return out
    if re.match(r'^(в\.|вершина|верш\.)', bl):
        out.update(type='peak', name=title('в. ', r'^(вершина|верш\.|в\.)\s*'), grade=grade, h=h); return out
    if re.match(r'^пик\b', bl):
        out.update(type='peak', name=base, grade=grade, h=h); return out
    for t, pat in KEYWORDS:
        if re.search(pat, bl):
            if t == 'skip': return out
            if f_pass and t in ('campsite', 'sight', 'house'): continue
            out.update(type=t, name=(base if t in OBJ_TYPES else keep) if t not in ('campsite',) or len(base) > 12 else base.capitalize(), grade=grade if TYPE[t].get('graded') else None, h=h if t in OBJ_TYPES else None)
            return out
    if f_pass:
        out.update(type='pass' if h else 'obst', name=('пер. ' + base) if h else base, grade=grade, h=h); return out
    if grade:
        out.update(type='pass' if h else 'obst', name=('пер. ' + base) if h else base, grade=grade, h=h); return out
    if f_camp:
        out.update(type='campsite', name=base); return out
    sl = (sym or '').lower()
    for t, pat in (('camp', r'camp|tent'), ('water', r'water|drink'), ('peak', r'summit|peak'), ('shop', r'shop|store'), ('sight', r'scenic|museum|monument')):
        if re.search(pat, sl):
            out.update(type='campsite' if t == 'camp' else t, name=base); return out
    out.update(type='mark', name=base)
    out['guess'] = True
    return out


# ---------------------------------------------------------------- сборка проекта
def new_project(title=''):
    return dict(v=1, meta=dict(title=title, area='', kind='пеший', category='', start_date=None, kcoef=1.0, radial_one_way=True, elev='auto', note=''),
                pieces=[], points=[], bands=[], custom_types=[], sources=[], variants=[],
                sheets=dict(region=True, overview=True, work=True, days=True, profile=True, work_scale=100000, photos=True, logo=True, base='otm', adjust={}))

def cut_piece(proj, pid, lat, lon):
    """Разрезает кусок нитки в ближайшей к (lat, lon) точке линии. Обе половины остаются в том же дне."""
    p = next((x for x in proj['pieces'] if x['id'] == pid), None)
    if not p: return dict(ok=False, msg='Этот кусок нитки не найден — обнови страницу.')
    pts = p['pts']; k = math.cos(math.radians(lat)); best = None
    for i in range(len(pts) - 1):
        ax, ay = (pts[i][1] - lon) * k, pts[i][0] - lat; bx, by = (pts[i + 1][1] - lon) * k, pts[i + 1][0] - lat
        dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
        d2 = (ax + dx * t) ** 2 + (ay + dy * t) ** 2
        if best is None or d2 < best[0]: best = (d2, i, t)
    _, i, t = best
    a, b = pts[i], pts[i + 1]
    mid = [round(a[0] + (b[0] - a[0]) * t, 6), round(a[1] + (b[1] - a[1]) * t, 6)]
    if len(a) > 2 and len(b) > 2: mid.append(round(a[2] + (b[2] - a[2]) * t, 1))
    first = pts[:i + 1] + ([mid] if t > 1e-6 else [])
    second = ([mid] if t < 1 - 1e-6 else []) + pts[i + 1:]
    if t <= 1e-6: second = pts[i:]
    if t >= 1 - 1e-6: first = pts[:i + 2]
    if length_km(first) < 0.03 or length_km(second) < 0.03 or len(first) < 2 or len(second) < 2:
        return dict(ok=False, msg='Слишком близко к концу куска — резать тут нечего. Нажми подальше от края.')
    base = re.sub(r'\s*\((начало|продолжение)\)$', '', p['name'])
    ts = piece_times(p); tf = ts2 = None
    if ts:                                    # время делится вместе с точками
        tmid = ts[i] + (ts[i + 1] - ts[i]) * t
        tf = ts[:i + 1] + ([tmid] if t > 1e-6 else []); ts2 = ([tmid] if t < 1 - 1e-6 else []) + ts[i + 1:]
        if t <= 1e-6: ts2 = ts[i:]
        if t >= 1 - 1e-6: tf = ts[:i + 2]
    new = dict(p, id=uid(), name=base + ' (продолжение)', pts=second, km=round(length_km(second), 2), order=p.get('order', 0) + 0.5)
    p.update(name=base + ' (начало)' if not p['name'].endswith('(продолжение)') else p['name'], pts=first, km=round(length_km(first), 2))
    set_times(p, tf); set_times(new, ts2)
    proj['pieces'].append(new)
    for n, x in enumerate(sorted(proj['pieces'], key=lambda x: x.get('order', 0))): x['order'] = n
    return dict(ok=True, new=new['id'], msg='Нитка разрезана: теперь это два куска. Поставь им дни, если нужно.')


# ---------------------------------------------------------------- время в треке: остановки, «звёздочки», ходовое время
STOP_R, STOP_T = 40.0, 300          # остановка: дольше 5 минут в круге 40 м

def piece_times(p):
    """Время каждой точки куска (секунды UTC) или None, если трек без времени."""
    tm = p.get('tm')
    if not tm or len(tm) != len(p.get('pts', [])): return None
    t0 = p.get('t0') or 0
    return [t0 + d for d in tm]

def set_times(p, ts):
    if not ts or len(ts) != len(p.get('pts', [])) or any(t is None for t in ts): p.pop('tm', None); p.pop('t0', None); return
    t0 = int(ts[0]); p['t0'] = t0; p['tm'] = [int(round(t - t0)) for t in ts]

def _fill_times(ts):
    """Пропуски во времени — по соседям (линейно по номеру точки)."""
    idx = [i for i, t in enumerate(ts) if t]
    if len(idx) < max(2, 0.9 * len(ts)): return None
    out = list(ts)
    for i in range(len(out)):
        if out[i]: continue
        a = max((k for k in idx if k < i), default=None); b = min((k for k in idx if k > i), default=None)
        out[i] = out[a] if b is None else out[b] if a is None else out[a] + (out[b] - out[a]) * (i - a) / (b - a)
    return out

def collapse_stops(seg):
    """«Звёздочки»: пока группа стоит, часы пишут точки вокруг одного места — это лишние сотни метров пути.
    Остановка (дольше 5 минут в круге 40 м) заменяется двумя точками в её центре — начало и конец, по ним видно её время.
    Возвращает (новые точки, номера точек, которые нельзя выкинуть при прореживании, сколько метров убрано, остановок)."""
    out, keep = [], []; removed = 0.0; n = len(seg); i = 0; stops = 0
    while i < n:
        j = i
        while j + 1 < n and dist(seg[i][:2], seg[j + 1][:2]) < STOP_R: j += 1
        if j > i and seg[i][3] and seg[j][3] and seg[j][3] - seg[i][3] >= STOP_T:
            grp = seg[i:j + 1]; la = sum(q[0] for q in grp) / len(grp); lo = sum(q[1] for q in grp) / len(grp)
            el = [q[2] for q in grp if q[2] is not None]; e = sum(el) / len(el) if el else None
            removed += sum(dist(seg[k][:2], seg[k + 1][:2]) for k in range(i, j))
            if out: removed += dist(out[-1][:2], (la, lo)) - dist(out[-1][:2], seg[i][:2])
            keep += [len(out), len(out) + 1]; stops += 1
            out.append((la, lo, e, seg[i][3])); out.append((la, lo, e, seg[j][3]))
            i = j + 1
        else:
            out.append(seg[i]); i += 1
    return out, keep, max(0.0, removed), stops

def time_info(pts, ts):
    """Время куска: начало и конец движения, ходовое время, остановки [(lat, lon, начало, конец)].
    Остановка в самом начале или конце куска — это ночёвка, а не привал: время выхода и прихода считается без неё."""
    if not ts or len(ts) != len(pts) or len(pts) < 2: return None
    stops = []; i = 0; n = len(pts)
    while i < n:
        j = i
        while j + 1 < n and dist(pts[i][:2], pts[j + 1][:2]) < STOP_R: j += 1
        if j > i and ts[j] - ts[i] >= STOP_T: stops.append([i, j]); i = j
        i += 1
    a, b = ts[0], ts[-1]
    if stops and stops[0][0] == 0: a = ts[stops[0][1]]; stops = stops[1:]
    if stops and stops[-1][1] == n - 1: b = ts[stops[-1][0]]; stops = stops[:-1]
    pause = sum(ts[j] - ts[i] for i, j in stops)
    return dict(start=a, end=b, move=max(0, b - a - pause),
                stops=[(round(pts[i][0], 6), round(pts[i][1], 6), ts[i], ts[j]) for i, j in stops])

def trip_tz(proj, lon=None):
    """Часовой пояс похода, часы: выбранный туристом, иначе — по долготе."""
    tz = proj.get('meta', {}).get('tz')
    if isinstance(tz, (int, float)) and -12 <= tz <= 14: return tz
    return _tz_hours(lon) if lon is not None else 3

def hm(ts, tz):
    """«9:42» — местное время."""
    t = datetime.fromtimestamp(ts + tz * 3600, timezone.utc); return f'{t.hour}:{t.minute:02d}'

def dur(sec):
    """«3:55» — часы и минуты."""
    m = int(round(sec / 60)); return f'{m // 60}:{m % 60:02d}'

def _cells(pts, k):
    """Ячейки ~37 м с соседями — быстро понять, лежит ли точка на другой линии."""
    s = set()
    for q in pts:
        y, x = int(round(q[0] * 3000)), int(round(q[1] * 3000 * k))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1): s.add((y + dy, x + dx))
    return s

def _share_on(a, cells, k):
    smp = a[::max(1, len(a) // 80)]
    return sum(1 for q in smp if (int(round(q[0] * 3000)), int(round(q[1] * 3000 * k))) in cells) / max(1, len(smp))

def upgrade_times(proj, pts, ts):
    """Трек с временем загружен повторно: куски, загруженные раньше без времени, получают его, а дни, роли, названия
    и правки остаются. Кусок раньше мог быть разрезан — тогда каждой части достаётся её отрезок. Возвращает число кусков."""
    if not ts or len(pts) < 2: return 0
    k = math.cos(math.radians(pts[0][0])); new_cells = _cells(pts, k); got = 0
    for p in proj['pieces']:
        if p.get('tm') or len(p.get('pts', [])) < 2 or p.get('role') == 'skip': continue
        if _share_on(p['pts'], new_cells, k) < 0.85: continue
        i0 = min(range(len(pts)), key=lambda i: dist(pts[i][:2], p['pts'][0][:2]))
        i1 = min(range(len(pts)), key=lambda i: dist(pts[i][:2], p['pts'][-1][:2]))
        if i1 - i0 < 1: continue
        sub = pts[i0:i1 + 1]; L_old, L_new = length_km(p['pts']), length_km(sub)
        if L_old > 0.05 and not (0.3 < L_new / L_old < 2.0): continue      # старый кусок мог быть длиннее из-за «звёздочек»
        p['pts'] = sub; set_times(p, ts[i0:i1 + 1]); p['km'] = round(L_new, 2); got += 1
    return got

def _tz_hours(lon): return int(round(lon / 15.0))

def _split_by_date(seg, tzh=None):
    """Кусок GPS-трека с отметками времени — по местным суткам."""
    if not seg or sum(1 for p in seg if p[3]) < len(seg) * 0.9: return None
    tz = timezone(timedelta(hours=tzh if tzh is not None else _tz_hours(seg[0][1])))
    out = {}
    last = None
    for p in seg:
        d = datetime.fromtimestamp(p[3], tz).date() if p[3] else last
        if d is None: continue
        out.setdefault(d, []).append(p); last = d
    return out if len(out) > 1 or out else None

def _pts_out(seg, stat=None):
    """Точки куска для хранения и их время (или None). stat — сюда пишется, сколько «звёздочек» убрано."""
    timed = sum(1 for p in seg if p[3]) >= 0.9 * len(seg)
    keep = []
    if timed:
        seg, keep, removed, nstops = collapse_stops(seg)
        if stat is not None: stat['removed'] = stat.get('removed', 0.0) + removed; stat['stops'] = stat.get('stops', 0) + nstops
    # упрощаются только записанные GPS-треки (густые, с временем); нарисованная вручную нитка остаётся как есть
    if len(seg) > 2500 or sum(1 for p in seg if p[3]) > len(seg) * 0.5: seg = simplify(seg, 2.0, keep)
    ts = _fill_times([p[3] for p in seg]) if timed else None
    has_ele = sum(1 for p in seg if p[2] is not None) >= len(seg) * 0.9
    if has_ele:
        last = next(p[2] for p in seg if p[2] is not None); out = []
        for p in seg:
            if p[2] is not None: last = p[2]
            out.append([round(p[0], 6), round(p[1], 6), round(last, 1)])
        return out, ts
    return [[round(p[0], 6), round(p[1], 6)] for p in seg], ts

def merge_import(proj, layers, src_name, as_role='main', tz=None):
    """Добавляет слои в проект. as_role: main — как обычно (варианты узнаются по названию), alt / exit — всё из этой
    загрузки — запасной вариант или аварийный выход. Возвращает сводку: что нашлось."""
    meta = proj['meta']
    meta.pop('no_passes', None)      # нитка меняется — перевалы по каталогу стоит поискать заново
    has_time = any(sum(1 for q in s if q[3]) >= 0.9 * len(s) for L in layers for s in L['segs'] if s)
    if has_time and not isinstance(meta.get('tz'), (int, float)) and isinstance(tz, (int, float)) and -12 <= tz <= 14: meta['tz'] = tz      # пояс браузера туриста
    tzh = meta.get('tz') if isinstance(meta.get('tz'), (int, float)) else None
    sid = uid()
    # год: из start_date, из названий точек, из времени GPS, иначе текущий
    year = int(meta['start_date'][:4]) if meta.get('start_date') else None
    if not year:
        for L in layers:
            for w in L['wpts']:
                m = re.search(r'\d{1,2}\.\d{1,2}\.(\d{4}|\d{2})(?!\d)', w.get('name') or '')
                if m: year = _year(m[1], None); break
            if year: break
    if not year:
        for L in layers:
            for s in L['segs']:
                t = next((p[3] for p in s if p[3]), None)
                if t: year = datetime.fromtimestamp(t, timezone.utc).year; break
            if year: break
    year = year or datetime.now().year

    new_pieces = []
    for L in layers:
        nsegs = len(L['segs'])
        for k, seg in enumerate(L['segs']):
            if len(seg) < 2: continue
            name = (L['name'] or 'трек').strip()
            hint = piece_hint(name, year)
            # время в самом треке надёжнее голого числа в названии («15 activity_…» — это 15-е число, а не 15-й день)
            parts = _split_by_date(seg, tzh) if (not hint or hint[0] == 'lead') else None
            if parts and len(parts) > 1:
                for d, s in sorted(parts.items()):
                    if len(s) > 1: new_pieces.append(dict(name=f'{name} · {d:%d.%m}', seg=s, hint=('date', d)))
            else:
                if parts: hint = ('date', next(iter(parts)))
                new_pieces.append(dict(name=name if nsegs == 1 else f'{name} · часть {k + 1}', seg=seg, hint=hint, base=name))

    # дубли («Весь маршрут» повторяет дневные треки)
    hinted = set()
    for p in new_pieces:
        if p['hint']: hinted.update((round(q[0], 4), round(q[1], 4)) for q in p['seg'])
    for p in new_pieces:
        p['skip'] = False
        if not p['hint'] and hinted:
            same = sum(1 for q in p['seg'] if (round(q[0], 4), round(q[1], 4)) in hinted)
            if same > 0.85 * len(p['seg']) or is_summary_name(p['name']): p['skip'] = True

    def shift_days(delta):                    # вся нумерация проекта сдвигается: появился трек раньше «первого дня»
        for x in proj['pieces']:
            if x.get('day'): x['day'] += delta
        for q in proj['points']:
            if q.get('after_day') is not None: q['after_day'] += delta
            if q.get('type') == 'photo' and q.get('day'): q['day'] += delta

    dates = [p['hint'][1] for p in new_pieces if p['hint'] and p['hint'][0] == 'date']
    old_days = [x.get('day') for x in proj['pieces'] if x.get('day')]
    if dates and not meta.get('start_date') and not old_days: meta['start_date'] = min(dates).isoformat()
    start = date.fromisoformat(meta['start_date']) if meta.get('start_date') else None
    if dates and start and min(dates) < start and (start - min(dates)).days <= 60:
        delta = (start - min(dates)).days; shift_days(delta); start = min(dates); meta['start_date'] = start.isoformat()
        if meta.get('lead_base') is not None: meta['lead_base'] -= delta

    # голые числа в начале названий без времени в треке: «1, 2, 3…» — номера дней, «15, 16, 17…» — числа месяца
    leads = [p for p in new_pieces if p['hint'] and p['hint'][0] == 'lead']
    if leads:
        off = 0; prev = None
        for p in leads:                       # переход через конец месяца: …30, 31, 1, 2…
            v = p['hint'][1]
            if prev is not None and v < prev and prev >= 28 and v <= 5: off += prev
            prev = v; p['lead'] = v + off
        lo = min(p['lead'] for p in leads)
        if meta.get('lead_base') is None:
            meta['lead_base'] = (start.day - 1) if (start and start.day == lo and not old_days) else (lo - 1 if not old_days else 0)
        if lo - meta['lead_base'] < 1:
            delta = 1 - (lo - meta['lead_base']); shift_days(delta); meta['lead_base'] -= delta
            if start: start = start - timedelta(days=delta); meta['start_date'] = start.isoformat()
    order0 = len(proj['pieces'])
    n_assigned = 0; var_pieces = []; stat = {}; upgraded = 0
    for i, p in enumerate(new_pieces):
        day = None
        if p['hint']:
            if p['hint'][0] == 'n': day = p['hint'][1]
            elif p['hint'][0] == 'lead': day = p['lead'] - meta['lead_base']
            elif start: day = (p['hint'][1] - start).days + 1
            if day is not None and day < 1: day = None
        pts, ts = _pts_out(p['seg'], stat)
        if ts and not p['skip']:
            got = upgrade_times(proj, pts, ts)
            if got: upgraded += got; continue          # этот трек уже был в походе — ему просто добавилось время
        vk = as_role if as_role in VAR_ROLES else (None if (day or p['skip']) else variant_kind(p.get('base') or p['name']))
        if vk:                                # запасной вариант или аварийный выход — в варианты после разбора всех кусков
            x = dict(id=uid(), src=sid, name=p['name'], pts=pts, day=None, role=vk, order=order0 + i, km=round(length_km(pts), 2))
            set_times(x, ts); proj['pieces'].append(x); var_pieces.append((vk, x))
            continue
        role = 'skip' if p['skip'] else ('radial' if is_radial_name(p['name']) else 'main')
        if day: n_assigned += 1
        x = dict(id=uid(), src=sid, name=p['name'], pts=pts, day=day, role=role, order=order0 + i, km=round(length_km(pts), 2))
        set_times(x, ts); proj['pieces'].append(x)

    var_made = [v for kind in VAR_ROLES for v in group_variants(proj, [x for k, x in var_pieces if k == kind], kind)]

    # точки
    seen = [(q.get('raw'), q['lat'], q['lon']) for q in proj['points']]
    counts = {}
    for L in layers:
        for w in L['wpts']:
            raw = (w.get('name') or '').strip()
            if any(r == raw and dist((la, lo), (w['lat'], w['lon'])) < 15 for r, la, lo in seen): continue
            seen.append((raw, w['lat'], w['lon']))
            c = classify_point(raw, L['name'] if not L['segs'] or len(L['wpts']) > 0 else '', w.get('sym') or '', year)
            pt = dict(id=uid(), src=sid, raw=raw, folder=L['name'], lat=round(w['lat'], 6), lon=round(w['lon'], 6), type=c['type'], name=c['name'],
                      grade=c['grade'], h=c['h'], date=c['date'], nights=c['nights'], after_day=c['after_day'], day=c['day'], nums=c['nums'],
                      on_map=True, on_profile=c['type'] in OBJ_TYPES or c['type'] == 'camp', note=(w.get('desc') or '')[:300])
            if c.get('guess'): pt['guess'] = True
            if c['type'] != 'camp' and c['type'] in OBJ_TYPES and c['h'] is None and w.get('ele'): pass
            proj['points'].append(pt)
            counts[c['type']] = counts.get(c['type'], 0) + 1

    # старт по ночёвкам, если треки названы «N день»
    if not meta.get('start_date'):
        cd = sorted(p['date'] for p in proj['points'] if p['type'] == 'camp' and p.get('date'))
        if cd and any(x.get('day') for x in proj['pieces']):
            meta['start_date'] = cd[0]
    chain_order(proj, {x['day'] for x in proj['pieces'] if x.get('src') == sid and x.get('day')})
    proj['sources'].append(dict(id=sid, name=src_name, pieces=len(new_pieces), points=sum(counts.values())))
    return dict(source=sid, pieces=len(new_pieces), assigned=n_assigned, skipped=sum(1 for p in new_pieces if p['skip']), points=counts,
                variants=[dict(kind=v['kind'], title=v['title']) for v in var_made],
                timed=sum(1 for x in proj['pieces'] if x.get('src') == sid and x.get('tm')), upgraded=upgraded,
                stops=stat.get('stops', 0), stars_km=round(stat.get('removed', 0.0) / 1000, 2),
                unassigned=sum(1 for p in proj['pieces'] if not p.get('day') and p['role'] in ('main', 'radial')))

def chain_order(proj, days=None):
    """Куски основной нитки внутри дня — по ходу движения: день продолжает предыдущий, каждый следующий кусок
    начинается там, где кончился прошлый. В файлах сегменты часто лежат не по порядку. Возвращает число исправленных дней."""
    by = {}
    for p in sorted(proj.get('pieces', []), key=lambda p: p.get('order', 0)):
        if p.get('role') == 'main' and p.get('day') and len(p.get('pts', [])) > 1: by.setdefault(int(p['day']), []).append(p)
    ds = sorted(by); changed = 0; prev_end = None
    for k, d in enumerate(ds):
        L = by[d]
        if len(L) > 1 and (days is None or d in days) and len(L) <= 7:
            nxt = [q['pts'][0] for q in by[ds[k + 1]]] if k + 1 < len(ds) else []
            def cost(perm):
                c = dist(prev_end, perm[0]['pts'][0]) if prev_end else 0.0
                c += sum(dist(a['pts'][-1], b['pts'][0]) for a, b in zip(perm, perm[1:]))
                if nxt: c += min(dist(perm[-1]['pts'][-1], q) for q in nxt)
                return c
            best = min(itertools.permutations(L), key=cost)
            if cost(best) < cost(L) - 50:                       # меняем, только если нитка действительно становится связнее
                for p, o in zip(best, sorted(p.get('order', 0) for p in L)): p['order'] = o
                by[d] = list(best); changed += 1
        prev_end = by[d][-1]['pts'][-1]
    return changed

def remove_source(proj, sid):
    proj['pieces'] = [p for p in proj['pieces'] if p.get('src') != sid]
    proj['points'] = [p for p in proj['points'] if p.get('src') != sid]
    proj['sources'] = [s for s in proj['sources'] if s['id'] != sid]

def split_by_camps(proj):
    """Нитка без разбивки на дни режется в точках ночёвок (по порядку дат)."""
    loose = [p for p in sorted(proj['pieces'], key=lambda p: p['order']) if not p.get('day') and p['role'] == 'main']
    camps = sorted((c for c in proj['points'] if c['type'] == 'camp' and c.get('date')), key=lambda c: c['date'])
    if not loose: return dict(ok=False, msg='Все треки уже разложены по дням.')
    if not camps: return dict(ok=False, msg='Нет ночёвок с датами — отметьте ночёвки (тип «Ночёвка», дата) и повторите.')
    chain = [q for p in loose for q in p['pts']]
    tl = [piece_times(p) for p in loose]; tchain = [t for x in tl for t in x] if all(tl) else None
    meta = proj['meta']
    first = date.fromisoformat(camps[0]['date'])
    start = date.fromisoformat(meta['start_date']) if meta.get('start_date') else first
    if start > first: start = first
    meta['start_date'] = start.isoformat()
    cuts = []; pos = 0
    for c in camps:
        best = None; bd = 1e9
        for i in range(pos, len(chain)):
            d = dist(chain[i], (c['lat'], c['lon']))
            if d < bd: bd = d; best = i
        if best is not None and bd < 600 and best > pos:
            cuts.append((best, c)); pos = best
    if not cuts: return dict(ok=False, msg='Ночёвки лежат далеко от нитки (дальше 600 м) — разрезать не получилось.')
    keep = [p for p in proj['pieces'] if p not in loose]
    src = loose[0].get('src'); a = 0; order = min(p['order'] for p in loose); made = 0
    def add(i0, i1, day):
        nonlocal order, made
        pts = chain[i0:i1]
        if len(pts) < 2: return
        x = dict(id=uid(), src=src, name=f'день {day}', pts=pts, day=day, role='main', order=order, km=round(length_km(pts), 2))
        if tchain: set_times(x, tchain[i0:i1])
        keep.append(x); order += 1; made += 1
    for i, c in cuts:
        day = (date.fromisoformat(c['date']) - start).days + 1
        add(a, i + 1, max(1, day)); a = i
    last = cuts[-1][1]
    add(a, len(chain), (date.fromisoformat(last['date']) - start).days + 1 + (last.get('nights') or 1))
    proj['pieces'] = keep
    return dict(ok=True, msg=f'Нитка разрезана на {made} дн. по {len(cuts)} ночёвкам.')
