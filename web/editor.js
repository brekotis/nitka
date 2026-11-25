// Редактор трека: правка одного или нескольких кусков нитки на карте — сессия «Сохранить / Отмена».
// Подсмотрено у gpx.studio, nakarte.me, Locus, OsmAnd и Leaflet.draw:
// • точки показываются «уровнями» (по Дугласу—Пекеру): издалека — только опорные, ближе — всё подробнее;
//   опорную тянешь — скрытые соседи сдвигаются плавно, «резинкой», без зубца;
// • кружок между соседними точками — потяни, появится новая точка;
// • выделенную точку можно удалить (Delete, кнопка, правый клик) или обрезать трек до/после неё;
// • «Спрямить участок», «Перерисовать» (можно «по тропе»), «Удалить в рамке», «Продолжить»;
// • несколько кусков сразу: другой кусок на карте — «Править вместе»; спрямлять и перерисовывать можно через стык
//   соединённых кусков; куски остаются отдельными, соединяющий отрезок достаётся первому;
// • всё отменяется (Ctrl+Z / Ctrl+Y), на сервер уходит только по «Сохранить».
//
// Внутри все куски — одна цепочка точек [lat, lon, id куска] в порядке маршрута; при сохранении она снова
// раскладывается по кускам. Между несоединёнными кусками (переезд) линия не рисуется и участок не выбирается.

export function createEditor(ctx) {
  const { L, getMap, api, toast, pieceById, onSaved, onClose, confirmBox, esc } = ctx;
  const MAX_HANDLES = 350, JOIN_M = 300;
  let E = null;

  const ll = q => [q[0], q[1]];
  const geo = (a, b) => L.latLng(ll(a)).distanceTo(ll(b));
  const len = pts => { let s = 0; for (let i = 1; i < pts.length; i++) if (!brk(i, pts)) s += geo(pts[i - 1], pts[i]); return s; };
  const kmTxt = m => (m / 1000).toFixed(m < 10000 ? 2 : 1).replace('.', ',');
  const pl = n => { n = Math.abs(n) % 100; const m = n % 10; return n > 10 && n < 15 ? 'точек' : m === 1 ? 'точка' : m >= 2 && m <= 4 ? 'точки' : 'точек'; };
  const touch = () => matchMedia('(pointer: coarse)').matches;
  const changed = () => { E.ver++; E.dirty = true; };
  const snap = () => { E.undo.push(E.pts.map(q => q.slice())); if (E.undo.length > 200) E.undo.shift(); E.redo = []; changed(); };
  // разрыв в цепочке перед точкой i: куски не соединены (переезд) — линия не рисуется, участок через него не выбирается
  function brk(i, pts = E.pts) { const a = pts[i - 1], b = pts[i]; return !!(a && b && a[2] !== b[2] && !E.conn.has(a[2] + '|' + b[2])); }
  const runOf = i => { let a = i, b = i; while (a > 0 && !brk(a)) a--; while (b + 1 < E.pts.length && !brk(b + 1)) b++; return [a, b]; };
  const partName = id => (E.parts.find(p => p.id === id) || {}).name || '';
  const HINT = {
    move: () => (touch() ? 'Тяни точку — передвинешь. Нажми на точку — «Удалить», «Обрезать до» и «после». Кружок между точками — потяни, будет новая точка. Бледный кусок на карте — нажми, чтобы править вместе.'
      : 'Тяни точку — передвинешь (скрытые соседи сдвинутся плавно). Нажми на точку — выделится: Delete — удалить, ещё — «Обрезать до/после». Правый клик — удалить сразу. Кружок между точками — потяни, будет новая точка. Бледный кусок на карте — нажми, чтобы править вместе.'),
    cut: () => 'Всё между двумя выбранными точками уберётся, края соединятся прямой — так лечатся выбросы GPS, «зубцы» и клубки, в том числе на стыке кусков.',
    redraw: () => 'Участок между двумя выбранными точками заменится новым путём. «По тропе» — путь между кликами проложится по тропам.',
    box: () => 'Точки трека внутри рамки удалятся — например, «звёздочка» на стоянке или мусор от сбоя GPS.',
    extend: () => 'Каждый клик — новая точка; ↶ — убрать последнюю.'
  };

  // ---------------------------------------------------------------- куски в сессии
  const routeKey = p => [p.day || 999, p.order || 0];
  function buildChain(parts) {      // куски по ходу маршрута → одна цепочка; какие соседи соединены
    parts.sort((a, b) => { const x = routeKey(a), y = routeKey(b); return x[0] - y[0] || x[1] - y[1]; });
    const pts = [], conn = new Set();
    parts.forEach((p, k) => {
      const q = p.pts;
      if (k > 0) {
        const prev = parts[k - 1].pts;
        if (prev.length && q.length && geo(prev[prev.length - 1], q[0]) < JOIN_M) {
          conn.add(parts[k - 1].id + '|' + p.id);
          // конец первого куска совпадает с началом второго (так их сохраняет редактор) — одна точка на стыке, не две
          if (prev.length > 2 && geo(prev[prev.length - 1], q[0]) < 0.5 && pts.length && pts[pts.length - 1][2] === parts[k - 1].id) pts.pop();
        }
      }
      q.forEach(x => pts.push([x[0], x[1], p.id]));
    });
    return { pts, conn };
  }
  function splitChain() {      // цепочка → точки по кускам; к концу куска добавляется начало следующего соединённого
    const by = {}; E.parts.forEach(p => { by[p.id] = []; });
    E.pts.forEach(q => (by[q[2]] = by[q[2]] || []).push([q[0], q[1]]));
    const order = E.parts.map(p => p.id).filter(id => by[id] && by[id].length);
    for (let k = 0; k + 1 < order.length; k++) {
      const a = order[k], b = order[k + 1];
      if (!E.conn.has(a + '|' + b)) continue;
      const A = by[a], B = by[b]; const last = A[A.length - 1], first = B[0];
      if (geo(last, first) > 0.5) A.push([first[0], first[1]]);      // стык без разрыва: соединяющий отрезок — у первого куска
    }
    return by;
  }
  const copyPart = x => ({ id: x.id, name: x.name, day: x.day, order: x.order, pts: x.pts.map(q => [q[0], q[1]]) });

  function open(pieceId, opts = {}) {
    const p = pieceById(pieceId); const map = getMap();
    if (!p || !map) return;
    if (E) close(true);
    const ids = [pieceId, ...(opts.with || [])].filter((v, i, a) => a.indexOf(v) === i);
    const parts = ids.map(id => pieceById(id)).filter(Boolean).map(copyPart);
    const ch = buildChain(parts);
    E = { parts, pts: ch.pts, conn: ch.conn, undo: [], redo: [], tool: opts.tool || 'move', sel: null, pick: [], draft: [], steps: [], box: [], end: 'end',
      dirty: false, ver: 0, rk: null, rkVer: -1, layer: L.layerGroup().addTo(map), route: false, ask: null, suggested: false };
    // выбранные точки из проверки трека: {piece, i} или номер точки первого куска → номер в цепочке
    E.pick = (opts.pick || []).map(x => typeof x === 'number' ? idxOf(pieceId, x) : idxOf(x.piece || pieceId, x.i)).filter(i => i >= 0).sort((a, b) => a - b);
    document.body.classList.add('editing');
    buildBar(); map.on('moveend zoomend', render); document.addEventListener('keydown', onKey);
    const fx = opts.focus && E.pick.length ? E.pts.slice(Math.max(0, E.pick[0] - 2), E.pick[E.pick.length - 1] + 3) : E.pts;
    ctx.redraw(); render();
    // верх карты закрыт панелью редактора — наводимся на видимую часть
    const top = E.bar ? E.bar.getBoundingClientRect().bottom - map.getContainer().getBoundingClientRect().top + 8 : 0;
    map.fitBounds(L.latLngBounds(fx.map(ll)).pad(opts.focus ? 0.6 : 0.08), { maxZoom: opts.focus ? map.getMaxZoom() : 16, paddingTopLeft: [0, Math.max(0, top)], animate: false });
  }
  function idxOf(pid, i) {      // номер точки куска → номер в цепочке (точка на стыке могла слиться с началом следующего)
    let k = 0, last = -1;
    for (const q of E.pts) { if (q[2] === pid) { if (i === 0) return k; i--; last = k; } k++; }
    return i === 0 && last >= 0 && last + 1 < E.pts.length ? last + 1 : -1;
  }

  function addPart(pid) {      // кусок — в ту же правку
    const p = pieceById(pid); if (!p || E.parts.some(x => x.id === pid)) return;
    const by = splitChain();
    E.parts.forEach(x => { if (by[x.id] && by[x.id].length) x.pts = by[x.id]; });
    E.parts.push(copyPart(p));
    const ch = buildChain(E.parts); E.pts = ch.pts; E.conn = ch.conn; E.undo = []; E.redo = []; E.ver++; E.sel = null; E.pick = []; E.ask = null;
    ctx.redraw(); render();
    const joined = [...E.conn].some(k => k.split('|').includes(pid));
    toast(joined ? `«${p.name}» — в правке. Куски стыкуются: спрямлять и перерисовывать можно через стык.` : `«${p.name}» — в правке. С остальными он не стыкуется (между ними переезд) — через этот разрыв участок не выбрать.`);
  }

  function close(force) {
    if (!E) return;
    const done = () => {
      const map = getMap(); map && map.off('moveend zoomend', render); document.removeEventListener('keydown', onKey);
      E.layer.remove(); E.bar && E.bar.remove(); E = null; document.body.classList.remove('editing'); ctx.redraw(); onClose && onClose();
    };
    if (!force && E.dirty) return confirmBox('Выйти без сохранения?', 'Правки пропадут.', 'Выйти', done);
    done();
  }

  async function save(after) {
    if (!E) return;
    if (!E.dirty) { close(true); return after && after(); }
    const by = splitChain(), items = [];
    for (const p of E.parts) {
      const pts = by[p.id];
      if (!pts || pts.length < 2) return toast(`В куске «${p.name}» осталось меньше двух точек — так нельзя. Отмени последнее действие`, true);
      const same = pts.length === p.pts.length && pts.every((q, i) => Math.abs(q[0] - p.pts[i][0]) < 1e-7 && Math.abs(q[1] - p.pts[i][1]) < 1e-7);
      if (!same) items.push({ piece: p.id, pts });
    }
    if (!items.length) { close(true); return after && after(); }
    try {
      const r = await api('POST', `/api/projects/${ctx.pid()}/track-edit`, { action: 'replace_many', items }, false, 'Сохраняю трек…');
      toast(r.msg, !r.ok); if (!r.ok) return;
      E.dirty = false; close(true); onSaved && onSaved(r); after && after();
    } catch (e) { toast(e.message || 'Не получилось сохранить', true); }
  }

  // ---------------------------------------------------------------- «уровни» точек
  function ranks() {
    if (E.rkVer === E.ver && E.rk) return E.rk;
    const P = E.pts, n = P.length, r = new Float64Array(n);
    const k = Math.cos(P[0][0] * Math.PI / 180), X = P.map(q => q[1] * 111320 * k), Y = P.map(q => q[0] * 110540);
    const st = [];
    let a = 0;
    for (let i = 1; i <= n; i++) if (i === n || P[i][2] !== P[i - 1][2]) { r[a] = r[i - 1] = Infinity; st.push([a, i - 1, Infinity]); a = i; }      // края кусков — всегда с ручкой
    while (st.length) {
      const [s, e, par] = st.pop(); if (e - s < 2) continue;
      const dx = X[e] - X[s], dy = Y[e] - Y[s], D = Math.hypot(dx, dy); let best = -1, bi = s + 1;
      for (let i = s + 1; i < e; i++) { const d = D > 0 ? Math.abs(dx * (Y[s] - Y[i]) - dy * (X[s] - X[i])) / D : Math.hypot(X[i] - X[s], Y[i] - Y[s]); if (d > best) { best = d; bi = i; } }
      r[bi] = Math.min(best, par); st.push([s, bi, r[bi]], [bi, e, r[bi]]);
    }
    E.rk = r; E.rkVer = E.ver; return r;
  }
  function shown() {
    const map = getMap(), b = map.getBounds().pad(0.15), r = ranks(), z = map.getZoom();
    const mpp = 40075016 * Math.cos(map.getCenter().lat * Math.PI / 180) / 2 ** (z + 8);
    let tol = mpp * (touch() ? 12 : 7), out;
    for (let k = 0; k < 12; k++) {
      out = [];
      E.pts.forEach((q, i) => { if ((r[i] >= tol || i === E.sel) && b.contains(ll(q))) out.push(i); });
      if (out.length <= MAX_HANDLES) break; tol *= 1.8;
    }
    return out;
  }

  // ---------------------------------------------------------------- панель
  function buildBar() {
    const wrap = document.querySelector('.mapwrap'); if (!wrap) return;
    E.bar = document.createElement('div'); E.bar.className = 'edbar'; wrap.append(E.bar);
    E.bar.addEventListener('click', ev => {
      const b = ev.target.closest('[data-ed]'); if (!b) return; ev.stopPropagation();
      const a = b.dataset.ed;
      if (a === 'tool') { E.tool = b.dataset.v; E.pick = []; E.draft = []; E.steps = []; E.box = []; E.sel = null; E.suggested = false; render(); }
      else if (a === 'end') { E.end = b.dataset.v; render(); }
      else if (a === 'route') {
        E.route = !E.route;
        if (E.route && E.tool === 'redraw' && E.pick.length === 2 && !E.draft.length) return suggest();
        if (!E.route && E.suggested) { E.draft = []; E.steps = []; E.suggested = false; }
        toast(E.route ? 'По тропе: теперь кликай по карте — путь между кликами проложится по тропам' : 'Прямыми отрезками: путь пойдёт прямо через твои клики'); render();
      }
      else if (a === 'suggest') suggest();
      else if (a === 'undo') undo(); else if (a === 'redo') redo();
      else if (a === 'delpt') delPoint(E.sel); else if (a === 'trimb') trim('before'); else if (a === 'trima') trim('after');
      else if (a === 'cutgo') applyCut(); else if (a === 'drawgo') applyRedraw(); else if (a === 'draftback') draftBack(); else if (a === 'boxgo') applyBox();
      else if (a === 'reset') { E.pick = []; E.draft = []; E.steps = []; E.box = []; E.suggested = false; render(); }
      else if (a === 'join') addPart(E.ask.id);
      else if (a === 'goto') {
        const id = E.ask.id; E.ask = null; const go = () => open(id);
        if (E.dirty) confirmBox('Перейти к другому куску?', 'Несохранённые правки этого куска пропадут. Чтобы их оставить — «Сохранить», потом нажми на кусок ещё раз. Или «Править вместе».', 'Перейти', () => { E.dirty = false; go(); });
        else go();
      }
      else if (a === 'askno') { E.ask = null; render(); }
      else if (a === 'save') save(); else if (a === 'cancel') close(false);
    });
    L.DomEvent.disableClickPropagation(E.bar); L.DomEvent.disableScrollPropagation(E.bar);
  }
  function inBox() { if (E.box.length < 2) return []; const bb = L.latLngBounds(E.box.map(ll)); const o = []; E.pts.forEach((q, i) => { if (bb.contains(ll(q))) o.push(i); }); return o; }
  function paintBar(info) {
    if (!E.bar) return;
    const t = (v, label, title) => `<button class="${E.tool === v ? 'on' : ''}" data-ed="tool" data-v="${v}" title="${title}">${label}</button>`;
    let sub = '', step = '', stepBtn = '';
    if (E.tool === 'move' && E.sel != null) {
      const [ra, rb] = runOf(E.sel);
      const before = len(E.pts.slice(ra, E.sel + 1)), after = len(E.pts.slice(E.sel, rb + 1));
      sub = `<button class="btn sm warn" data-ed="delpt">Удалить точку</button>${E.sel > ra ? `<button class="btn sm" data-ed="trimb" title="Убрать всё от начала до этой точки">Обрезать до неё · ${kmTxt(before)} км</button>` : ''}${E.sel < rb ? `<button class="btn sm" data-ed="trima" title="Убрать всё от этой точки до конца">Обрезать после · ${kmTxt(after)} км</button>` : ''}`;
    } else if (E.tool === 'cut' || E.tool === 'redraw') {
      step = E.pick.length === 0 ? '1/3 · Нажми на оранжевый трек в начале участка'
        : E.pick.length === 1 ? '2/3 · Теперь нажми на трек в конце участка'
        : E.tool === 'cut' ? '3/3 · Нажми «Спрямить» — участок между красными точками станет прямой'
        : E.suggested ? `3/3 · Предложил путь по тропе — зелёный, ${kmTxt(len([E.pts[E.pick[0]], ...E.draft, E.pts[E.pick[1]]]))} км. Подходит — «Готово». Не туда — кликни по карте, где шли: путь пойдёт через эту точку`
        : E.draft.length ? `3/3 · Кликай дальше по пути${E.route ? ' (между кликами — по тропе)' : ''}, потом «Готово»`
        : `3/3 · Как шли между красными точками? Кликай по карте, где шли${E.route ? ' — между кликами путь пойдёт по тропе' : ''}, или пусть путь предложит «Нитка»`;
      if (E.tool === 'redraw' && E.pick.length === 2 && !E.draft.length) stepBtn = `<button class="btn sm go" data-ed="suggest" title="Путь по тропам OpenStreetMap между красными точками (BRouter)">↯ Проложить по тропе</button>`;
      if (E.pick.length === 2) {
        const n = E.pick[1] - E.pick[0] - 1, L0 = len(E.pts.slice(E.pick[0], E.pick[1] + 1)), L1 = geo(E.pts[E.pick[0]], E.pts[E.pick[1]]);
        const across = E.pts[E.pick[0]][2] !== E.pts[E.pick[1]][2] ? ' · через стык' : '';
        sub = E.tool === 'cut' ? `<button class="btn sm warn" data-ed="cutgo">Спрямить: ${n} ${pl(n)}, ${kmTxt(L0)} → ${kmTxt(L1)} км${across}</button><button class="btn sm" data-ed="reset">Заново</button>`
          : `${E.draft.length ? '<button class="btn sm" data-ed="draftback">↶ точку</button>' : ''}<button class="btn sm go" data-ed="drawgo">Готово — заменить участок${across}</button><button class="btn sm" data-ed="reset">Заново</button>`;
      }
    } else if (E.tool === 'box') {
      step = E.box.length === 0 ? '1/3 · Нажми на карте в одном углу рамки' : E.box.length === 1 ? '2/3 · Теперь — в противоположном углу' : '3/3 · Нажми «Удалить … в рамке» — или поставь рамку заново';
      const n = inBox().length; if (E.box.length === 2) sub = `<button class="btn sm warn" data-ed="boxgo" ${n ? '' : 'disabled'}>Удалить ${n} ${pl(n)} в рамке</button><button class="btn sm" data-ed="reset">Заново</button>`;
    } else if (E.tool === 'extend') {
      step = `Кликни на карте, куда продолжить трек ${E.end === 'end' ? 'от конца' : 'от начала'} (зелёный кружок)${E.route ? ' — путь проложится по тропе' : ''}`;
      sub = `<span class="seg sm"><button class="${E.end === 'start' ? 'on' : ''}" data-ed="end" data-v="start">от начала</button><button class="${E.end === 'end' ? 'on' : ''}" data-ed="end" data-v="end">от конца</button></span>`;
    }
    const np = E.parts.length;
    const names = np > 1 ? `${np} ${np < 5 ? 'куска' : 'кусков'}: ${E.parts.map(p => '«' + esc(p.name) + '»').join(', ')}` : `«${esc(E.parts[0].name)}»`;
    const ask = E.ask ? `<div class="edask"><span><b>«${esc(E.ask.name)}»${E.ask.day ? ` (день ${E.ask.day})` : ''} — другой кусок.</b> Сейчас правится ${names}. Их можно править вместе — спрямлять и перерисовывать через стык.</span>
        <span class="chips"><button class="btn sm go" data-ed="join">Править вместе</button><button class="btn sm" data-ed="goto">Перейти к нему</button><button class="btn sm ghost" data-ed="askno">Нет</button></span></div>` : '';
    E.bar.innerHTML = `<div class="edrow"><b class="edt">Редактор трека</b><span class="edn">${names} · ${kmTxt(len(E.pts))} км · ${E.pts.length} ${pl(E.pts.length)}${E.dirty ? ' · <i>не сохранено</i>' : ''}</span>
        <span class="sp"></span><button class="icon" data-ed="undo" title="Отменить (Ctrl+Z)" ${E.undo.length ? '' : 'disabled'}>↶</button><button class="icon" data-ed="redo" title="Вернуть (Ctrl+Y)" ${E.redo.length ? '' : 'disabled'}>↷</button>
        <button class="btn sm" data-ed="cancel" title="Выйти без сохранения (Esc)">Отмена</button><button class="btn sm go" data-ed="save" title="Сохранить (Ctrl+S)">Сохранить</button></div>
      <div class="edrow"><span class="seg sm tools">${t('move', '● Точки', 'Двигать, удалять и добавлять точки')}${t('cut', '✂ Спрямить участок', 'Убрать участок — края соединятся прямой')}${t('redraw', '✎ Перерисовать', 'Заменить участок новым путём')}${t('box', '▭ Удалить в рамке', 'Удалить точки внутри рамки')}${t('extend', '➜ Продолжить', 'Дорисовать трек от начала или конца')}</span>
        ${E.tool === 'redraw' || E.tool === 'extend' ? `<button class="chip${E.route ? ' on' : ''}" data-ed="route" title="Путь между кликами — по тропам OpenStreetMap (BRouter); вне троп — прямая">по тропе</button>` : ''}${sub}</div>
      ${ask}${step && !ask ? `<div class="edstep"><span>${step}</span>${stepBtn}</div>` : ''}<div class="edhint">${info || HINT[E.tool]()}</div>`;
  }

  // ---------------------------------------------------------------- карта
  let lines = [];
  function runs() { const out = []; let a = 0; for (let i = 1; i <= E.pts.length; i++) if (i === E.pts.length || brk(i)) { out.push([a, i - 1]); a = i; } return out; }
  function render() {
    if (!E) return; const map = getMap(); E.layer.clearLayers(); lines = [];
    for (const [a, b] of runs()) {
      const seg = E.pts.slice(a, b + 1).map(ll);
      L.polyline(seg, { color: '#fff', weight: 10, opacity: .95, interactive: false }).addTo(E.layer);
      lines.push([a, L.polyline(seg, { color: '#F68A1D', weight: 5, interactive: false }).addTo(E.layer)]);
    }
    // стыки кусков — жёлтые кружки
    for (let i = 1; i < E.pts.length; i++) if (E.pts[i][2] !== E.pts[i - 1][2] && !brk(i)) L.circleMarker(ll(E.pts[i]), { radius: 5, color: '#133C6A', weight: 2, fillColor: '#FEBC0E', fillOpacity: 1, interactive: false }).addTo(E.layer);
    if (E.pick.length) {
      const [a, b] = E.pick.length === 2 ? E.pick : [E.pick[0], E.pick[0]];
      if (b > a) L.polyline(E.pts.slice(a, b + 1).map(ll), { color: '#E2001A', weight: 7, dashArray: '8 7', interactive: false }).addTo(E.layer);
      if (E.tool === 'cut' && E.pick.length === 2) L.polyline([ll(E.pts[a]), ll(E.pts[b])], { color: '#0B8A3E', weight: 4, dashArray: '4 6', interactive: false }).addTo(E.layer);
      E.pick.forEach(i => L.circleMarker(ll(E.pts[i]), { radius: 8, color: '#E2001A', weight: 3, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(E.layer));
    }
    if (E.tool === 'redraw' && E.pick.length === 2) {
      const ch = [E.pts[E.pick[0]], ...E.draft, E.pts[E.pick[1]]].map(ll);
      L.polyline(ch, { color: '#0B8A3E', weight: 5, dashArray: '9 6', interactive: false }).addTo(E.layer);
      E.draft.forEach(q => L.circleMarker(ll(q), { radius: 4, color: '#0B8A3E', weight: 2, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(E.layer));
    }
    if (E.tool === 'box' && E.box.length) {
      if (E.box.length === 2) { L.rectangle(L.latLngBounds(E.box.map(ll)), { color: '#E2001A', weight: 2, dashArray: '6 5', fillOpacity: .08, interactive: false }).addTo(E.layer);
        inBox().forEach(i => L.circleMarker(ll(E.pts[i]), { radius: 4, color: '#E2001A', weight: 2, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(E.layer)); }
      else L.circleMarker(ll(E.box[0]), { radius: 6, color: '#E2001A', weight: 3, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(E.layer);
    }
    if (E.tool === 'extend') {
      const q = E.end === 'end' ? E.pts[E.pts.length - 1] : E.pts[0];
      L.circleMarker(ll(q), { radius: 9, color: '#0B8A3E', weight: 3, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(E.layer);
    }
    let info = '';
    if (E.tool === 'move') {
      const S = shown(), sz = touch() ? 22 : 14, icon = cls => L.divIcon({ className: cls, iconSize: [sz, sz], iconAnchor: [sz / 2, sz / 2] });
      const all = E.pts.length;
      if (all - S.length > 0 && S.length) info = `Показаны опорные точки: ${S.length} из ${all} в кадре и рядом — приблизь карту, станет подробнее.`;
      for (let k = 0; k < S.length; k++) {
        const i = S[k], [ra, rb] = runOf(i);
        const a = k > 0 && S[k - 1] >= ra ? S[k - 1] : ra, b = k + 1 < S.length && S[k + 1] <= rb ? S[k + 1] : rb;
        if (k + 1 < S.length && S[k + 1] <= rb) {
          const j = S[k + 1], pa = map.latLngToContainerPoint(ll(E.pts[i])), pb = map.latLngToContainerPoint(ll(E.pts[j]));
          if (Math.hypot(pa.x - pb.x, pa.y - pb.y) > (touch() ? 56 : 36)) {
            const m = j - i >= 2 ? Math.round((i + j) / 2) : null;
            const pos = m != null ? ll(E.pts[m]) : [(E.pts[i][0] + E.pts[j][0]) / 2, (E.pts[i][1] + E.pts[j][1]) / 2];
            const g = L.marker(pos, { icon: icon('edg'), draggable: true, keyboard: false, title: m != null ? 'Потяни — участок сдвинется плавно' : 'Потяни — добавится точка' }).addTo(E.layer);
            if (m != null) rubber(g, m, i, j);
            else {
              g.on('dragstart', () => snap());
              g.on('drag', ev => { const c = ev.target.getLatLng(); redrawLines([...E.pts.slice(0, i + 1), [c.lat, c.lng, E.pts[i][2]], ...E.pts.slice(i + 1)]); });
              g.on('dragend', ev => { const c = ev.target.getLatLng(); E.pts.splice(i + 1, 0, [+c.lat.toFixed(6), +c.lng.toFixed(6), E.pts[i][2]]); changed(); E.sel = i + 1; render(); });
            }
          }
        }
        const h = L.marker(ll(E.pts[i]), { icon: icon('edh' + (E.sel === i ? ' sel' : '') + (i === ra || i === rb ? ' end' : '')), draggable: true, keyboard: false, title: `Точка ${i + 1}${E.parts.length > 1 ? ' · ' + partName(E.pts[i][2]) : ''}` }).addTo(E.layer);
        rubber(h, i, a, b);
        h.on('click', ev => { L.DomEvent.stop(ev); E.sel = E.sel === i ? null : i; render(); });
        h.on('contextmenu', ev => { L.DomEvent.stop(ev); delPoint(i); });
      }
    }
    paintBar(info);
  }
  function redrawLines(pts) { const keep = E.pts; E.pts = pts; for (const [a, l] of lines) { const [x, y] = runOf(Math.min(a, pts.length - 1)); l.setLatLngs(pts.slice(x, y + 1).map(ll)); } E.pts = keep; }
  function rubber(mk, i, a, b) {
    let orig = null, wa = null, wb = null;
    mk.on('dragstart', () => {
      snap(); E.sel = i; orig = E.pts.map(q => q.slice());
      const cum = [0]; for (let j = 1; j < orig.length; j++) cum.push(cum[j - 1] + geo(orig[j - 1], orig[j]));
      wa = j => (cum[i] - cum[a] > 0 ? (cum[j] - cum[a]) / (cum[i] - cum[a]) : 1);
      wb = j => (cum[b] - cum[i] > 0 ? (cum[b] - cum[j]) / (cum[b] - cum[i]) : 1);
    });
    const apply = c => {
      const dla = c.lat - orig[i][0], dlo = c.lng - orig[i][1];
      for (let j = a + 1; j < i; j++) { const w = wa(j); E.pts[j] = [orig[j][0] + dla * w, orig[j][1] + dlo * w, orig[j][2]]; }
      for (let j = i + 1; j < b; j++) { const w = wb(j); E.pts[j] = [orig[j][0] + dla * w, orig[j][1] + dlo * w, orig[j][2]]; }
      E.pts[i] = [c.lat, c.lng, orig[i][2]];
    };
    mk.on('drag', ev => { apply(ev.target.getLatLng()); for (const [x, l] of lines) { const [p, q] = runOf(x); l.setLatLngs(E.pts.slice(p, q + 1).map(ll)); } });
    mk.on('dragend', ev => { apply(ev.target.getLatLng()); for (let j = a; j <= b; j++) E.pts[j] = [+E.pts[j][0].toFixed(6), +E.pts[j][1].toFixed(6), E.pts[j][2]]; changed(); render(); });
  }

  function nearestIdx(latlng) {
    const map = getMap(); const cp = map.latLngToContainerPoint(latlng); let best = null;
    E.pts.forEach((q, i) => { const c = map.latLngToContainerPoint(ll(q)); const d = (c.x - cp.x) ** 2 + (c.y - cp.y) ** 2; if (!best || d < best.d) best = { d, i }; });
    return best && best.d < (touch() ? 56 : 44) ** 2 ? best.i : null;
  }
  async function routeTo(a, q) {
    if (!E.route) return [q];
    try {
      const r = await api('GET', `/api/projects/${ctx.pid()}/route?a=${a[0]},${a[1]}&b=${q[0]},${q[1]}`, undefined, false, 'Ищу тропу…');
      if (r.ok && r.pts.length > 1) return r.pts.slice(1);
      toast(r.msg || 'Тропу не нашёл — соединяю прямой', true);
    } catch (e) { toast('Тропу не нашёл — соединяю прямой', true); }
    return [q];
  }
  async function suggest() {
    const a = E.pts[E.pick[0]], b = E.pts[E.pick[1]];
    try {
      const r = await api('GET', `/api/projects/${ctx.pid()}/route?a=${a[0]},${a[1]}&b=${b[0]},${b[1]}`, undefined, false, 'Прокладываю путь по тропе…');
      if (r.ok && r.pts.length > 2) { E.route = true; E.draft = r.pts.slice(1, -1); E.steps = [E.draft.length]; E.suggested = true; return render(); }
      toast('Тропы между этими точками на карте нет — поставь точки пути сам, кликами', true);
    } catch (e) { toast('Не получилось проложить путь — поставь точки сам, кликами', true); }
    render();
  }

  // другой кусок на карте: спросить — править вместе или перейти к нему
  function pieceClick(pid, latlng) {
    if (!E) return;
    if (E.parts.some(p => p.id === pid)) return click(latlng);
    const p = pieceById(pid); if (!p) return;
    E.ask = { id: pid, name: p.name, day: p.day }; render();
  }

  async function click(latlng) {
    if (!E) return;
    const q = [+latlng.lat.toFixed(6), +latlng.lng.toFixed(6)];
    if (E.ask) { E.ask = null; render(); }
    if (E.tool === 'move') { if (E.sel != null) { E.sel = null; render(); } return; }
    if (E.tool === 'box') { if (E.box.length >= 2) E.box = []; E.box.push(q); return render(); }
    if (E.tool === 'cut' || (E.tool === 'redraw' && E.pick.length < 2)) {
      const i = nearestIdx(latlng); if (i == null) return toast('Нажми прямо на трек — он оранжевый. Бледный — это другой кусок: нажми на него, чтобы править вместе', true);
      if (E.pick.length >= 2) E.pick = [];
      E.pick.push(i);
      if (E.pick.length === 2) {
        E.pick.sort((x, y) => x - y);
        if (E.pick[0] === E.pick[1]) { E.pick.pop(); return toast('Это та же точка — нажми подальше', true); }
        const [ra] = runOf(E.pick[0]), [rb] = runOf(E.pick[1]);
        if (ra !== rb) { E.pick = [i]; render(); return toast('Между этими точками куски не соединены (переезд) — выбери участок в одном куске или в стыкующихся', true); }
      }
      if (E.tool === 'redraw' && E.pick.length === 2 && E.route) return suggest();
      return render();
    }
    if (E.tool === 'redraw') {
      if (E.suggested) { E.draft = []; E.steps = []; E.suggested = false; }
      const from = E.draft.length ? E.draft[E.draft.length - 1] : E.pts[E.pick[0]];
      const add = await routeTo(from, q); E.draft.push(...add); E.steps.push(add.length); return render();
    }
    if (E.tool === 'extend') {
      const end = E.end === 'end', from = end ? E.pts[E.pts.length - 1] : E.pts[0];
      const add = (await routeTo(from, q)).map(x => [x[0], x[1], from[2]]); snap();
      if (end) E.pts.push(...add); else E.pts.unshift(...add.reverse());
      return render();
    }
  }

  function okParts(pts) { const c = {}; pts.forEach(q => { c[q[2]] = (c[q[2]] || 0) + 1; }); return E.parts.every(p => (c[p.id] || 0) >= 2); }
  function delPoint(i) {
    if (i == null || !E) return;
    const next = E.pts.filter((q, k) => k !== i);
    if (!okParts(next)) return toast('В куске должно остаться хотя бы две точки', true);
    snap(); E.pts = next; E.sel = null; render();
  }
  function trim(side) {
    const i = E.sel; if (i == null) return; const [ra, rb] = runOf(i);
    const from = side === 'before' ? ra : i + 1, to = side === 'before' ? i - 1 : rb;
    if (to < from) return;
    const next = [...E.pts.slice(0, from), ...E.pts.slice(to + 1)];
    if (!okParts(next)) return toast('Так от одного из кусков почти ничего не останется. Обрезай кусок по отдельности или спрямляй участок', true);
    snap(); E.pts = next; E.sel = null; render(); toast(side === 'before' ? 'Начало обрезано. Не то? ↶' : 'Конец обрезан. Не то? ↶');
  }
  function applyCut() {
    const [a, b] = E.pick; if (b - a < 2) return toast('Между этими точками убирать нечего', true);
    const next = [...E.pts.slice(0, a + 1), ...E.pts.slice(b)];
    if (!okParts(next)) return toast('Так в одном из кусков останется меньше двух точек — выбери участок поменьше', true);
    snap(); E.pts = next; E.pick = []; render(); toast('Участок спрямлён. Не то? ↶');
  }
  async function applyRedraw() {
    const [a, b] = E.pick, tag = E.pts[a][2];
    if (E.route && E.draft.length && !E.suggested) { const tail = await routeTo(E.draft[E.draft.length - 1], E.pts[b]); E.draft.push(...tail.slice(0, -1)); }
    const next = [...E.pts.slice(0, a + 1), ...E.draft.map(q => [q[0], q[1], tag]), ...E.pts.slice(b)];
    if (!okParts(next)) return toast('Так в одном из кусков останется меньше двух точек', true);
    snap(); E.pts = next; E.pick = []; E.draft = []; E.steps = []; E.suggested = false; render(); toast('Участок перерисован. Не то? ↶');
  }
  function applyBox() {
    const del = new Set(inBox()); if (!del.size) return;
    const next = E.pts.filter((q, i) => !del.has(i));
    if (!okParts(next)) return toast('Так от одного из кусков почти ничего не останется', true);
    snap(); E.pts = next; E.box = []; render(); toast(`Удалено точек: ${del.size}. Не то? ↶`);
  }
  function draftBack() { const n = E.steps.pop() || 1; E.draft.splice(-n, n); E.suggested = false; render(); }
  function undo() { if (!E.undo.length) return; E.redo.push(E.pts.map(q => q.slice())); E.pts = E.undo.pop(); changed(); E.sel = null; E.pick = []; render(); }
  function redo() { if (!E.redo.length) return; E.undo.push(E.pts.map(q => q.slice())); E.pts = E.redo.pop(); changed(); E.sel = null; render(); }

  function onKey(e) {
    if (!E || (e.target.closest && e.target.closest('input,textarea,select'))) return;
    const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (mod && (k === 'z' || k === 'я') && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (mod && (k === 'y' || k === 'н' || ((k === 'z' || k === 'я') && e.shiftKey))) { e.preventDefault(); redo(); }
    else if ((k === 'delete' || k === 'backspace') && E.sel != null) { e.preventDefault(); delPoint(E.sel); }
    else if (k === 'escape') { e.preventDefault(); e.stopPropagation(); if (E.ask || E.pick.length || E.draft.length || E.box.length || E.sel != null) { E.ask = null; E.pick = []; E.draft = []; E.box = []; E.sel = null; render(); } else close(false); }
    else if (mod && (k === 's' || k === 'ы')) { e.preventDefault(); save(); }
  }

  return { open, close, click, pieceClick, active: () => !!E, piece: () => E && E.parts[0].id, has: id => !!(E && E.parts.some(p => p.id === id)) };
}
