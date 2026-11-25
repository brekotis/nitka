"""«График движения» в .docx — без сторонних библиотек: docx — это zip с несколькими XML.

Берёт сводку сборки (summary.json): дни, ночёвки, варианты, препятствия. Лист альбомный, A4,
Times New Roman 10 — как принято в отчётах и заявках.
"""
import io, zipfile
from xml.sax.saxutils import escape

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
HEAD_FILL = 'D5E9F7'


def _run(text, bold=False, size=None, italic=False, color=None):
    rp = ('<w:b/>' if bold else '') + ('<w:i/>' if italic else '') + (f'<w:color w:val="{color}"/>' if color else '') + (f'<w:sz w:val="{size}"/>' if size else '')
    parts = str(text).split('\n')
    t = '<w:br/>'.join(f'<w:t xml:space="preserve">{escape(p)}</w:t>' for p in parts)
    return f'<w:r>{"<w:rPr>" + rp + "</w:rPr>" if rp else ""}{t}</w:r>'

def _p(text='', bold=False, size=None, align=None, after=60, italic=False, color=None):
    pp = (f'<w:jc w:val="{align}"/>' if align else '') + f'<w:spacing w:before="0" w:after="{after}"/>'
    return f'<w:p><w:pPr>{pp}</w:pPr>{_run(text, bold, size, italic, color) if text != "" else ""}</w:p>'

def _table(head, rows, widths, num_cols=(), total=None):
    """head — заголовки; rows — строки (списки строк); widths — ширины колонок, dxa; num_cols — номера колонок с числами (вправо)."""
    b = ''.join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="133C6A"/>' for s in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{b}</w:tblBorders><w:tblLayout w:type="fixed"/>'
           '<w:tblCellMar><w:top w:w="30" w:type="dxa"/><w:left w:w="70" w:type="dxa"/><w:bottom w:w="30" w:type="dxa"/><w:right w:w="70" w:type="dxa"/></w:tblCellMar></w:tblPr>',
           '<w:tblGrid>' + ''.join(f'<w:gridCol w:w="{w}"/>' for w in widths) + '</w:tblGrid>']
    def row(cells, header=False, bold=False):
        x = ['<w:tr>' + ('<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>' if header else '<w:trPr><w:cantSplit/></w:trPr>')]
        for i, (c, w) in enumerate(zip(cells, widths)):
            shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{HEAD_FILL}"/>' if header else ''
            al = 'right' if (i in num_cols and not header) else ('center' if header else None)
            x.append(f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shd}<w:vAlign w:val="{"center" if header else "top"}"/></w:tcPr>'
                     + _p(c, bold=header or bold, size=18 if header else None, align=al, after=0) + '</w:tc>')
        x.append('</w:tr>'); return ''.join(x)
    out.append(row(head, header=True))
    out += [row(r) for r in rows]
    if total: out.append(row(total, bold=True))
    out.append('</w:tbl>')
    return ''.join(out)


def schedule_docx(summary, meta, title):
    """Байты .docx «График движения»."""
    S = summary; plan = S.get('plan')
    times = any(d.get('t_start') for d in S.get('days', []))
    body = [_p('График движения' + (' (план)' if plan else ''), bold=True, size=30, after=40),
            _p(' · '.join(x for x in [title, (meta.get('area') or '').strip(), meta.get('kind') or '', S.get('period') or ''] if x), size=22, after=40)]
    k = S.get('kcoef') or 1
    line = f"Протяжённость {str(S['km']).replace('.', ',')} км" + (f" (длина по карте × {str(k).replace('.', ',')})" if abs(k - 1) > 1e-6 else '') + f", набор +{S['up']} м, сброс −{S['dn']} м"
    if S.get('move'): line += f", чистое ходовое время {S['move']}"
    body.append(_p(line + '.', after=160))

    camps = {c['after']: c for c in S.get('camps', [])}
    days = {d['n']: d for d in S.get('days', [])}
    rest = set(S.get('rest', []))
    head = ['День', 'Дата', 'Участок маршрута'] + ['Км', 'Набор, м', 'Сброс, м'] + (['Выход', 'Приход', 'ЧХВ'] if times else []) + ['Ночёвка']
    widths = [620, 820, 6000 if times else 7700, 760, 900, 900] + ([780, 780, 780] if times else []) + [2600 if times else 3400]
    rows = []
    for n in range(1, max(list(days) + list(rest) + [0]) + 1):
        c = camps.get(n); camp = ', '.join(x for x in ([c['lab'], f"{c['h']} м" if c.get('h') else '', c.get('name') or ''] if c else []) if x)
        if n in days:
            d = days[n]
            route = d.get('route') or ''
            if d.get('objects') and not route: route = '; '.join(d['objects'])
            rows.append([str(n), d.get('date') or '', route, str(d['km']).replace('.', ','), f"+{d['up']}", f"−{d['dn']}"]
                        + ([d.get('t_start') or '', d.get('t_end') or '', d.get('move') or ''] if times else []) + [camp])
        elif n in rest:
            rows.append([str(n), '', 'Днёвка', '', '', ''] + (['', '', ''] if times else []) + [camp])
    total = ['', '', 'Всего', str(S['km']).replace('.', ','), f"+{S['up']}", f"−{S['dn']}"] + (['', '', S.get('move') or ''] if times else []) + ['']
    nums = (3, 4, 5, 6, 7, 8) if times else (3, 4, 5)
    body.append(_table(head, rows, widths, nums, total))

    objs = S.get('objects') or []
    if objs:
        body.append(_p('', after=120)); body.append(_p('Препятствия по ходу маршрута', bold=True, size=24, after=60))
        orow = []
        for o in objs:
            where = ('дни ' if len(o['days']) > 1 else 'день ') + ', '.join(map(str, o['days'])) if o.get('days') else ('вариант ' + ', '.join(o['vars']) if o.get('vars') else '')
            orow.append([str(o['num']), o['title'], o.get('grade') or '', str(o['h']) if o.get('h') else '', where, 'определяющее' if o.get('key') else ''])
        body.append(_table(['№', 'Название', 'Категория', 'Высота, м', 'Где', 'Примечание'], orow, [600, 6200, 1400, 1300, 2200, 2400], (3,)))

    vs = S.get('variants') or []
    if vs:
        body.append(_p('', after=120)); body.append(_p('Запасные варианты и аварийные выходы', bold=True, size=24, after=60))
        vrow = [[v['lab'], v['title'] + (('\n' + v['note']) if v.get('note') else ''), v['route'], str(v['km']).replace('.', ','), f"+{v['up']}", f"−{v['dn']}", '; '.join(v.get('objects') or [])] for v in vs]
        body.append(_table(['№', 'Вариант', 'Откуда — куда', 'Км', 'Набор, м', 'Сброс, м', 'Препятствия'], vrow, [600, 4300, 3000, 800, 950, 950, 3500], (3, 4, 5)))
        body.append(_p('Варианты не входят в протяжённость маршрута. Набор и сброс вариантов — по модели рельефа.', italic=True, size=18, after=0))

    body.append(_p(('Высоты — из записи трека' if S.get('elev') == 'track' else 'Высоты — по цифровой модели рельефа') + '. Подготовлено в «Нитке».', italic=True, size=18, color='56677D'))
    sect = ('<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
            '<w:pgMar w:top="850" w:right="850" w:bottom="850" w:left="850" w:header="400" w:footer="400" w:gutter="0"/></w:sectPr>')
    doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>{"".join(body)}{sect}</w:body></w:document>'
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}><w:docDefaults><w:rPrDefault><w:rPr>'
              '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman" w:eastAsia="Times New Roman"/>'
              '<w:sz w:val="20"/><w:szCs w:val="20"/><w:lang w:val="ru-RU"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    drels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct); z.writestr('_rels/.rels', rels)
        z.writestr('word/document.xml', doc); z.writestr('word/styles.xml', styles); z.writestr('word/_rels/document.xml.rels', drels)
    return buf.getvalue()
