// Планировщик маршрута: ключевые точки на карте → нитка по тропам или напрямую, ночёвки делят её на дни.
// Перевалы и вершины каталога видны на карте — нажми, и маршрут пройдёт через них. Км, набор и сброс — по рельефу, сразу.
// В поход уходит по «Готово»: куски по дням, ночёвки, перевалы. Черновик живёт в браузере, пока не нажато «Готово».

export function createPlanner(ctx) {
  const { L, getMap, api, toast, esc, confirmBox, sym } = ctx;
  let P = null;
  const RC = new Map();      // отрезок «по тропе» → путь (кэш: точки не двигались — заново не ищем)
  let inflight = 0; const queue = [];
  const KIND = { via: 'точка', camp: 'ночёвка', pass: 'перевал', peak: 'вершина' };
  const ll = q => [q.lat, q.lon];
  const geo = (a, b) => L.latLng(a).distanceTo(L.latLng(b));
  const kmTxt = m => (m / 1000).toFixed(m < 10000 ? 2 : 1).replace('.', ',');
  const pl = (n, a, b, c) => { n = Math.abs(n) % 100; const x = n % 10; return n > 10 && n < 15 ? c : x === 1 ? a : x >= 2 && x <= 4 ? b : c; };
  const touch = () => matchMedia('(pointer: coarse)').matches;
  const draftKey = () => 'nitkaPlan:' + ctx.pid();

  // ---------------------------------------------------------------- открыть / закрыть
  function open() {
    const pr = ctx.proj(), map = getMap(); if (!map) return;
    RC.clear();
    let pts = [], def = 'trail', fromDraft = false;
    if (pr.plan && pr.plan.pts) {
      pts = pr.plan.pts.map(p => ({ ...p })); def = (pts[0] && pts[0].mode) || 'trail';
      (pr.plan.legs || []).forEach((g, i) => { const a = pts[i], b = pts[i + 1]; if (a && b && g.mode !== 'line' && g.pts && g.pts.length > 1) RC.set(lkey(a, b, g.mode), { pts: g.pts, ok: g.ok !== false, used: g.mode }); });
    }
    let draft = null; try { draft = JSON.parse(localStorage[draftKey()] || 'null'); } catch (e) { draft = null; }
    if (draft && draft.pts && JSON.stringify(draft.pts) !== JSON.stringify(pts)) { pts = draft.pts; def = draft.def || def; fromDraft = true; (draft.rc || []).forEach(([k, v]) => RC.set(k, v)); }
    P = { pts, def, undo: [], redo: [], sel: null, prof: null, profKey: '', layer: L.layerGroup().addTo(map), catLayer: L.layerGroup().addTo(map), genLayer: L.layerGroup().addTo(map), hover: null, tab: 'draw', gen: null,
      cat: new Map(), catBoxes: [], camps: new Map(), campBoxes: [], init: JSON.stringify(pr.plan ? pr.plan.pts : []), fromDraft };
    map.on('moveend', loadCat); document.addEventListener('keydown', onKey); document.addEventListener('click', onClick, true); document.addEventListener('change', onChange, true); document.addEventListener('submit', onSubmit, true);
    const fit = pts.length ? pts.map(ll) : pr.pieces.filter(p => p.role !== 'skip').flatMap(p => p.pts.map(q => [q[0], q[1]]));
    if (fit.length) map.fitBounds(L.latLngBounds(fit).pad(0.15), { maxZoom: 14 });
    if (fromDraft) toast('Продолжаю черновик маршрута — в поход он ещё не попал');
    render(); loadCat(); schedProfile();
  }
  function close() {
    if (!P) return; const map = getMap();
    map && map.off('moveend', loadCat); document.removeEventListener('keydown', onKey); document.removeEventListener('click', onClick, true); document.removeEventListener('change', onChange, true); document.removeEventListener('submit', onSubmit, true);
    P.layer.remove(); P.catLayer.remove(); P.genLayer.remove(); P.hover && P.hover.remove(); P = null; ctx.onClose();
  }
  function cancel() {
    const changed = JSON.stringify(P.pts) !== P.init;
    const go = () => { try { delete localStorage[draftKey()]; } catch (e) { /* ок */ } close(); };
    if (changed) return confirmBox('Выйти без сохранения?', 'Нарисованное не попадёт в поход, черновик сотрётся.', 'Выйти', go);
    go();
  }
  async function done() {
    if (P.pts.length < 2) return toast('Поставь хотя бы две точки — старт и финиш', true);
    const legs = P.pts.slice(0, -1).map((_, i) => legOf(i));
    if (legs.some(g => g.busy)) return toast('Секунду — ещё ищу тропы на некоторых отрезках', true);
    try {
      const r = await api('POST', `/api/projects/${ctx.pid()}/plan`, { plan: { pts: P.pts, legs: legs.map(g => ({ pts: g.pts, ok: g.ok })) } }, false, 'Переношу маршрут в поход…');
      toast(r.msg, !r.ok); if (!r.ok) return;
      try { delete localStorage[draftKey()]; } catch (e) { /* ок */ }
      const done_ = ctx.onDone; close(); done_(r);
    } catch (e) { toast(e.message, true); }
  }

  // ---------------------------------------------------------------- точки и отрезки
  const lkey = (a, b, mode) => `${mode}|${(+a.lat).toFixed(5)},${(+a.lon).toFixed(5)}|${(+b.lat).toFixed(5)},${(+b.lon).toFixed(5)}`;
  function legOf(i) {
    const a = P.pts[i], b = P.pts[i + 1], mode = a.mode || P.def, line = [ll(a), ll(b)];
    if (mode === 'line') return { mode, pts: line, ok: true };
    const k = lkey(a, b, mode), c = RC.get(k);
    if (c && c !== 'busy') return { mode, pts: withEnds(c.pts, a, b), ok: c.ok, msg: c.msg, used: c.used, deg: c.deg };
    if (!c) fetchLeg(k, a, b, mode);
    return { mode, pts: line, ok: true, busy: true };
  }
  // тропа начинается не точно в точке маршрута: подход от точки к тропе и сход с неё — часть пути
  function withEnds(pts, a, b) {
    const out = pts.map(q => [q[0], q[1]]);
    if (geo(out[0], ll(a)) > 5) out.unshift(ll(a)); else out[0] = ll(a);
    if (geo(out[out.length - 1], ll(b)) > 5) out.push(ll(b)); else out[out.length - 1] = ll(b);
    return out;
  }
  function fetchLeg(k, a, b, mode) { RC.set(k, 'busy'); queue.push([k, a, b, mode]); pump(); }
  async function pump() {
    while (inflight < 3 && queue.length) {
      const [k, a, b, mode] = queue.shift(); inflight++;
      (async () => {
        let v;
        try {
          const r = await api('GET', `/api/projects/${ctx.pid()}/route?a=${a.lat},${a.lon}&b=${b.lat},${b.lon}&${mode === 'terrain' ? 'mode=terrain' : 'full=1'}`, undefined, false, false);
          v = r.pts && r.pts.length > 1 ? { pts: r.pts, ok: mode === 'terrain' ? true : r.ok, used: r.used, deg: r.maxdeg, msg: r.ok ? '' : (mode === 'terrain' ? 'рельеф не посчитался — прямая' : 'тропы нет — по рельефу') }
            : { pts: [ll(a), ll(b)], ok: false, msg: 'не нашёл пути — прямая' };
        } catch (e) { v = { pts: [ll(a), ll(b)], ok: false, msg: /далеко/.test(e.message) ? 'далеко — поставь точку посередине' : 'не нашёл пути — прямая' }; }
        RC.set(k, v); inflight--;
        if (P) { render(); schedProfile(); keepDraft(); }
        pump();
      })();
    }
  }
  const snap = () => { P.undo.push(JSON.stringify(P.pts)); if (P.undo.length > 150) P.undo.shift(); P.redo = []; };
  function changed() { P.prof = P.prof && { ...P.prof, stale: true }; render(); schedProfile(); keepDraft(); }
  function keepDraft() {
    try { localStorage[draftKey()] = JSON.stringify({ pts: P.pts, def: P.def, t: Date.now(), rc: [...RC].filter(([, v]) => v !== 'busy').slice(-300) }); } catch (e) { /* место кончилось — не страшно */ }
  }
  function addPoint(q, at) {
    snap(); const p = { lat: +(+q.lat).toFixed(6), lon: +(+(q.lon ?? q.lng)).toFixed(6), kind: q.kind || 'via', name: q.name || '', h: q.h ?? null, grade: q.grade || null, ref: q.ref || null, mode: P.def };
    if (at == null) P.pts.push(p); else P.pts.splice(at, 0, p);
    P.sel = at == null ? P.pts.length - 1 : at; changed();
  }
  function addCat(c) {      // перевал или вершина каталога — в самое подходящее место маршрута
    if (P.tab === 'gen') return genCycle(c);
    if (P.pts.some(p => p.ref && p.ref === c.id)) return toast(`${c.title} уже в маршруте`);
    const q = { lat: c.lat, lon: c.lon, kind: c.kind, name: c.title, h: c.h, grade: c.grade, ref: c.id };
    if (P.pts.length < 2) return addPoint(q);
    let best = { cost: geo(ll(P.pts[P.pts.length - 1]), [c.lat, c.lon]), at: null };
    for (let i = 0; i + 1 < P.pts.length; i++) {
      const a = ll(P.pts[i]), b = ll(P.pts[i + 1]), cost = geo(a, [c.lat, c.lon]) + geo([c.lat, c.lon], b) - geo(a, b);
      if (cost < best.cost) best = { cost, at: i + 1 };
    }
    addPoint(q, best.at);
    toast(`${c.title} — в маршруте${best.at != null ? ' (между соседними точками)' : ''}`);
  }
  function addCamp(c) {      // стоянка OSM — ночёвкой, в самое подходящее место маршрута
    const q = { lat: c[0], lon: c[1], kind: 'camp', name: c[2] ? `${c[3]} «${c[2]}»` : c[3] };
    if (P.pts.length < 2) return addPoint(q);
    let best = { cost: Infinity, at: null };
    for (let i = 0; i + 1 < P.pts.length; i++) { const a = ll(P.pts[i]), b = ll(P.pts[i + 1]), cost = geo(a, [c[0], c[1]]) + geo([c[0], c[1]], b) - geo(a, b); if (cost < best.cost) best = { cost, at: i + 1 }; }
    addPoint(q, best.at); toast(`Ночёвка: ${q.name} — маршрут поделился на дни`);
  }
  function delPoint(i) { snap(); P.pts.splice(i, 1); P.sel = null; changed(); }
  function undo() { if (!P.undo.length) return; P.redo.push(JSON.stringify(P.pts)); P.pts = JSON.parse(P.undo.pop()); P.sel = null; changed(); }
  function redo() { if (!P.redo.length) return; P.undo.push(JSON.stringify(P.pts)); P.pts = JSON.parse(P.redo.pop()); P.sel = null; changed(); }

  // ---------------------------------------------------------------- высоты и профиль
  let profT = 0;
  function schedProfile() { clearTimeout(profT); profT = setTimeout(profile, 700); }
  async function profile() {
    if (!P || P.pts.length < 2) { if (P) { P.prof = null; ctx.repaint(); } return; }
    const legs = P.pts.slice(0, -1).map((_, i) => legOf(i));
    const R = [], marks = [0];
    legs.forEach(g => {
      const src = g.pts; const out = [];
      for (let j = 1; j < src.length; j++) {      // прямые и редкие участки дробим до ~100 м — иначе перевал между точками не виден
        const a = src[j - 1], b = src[j], d = geo(a, b), n = Math.min(400, Math.floor(d / 100));
        for (let k = 1; k <= n; k++) out.push([a[0] + (b[0] - a[0]) * k / (n + 1), a[1] + (b[1] - a[1]) * k / (n + 1)]);
        out.push(b);
      }
      if (!R.length) R.push(src[0]);
      R.push(...out); marks.push(R.length - 1);
    });
    const key = R.length + '|' + R[0] + '|' + R[R.length - 1] + '|' + marks.join(',') + '|' + JSON.stringify(P.pts.map(p => [p.lat, p.lon]));
    if (key === P.profKey && P.prof && !P.prof.stale) return;
    let pick = R, idx = R.map((_, i) => i);
    if (R.length > 6000) { const st = R.length / 6000; idx = Array.from({ length: 6000 }, (_, i) => Math.round(i * st)); marks.forEach(m => { if (!idx.includes(m)) idx.push(m); }); idx.sort((a, b) => a - b); pick = idx.map(i => R[i]); }
    try {
      const r = await api('POST', '/api/elev', { pts: pick.map(q => [+q[0].toFixed(6), +q[1].toFixed(6)]) }, false, false);
      if (!P) return;
      const E0 = r.ele, D = [0];
      for (let i = 1; i < pick.length; i++) D.push(D[i - 1] + geo(pick[i - 1], pick[i]));
      const E = E0.map((e, i) => (E0[Math.max(0, i - 1)] + e + E0[Math.min(E0.length - 1, i + 1)]) / 3);      // сглаживание: шум рельефа не копится в набор
      const M = marks.map(m => idx.indexOf(m));
      const seg = (a, b) => { let up = 0, dn = 0; for (let i = a + 1; i <= b; i++) { const d = E[i] - E[i - 1]; if (d > 0) up += d; else dn -= d; } return { km: (D[b] - D[a]) / 1000, up: Math.round(up), dn: Math.round(dn) }; };
      const legS = M.slice(0, -1).map((m, i) => seg(m, M[i + 1]));
      const steep = (a, b) => { let best = 0, j = a; for (let i = a; i <= b; i++) { while (j < b && D[j] - D[i] < 60) j++; if (D[j] - D[i] < 60) break; best = Math.max(best, Math.atan(Math.abs(E[j] - E[i]) / (D[j] - D[i])) * 180 / Math.PI); } return Math.round(best); };
      legS.forEach((s, i) => { s.deg = steep(M[i], M[i + 1]); });
      const cut = [0, ...P.pts.map((p, i) => i).filter(i => i > 0 && i < P.pts.length - 1 && P.pts[i].kind === 'camp'), P.pts.length - 1];
      const days = cut.slice(0, -1).map((a, i) => seg(M[a], M[cut[i + 1]]));
      P.prof = { D, E, R: pick, M, legS, days, all: seg(0, E.length - 1), stale: false }; P.profKey = key;
      ctx.repaint();
    } catch (e) { if (P) { P.prof = P.prof && { ...P.prof, stale: true, err: e.message }; ctx.repaint(); } }
  }
  function profSvg() {
    const pr = P.prof; if (!pr) return P.pts.length > 1 ? '<div class="hint">Считаю высоты по рельефу…</div>' : '';
    const W = 600, H = 160, l = 38, b = 18, t = 26, D = pr.D, E = pr.E, Dm = D[D.length - 1] || 1;
    let e0 = Math.min(...E), e1 = Math.max(...E); const pad = Math.max(30, (e1 - e0) * 0.08); e0 -= pad; e1 += pad;
    const x = d => l + (W - l - 8) * d / Dm, y = e => t + (H - t - b) * (1 - (e - e0) / (e1 - e0));
    const st = Math.max(1, Math.floor(E.length / 700)); let dl = '';
    for (let i = 0; i < E.length; i += st) dl += (dl ? 'L' : 'M') + x(D[i]).toFixed(1) + ' ' + y(E[i]).toFixed(1);
    dl += 'L' + x(Dm).toFixed(1) + ' ' + y(E[E.length - 1]).toFixed(1);
    const area = dl + `L${x(Dm).toFixed(1)} ${H - b}L${l} ${H - b}Z`;
    const marks = P.pts.map((p, i) => ({ p, i, d: D[pr.M[i]] || 0, e: E[pr.M[i]] || 0 }));
    const camps = marks.filter(m => m.p.kind === 'camp' && m.i > 0 && m.i < P.pts.length - 1);
    const bounds = [0, ...camps.map(m => m.d), Dm];
    const dayLab = bounds.length > 2 ? bounds.slice(0, -1).map((a, i) => `<text x="${x((a + bounds[i + 1]) / 2).toFixed(1)}" y="12" class="pd">день ${i + 1}</text>`).join('') : '';
    const peaks = marks.filter(m => m.p.kind === 'pass' || m.p.kind === 'peak');
    return `<svg class="plprof${pr.stale ? ' stale' : ''}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-pl="prof">
      <path d="${area}" class="pa"/><path d="${dl}" class="pln_"/>
      ${camps.map(m => `<line x1="${x(m.d).toFixed(1)}" x2="${x(m.d).toFixed(1)}" y1="16" y2="${H - b}" class="pc_"/>`).join('')}${dayLab}
      ${peaks.map(m => `<circle cx="${x(m.d).toFixed(1)}" cy="${y(m.e).toFixed(1)}" r="3.6" class="pp"/><text x="${x(m.d).toFixed(1)}" y="${(y(m.e) - 7).toFixed(1)}" class="pt">${esc((m.p.name || '').replace(/^(пер\.|в\.)\s*/, '').slice(0, 16))}</text>`).join('')}
      <text x="${l - 4}" y="${(y(e1 - pad) + 4).toFixed(1)}" class="py">${Math.round(e1 - pad)}</text><text x="${l - 4}" y="${(y(e0 + pad) + 4).toFixed(1)}" class="py">${Math.round(e0 + pad)}</text>
      <text x="${W - 8}" y="${H - 4}" class="px">${kmTxt(Dm)} км</text><text x="${l}" y="${H - 4}" class="px0">0</text>
      <line class="phx" x1="-10" x2="-10" y1="${t - 6}" y2="${H - b}"/><text class="pht" x="-100" y="${t - 10}"></text></svg>`;
  }
  function profHover(ev) {
    const sv = ev.target.closest('svg.plprof'); if (!sv || !P || !P.prof) return;
    const r = sv.getBoundingClientRect(), W = 600, l = 38, pr = P.prof, Dm = pr.D[pr.D.length - 1];
    const d = Math.max(0, Math.min(Dm, ((ev.clientX - r.left) / r.width * W - l) / (W - l - 8) * Dm));
    let i = pr.D.findIndex(v => v >= d); if (i < 0) i = pr.D.length - 1;
    const xx = l + (W - l - 8) * pr.D[i] / Dm; const ln = sv.querySelector('.phx'), tx = sv.querySelector('.pht');
    ln.setAttribute('x1', xx); ln.setAttribute('x2', xx); tx.setAttribute('x', Math.min(W - 70, Math.max(l + 30, xx))); tx.textContent = `${kmTxt(pr.D[i])} км · ${Math.round(pr.E[i])} м`;
    const map = getMap(), q = pr.R[i];
    if (!P.hover) P.hover = L.circleMarker(q, { radius: 7, color: '#133C6A', weight: 3, fillColor: '#FEBC0E', fillOpacity: 1, interactive: false }).addTo(map); else P.hover.setLatLng(q);
  }

  // ---------------------------------------------------------------- каталог перевалов на карте
  async function loadCat() {
    if (!P) return; const map = getMap(); const z = map.getZoom();
    if (z < 9) { P.catLayer.clearLayers(); return; }
    const b = map.getBounds().pad(0.4);
    const box = [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()].map(v => +v.toFixed(3));
    if (!P.catBoxes.some(c => c[0] <= box[0] && c[1] <= box[1] && c[2] >= box[2] && c[3] >= box[3])) {
      try {
        const r = await api('GET', `/api/catalog?bbox=${box.join(',')}`, undefined, false, false);
        if (!P) return; if (!r.wide) P.catBoxes.push(box); r.items.forEach(c => P.cat.set(c.id, c));
      } catch (e) { /* каталог недоступен — рисуем и без него */ }
    }
    if (z >= 12) {
      const b2 = map.getBounds().pad(0.3), bx = [b2.getSouth(), b2.getWest(), b2.getNorth(), b2.getEast()].map(v => +v.toFixed(2));
      if (!P.campBoxes.some(c => c[0] <= bx[0] && c[1] <= bx[1] && c[2] >= bx[2] && c[3] >= bx[3])) {
        try { const r = await api('GET', `/api/camps?bbox=${bx.join(',')}`, undefined, false, false); if (!P) return; if (!r.wide) { P.campBoxes.push(bx); r.items.forEach(c => P.camps.set(c[0] + ',' + c[1], c)); } } catch (e) { /* без стоянок */ }
      }
    }
    drawCat();
  }
  function drawCat() {
    if (!P) return; const map = getMap(), z = map.getZoom(), vb = map.getBounds().pad(0.1); P.catLayer.clearLayers();
    if (z < 9) return;
    const G = P.tab === 'gen' ? P.gen : null, st = id => G && (G.must.some(x => x.id === id) ? 'must' : G.ban.some(x => x.id === id) ? 'ban' : '');
    const used = new Set(P.pts.map(p => p.ref).filter(Boolean));
    if (z >= 12) for (const c of P.camps.values()) {      // стоянки — мелко и под перевалами, чтобы не забивали карту
      if (!vb.contains([c[0], c[1]])) continue;
      const m = L.marker([c[0], c[1]], { icon: L.divIcon({ className: 'catw', html: `<div class="campm">${sym('camp')}</div>`, iconSize: [14, 14], iconAnchor: [7, 7] }), keyboard: false, zIndexOffset: -200 }).addTo(P.catLayer);
      m.bindTooltip(`<b>${esc(c[2] || c[3])}</b>${c[2] ? ' · ' + esc(c[3]) : ''}<br><small>${G ? 'стоянка по OpenStreetMap — подбор ставит ночёвки на такие' : 'нажми — ночёвка здесь'}</small>`, { className: 'tt', direction: 'top', offset: [0, -8] });
      m.on('click', ev => { L.DomEvent.stop(ev); if (G) return; addCamp(c); });
    }
    let n = 0;
    for (const c of P.cat.values()) {
      if ((!G && used.has(c.id)) || !vb.contains([c.lat, c.lon]) || (c.kind === 'peak' && (z < 12 || G))) continue;
      if (++n > 500) break;
      const col = (ctx.gcol()[c.grade] || '#7d8a99'), lab = z >= 13 ? `<span class="cl">${esc(c.name || c.title)}${c.h ? ' ' + c.h : ''}</span>` : '';
      const dim = G && !st(c.id) && !(G.quota[c.grade] && +G.quota[c.grade][1] > 0) ? ' dim' : '';
      const html = c.kind === 'peak' ? `<div class="catm peak"><i></i>${lab}</div>` : `<div class="catm pass ${st(c.id) || ''}${dim}" style="--g:${col}"><b>${esc(c.grade || '')}</b>${lab}</div>`;
      const m = L.marker([c.lat, c.lon], { icon: L.divIcon({ className: 'catw', html, iconSize: [24, 24], iconAnchor: [12, 12] }), keyboard: false, riseOnHover: true, zIndexOffset: 100 }).addTo(P.catLayer);
      const how = G ? (st(c.id) === 'must' ? 'обязательно через него · нажми — «не через него»' : st(c.id) === 'ban' ? 'не через него · нажми — как все' : 'нажми — обязательно через него')
        : `нажми — провести маршрут через ${c.kind === 'peak' ? 'вершину' : 'перевал'}`;
      m.bindTooltip(`<b>${esc(c.title)}</b>${c.grade ? ' · ' + esc(c.grade) : ''}${c.h ? ' · ' + c.h + ' м' : ''}${c.alt ? `<br><i>${esc(c.alt)}</i>` : ''}${c.note ? `<br>${esc(c.note)}` : ''}<br><small>${how}</small>`, { className: 'tt', direction: 'top', offset: [0, -10] });
      m.on('click', ev => { L.DomEvent.stop(ev); addCat(c); });
    }
  }


  // ---------------------------------------------------------------- подбор маршрута (эксперимент)
  const GRADES = ['н/к', '1А', '1Б', '2А', '2Б', '3А', '3Б'];
  const VCOL = ['#E2001A', '#1F6FD1', '#0B8A3E', '#7A1AAA', '#D96C00', '#00838F'];
  function genInit() {
    if (P.gen) return;
    const f = P.pts[0], l = P.pts[P.pts.length - 1];
    P.gen = { start: f ? ll(f) : null, finish: l && P.pts.length > 1 ? ll(l) : null, loop: false, km: 60, days: '', trail: true,
      quota: { 'н/к': [0, 0], '1А': [1, 2], '1Б': [0, 1], '2А': [0, 0], '2Б': [0, 0], '3А': [0, 0], '3Б': [0, 0] }, must: [], ban: [], more3: false,
      pick: f ? (P.pts.length > 1 ? null : 'finish') : 'start', res: [], sel: 0, job: null, secs: 0, shown: [], msg: '', prog: null };
  }
  function genFlag(q, kind) {
    const m = L.marker(q, { icon: L.divIcon({ className: 'plkw', html: `<div class="gflag ${kind}">${kind === 'start' ? 'С' : 'Ф'}</div>`, iconSize: [30, 30], iconAnchor: [15, 15] }), draggable: true, zIndexOffset: 800 }).addTo(P.genLayer);
    m.bindTooltip(kind === 'start' ? 'Старт — тяни, чтобы передвинуть' : 'Финиш — тяни, чтобы передвинуть', { className: 'tt', direction: 'top', offset: [0, -14] });
    m.on('dragend', ev => { const c = ev.target.getLatLng(); P.gen[kind] = [+c.lat.toFixed(6), +c.lng.toFixed(6)]; genRender(); ctx.repaint(); });
  }
  function genRender() {
    if (!P || !P.genLayer) return; P.genLayer.clearLayers();
    if (P.tab !== 'gen') return;
    const G = P.gen;
    G.res.forEach((v, i) => { if (i === G.sel) return; L.polyline(v.line, { color: VCOL[i % VCOL.length], weight: 3.5, opacity: .55, dashArray: '6 6' }).on('click', () => { G.sel = i; genRender(); ctx.repaint(); }).addTo(P.genLayer); });
    const v = G.res[G.sel];
    if (v) {
      L.polyline(v.line, { color: '#fff', weight: 10, opacity: .9, interactive: false }).addTo(P.genLayer);
      L.polyline(v.line, { color: VCOL[G.sel % VCOL.length], weight: 5.5, interactive: false }).addTo(P.genLayer);
      v.plan.pts.forEach(p => { if (p.kind !== 'pass' && p.kind !== 'camp') return;
        L.marker([p.lat, p.lon], { icon: L.divIcon({ className: 'plkw', html: `<div class="plk ${p.kind}">${sym(p.kind)}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] }), interactive: true })
          .bindTooltip(`${esc(p.name || (p.kind === 'camp' ? 'ночёвка' : ''))}${p.grade ? ' · ' + esc(p.grade) : ''}${p.h ? ' · ' + p.h + ' м' : ''}`, { className: 'tt', direction: 'top' }).addTo(P.genLayer); });
    }
    if (G.start) genFlag(G.start, 'start');
    if (G.finish && !G.loop) genFlag(G.finish, 'finish');
  }
  function findHtml() {
    return `<form class="plfind" data-plfind><input type="search" name="q" placeholder="Куда идём? Село, вершина, перевал…" autocomplete="off" value="${esc(P.findQ || '')}"><button class="btn sm">Найти</button></form>
      ${P.found ? `<div class="plfound">${P.found.length ? P.found.map((f, i) => `<button data-pl="goto" data-i="${i}">${esc(f.name)}</button>`).join('') : '<div class="hint">Ничего не нашлось — попробуй иначе или найди на карте.</div>'}</div>` : ''}`;
  }
  function quotaHtml() {
    const G = P.gen, rows = GRADES.filter((g, i) => i < 5 || G.more3 || G.quota[g][1] > 0);
    const lo = GRADES.reduce((a, g) => a + (+G.quota[g][0] || 0), 0), hi = GRADES.reduce((a, g) => a + (+G.quota[g][1] || 0), 0);
    return `<div class="field"><span>Перевалы по категориям</span><div class="gq"><div class="gqh"><span></span><span>от</span><span>до</span></div>
      ${rows.map(g => `<div class="gqr${+G.quota[g][1] > 0 ? '' : ' off'}"><b class="gqb" style="--g:${ctx.gcol()[g] || '#7d8a99'}">${g}</b>
        <input type="number" min="0" max="6" data-gq="${g}" data-k="0" value="${G.quota[g][0]}" aria-label="${g}: от"><input type="number" min="0" max="6" data-gq="${g}" data-k="1" value="${G.quota[g][1]}" aria-label="${g}: до"></div>`).join('')}
      ${G.more3 ? '' : '<button class="linkbtn" data-pl="gmore3">+ 3А и 3Б</button>'}</div>
      <div class="hint">Всего ${lo === hi ? lo : `${lo}–${hi}`} ${pl(hi, 'перевал', 'перевала', 'перевалов')}${hi > 6 ? ' — больше шести «Нитка» пока не подбирает' : ''}. «До» = 0 — такие не брать. На карте перевалы нужных категорий яркие, остальные — бледные.</div></div>`;
  }
  function pickedHtml() {
    const G = P.gen, chip = (x, k) => `<span class="gpick ${k}"><b style="--g:${ctx.gcol()[x.grade] || '#7d8a99'}">${esc(x.grade || '?')}</b>${esc(x.name)}<button data-pl="gunpick" data-id="${esc(x.id)}" title="Убрать">×</button></span>`;
    return `<div class="field"><span>Конкретные перевалы</span>
      ${G.must.length ? `<div class="gpicks"><i>Обязательно через:</i>${G.must.map(x => chip(x, 'must')).join('')}</div>` : ''}
      ${G.ban.length ? `<div class="gpicks"><i>Не через:</i>${G.ban.map(x => chip(x, 'ban')).join('')}</div>` : ''}
      <div class="hint">Нажми на перевал на карте: один раз — обязательно через него, второй — не через него, третий — как все.</div></div>`;
  }
  function progHtml() {
    const G = P.gen, pr = G.prog || {};
    return `<div class="gpb"><i style="width:${pr.pct || 2}%"></i></div><div class="gps_"><b>${esc(pr.stage || 'Начинаю…')}</b><span class="hint">${G.secs} с</span></div>
      <div class="hint">${pr.cands ? `кандидатов: ${pr.cands.length}` : ''}${pr.built ? ` · готово вариантов: ${pr.built.length}` : ''} — на карте видно, что сейчас пробую</div>`;
  }
  function genPanel() {
    const G = P.gen, run = !!G.job;
    const gsel = (f, v) => `<select data-gf="${f}">${GRADES.map(g => `<option${g === v ? ' selected' : ''}>${g}</option>`).join('')}</select>`;
    const where = q => q ? `${q[0].toFixed(4)}, ${q[1].toFixed(4)}` : 'не указан';
    return `<div class="banner gexp"><div><b>Эксперимент.</b> «Нитка» складывает маршрут из перевалов каталога, троп OpenStreetMap и рельефа. Это идея для разговора, а не готовая нитка: каждый перевал проверь по отчётам — с какой стороны заход, есть ли тропа, открыт ли район.</div></div>
      ${findHtml()}
      <div class="gpts">
        <div class="gpt${G.pick === 'start' ? ' pick' : ''}"><span class="gflag start">С</span><div><b>Старт</b><div class="hint">${G.start ? where(G.start) : 'нажми на карте'}</div></div><button class="btn sm" data-pl="gpick" data-v="start">${G.pick === 'start' ? 'Жду клик по карте…' : 'Указать на карте'}</button></div>
        <label class="switch gloop"><input type="checkbox" data-gf="loop" ${G.loop ? 'checked' : ''}><span>Кольцо — финиш там же, где старт</span></label>
        ${G.loop ? '' : `<div class="gpt${G.pick === 'finish' ? ' pick' : ''}"><span class="gflag finish">Ф</span><div><b>Финиш</b><div class="hint">${G.finish ? where(G.finish) : 'нажми на карте'}</div></div><button class="btn sm" data-pl="gpick" data-v="finish">${G.pick === 'finish' ? 'Жду клик по карте…' : 'Указать на карте'}</button></div>`}
      </div>
      <div class="ggrid">
        <label class="field"><span>Длина, км</span><input type="number" min="5" max="400" step="5" data-gf="km" value="${G.km}"></label>
        <label class="field"><span>Дней</span><input type="number" min="1" max="40" data-gf="days" value="${G.days}" placeholder="как выйдет"></label>
      </div>
      ${quotaHtml()}
      ${pickedHtml()}
      <div class="field"><span>Как идти</span><div class="seg sm even"><button class="${G.trail ? 'on' : ''}" data-pl="gtrail" data-v="1">По тропам</button><button class="${G.trail ? '' : 'on'}" data-pl="gtrail" data-v="0">По рельефу, без троп</button></div>
        <div class="hint">${G.trail ? 'Тропы OpenStreetMap; к перевалу, куда тропа не доходит, — подход по рельефу.' : 'Без троп: самый удобный путь по склонам, в обход круч.'}${G.days ? ' Ночёвки встанут у границ дней — на стоянке или приюте из OpenStreetMap, если есть рядом, иначе в самой низкой точке — в долине.' : ''}</div></div>
      <div class="plbar"><button class="btn go" data-pl="grun" ${run || !G.start || (!G.finish && !G.loop) ? 'disabled' : ''}>${run ? 'Подбираю…' : 'Подобрать варианты'}</button></div>
      <div class="gprog"${run ? '' : ' hidden'}>${progHtml()}</div>
      ${G.msg ? `<div class="banner"><div>${esc(G.msg)}</div></div>` : ''}
      ${G.res.length ? `<div class="gvars">${G.res.map((v, i) => `<div class="gvar${i === G.sel ? ' sel' : ''}" data-pl="gsel" data-i="${i}" style="--vc:${VCOL[i % VCOL.length]}">
          <div class="gvh"><b>Вариант ${i + 1}</b><span class="gkm">${String(v.km).replace('.', ',')} км</span><span class="hint">${v.delta > 0 ? '+' : ''}${String(v.delta).replace('.', ',')} к задуманному</span><span>+${v.up} / −${v.dn} м</span></div>
          <div class="gps">${v.passes.map(p => `<span class="gp${p.extra ? ' extra' : ''}${p.crossed === false ? ' bad' : ''}" style="--g:${ctx.gcol()[p.grade] || '#7d8a99'}" title="${esc((p.extra ? 'По пути: нитка сама переваливает через него. ' : '') + (p.crossed === false ? 'Нитка подходит к нему, но не переходит в другую долину. ' : '') + (p.note || ''))}"><b>${esc(p.grade || '?')}</b>${esc(p.name.replace(/^пер\.\s*/, ''))}${p.h ? ` <i>${p.h}</i>` : ''}${p.extra ? ' <u>по пути</u>' : p.trail === 'none' ? ' <u>без тропы</u>' : p.trail === 'one' ? ' <u>тропа с одной стороны</u>' : ''}</span>`).join('')}</div>
          ${(v.over || []).length ? `<div class="warnx">Перевалов больше, чем просил: ${esc(v.over.join('; '))} — с учётом тех, что по пути</div>` : ''}
          ${(v.uturn || []).length ? `<div class="warnx">${esc(v.uturn.join(', '))}: подход без перехода в другую долину</div>` : ''}
          ${v.camps.length ? `<div class="hint">Ночёвки: ${v.camps.map(c => `${String(c.km).replace('.', ',')} км, ${c.h} м${c.name ? ` — <b>${esc(c.name)}</b>` : ''}`).join(' · ')}</div>` : ''}
          ${v.camps.some(c => c.h > 3300) ? `<div class="warnx">Ночёвка на ${Math.max(...v.camps.map(c => c.h))} м — высоко: дни выходят по гребням или ледникам. Поменяй число дней или передвинь ночёвку.</div>` : ''}
          ${v.nolink ? `<div class="warnx">Без тропы ${v.nolink} из ${v.legs} отрезков — там путь по рельефу</div>` : ''}
          ${i === G.sel ? '<div class="chips"><button class="btn sm go" data-pl="gtake">Взять в маршрут</button><span class="hint">дальше правится как обычно</span></div>' : ''}</div>`).join('')}</div>
        <button class="btn sm" data-pl="gmore" ${run ? 'disabled' : ''}>Ещё варианты — с другими перевалами</button>` : ''}`;
  }
  function genCycle(c) {      // перевал на карте в подборе: обязательно → не через него → как все
    const G = P.gen, it = { id: c.id, name: c.title, grade: c.grade };
    if (c.kind === 'peak') return toast('Подбор ведёт маршрут через перевалы — вершины в нём не участвуют');
    if (G.must.some(x => x.id === c.id)) { G.must = G.must.filter(x => x.id !== c.id); G.ban.push(it); toast(`${c.title}: маршрут не пойдёт через него`); }
    else if (G.ban.some(x => x.id === c.id)) { G.ban = G.ban.filter(x => x.id !== c.id); toast(`${c.title}: как все`); }
    else { if (G.must.length >= 6) return toast('Обязательных — не больше шести', true); G.must.push(it); toast(`${c.title}: маршрут обязательно пройдёт через него`); }
    drawCat(); ctx.repaint();
  }
  async function genRun(more) {
    const G = P.gen; if (G.job) return;
    if (!more) { G.res = []; G.shown = []; G.sel = 0; }
    G.msg = ''; G.secs = 0;
    const quota = {}; GRADES.forEach(g => { const a = +G.quota[g][0] || 0, b = +G.quota[g][1] || 0; if (a || b) quota[g] = [a, Math.max(a, b)]; });
    const body = { start: G.start, finish: G.loop ? G.start : G.finish, km: +G.km, days: G.days ? +G.days : null, quota, trail: G.trail,
      must: G.must.map(x => x.id), ban: G.ban.map(x => x.id), exclude: [...G.shown, ...G.ban.map(x => x.id)] };
    G.prog = null; G.dimmed = false;
    try { G.job = (await api('POST', `/api/projects/${ctx.pid()}/route-gen`, body, false, false)).job; } catch (e) { G.msg = e.message; ctx.repaint(); return; }
    ctx.repaint();
    setTimeout(() => { const el = document.querySelector('#panel .gprog'); el && el.scrollIntoView({ block: 'center', behavior: 'smooth' }); }, 50);
    while (P && P.gen === G && G.job) {
      await new Promise(r => setTimeout(r, 900));
      let r; try { r = await api('GET', `/api/projects/${ctx.pid()}/route-gen?job=${G.job}`, undefined, false, false); } catch (e) { G.job = null; G.msg = e.message; break; }
      G.secs = r.secs; G.prog = r.prog || G.prog;
      if (r.state !== 'done') { progLive(); continue; }
      if (r.state === 'done') {
        G.job = null;
        if (!r.ok) G.msg = r.msg; else if (r.warn) G.msg = r.warn;
        else { const n0 = G.res.length; G.res.push(...r.variants); G.sel = n0; r.variants.forEach(v => v.passes.forEach(p => { if (!G.must.some(x => x.id === p.ref)) G.shown.push(p.ref); }));
          const b = L.latLngBounds(r.variants.flatMap(v => v.line)); getMap().fitBounds(b.pad(0.1)); }
      }
      if (P) { G.prog = null; genRender(); drawCat(); ctx.repaint(); }
    }
  }
  // ход подбора: кандидаты, перебираемые цепочки, вариант, который сейчас прокладывается, и готовые
  function progLive() {
    const G = P.gen, pr = G.prog || {}, el = document.querySelector('#panel .gprog');
    if (el) { el.hidden = false; el.innerHTML = progHtml(); }
    if (!G.dimmed) { G.dimmed = true; P.catLayer.eachLayer(m => m.setOpacity && m.setOpacity(.3)); }      // каталог бледнеет — виден перебор
    genRender();
    (pr.cands || []).forEach(c => L.circleMarker([c[0], c[1]], { radius: 6, color: '#fff', weight: 2, fillColor: ctx.gcol()[c[2]] || '#7d8a99', fillOpacity: .95, interactive: false }).addTo(P.genLayer));
    (pr.sketch || []).forEach((l, i) => L.polyline(l, { color: '#133C6A', weight: i ? 1.6 : 2.6, opacity: i ? .3 : .7, dashArray: '5 6', interactive: false }).addTo(P.genLayer));
    if (pr.trying) L.polyline(pr.trying, { color: '#FEBC0E', weight: 4, opacity: .95, dashArray: '2 7', lineCap: 'round', interactive: false }).addTo(P.genLayer);
    (pr.built || []).forEach((l, i) => { L.polyline(l, { color: '#fff', weight: 7, opacity: .8, interactive: false }).addTo(P.genLayer); L.polyline(l, { color: VCOL[(G.res.length + i) % VCOL.length], weight: 4, interactive: false }).addTo(P.genLayer); });
  }
  function genTake() {
    const v = P.gen.res[P.gen.sel]; if (!v) return;
    const go = () => {
      snap();
      P.pts = v.plan.pts.map(p => ({ ...p }));
      v.plan.legs.forEach((g, i) => { const a = P.pts[i], b = P.pts[i + 1]; if (a && b && g.mode !== 'line') RC.set(lkey(a, b, g.mode), { pts: g.pts, ok: g.ok !== false, used: g.mode }); });
      P.tab = 'draw'; P.sel = null; genRender(); changed();
      toast('Вариант в маршруте — двигай точки, меняй отрезки и ночёвки, потом «Готово — в поход»');
    };
    if (P.pts.length > 1) return confirmBox('Заменить нарисованный маршрут?', 'Точки, которые сейчас в планировщике, заменятся выбранным вариантом. Передумаешь — Ctrl+Z.', 'Заменить', go);
    go();
  }

  // ---------------------------------------------------------------- карта
  function render() {
    if (!P) return; const map = getMap(); P.layer.clearLayers();
    P.pts.slice(0, -1).forEach((_, i) => {
      const g = legOf(i), line = g.pts.map(q => [q[0], q[1]]);
      L.polyline(line, { color: '#fff', weight: 10, opacity: .9, interactive: false }).addTo(P.layer);
      const off = g.mode === 'terrain' || g.used === 'terrain';
      const pl_ = L.polyline(line, { color: g.ok || off ? '#E2001A' : '#B4561A', weight: 5, dashArray: g.mode === 'line' ? '10 7' : off ? '2 8' : null, lineCap: off ? 'round' : 'butt', opacity: g.busy ? .55 : 1 }).addTo(P.layer);
      pl_.bindTooltip(`Отрезок ${i + 1}–${i + 2} · ${g.mode === 'line' ? 'напрямую' : g.mode === 'terrain' ? 'по рельефу, без тропы' : g.ok ? 'по тропе' : (g.msg || 'тропы нет — по рельефу')}<br><small>нажми — вставить точку здесь</small>`, { sticky: true, className: 'tt' });
      pl_.on('click', ev => { L.DomEvent.stop(ev); addPoint(ev.latlng, i + 1); });
    });
    const sz = touch() ? 30 : 24;
    P.pts.forEach((p, i) => {
      const end = i === 0 ? ' start' : i === P.pts.length - 1 && i > 0 ? ' finish' : '';
      const inner = p.kind === 'via' ? `<span>${i + 1}</span>` : sym(p.kind);
      const m = L.marker(ll(p), { icon: L.divIcon({ className: 'plkw', html: `<div class="plk ${p.kind}${end}${P.sel === i ? ' sel' : ''}">${inner}</div>`, iconSize: [sz, sz], iconAnchor: [sz / 2, sz / 2] }),
        draggable: true, keyboard: false, zIndexOffset: 500 }).addTo(P.layer);
      m.bindTooltip(`${i === 0 ? 'Старт' : i === P.pts.length - 1 ? 'Финиш' : `Точка ${i + 1}`}${p.name ? ' · ' + esc(p.name) : ''}${p.kind !== 'via' ? ' · ' + KIND[p.kind] : ''}<br><small>тяни — передвинуть, правый клик — убрать</small>`, { className: 'tt', direction: 'top', offset: [0, -12] });
      m.on('dragstart', () => snap());
      m.on('dragend', ev => { const c = ev.target.getLatLng(); const q = P.pts[i]; if (q.ref && geo(ll(q), [c.lat, c.lng]) > 150) { q.ref = null; } q.lat = +c.lat.toFixed(6); q.lon = +c.lng.toFixed(6); P.sel = i; changed(); });
      m.on('click', ev => { L.DomEvent.stop(ev); P.sel = i; render(); const r = document.querySelector(`.plp[data-i="${i}"]`); r && r.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); });
      m.on('contextmenu', ev => { L.DomEvent.stop(ev); delPoint(i); });
    });
    drawCat(); ctx.repaint();
  }
  function click(latlng) {
    if (!P) return;
    if (P.tab === 'gen') {
      const G = P.gen, q = [+latlng.lat.toFixed(6), +latlng.lng.toFixed(6)];
      const t = G.pick || (!G.start ? 'start' : !G.finish && !G.loop ? 'finish' : null);
      if (!t) return toast('Старт и финиш стоят. Передвинуть — потяни флажок или нажми «Указать на карте»');
      G[t] = q; G.pick = t === 'start' && !G.finish && !G.loop ? 'finish' : null; genRender(); ctx.repaint(); return;
    }
    // рядом значок перевала — значит, хотели перевал
    const map = getMap(), cp = map.latLngToContainerPoint(latlng); let near = null;
    for (const c of P.cat.values()) { const p = map.latLngToContainerPoint([c.lat, c.lon]); const d = Math.hypot(p.x - cp.x, p.y - cp.y); if (d < 16 && (!near || d < near.d)) near = { c, d }; }
    if (near && map.getZoom() >= 9) return addCat(near.c);
    addPoint(latlng);
  }

  // ---------------------------------------------------------------- панель
  function panel() {
    if (!P) return '';
    const n = P.pts.length, pr = P.prof && !P.prof.stale ? P.prof : P.prof;
    const legs = P.pts.slice(0, -1).map((_, i) => legOf(i));
    const busy = legs.filter(g => g.busy).length, bad = legs.filter(g => !g.ok).length;
    const step = n === 0 ? 'Нажми на карте старт маршрута.'
      : n === 1 ? 'Теперь — следующую точку: поворот, ночёвку или перевал. Перевалы каталога — значки с категорией на карте: нажми на нужный, и маршрут пройдёт через него.'
      : 'Добавляй точки дальше — кликом по карте. Тяни точку, чтобы передвинуть; нажми на линию, чтобы вставить точку посередине. Ночёвки делят маршрут на дни.';
    const other = ctx.proj().pieces.some(p => p.src !== 'plan' && (p.role === 'main' || p.role === 'radial'));
    const st = i => pr && pr.legS && pr.legS[i] ? `${kmTxt(pr.legS[i].km * 1000)} км · +${pr.legS[i].up} −${pr.legS[i].dn}` : `${kmTxt(legs[i].pts.reduce((s, q, j, a) => j ? s + geo(a[j - 1], q) : 0, 0))} км`;
    const hAt = i => pr && pr.M ? Math.round(pr.E[pr.M[i]]) : null;
    const degTxt = i => { const d = pr && pr.legS && pr.legS[i] ? pr.legS[i].deg : null; return d == null ? '' : `<span class="${d >= 30 ? 'warnx' : 'hint'}" title="Самый крутой участок отрезка (по модели рельефа, на 60 м)">круче всего ${d}°${d >= 35 && legs[i].mode === 'line' ? ' — попробуй «рельеф»' : ''}</span>`; };
    return `<div class="hhead"><h2>Маршрут</h2><span class="sp"></span><button class="icon" data-pl="undo" title="Отменить (Ctrl+Z)" ${P.undo.length ? '' : 'disabled'}>↶</button><button class="icon" data-pl="redo" title="Вернуть (Ctrl+Y)" ${P.redo.length ? '' : 'disabled'}>↷</button></div>
      <div class="seg sm even gtabs"><button class="${P.tab === 'draw' ? 'on' : ''}" data-pl="tab" data-v="draw">Рисую сам</button><button class="${P.tab === 'gen' ? 'on' : ''}" data-pl="tab" data-v="gen">Подобрать <sup>β</sup></button></div>
      ${P.tab === 'gen' ? genPanel() : `
      <div class="plbar"><button class="btn sm" data-pl="cancel">Отмена</button><button class="btn go" data-pl="done" ${n < 2 ? 'disabled' : ''}>Готово — в поход</button></div>
      <div class="field"><span>Между точками</span><div class="seg sm even"><button class="${P.def === 'trail' ? 'on' : ''}" data-pl="def" data-v="trail">По тропам</button><button class="${P.def === 'terrain' ? 'on' : ''}" data-pl="def" data-v="terrain">По рельефу</button><button class="${P.def === 'line' ? 'on' : ''}" data-pl="def" data-v="line">Напрямую</button></div>
        <div class="hint">${P.def === 'trail' ? 'По тропам OpenStreetMap; где тропа не доходит до точки (перевал, вершина) — подход по рельефу, в обход скал. Тропы нет вовсе — весь отрезок по рельефу.' : P.def === 'terrain' ? 'Без троп: самый удобный путь по склонам — долинами, в обход круч (круче 35° почти не ходит), на седловину.' : 'Прямыми — как по линейке. Набор на гребнях выйдет завышенным, круче всего — смотри у отрезка.'} Это — для новых точек; у каждого отрезка свой переключатель ниже.</div></div>
      ${findHtml()}
      <div class="edstep plstep"><span>${step}</span></div>
      ${P.fromDraft ? '<div class="hint">Это черновик из браузера — в поход он попадёт по «Готово». <a href="#" data-pl="drop">Отбросить черновик</a></div>' : ''}
      ${other ? '<div class="banner"><div>В походе уже есть загруженный трек — нарисованный маршрут добавится к нему как основная нитка. Если это вариант, отметь его в «Днях».</div></div>' : ''}
      ${n > 1 ? `<div class="pltot">${pr ? `<b>${kmTxt(pr.all.km * 1000)} км</b><span>+${pr.all.up} м</span><span>−${pr.all.dn} м</span>` : ''}<span>${pr && pr.days ? `${pr.days.length} ${pl(pr.days.length, 'день', 'дня', 'дней')}` : ''}</span>${busy ? `<span class="hint">ищу тропы: ${busy}</span>` : ''}${bad ? `<span class="warnx">без тропы: ${bad}</span>` : ''}</div>${profSvg()}` : ''}
      ${enrouteHtml()}
      ${pr && pr.days && pr.days.length > 1 ? `<div class="pldays">${pr.days.map((d, i) => `<div><b>День ${i + 1}</b><span>${kmTxt(d.km * 1000)} км</span><span>+${d.up}</span><span>−${d.dn}</span></div>`).join('')}</div>` : ''}
      <div class="plist">${P.pts.map((p, i) => `
        <div class="plp${P.sel === i ? ' sel' : ''}" data-i="${i}"><span class="plk mini ${p.kind}${i === 0 ? ' start' : i === n - 1 && i > 0 ? ' finish' : ''}">${p.kind === 'via' ? i + 1 : sym(p.kind)}</span>
          <input class="pname" data-plf="name" data-i="${i}" value="${esc(p.name || '')}" placeholder="${i === 0 ? 'старт' : i === n - 1 ? 'финиш' : KIND[p.kind]}" maxlength="80">
          <select data-plf="kind" data-i="${i}" aria-label="Что это">${Object.entries(KIND).map(([k, t]) => `<option value="${k}"${p.kind === k ? ' selected' : ''}>${t}</option>`).join('')}</select>
          <span class="hint plh">${p.grade ? esc(p.grade) + ' · ' : ''}${p.h || hAt(i) ? (p.h || hAt(i)) + ' м' : ''}</span>
          <button class="icon-btn" data-pl="del" data-i="${i}" title="Убрать точку">×</button></div>
        ${i < n - 1 ? `<div class="pll"><span class="seg sm"><button class="${legs[i].mode === 'trail' ? 'on' : ''}" data-pl="lmode" data-i="${i}" data-v="trail" title="По тропам; к точке вне тропы — подход по рельефу">тропа</button><button class="${legs[i].mode === 'terrain' ? 'on' : ''}" data-pl="lmode" data-i="${i}" data-v="terrain" title="Без тропы — по склонам, в обход круч">рельеф</button><button class="${legs[i].mode === 'line' ? 'on' : ''}" data-pl="lmode" data-i="${i}" data-v="line" title="Прямой линией">прямо</button></span>
          <span>${st(i)}</span>${degTxt(i)}${legs[i].busy ? `<span class="hint">${legs[i].mode === 'terrain' ? 'считаю по рельефу…' : 'ищу тропу…'}</span>` : !legs[i].ok ? `<span class="warnx">${esc(legs[i].msg || 'тропы нет — по рельефу')}</span>` : ''}</div>` : ''}`).join('')}</div>
      ${n ? '<div class="hint">Правый клик по точке на карте — убрать её. Ctrl+Z — отменить.</div>' : ''}`}`;
  }
  // нитка переваливает через седловину: местный максимум высоты, до и после — ниже на 80+ м (как на сервере)
  function crossesAt(E, D, i) {
    const n = E.length; let lo = i, hi = i;
    while (lo > 0 && D[i] - D[lo] < 350) lo--; while (hi < n - 1 && D[hi] - D[i] < 350) hi++;
    let top = -1e9; for (let k = lo; k <= hi; k++) top = Math.max(top, E[k]);
    if (E[i] < top - 60) return false;
    let a = i, b = i; while (a > 0 && D[i] - D[a] < 1200) a--; while (b < n - 1 && D[b] - D[i] < 1200) b++;
    let m1 = E[a], m2 = E[b]; for (let k = lo; k <= i; k++) m1 = Math.min(m1, E[k]); for (let k = i; k <= hi; k++) m2 = Math.min(m2, E[k]);
    return a < i && i < b && m1 < top - 80 && m2 < top - 80;
  }
  function enroute() {
    const pr = P.prof; if (!pr || pr.stale || !pr.R) return [];
    const have = new Set(P.pts.map(p => p.ref).filter(Boolean)), out = [];
    const keyD = P.pts.map((p, i) => pr.D[pr.M[i]]);
    for (const c of P.cat.values()) {
      if (c.kind !== 'pass' || have.has(c.id)) continue;
      let best = null;
      for (let i = 0; i < pr.R.length; i++) { const q = pr.R[i]; if (Math.abs(q[0] - c.lat) > 0.004 || Math.abs(q[1] - c.lon) > 0.006) continue; const d = geo(q, [c.lat, c.lon]); if (!best || d < best.d) best = { d, i }; }
      if (!best || best.d > 220 || !crossesAt(pr.E, pr.D, best.i)) continue;
      if (keyD.some(d => Math.abs(d - pr.D[best.i]) < 600) || out.some(x => Math.abs(pr.D[x.i] - pr.D[best.i]) < 800)) continue;      // эта седловина уже отмечена
      out.push({ c, i: best.i });
    }
    return out.sort((a, b) => a.i - b.i);
  }
  function enrouteHtml() {
    const L_ = enroute(); P.enroute = L_; if (!L_.length) return '';
    return `<div class="enroute"><div><b>По пути нитка переваливает и через:</b></div><div class="gps">${L_.map((x, k) => `<span class="gp" style="--g:${ctx.gcol()[x.c.grade] || '#7d8a99'}"><b>${esc(x.c.grade || '?')}</b>${esc(x.c.title.replace(/^пер\.\s*/, ''))}${x.c.h ? ` <i>${x.c.h}</i>` : ''}<button data-pl="enadd" data-i="${k}" title="Отметить перевалом маршрута">+</button></span>`).join('')}</div>
      <div class="chips"><button class="btn sm" data-pl="enall">Отметить все</button><span class="hint">иначе их не будет ни в точках похода, ни в квотах</span></div></div>`;
  }
  function onClick(e) {
    if (!P) return;
    const b = e.target.closest('[data-pl]'); if (!b || !b.closest('#panel')) return;
    e.preventDefault(); e.stopPropagation();
    const a = b.dataset.pl, i = +b.dataset.i;
    if (a === 'cancel') cancel(); else if (a === 'done') done(); else if (a === 'undo') undo(); else if (a === 'redo') redo();
    else if (a === 'def') { P.def = b.dataset.v; ctx.repaint(); keepDraft(); }
    else if (a === 'lmode') { snap(); P.pts[i].mode = b.dataset.v; changed(); }
    else if (a === 'del') delPoint(i);
    else if (a === 'tab') { P.tab = b.dataset.v; if (P.tab === 'gen') genInit(); P.layer.eachLayer(l => l.setStyle && l.setStyle({ opacity: P.tab === 'gen' ? .25 : 1 })); genRender(); drawCat(); ctx.repaint(); }
    else if (a === 'gpick') { P.gen.pick = b.dataset.v; ctx.repaint(); toast(`Нажми на карте ${b.dataset.v === 'start' ? 'старт' : 'финиш'}`); }
    else if (a === 'gtrail') { P.gen.trail = b.dataset.v === '1'; ctx.repaint(); }
    else if (a === 'grun') genRun(false); else if (a === 'gmore') genRun(true);
    else if (a === 'gsel') { if (e.target.closest('[data-pl="gtake"]')) return; P.gen.sel = i; genRender(); ctx.repaint(); }
    else if (a === 'gtake') genTake();
    else if (a === 'enadd') { const x = (P.enroute || [])[i]; if (x) addCat(x.c); }
    else if (a === 'enall') { const L_ = (P.enroute || []).slice(); if (!L_.length) return; snap(); const u0 = P.undo.length; L_.forEach(x => addCat(x.c)); P.undo.splice(u0, P.undo.length - u0); toast(`Отмечено перевалов: ${L_.length}`); }
    else if (a === 'gmore3') { P.gen.more3 = true; ctx.repaint(); }
    else if (a === 'gunpick') { const id = b.dataset.id; P.gen.must = P.gen.must.filter(x => x.id !== id); P.gen.ban = P.gen.ban.filter(x => x.id !== id); drawCat(); ctx.repaint(); }
    else if (a === 'goto') { const f = P.found[i], map = getMap(); P.found = null; if (f.bbox) map.fitBounds([[f.bbox[0], f.bbox[2]], [f.bbox[1], f.bbox[3]]], { maxZoom: 13 }); else map.setView([f.lat, f.lon], 12); ctx.repaint(); }
    else if (a === 'drop') { try { delete localStorage[draftKey()]; } catch (er) { /* ок */ } const pr = ctx.proj(); P.pts = pr.plan ? pr.plan.pts.map(p => ({ ...p })) : []; P.fromDraft = false; P.undo = []; changed(); }
  }
  async function onSubmit(e) {
    const f = e.target.closest('[data-plfind]'); if (!f || !P) return; e.preventDefault();
    P.findQ = f.q.value.trim(); if (P.findQ.length < 2) return;
    try { P.found = (await api('GET', `/api/geocode?q=${encodeURIComponent(P.findQ)}`, undefined, false, 'Ищу место…')).items; } catch (er) { toast(er.message, true); P.found = null; }
    if (P && P.found && P.found.length === 1) { const x = P.found[0]; P.found = null; getMap().setView([x.lat, x.lon], 12); }
    ctx.repaint();
  }
  function onChange(e) {
    if (!P) return;
    const gf = e.target.closest('[data-gf]');
    const gq = e.target.closest('[data-gq]');
    if (gq && P.gen) { const g = gq.dataset.gq, k = +gq.dataset.k; let v = Math.max(0, Math.min(6, Math.round(+gq.value || 0))); const q = P.gen.quota[g];
      q[k] = v; if (k === 0 && q[1] < v) q[1] = v; if (k === 1 && q[0] > v) q[0] = v; drawCat(); ctx.repaint(); return; }
    if (gf && P.gen) { const f = gf.dataset.gf; P.gen[f] = f === 'loop' ? gf.checked : gf.value; if (f === 'loop') { P.gen.pick = null; genRender(); } ctx.repaint(); return; }
    const el = e.target.closest('[data-plf]'); if (!el) return;
    const i = +el.dataset.i, p = P.pts[i]; if (!p) return;
    snap();
    if (el.dataset.plf === 'name') { p.name = el.value.trim().slice(0, 80); keepDraft(); render(); }
    else if (el.dataset.plf === 'kind') { p.kind = el.value; if (p.kind === 'via' || p.kind === 'camp') { p.grade = null; } changed(); }
  }
  function onKey(e) {
    if (!P || (e.target.closest && e.target.closest('input,textarea,select'))) return;
    const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (mod && (k === 'z' || k === 'я') && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (mod && (k === 'y' || k === 'н' || ((k === 'z' || k === 'я') && e.shiftKey))) { e.preventDefault(); redo(); }
    else if ((k === 'delete' || k === 'backspace') && P.sel != null) { e.preventDefault(); delPoint(P.sel); }
  }
  document.addEventListener('mousemove', e => { if (P && e.target.closest && e.target.closest('svg.plprof')) profHover(e); });
  document.addEventListener('mouseleave', e => { if (P && P.hover && e.target.closest && e.target.closest('svg.plprof')) { P.hover.remove(); P.hover = null; } }, true);

  return { open, close, click, panel, active: () => !!P };
}
