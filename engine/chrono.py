"""Хронометраж → точки на треке.

Хронометрист пишет как придётся: «9:45 Суаканская теснина», «10.50 — привал на леднике», даты строками «24.07»,
«24 июля», «День 3». Каждая запись ставится на трек по времени (нужен трек с временем — с часов или навигатора);
если рядом остановка, точка встаёт прямо на неё. Тип угадывается по словам (привал, брод, перевал…).
"""
import bisect, re
from datetime import date, datetime, timedelta, timezone
from .classify import MONTHS, OBJ_TYPES, classify_point, piece_times, time_info, trip_tz, dist

_TIME = re.compile(r'^\s*(?:[-–—•*]\s*)?(\d{1,2})[:.\-](\d{2})(?!\d)\s*(?:[-–—:,.)]\s*)?(.*)$')
_DATE = re.compile(r'^\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s*[.:]?\s*(.*)$')
_DATE_W = re.compile(r'^\s*(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?\b\s*[.:,—-]?\s*(.*)$', re.I)
_DAYN = re.compile(r'^\s*(?:день\s*(\d{1,3})|(\d{1,3})\s*(?:-?й)?\s*день)\b\s*[.:,—-]?\s*(.*)$', re.I)


def parse(text, start=None, year=None):
    """Строки хронометража → [dict(date | None, day | None, hh, mm, text, line)]. Даты и «день N» задают день для строк ниже."""
    out = []; cur_date = None; cur_day = None
    for n, raw in enumerate((text or '').splitlines(), 1):
        line = raw.strip()
        if not line: continue
        # «24.07» (одна, можно с годом и днём недели) — дата; «10.00 спуск», «21.08 обед», «9:45 …» — время
        d = _DATE.match(line); m = _TIME.match(line)
        date_only = d and 1 <= int(d[1]) <= 31 and 1 <= int(d[2]) <= 12 and (d[3] or len(re.sub(r'[\s,.()\-–—]|пн|вт|ср|чт|пт|сб|вс', '', d[4].lower())) == 0)
        if m and not date_only and int(m[1]) < 24 and int(m[2]) < 60:
            out.append(dict(date=cur_date, day=cur_day, hh=int(m[1]), mm=int(m[2]), text=m[3].strip(' -–—,.;:') or '', line=n)); continue
        m = d
        if date_only:
            y = int(m[3]) if m[3] else (year or (start.year if start else date.today().year))
            if y < 100: y += 2000
            try: cur_date = date(y, int(m[2]), int(m[1])); cur_day = None
            except ValueError: pass
            continue
        m = _DATE_W.match(line)
        if m and 1 <= int(m[1]) <= 31:
            mon = next((v for k, v in MONTHS.items() if m[2].lower().startswith(k)), None)
            if mon:
                y = int(m[3]) if m[3] else (year or (start.year if start else date.today().year))
                try: cur_date = date(y, mon, int(m[1])); cur_day = None
                except ValueError: pass
                continue
        m = _DAYN.match(line)
        if m:
            cur_day = int(m[1] or m[2]); cur_date = (start + timedelta(days=cur_day - 1)) if start else None
            continue
    return out


def place(proj, text):
    """Записи хронометража, поставленные на трек: [dict(time, text, type, name, lat, lon, where, mins, day)].
    where: stop — на остановке (mins — её длина), move — на ходу, none — в это время трека нет."""
    meta = proj.get('meta', {})
    start = date.fromisoformat(meta['start_date']) if meta.get('start_date') else None
    timed = []                                # (время, lat, lon, день) по всем кускам с временем
    stops = []                                # (начало, конец, lat, lon)
    lon0 = None
    for p in proj.get('pieces', []):
        if p.get('role') == 'skip': continue
        ts = piece_times(p)
        if not ts: continue
        lon0 = lon0 if lon0 is not None else p['pts'][0][1]
        timed += [(t, q[0], q[1], p.get('day')) for t, q in zip(ts, p['pts'])]
        ti = time_info(p['pts'], ts)
        if ti: stops += [(a, b, la, lo) for la, lo, a, b in ti['stops']]
    if not timed: return dict(ok=False, msg='В походе нет трека с временем — хронометраж не к чему привязать. Загрузи GPX с часов или навигатора.', items=[])
    timed.sort(); T = [x[0] for x in timed]
    tz = trip_tz(proj, lon0)
    days_with_time = sorted({datetime.fromtimestamp(t + tz * 3600, timezone.utc).date() for t in T[::max(1, len(T) // 400)]})
    rows = parse(text, start, start.year if start else datetime.fromtimestamp(T[0], timezone.utc).year)
    if not rows: return dict(ok=False, msg='Не нашёл ни одной строки вида «9:45 описание».', items=[])
    # строки без даты: день трека по порядку — время пошло назад, значит, наступил следующий день
    cur = days_with_time[0]; last = -1
    for r in rows:
        if r['date']: cur = r['date']; last = -1
        else:
            mins = r['hh'] * 60 + r['mm']
            if mins < last:
                nxt = [d for d in days_with_time if d > cur]; cur = nxt[0] if nxt else cur + timedelta(days=1)
            r['date'] = cur; last = mins
            continue
        last = r['hh'] * 60 + r['mm']
    items = []
    for r in rows:
        loc = datetime(r['date'].year, r['date'].month, r['date'].day, r['hh'], r['mm'], tzinfo=timezone.utc).timestamp()
        t = loc - tz * 3600
        it = dict(date=r['date'].isoformat(), time=f"{r['hh']}:{r['mm']:02d}", text=r['text'], line=r['line'], where='none', lat=None, lon=None, mins=None, day=None)
        st = next((s for s in stops if s[0] - 300 <= t <= s[1] + 300), None)
        k = bisect.bisect_left(T, t)
        if st:
            it.update(where='stop', lat=round(st[2], 6), lon=round(st[3], 6), mins=round((st[1] - st[0]) / 60))
            it['day'] = timed[min(k, len(T) - 1)][3]
        elif 0 < k < len(T) and T[k] - T[k - 1] < 3 * 3600 or (k < len(T) and abs(T[k] - t) < 900) or (k > 0 and abs(t - T[k - 1]) < 900):
            if 0 < k < len(T):
                a, b = timed[k - 1], timed[k]; f = 0 if b[0] == a[0] else (t - a[0]) / (b[0] - a[0])
                la, lo = a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f; day = a[3] or b[3]
            else:
                x = timed[min(k, len(T) - 1)]; la, lo, day = x[1], x[2], x[3]
            it.update(where='move', lat=round(la, 6), lon=round(lo, 6), day=day)
        c = classify_point(r['text'] or 'отметка', '', '', r['date'].year)
        typ = c['type'] if c['type'] not in ('skip', 'camp', 'campsite', 'photo') else ('camp' if c['type'] == 'camp' else 'mark')
        # у перевала категория и высота уходят в свои поля, в названии — только «пер. Долра»
        it.update(type=typ, name=(c.get('name') if typ in OBJ_TYPES and c.get('name') else r['text']) or 'отметка', grade=c.get('grade'), h=c.get('h'))
        items.append(it)
    placed = sum(1 for i in items if i['where'] != 'none')
    return dict(ok=True, items=items, msg=f'Разобрано записей: {len(items)}, на трек встало {placed}.')
