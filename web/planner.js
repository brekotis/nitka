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
      (pr.plan.legs || []).forEach((g, i) => { const a = pts[i], b = pts[i + 1]; if (a && b && g.mode === 'trail' && g.pts && g.pts.length > 1) RC.set(lkey(a, b, 'trail'), { pts: g.pts, ok: g.ok !== false }); });
    }
    let draft = null; try { draft = JSON.parse(localStorage[draftKey()] || 'null'); } catch (e) { draft = null; }
    if (draft && draft.pts && JSON.stringify(draft.pts) !== JSON.stringify(pts)) { pts = draft.pts; def = draft.def || def; fromDraft = true; (draft.rc || []).forEach(([k, v]) => RC.set(k, v)); }
    P = { pts, def, undo: [], redo: [], sel: null, prof: null, profKey: '', layer: L.layerGroup().addTo(map), catLayer: L.layerGroup().addTo(map), hover: null,
      cat: new Map(), catBoxes: [], init: JSON.stringify(pr.plan ? pr.plan.pts : []), fromDraft };
    map.on('moveend', loadCat); document.addEventListener('keydown', onKey); document.addEventListener('click', onClick, true); document.addEventListener('change', onChange, true); document.addEventListener('submit', onSubmit, true);
    const fit = pts.length ? pts.map(ll) : pr.pieces.filter(p => p.role !== 'skip').flatMap(p => p.pts.map(q => [q[0], q[1]]));
    if (fit.length) map.fitBounds(L.latLngBounds(fit).pad(0.15), { maxZoom: 14 });
    if (fromDraft) toast('Продолжаю черновик маршрута — в поход он ещё не попал');
    render(); loadCat(); schedProfile();
  }
  function close() {
    if (!P) return; const map = getMap();
    map && map.off('moveend', loadCat); document.removeEventListener('keydown', onKey); document.removeEventListener('click', onClick, true); document.removeEventListener('change', onChange, true); document.removeEventListener('submit', onSubmit, true);
    P.layer.remove(); P.catLayer.remove(); P.hover && P.hover.remove(); P = null; ctx.onClose();
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
    if (c && c !== 'busy') return { mode, pts: withEnds(c.pts, a, b), ok: c.ok, msg: c.msg };
    if (!c) fetchLeg(k, a, b);
    return { mode, pts: line, ok: true, busy: true };
  }
  // тропа начинается не точно в точке маршрута: подход от точки к тропе и сход с неё — часть пути
  function withEnds(pts, a, b) {
    const out = pts.map(q => [q[0], q[1]]);
    if (geo(out[0], ll(a)) > 5) out.unshift(ll(a)); else out[0] = ll(a);
    if (geo(out[out.length - 1], ll(b)) > 5) out.push(ll(b)); else out[out.length - 1] = ll(b);
    return out;
  }
  function fetchLeg(k, a, b) { RC.set(k, 'busy'); queue.push([k, a, b]); pump(); }
  async function pump() {
    while (inflight < 3 && queue.length) {
      const [k, a, b] = queue.shift(); inflight++;
      (async () => {
        let v;
        try {
          const r = await api('GET', `/api/projects/${ctx.pid()}/route?a=${a.lat},${a.lon}&b=${b.lat},${b.lon}`, undefined, false, false);
          v = r.ok && r.pts.length > 1 ? { pts: r.pts, ok: true } : { pts: [ll(a), ll(b)], ok: false, msg: 'тропы нет — прямая' };
        } catch (e) { v = { pts: [ll(a), ll(b)], ok: false, msg: /далеко/.test(e.message) ? 'далеко — поставь точку посередине' : 'тропу не нашёл — прямая' }; }
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
    drawCat();
  }
  function drawCat() {
    if (!P) return; const map = getMap(), z = map.getZoom(), vb = map.getBounds().pad(0.1); P.catLayer.clearLayers();
    if (z < 9) return;
    const used = new Set(P.pts.map(p => p.ref).filter(Boolean));
    let n = 0;
    for (const c of P.cat.values()) {
      if (used.has(c.id) || !vb.contains([c.lat, c.lon])) continue;
      if (++n > 500) break;
      const col = (ctx.gcol()[c.grade] || '#7d8a99'), lab = z >= 13 ? `<span class="cl">${esc(c.name || c.title)}${c.h ? ' ' + c.h : ''}</span>` : '';
      const html = c.kind === 'peak' ? `<div class="catm peak"><i></i>${lab}</div>` : `<div class="catm pass" style="--g:${col}"><b>${esc(c.grade || '')}</b>${lab}</div>`;
      const m = L.marker([c.lat, c.lon], { icon: L.divIcon({ className: 'catw', html, iconSize: [24, 24], iconAnchor: [12, 12] }), keyboard: false, riseOnHover: true }).addTo(P.catLayer);
      m.bindTooltip(`<b>${esc(c.title)}</b>${c.grade ? ' · ' + esc(c.grade) : ''}${c.h ? ' · ' + c.h + ' м' : ''}${c.alt ? `<br><i>${esc(c.alt)}</i>` : ''}${c.note ? `<br>${esc(c.note)}` : ''}<br><small>нажми — провести маршрут через ${c.kind === 'peak' ? 'вершину' : 'перевал'}</small>`, { className: 'tt', direction: 'top', offset: [0, -10] });
      m.on('click', ev => { L.DomEvent.stop(ev); addCat(c); });
    }
  }

  // ---------------------------------------------------------------- карта
  function render() {
    if (!P) return; const map = getMap(); P.layer.clearLayers();
    P.pts.slice(0, -1).forEach((_, i) => {
      const g = legOf(i), line = g.pts.map(q => [q[0], q[1]]);
      L.polyline(line, { color: '#fff', weight: 10, opacity: .9, interactive: false }).addTo(P.layer);
      const pl_ = L.polyline(line, { color: g.ok ? '#E2001A' : '#B4561A', weight: 5, dashArray: g.mode === 'line' || !g.ok ? '10 7' : null, opacity: g.busy ? .55 : 1 }).addTo(P.layer);
      pl_.bindTooltip(`Отрезок ${i + 1}–${i + 2} · ${g.mode === 'line' ? 'напрямую' : g.ok ? 'по тропе' : (g.msg || 'тропы нет — прямая')}<br><small>нажми — вставить точку здесь</small>`, { sticky: true, className: 'tt' });
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
    return `<div class="hhead"><h2>Маршрут</h2><span class="sp"></span><button class="icon" data-pl="undo" title="Отменить (Ctrl+Z)" ${P.undo.length ? '' : 'disabled'}>↶</button><button class="icon" data-pl="redo" title="Вернуть (Ctrl+Y)" ${P.redo.length ? '' : 'disabled'}>↷</button></div>
      <div class="plbar"><button class="btn sm" data-pl="cancel">Отмена</button><button class="btn go" data-pl="done" ${n < 2 ? 'disabled' : ''}>Готово — в поход</button></div>
      <div class="field"><span>Между точками</span><div class="seg sm even"><button class="${P.def === 'trail' ? 'on' : ''}" data-pl="def" data-v="trail">По тропам</button><button class="${P.def === 'line' ? 'on' : ''}" data-pl="def" data-v="line">Напрямую</button></div>
        <div class="hint">${P.def === 'trail' ? 'Путь между точками прокладывается по тропам OpenStreetMap. Где тропы нет (ледник, перевал без тропы) — переключи отрезок на «прямо».' : 'Точки соединяются прямыми — для бездорожья. Любой отрезок можно отдельно пустить по тропе.'} Это — для новых точек; у каждого отрезка свой переключатель ниже.</div></div>
      <form class="plfind" data-plfind><input type="search" name="q" placeholder="Куда идём? Село, вершина, перевал…" autocomplete="off" value="${esc(P.findQ || '')}"><button class="btn sm">Найти</button></form>
      ${P.found ? `<div class="plfound">${P.found.length ? P.found.map((f, i) => `<button data-pl="goto" data-i="${i}">${esc(f.name)}</button>`).join('') : '<div class="hint">Ничего не нашлось — попробуй иначе или найди на карте.</div>'}</div>` : ''}
      <div class="edstep plstep"><span>${step}</span></div>
      ${P.fromDraft ? '<div class="hint">Это черновик из браузера — в поход он попадёт по «Готово». <a href="#" data-pl="drop">Отбросить черновик</a></div>' : ''}
      ${other ? '<div class="banner"><div>В походе уже есть загруженный трек — нарисованный маршрут добавится к нему как основная нитка. Если это вариант, отметь его в «Днях».</div></div>' : ''}
      ${n > 1 ? `<div class="pltot">${pr ? `<b>${kmTxt(pr.all.km * 1000)} км</b><span>+${pr.all.up} м</span><span>−${pr.all.dn} м</span>` : ''}<span>${pr && pr.days ? `${pr.days.length} ${pl(pr.days.length, 'день', 'дня', 'дней')}` : ''}</span>${busy ? `<span class="hint">ищу тропы: ${busy}</span>` : ''}${bad ? `<span class="warnx">без тропы: ${bad}</span>` : ''}</div>${profSvg()}` : ''}
      ${pr && pr.days && pr.days.length > 1 ? `<div class="pldays">${pr.days.map((d, i) => `<div><b>День ${i + 1}</b><span>${kmTxt(d.km * 1000)} км</span><span>+${d.up}</span><span>−${d.dn}</span></div>`).join('')}</div>` : ''}
      <div class="plist">${P.pts.map((p, i) => `
        <div class="plp${P.sel === i ? ' sel' : ''}" data-i="${i}"><span class="plk mini ${p.kind}${i === 0 ? ' start' : i === n - 1 && i > 0 ? ' finish' : ''}">${p.kind === 'via' ? i + 1 : sym(p.kind)}</span>
          <input class="pname" data-plf="name" data-i="${i}" value="${esc(p.name || '')}" placeholder="${i === 0 ? 'старт' : i === n - 1 ? 'финиш' : KIND[p.kind]}" maxlength="80">
          <select data-plf="kind" data-i="${i}" aria-label="Что это">${Object.entries(KIND).map(([k, t]) => `<option value="${k}"${p.kind === k ? ' selected' : ''}>${t}</option>`).join('')}</select>
          <span class="hint plh">${p.grade ? esc(p.grade) + ' · ' : ''}${p.h || hAt(i) ? (p.h || hAt(i)) + ' м' : ''}</span>
          <button class="icon-btn" data-pl="del" data-i="${i}" title="Убрать точку">×</button></div>
        ${i < n - 1 ? `<div class="pll"><span class="seg sm"><button class="${legs[i].mode === 'trail' ? 'on' : ''}" data-pl="lmode" data-i="${i}" data-v="trail" title="По тропам OpenStreetMap">тропа</button><button class="${legs[i].mode === 'line' ? 'on' : ''}" data-pl="lmode" data-i="${i}" data-v="line" title="Прямой линией">прямо</button></span>
          <span>${st(i)}</span>${legs[i].busy ? '<span class="hint">ищу тропу…</span>' : !legs[i].ok ? `<span class="warnx">${esc(legs[i].msg || 'тропы нет — прямая')}</span>` : ''}</div>` : ''}`).join('')}</div>
      ${n ? '<div class="hint">Правый клик по точке на карте — убрать её. Ctrl+Z — отменить.</div>' : ''}`;
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
    if (!P) return; const el = e.target.closest('[data-plf]'); if (!el) return;
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
