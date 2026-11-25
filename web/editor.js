// Редактор трека: правка одного куска нитки на карте — сессия «Сохранить / Отмена».
// Подсмотрено у gpx.studio, nakarte.me, Locus, OsmAnd и Leaflet.draw:
// • точки показываются «уровнями» (по Дугласу—Пекеру): издалека — только опорные, ближе — всё подробнее;
//   опорную тянешь — скрытые соседи сдвигаются плавно, «резинкой», без зубца;
// • кружок между соседними точками — потяни, появится новая точка;
// • выделенную точку можно удалить (Delete, кнопка, правый клик) или обрезать трек до/после неё;
// • «Спрямить участок» (две точки — между ними прямая), «Перерисовать» (новый путь кликами, можно «по тропе»),
//   «Удалить в рамке» (два клика — углы рамки), «Продолжить» трек от начала или конца;
// • всё отменяется (Ctrl+Z / Ctrl+Y), на сервер уходит только по «Сохранить».

export function createEditor(ctx) {
  const { L, getMap, api, toast, pieceById, onSaved, onClose, confirmBox, esc } = ctx;
  const MAX_HANDLES = 350;
  let E = null;

  const ll = q => [q[0], q[1]];
  const geo = (a, b) => L.latLng(ll(a)).distanceTo(ll(b));
  const len = pts => { let s = 0; for (let i = 1; i < pts.length; i++) s += geo(pts[i - 1], pts[i]); return s; };
  const kmTxt = m => (m / 1000).toFixed(m < 10000 ? 2 : 1).replace('.', ',');
  const pl = n => { n = Math.abs(n) % 100; const m = n % 10; return n > 10 && n < 15 ? 'точек' : m === 1 ? 'точка' : m >= 2 && m <= 4 ? 'точки' : 'точек'; };
  const touch = () => matchMedia('(pointer: coarse)').matches;
  const changed = () => { E.ver++; E.dirty = true; };
  const snap = () => { E.undo.push(E.pts.map(q => q.slice())); if (E.undo.length > 200) E.undo.shift(); E.redo = []; changed(); };
  const HINT = {
    move: () => (touch() ? 'Тяни точку — передвинешь. Нажми на точку — появятся «Удалить», «Обрезать до» и «после». Кружок между точками — потяни, будет новая точка.'
      : 'Тяни точку — передвинешь (скрытые соседи сдвинутся плавно). Нажми на точку — выделится: Delete — удалить, ещё — «Обрезать до/после». Правый клик — удалить сразу. Кружок между точками — потяни, будет новая точка. Приблизь карту — точек станет больше.'),
    cut: () => 'Всё между двумя выбранными точками уберётся, края соединятся прямой — так лечатся выбросы GPS и «зубцы».',
    redraw: () => 'Участок между двумя выбранными точками заменится новым путём. «По тропе» — путь между кликами проложится по тропам.',
    box: () => 'Точки трека внутри рамки удалятся — например, «звёздочка» на стоянке или мусор от сбоя GPS.',
    extend: () => 'Кликай по карте — трек продолжится от выбранного конца. Каждый клик — новая точка; ↶ — убрать последнюю.'
  };

  function open(pieceId, opts = {}) {
    const p = pieceById(pieceId); const map = getMap();
    if (!p || !map) return;
    if (E) close(true);
    E = { piece: p.id, name: p.name, pts: p.pts.map(q => [q[0], q[1]]), undo: [], redo: [], tool: opts.tool || 'move', sel: null, pick: opts.pick || [], draft: [], steps: [],
      box: [], end: 'end', dirty: false, ver: 0, rk: null, rkVer: -1, layer: L.layerGroup().addTo(map), route: false };
    document.body.classList.add('editing');
    buildBar(); map.on('moveend zoomend', render); document.addEventListener('keydown', onKey);
    const ext = opts.focus ? E.pts.slice(Math.max(0, opts.focus[0] - 2), opts.focus[1] + 3) : E.pts;
    map.fitBounds(L.latLngBounds(ext.map(ll)).pad(opts.focus ? 0.6 : 0.08), { maxZoom: 16 });
    ctx.redraw(); render();
  }

  function close(force) {
    if (!E) return;
    const done = () => {
      const map = getMap(); map && map.off('moveend zoomend', render); document.removeEventListener('keydown', onKey);
      E.layer.remove(); E.bar && E.bar.remove(); E = null; document.body.classList.remove('editing'); ctx.redraw(); onClose && onClose();
    };
    if (!force && E.dirty) return confirmBox('Выйти без сохранения?', 'Правки этого куска пропадут.', 'Выйти', done);
    done();
  }

  async function save() {
    if (!E) return;
    if (!E.dirty) return close(true);
    if (E.pts.length < 2) return toast('В куске должно остаться хотя бы две точки', true);
    try {
      const r = await api('POST', `/api/projects/${ctx.pid()}/track-edit`, { action: 'replace', piece: E.piece, pts: E.pts }, false, 'Сохраняю трек…');
      toast(r.msg, !r.ok); if (!r.ok) return;
      E.dirty = false; close(true); onSaved && onSaved(r);
    } catch (e) { toast(e.message || 'Не получилось сохранить', true); }
  }

  // ---------------------------------------------------------------- «уровни» точек: важность по Дугласу—Пекеру
  function ranks() {
    if (E.rkVer === E.ver && E.rk) return E.rk;
    const P = E.pts, n = P.length, r = new Float64Array(n);
    r[0] = r[n - 1] = Infinity;
    const k = Math.cos(P[0][0] * Math.PI / 180), X = P.map(q => q[1] * 111320 * k), Y = P.map(q => q[0] * 110540);
    const st = [[0, n - 1, Infinity]];
    while (st.length) {
      const [a, b, par] = st.pop(); if (b - a < 2) continue;
      const dx = X[b] - X[a], dy = Y[b] - Y[a], D = Math.hypot(dx, dy); let best = -1, bi = a + 1;
      for (let i = a + 1; i < b; i++) { const d = D > 0 ? Math.abs(dx * (Y[a] - Y[i]) - dy * (X[a] - X[i])) / D : Math.hypot(X[i] - X[a], Y[i] - Y[a]); if (d > best) { best = d; bi = i; } }
      r[bi] = Math.min(best, par); st.push([a, bi, r[bi]], [bi, b, r[bi]]);
    }
    E.rk = r; E.rkVer = E.ver; return r;
  }
  function shown() {      // точки, у которых будут ручки: в кадре и достаточно «важные» для этого масштаба
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
      if (a === 'tool') { E.tool = b.dataset.v; E.pick = []; E.draft = []; E.steps = []; E.box = []; E.sel = null; render(); }
      else if (a === 'end') { E.end = b.dataset.v; render(); }
      else if (a === 'route') { E.route = !E.route; toast(E.route ? 'По тропе: путь между кликами проложит BRouter по тропам OpenStreetMap' : 'Прямыми отрезками'); render(); }
      else if (a === 'undo') undo(); else if (a === 'redo') redo();
      else if (a === 'delpt') delPoint(E.sel); else if (a === 'trimb') trim('before'); else if (a === 'trima') trim('after');
      else if (a === 'cutgo') applyCut(); else if (a === 'drawgo') applyRedraw(); else if (a === 'draftback') draftBack(); else if (a === 'boxgo') applyBox();
      else if (a === 'extback') undo();
      else if (a === 'reset') { E.pick = []; E.draft = []; E.steps = []; E.box = []; render(); }
      else if (a === 'save') save(); else if (a === 'cancel') close(false);
    });
    L.DomEvent.disableClickPropagation(E.bar); L.DomEvent.disableScrollPropagation(E.bar);
  }
  function inBox() { if (E.box.length < 2) return []; const bb = L.latLngBounds(E.box); const o = []; E.pts.forEach((q, i) => { if (bb.contains(ll(q))) o.push(i); }); return o; }
  function paintBar(info) {
    if (!E.bar) return;
    const t = (v, label, title) => `<button class="${E.tool === v ? 'on' : ''}" data-ed="tool" data-v="${v}" title="${title}">${label}</button>`;
    let sub = '', step = '';
    if (E.tool === 'move' && E.sel != null) {
      const before = len(E.pts.slice(0, E.sel + 1)), after = len(E.pts.slice(E.sel));
      sub = `<button class="btn sm warn" data-ed="delpt">Удалить точку</button>${E.sel > 0 ? `<button class="btn sm" data-ed="trimb" title="Убрать всё от начала до этой точки">Обрезать до неё · ${kmTxt(before)} км</button>` : ''}${E.sel < E.pts.length - 1 ? `<button class="btn sm" data-ed="trima" title="Убрать всё от этой точки до конца">Обрезать после · ${kmTxt(after)} км</button>` : ''}`;
    } else if (E.tool === 'cut' || E.tool === 'redraw') {
      step = E.pick.length === 0 ? 'Нажми на трек в начале участка.' : E.pick.length === 1 ? 'Теперь — в конце участка.' : E.tool === 'redraw' ? 'Ставь точки нового пути кликами по карте.' : 'Участок выделен красным.';
      if (E.pick.length === 2) {
        const n = E.pick[1] - E.pick[0] - 1, L0 = len(E.pts.slice(E.pick[0], E.pick[1] + 1)), L1 = geo(E.pts[E.pick[0]], E.pts[E.pick[1]]);
        sub = E.tool === 'cut' ? `<button class="btn sm warn" data-ed="cutgo">Спрямить: ${n} ${pl(n)}, ${kmTxt(L0)} → ${kmTxt(L1)} км</button><button class="btn sm" data-ed="reset">Заново</button>`
          : `${E.draft.length ? '<button class="btn sm" data-ed="draftback">↶ точку</button>' : ''}<button class="btn sm go" data-ed="drawgo">Готово — заменить участок</button><button class="btn sm" data-ed="reset">Заново</button>`;
      }
    } else if (E.tool === 'box') {
      step = E.box.length === 0 ? 'Нажми в одном углу рамки.' : E.box.length === 1 ? 'Теперь — в противоположном углу.' : '';
      const n = inBox().length; if (E.box.length === 2) sub = `<button class="btn sm warn" data-ed="boxgo" ${n ? '' : 'disabled'}>Удалить ${n} ${pl(n)} в рамке</button><button class="btn sm" data-ed="reset">Заново</button>`;
    } else if (E.tool === 'extend') {
      sub = `<span class="seg sm"><button class="${E.end === 'start' ? 'on' : ''}" data-ed="end" data-v="start">от начала</button><button class="${E.end === 'end' ? 'on' : ''}" data-ed="end" data-v="end">от конца</button></span>`;
    }
    E.bar.innerHTML = `<div class="edrow"><b class="edt">Редактор трека</b><span class="edn">«${esc(E.name)}» · ${kmTxt(len(E.pts))} км · ${E.pts.length} ${pl(E.pts.length)}${E.dirty ? ' · <i>не сохранено</i>' : ''}</span>
        <span class="sp"></span><button class="icon" data-ed="undo" title="Отменить (Ctrl+Z)" ${E.undo.length ? '' : 'disabled'}>↶</button><button class="icon" data-ed="redo" title="Вернуть (Ctrl+Y)" ${E.redo.length ? '' : 'disabled'}>↷</button>
        <button class="btn sm" data-ed="cancel" title="Выйти без сохранения (Esc)">Отмена</button><button class="btn sm go" data-ed="save" title="Сохранить (Ctrl+S)">Сохранить</button></div>
      <div class="edrow"><span class="seg sm tools">${t('move', '● Точки', 'Двигать, удалять и добавлять точки')}${t('cut', '✂ Спрямить участок', 'Убрать участок — края соединятся прямой')}${t('redraw', '✎ Перерисовать', 'Заменить участок новым путём')}${t('box', '▭ Удалить в рамке', 'Удалить точки внутри рамки')}${t('extend', '➜ Продолжить', 'Дорисовать трек от начала или конца')}</span>
        ${E.tool === 'redraw' || E.tool === 'extend' ? `<button class="chip${E.route ? ' on' : ''}" data-ed="route" title="Путь между кликами — по тропам OpenStreetMap (BRouter); вне троп — прямая">по тропе</button>` : ''}${sub}</div>
      <div class="edhint">${step ? `<b>${step}</b> ` : ''}${info || HINT[E.tool]()}</div>`;
  }

  // ---------------------------------------------------------------- карта
  let line = null;
  function render() {
    if (!E) return; const map = getMap(); E.layer.clearLayers();
    L.polyline(E.pts.map(ll), { color: '#fff', weight: 10, opacity: .95, interactive: false }).addTo(E.layer);
    line = L.polyline(E.pts.map(ll), { color: '#F68A1D', weight: 5, interactive: false }).addTo(E.layer);
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
      if (E.box.length === 2) { L.rectangle(L.latLngBounds(E.box), { color: '#E2001A', weight: 2, dashArray: '6 5', fillOpacity: .08, interactive: false }).addTo(E.layer);
        inBox().forEach(i => L.circleMarker(ll(E.pts[i]), { radius: 4, color: '#E2001A', weight: 2, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(E.layer)); }
      else L.circleMarker(E.box[0], { radius: 6, color: '#E2001A', weight: 3, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(E.layer);
    }
    if (E.tool === 'extend') {
      const q = E.end === 'end' ? E.pts[E.pts.length - 1] : E.pts[0];
      L.circleMarker(ll(q), { radius: 9, color: '#0B8A3E', weight: 3, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(E.layer);
    }
    let info = '';
    if (E.tool === 'move') {
      const S = shown(), sz = touch() ? 22 : 14, icon = cls => L.divIcon({ className: cls, iconSize: [sz, sz], iconAnchor: [sz / 2, sz / 2] });
      const all = E.pts.length, hidden = all - S.length;
      if (hidden > 0 && S.length) info = `Показаны опорные точки: ${S.length} из ${all} в кадре и рядом — приблизь карту, станет подробнее.`;
      for (let k = 0; k < S.length; k++) {
        const i = S[k], a = k > 0 ? S[k - 1] : 0, b = k + 1 < S.length ? S[k + 1] : all - 1;
        // кружок между соседними ручками: рядом стоящие точки — вставить новую; со скрытыми точками между — тянуть сам участок
        if (k + 1 < S.length) {
          const j = S[k + 1], pa = map.latLngToContainerPoint(ll(E.pts[i])), pb = map.latLngToContainerPoint(ll(E.pts[j]));
          if (Math.hypot(pa.x - pb.x, pa.y - pb.y) > (touch() ? 56 : 36)) {
            const m = j - i >= 2 ? Math.round((i + j) / 2) : null;
            const pos = m != null ? ll(E.pts[m]) : [(E.pts[i][0] + E.pts[j][0]) / 2, (E.pts[i][1] + E.pts[j][1]) / 2];
            const g = L.marker(pos, { icon: icon('edg'), draggable: true, keyboard: false, title: m != null ? 'Потяни — участок сдвинется плавно' : 'Потяни — добавится точка' }).addTo(E.layer);
            if (m != null) rubber(g, m, i, j);
            else {
              g.on('dragstart', () => snap());
              g.on('drag', ev => { const c = ev.target.getLatLng(); line.setLatLngs([...E.pts.slice(0, i + 1), [c.lat, c.lng], ...E.pts.slice(i + 1)].map(ll)); });
              g.on('dragend', ev => { const c = ev.target.getLatLng(); E.pts.splice(i + 1, 0, [+c.lat.toFixed(6), +c.lng.toFixed(6)]); changed(); E.sel = i + 1; render(); });
            }
          }
        }
        const h = L.marker(ll(E.pts[i]), { icon: icon('edh' + (E.sel === i ? ' sel' : '') + (i === 0 || i === all - 1 ? ' end' : '')), draggable: true, keyboard: false, title: `Точка ${i + 1}` }).addTo(E.layer);
        rubber(h, i, a, b);
        h.on('click', ev => { L.DomEvent.stop(ev); E.sel = E.sel === i ? null : i; render(); });
        h.on('contextmenu', ev => { L.DomEvent.stop(ev); delPoint(i); });
      }
    }
    paintBar(info);
  }
  // тянем точку i: скрытые точки между соседними ручками a и b сдвигаются плавно, пропорционально пути
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
      for (let j = a + 1; j < i; j++) { const w = wa(j); E.pts[j] = [orig[j][0] + dla * w, orig[j][1] + dlo * w]; }
      for (let j = i + 1; j < b; j++) { const w = wb(j); E.pts[j] = [orig[j][0] + dla * w, orig[j][1] + dlo * w]; }
      E.pts[i] = [c.lat, c.lng];
    };
    mk.on('drag', ev => { apply(ev.target.getLatLng()); line.setLatLngs(E.pts.map(ll)); });
    mk.on('dragend', ev => { apply(ev.target.getLatLng()); for (let j = a; j <= b; j++) E.pts[j] = [+E.pts[j][0].toFixed(6), +E.pts[j][1].toFixed(6)]; changed(); render(); });
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

  async function click(latlng) {
    if (!E) return;
    const q = [+latlng.lat.toFixed(6), +latlng.lng.toFixed(6)];
    if (E.tool === 'move') { if (E.sel != null) { E.sel = null; render(); } return; }
    if (E.tool === 'box') { if (E.box.length >= 2) E.box = []; E.box.push(q); return render(); }
    if (E.tool === 'cut' || (E.tool === 'redraw' && E.pick.length < 2)) {
      const i = nearestIdx(latlng); if (i == null) return toast('Нажми прямо на трек — он оранжевый', true);
      if (E.pick.length >= 2) E.pick = [];
      E.pick.push(i); if (E.pick.length === 2) { E.pick.sort((x, y) => x - y); if (E.pick[0] === E.pick[1]) { E.pick.pop(); return toast('Это та же точка — нажми подальше', true); } }
      return render();
    }
    if (E.tool === 'redraw') {
      const from = E.draft.length ? E.draft[E.draft.length - 1] : E.pts[E.pick[0]];
      const add = await routeTo(from, q); E.draft.push(...add); E.steps.push(add.length); return render();
    }
    if (E.tool === 'extend') {
      const from = E.end === 'end' ? E.pts[E.pts.length - 1] : E.pts[0];
      const add = await routeTo(from, q); snap();
      if (E.end === 'end') E.pts.push(...add); else E.pts.unshift(...add.reverse());
      return render();
    }
  }

  function delPoint(i) {
    if (i == null || !E) return;
    if (E.pts.length <= 2) return toast('В куске должно остаться хотя бы две точки', true);
    snap(); E.pts.splice(i, 1); E.sel = null; render();
  }
  function trim(side) {
    const i = E.sel; if (i == null) return;
    const keep = side === 'before' ? E.pts.slice(i) : E.pts.slice(0, i + 1);
    if (keep.length < 2) return toast('Останется меньше двух точек', true);
    snap(); E.pts = keep; E.sel = null; render(); toast(side === 'before' ? 'Начало обрезано. Не то? ↶' : 'Конец обрезан. Не то? ↶');
  }
  function applyCut() {
    const [a, b] = E.pick; if (b - a < 2) return toast('Между этими точками убирать нечего', true);
    snap(); E.pts.splice(a + 1, b - a - 1); E.pick = []; render(); toast('Участок спрямлён. Не то? ↶');
  }
  async function applyRedraw() {
    const [a, b] = E.pick;
    if (E.route && E.draft.length) { const tail = await routeTo(E.draft[E.draft.length - 1], E.pts[b]); E.draft.push(...tail.slice(0, -1)); }
    snap(); E.pts.splice(a + 1, b - a - 1, ...E.draft); E.pick = []; E.draft = []; E.steps = []; render(); toast('Участок перерисован. Не то? ↶');
  }
  function applyBox() {
    const del = new Set(inBox()); if (!del.size) return;
    const keep = E.pts.filter((q, i) => !del.has(i)); if (keep.length < 2) return toast('Так удалится почти весь кусок', true);
    snap(); E.pts = keep; E.box = []; render(); toast(`Удалено точек: ${del.size}. Не то? ↶`);
  }
  function draftBack() { const n = E.steps.pop() || 1; E.draft.splice(-n, n); render(); }
  function undo() { if (!E.undo.length) return; E.redo.push(E.pts.map(q => q.slice())); E.pts = E.undo.pop(); changed(); E.sel = null; E.pick = []; render(); }
  function redo() { if (!E.redo.length) return; E.undo.push(E.pts.map(q => q.slice())); E.pts = E.redo.pop(); changed(); E.sel = null; render(); }

  function onKey(e) {
    if (!E || (e.target.closest && e.target.closest('input,textarea,select'))) return;
    const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (mod && (k === 'z' || k === 'я') && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (mod && (k === 'y' || k === 'н' || ((k === 'z' || k === 'я') && e.shiftKey))) { e.preventDefault(); redo(); }
    else if ((k === 'delete' || k === 'backspace') && E.sel != null) { e.preventDefault(); delPoint(E.sel); }
    else if (k === 'escape') { e.preventDefault(); e.stopPropagation(); if (E.pick.length || E.draft.length || E.box.length || E.sel != null) { E.pick = []; E.draft = []; E.box = []; E.sel = null; render(); } else close(false); }
    else if (mod && (k === 's' || k === 'ы')) { e.preventDefault(); save(); }
  }

  return { open, close, click, active: () => !!E, piece: () => E && E.piece };
}
