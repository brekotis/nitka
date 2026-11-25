// Нитка — интерфейс. Без сборки: один модуль, Leaflet подключён отдельно.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const app = $('#app');
const S = { user: null, cfg: {}, types: [], grades: [], obj: [], projects: [], pid: null, proj: null, build: null, step: 'tracks', selPiece: null, selPoint: null,
  filter: 'all', folder: '', q: '', mode: null, ptab: 'points', selBand: null, bandA: null, bands: [], gcol: {}, newType: null, base: localStorage.nitkaBase || 'otm', lastInfo: null, busy: false };
const STEPS = [['tracks', 'Треки'], ['days', 'Дни'], ['points', 'Точки'], ['maps', 'Карты']];
const KINDS = ['пеший', 'горный', 'лыжный', 'водный', 'вело', 'другое'];
const RED = '#E2001A', VIOLET = '#7A1AAA';
const km = (v, n = 1) => (Math.round(v * 10 ** n) / 10 ** n).toFixed(n).replace('.', ',');
const plural = (n, a, b, c) => { n = Math.abs(n); const x = n % 10, y = n % 100; return x === 1 && y !== 11 ? a : (x >= 2 && x <= 4 && !(y >= 12 && y <= 14) ? b : c); };

// ---------------------------------------------------------------- сеть
const LOADS = []; let loadT = 0, lastBtn = null, lastBtnAt = 0;
function loadPaint() {
  const el = $('#load'); if (!el) return;
  if (!LOADS.length) { clearTimeout(loadT); loadT = 0; el.hidden = true; $$('.loading').forEach(b => b.classList.remove('loading')); return; }
  const msg = [...LOADS].reverse().find(x => x.msg); $('span', el).textContent = (msg && msg.msg) || 'Секунду…';
  if (el.hidden && !loadT) loadT = setTimeout(() => { loadT = 0; if (LOADS.length) el.hidden = false; }, 220);      // быстрые запросы индикатор не дёргают
}
function loadOn(msg) {
  const t = { msg }; LOADS.push(t);
  if (lastBtn && Date.now() - lastBtnAt < 600 && lastBtn.isConnected) lastBtn.classList.add('loading');      // кнопка, которую только что нажали, показывает, что работает
  loadPaint(); return () => { const i = LOADS.indexOf(t); if (i >= 0) LOADS.splice(i, 1); loadPaint(); };
}
async function api(method, url, body, raw, msg) {
  const o = { method, headers: {}, credentials: 'same-origin' };
  if (raw) o.body = body; else if (body !== undefined) { o.body = JSON.stringify(body); o.headers['Content-Type'] = 'application/json'; }
  const off = msg === false ? null : loadOn(msg);
  try {
    let r;
    try { r = await fetch(url, o); } catch (e) { throw new Error('Нет связи с сервером. Проверь интернет и попробуй ещё раз.'); }
    let d = {};
    try { d = await r.json(); } catch (e) { /* пусто */ }
    if (r.status === 401 && S.user) { S.user = null; go('#/'); render(); }
    if (!r.ok) throw new Error(d.error || `Ошибка ${r.status}`);
    return d;
  } finally { off && off(); }
}
function toast(msg, err) {
  const t = document.createElement('div'); t.className = 'toast' + (err ? ' err' : ''); t.textContent = msg; $('#toasts').append(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, err ? 6500 : 3600);
}
const fail = e => toast(e.message || String(e), true);

// ---------------------------------------------------------------- значки (повторяют условные знаки на картах)
const I = {
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  dl: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" width="20" height="20"><path d="M12 4v11M7 11l5 5 5-5M5 20h14"/></svg>',
  dots: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="2.2"/><circle cx="12" cy="12" r="2.2"/><circle cx="19" cy="12" r="2.2"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5l-7 7 7 7"/></svg>',
  eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>',
  eyeoff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 4l16 16M9.9 5.3A9.6 9.6 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.2 6.7C3.5 8.600 2 12 2 12s3.600 7 10 7c1.500 0 2.900-.4 4-1"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 14l6-6 6 6"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 10l6 6 6-6"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/></svg>',
  upload: '<svg viewBox="0 0 64 64" fill="none"><path d="M6 50c8-14 14-4 22-14s10-22 30-18" stroke="#E2001A" stroke-width="4" stroke-linecap="round"/><path d="M28 36l5 9h-10z" fill="#FEBC0E" stroke="#133C6A" stroke-width="2" stroke-linejoin="round"/><circle cx="6" cy="50" r="4.500" fill="#1E8C4B" stroke="#fff" stroke-width="1.500"/><path d="M52 12h8v8h-8z" fill="#133C6A"/><path d="M52 12h4v4h-4zM56 16h4v4h-4z" fill="#fff"/></svg>',
};
const letterSym = (t, c) => `<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" fill="${c || '#555'}" stroke="#fff" stroke-width="1.5"/><text x="12" y="16.3" text-anchor="middle" font-family="Sofia Sans Condensed,Arial Narrow,sans-serif" font-weight="800" font-size="${String(t).length > 1 ? 10 : 12.5}" fill="#fff">${esc(t)}</text></svg>`;
function sym(type) {
  const t = typeOf(type);
  switch (t.sym) {
    case 'camp': return '<svg viewBox="0 0 24 24"><path d="M12 3 21.500 20h-19Z" fill="#FFD200" stroke="#191919" stroke-width="1.700" stroke-linejoin="round"/><path d="M12 11v9" stroke="#191919" stroke-width="1.700"/></svg>';
    case 'plan': return '<svg viewBox="0 0 24 24"><path d="M12 4 20.500 19.500h-17Z" fill="#fff" stroke="#005AC8" stroke-width="2.200" stroke-linejoin="round"/></svg>';
    case 'pass': return '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" fill="#fff" opacity=".85"/><path d="M6 5a9.500 9.500 0 0 1 0 14M18 5a9.500 9.500 0 0 0 0 14" fill="none" stroke="#962800" stroke-width="2.400" stroke-linecap="round"/></svg>';
    case 'peak': return '<svg viewBox="0 0 24 24"><path d="M12 4 21 19.500H3Z" fill="#78401A" stroke="#fff" stroke-width="1.500" stroke-linejoin="round"/></svg>';
    case 'obst': return `<svg viewBox="0 0 24 24"><path d="M12 1.500 22.500 12 12 22.500 1.500 12Z" fill="#C80000" stroke="#fff" stroke-width="1.500"/><text x="12" y="16.500" text-anchor="middle" font-family="Arial,sans-serif" font-weight="800" font-size="12.500" fill="#fff">${esc(t.text || '!')}</text></svg>`;
    case 'prizhim': return '<svg viewBox="0 0 24 24"><path d="M2 16l5-8 5 8 5-8 5 8" fill="none" stroke="#fff" stroke-width="5.500" stroke-linejoin="round" stroke-linecap="round"/><path d="M2 16l5-8 5 8 5-8 5 8" fill="none" stroke="#C80000" stroke-width="2.400" stroke-linejoin="round" stroke-linecap="round"/></svg>';
    case 'water': return '<svg viewBox="0 0 24 24"><path d="M12 2.500c4 5.500 6.500 8.500 6.500 12a6.500 6.500 0 0 1-13 0c0-3.500 2.500-6.500 6.500-12Z" fill="#0078D7" stroke="#fff" stroke-width="1.500"/></svg>';
    case 'exit': return `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.500" fill="#fff" stroke="#C80000" stroke-width="2.600"/><text x="12" y="16.300" text-anchor="middle" font-family="Arial,sans-serif" font-weight="800" font-size="12" fill="#C80000">А</text></svg>`;
    case 'town': return type === 'mark' ? '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5" fill="#fff" stroke="#56677D" stroke-width="2.400"/></svg>' : '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="6.500" fill="#fff" stroke="#191919" stroke-width="2.600"/></svg>';
    case 'photo': return '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5.500" fill="#0046A0" stroke="#fff" stroke-width="2"/></svg>';
    case 'letter': return letterSym(t.text || '•', t.color);
    default: return '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" fill="none" stroke="#9AA7B6" stroke-width="2"/><path d="M6.500 17.500l11-11" stroke="#9AA7B6" stroke-width="2"/></svg>';
  }
}
function allTypes() {
  return [...S.types, ...((S.proj && S.proj.custom_types) || []).map(c => ({ id: c.id, title: c.title, sym: 'letter', text: c.letter, color: c.color, group: 'Свои', custom: true }))];
}
const typeOf = id => allTypes().find(t => t.id === id) || { id, title: id, sym: null, group: '—' };

// ---------------------------------------------------------------- навигация
function go(h) { if (location.hash !== h) location.hash = h; else route(); }
window.addEventListener('hashchange', route);
async function route() {
  const m = location.hash.match(/^#\/p\/([0-9a-f]{12})(?:\/(\w+))?/);
  if (!S.user) return render();
  if (needOrigin() && !S.proj) return renderOrigin();      // только на входе, не посреди работы в редакторе
  if (m) {
    const step = STEPS.some(s => s[0] === m[2]) ? m[2] : 'tracks';
    if (S.pid !== m[1]) { await openProject(m[1], step); return; }
    if (S.step !== step) { S.step = step; setMode(null); paintEditor(); }
    return;
  }
  await flush();
  S.pid = null; S.proj = null; stopPoll(); destroyMap();
  if (location.hash.startsWith('#/admin') && S.user.admin) return renderAdmin();
  await loadProjects(); render();
}
// ---------------------------------------------------------------- «Кто пользуется» — только для администратора, под отдельным кодом
async function renderAdmin() {
  destroyAtlas();
  document.title = 'Нитка — кто пользуется';
  const bar = topBar(`<span class="sp"></span><button class="btn ghost sm" data-a="home">← Мои походы</button>`);
  let st; try { st = await api('GET', '/api/admin/state', undefined, false, 'Открываю список…'); } catch (e) { fail(e); return go('#/'); }
  if (!st.unlocked) {
    app.innerHTML = bar + `<main class="home origin"><div class="home-head"><div><h1>${st.code_set ? 'Код администратора' : 'Придумай код администратора'}</h1>
      <p>${st.code_set ? 'Список пользователей открывается только по коду — на этом устройстве он спросится один раз.' : 'Вход на сайт — по имени, а имя brekotis известно всем. Чтобы список пользователей не открыл кто угодно, он закрыт отдельным кодом. Придумай его сейчас — хотя бы четыре символа.'}</p></div></div>
      <form id="admf" class="sec" style="max-width:420px"><label class="field"><span>Код</span><input name="code" type="password" autocomplete="${st.code_set ? 'current-password' : 'new-password'}" maxlength="80" ${isPhone() ? '' : 'autofocus'}></label>
      <div><button class="btn go big">${st.code_set ? 'Открыть' : 'Запомнить код'} →</button></div></form></main>`;
    $('#admf').onsubmit = async e => { e.preventDefault(); try { await api('POST', '/api/admin/code', { code: e.target.code.value }); renderAdmin(); } catch (err) { fail(err); e.target.code.select(); } };
    return;
  }
  let users; try { users = (await api('GET', '/api/admin/users', undefined, false, 'Загружаю список…')).users; } catch (e) { fail(e); return go('#/'); }
  const dt = t => t ? new Date(t * 1000).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
  const by = {}; users.forEach(u => { const k = u.origin || 'не указано'; by[k] = (by[k] || 0) + 1; });
  app.innerHTML = bar + `<main class="home"><div class="home-head"><div><h1>Кто пользуется</h1>
    <p>${users.length} ${plural(users.length, 'человек', 'человека', 'человек')} · ${users.reduce((s, u) => s + u.projects.length, 0)} походов. Новые — сверху.</p></div></div>
    <div class="chips" style="margin-bottom:16px">${Object.entries(by).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span class="gsum" style="--g:var(--navy)"><b>${n}</b> ${esc(k)}</span>`).join('')}</div>
    <div class="tblwrap"><table class="sum adm"><thead><tr><th>Кто</th><th>Откуда</th><th>Пришёл</th><th>Заходил</th><th>Походы</th></tr></thead><tbody>
    ${users.map(u => `<tr><td><b>${esc(u.name)}</b>${u.by === 'телефон' ? ' <span class="tag">телефон</span>' : ''}</td><td>${u.origin ? esc(u.origin) : '<span class="hint">не указано</span>'}</td><td class="n">${dt(u.created)}</td><td class="n">${dt(u.seen)}</td>
      <td>${u.projects.length ? u.projects.map(p => `<div>«${esc(p.title)}»${p.ndays ? ` — ${p.ndays} дн., ${km(p.km)} км` : ' — <span class="hint">без трека</span>'}${p.built ? ` <span class="tag ok">${p.built} ${plural(p.built, 'лист', 'листа', 'листов')}</span>` : ''}</div>`).join('') : '<span class="hint">пока нет</span>'}</td></tr>`).join('')}
    </tbody></table></div></main>`;
}
async function boot() {
  try {
    const [me, ty] = await Promise.all([api('GET', '/api/me', undefined, false, false), api('GET', '/api/types', undefined, false, false)]);
    S.user = me.user; S.cfg = me; S.types = ty.types; S.grades = ty.grades; S.obj = ty.obj; S.bands = ty.bands || []; S.gcol = ty.grade_col || {};
  } catch (e) { fail(e); }
  route();
}
function render() {
  setTimeout(updMore, 0);
  if (!S.user) return renderLogin();
  if (S.proj) return; // редактор рисуется сам
  renderHome();
}

// ---------------------------------------------------------------- вход
// ---- сцена на экране входа: время суток по часам посетителя, слои едут за курсором, турист отвечает на клик
const TODS = ['dawn', 'day', 'dusk', 'night'];
const todNow = () => { const h = new Date().getHours(); return h >= 5 && h < 8 ? 'dawn' : h >= 8 && h < 18 ? 'day' : h >= 18 && h < 22 ? 'dusk' : 'night'; };
const PHRASES = ['Ещё чуть-чуть — и перевал!', 'Скоро привал. Честно.', 'Там, за поворотом, уже видно.', '2Б или не 2Б — вот в чём вопрос.', 'Кто нёс верёвку?', 'Нитку не теряем!', 'А карты кто-нибудь распечатал?', 'Рюкзак сам себя не понесёт.', 'Здесь должна была быть тропа…', 'Дежурные, подъём!'];
// Турист говорит сам (вскоре после появления, потом раз в 9–14 секунд) и по клику — тогда ещё и подпрыгивает
function talker(h, b, phrases, first = 2600) {
  if (!h || !b || !phrases.length) return;
  let n = Math.floor(Math.random() * phrases.length), hideT = 0, autoT = 0;
  const say = hop => {
    if (!h.isConnected) return clearTimeout(autoT);
    b.textContent = phrases[n++ % phrases.length]; b.hidden = false; b.classList.remove('in'); void b.offsetWidth; b.classList.add('in');
    if (hop) { h.classList.remove('hop'); void h.offsetWidth; h.classList.add('hop'); }
    clearTimeout(hideT); hideT = setTimeout(() => { b.hidden = true; }, 4800);
    clearTimeout(autoT); autoT = setTimeout(() => say(false), 9000 + Math.random() * 5000);
  };
  h.onclick = () => say(true);
  autoT = setTimeout(() => say(false), first);
}
function sceneLive() {
  const sc = $('#scene'); if (!sc) return;
  const layers = $$('[data-d]', sc); let tx = 0, ty = 0, cx = 0, cy = 0, raf = 0;
  const tick = () => {
    cx += (tx - cx) * 0.08; cy += (ty - cy) * 0.08;
    layers.forEach(l => { const d = +l.dataset.d; l.style.transform = `translate(${(-cx * d * 0.5).toFixed(2)}px, ${(-cy * d * 0.28).toFixed(2)}px)`; });
    raf = Math.abs(tx - cx) + Math.abs(ty - cy) > 0.002 ? requestAnimationFrame(tick) : 0;
  };
  const move = e => { if (!sc.isConnected) return window.removeEventListener('pointermove', move); tx = e.clientX / innerWidth * 2 - 1; ty = e.clientY / innerHeight * 2 - 1; if (!raf) raf = requestAnimationFrame(tick); };
  if (matchMedia('(hover: hover) and (prefers-reduced-motion: no-preference)').matches) window.addEventListener('pointermove', move);
  $('#tod').onclick = () => { const cur = TODS.find(t => sc.classList.contains('t-' + t)); sc.classList.remove('t-' + cur); sc.classList.add('t-' + TODS[(TODS.indexOf(cur) + 1) % 4]); };
  const orb = $('.orb', sc); if (orb) orb.addEventListener('click', () => $('#tod').click());
  talker($('#hiker'), $('#bubble'), PHRASES);
}
function renderLogin() {
  document.title = 'Нитка — карты похода';
  app.innerHTML = `<div class="login">
    <div class="scene t-${todNow()}" id="scene">
      <svg viewBox="0 0 800 900" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
        <rect class="c-sky" x="-60" y="-60" width="920" height="1020"/>
        <g data-d="3" class="stars">${Array.from({ length: 46 }, (_, i) => `<circle cx="${(i * 173 + 31) % 800}" cy="${(i * 97 + 13) % 470}" r="${1.2 + (i % 3) * 0.7}" style="animation-delay:${(i % 7) * 0.4}s"/>`).join('')}</g>
        <g data-d="7"><g class="orbw"><circle class="orb" cx="610" cy="190" r="78"/><g class="craters"><circle cx="585" cy="170" r="13"/><circle cx="632" cy="212" r="9"/><circle cx="640" cy="160" r="6"/></g></g></g>
        <g data-d="10"><g class="cloud"><ellipse cx="80" cy="150" rx="70" ry="22"/><ellipse cx="130" cy="132" rx="46" ry="24"/><ellipse cx="40" cy="138" rx="34" ry="16"/></g>
          <g class="cloud" style="animation-duration:95s;animation-delay:-40s;opacity:.8"><ellipse cx="30" cy="290" rx="58" ry="16"/><ellipse cx="72" cy="276" rx="36" ry="17"/></g>
          <g class="birds"><path d="M0 0q7-9 14 0q7-9 14 0"/><path d="M34 16q6-8 12 0q6-8 12 0"/><path d="M-22 22q5-7 10 0q5-7 10 0"/></g></g>
        <g data-d="15"><path class="c-far" d="M-60 560C60 500 110 380 190 380s110 110 180 70 90-170 170-170 120 160 180 190 80-40 140-30V960H-60Z"/>
          <path class="c-cap" d="M512 296c12-10 34-18 50-6 22 16 30 48 42 70-22-14-40 4-58-6s-26-30-34-58Z"/></g>
        <g data-d="25"><path class="c-mid" d="M-60 690C50 640 120 500 220 500s120 120 190 90 110-130 190-130 150 150 260 170V960H-60Z"/>
          <path class="c-cap" d="M188 506c16-8 42-10 60 0 24 14 34 40 48 60-26-12-44 6-64-4s-30-32-44-56Z"/></g>
        <g data-d="38"><path class="c-near" d="M-60 800C80 760 150 640 260 640s150 110 240 100 130-110 210-110 90 60 150 70V960H-60Z"/>
          <path class="thread" d="M300 846C370 800 250 720 262 648c60-6 110 90 236 92 50-36 76-100 124-108" fill="none" stroke="#fff" stroke-width="13" stroke-linecap="round"/>
          <path class="thread" d="M300 846C370 800 250 720 262 648c60-6 110 90 236 92 50-36 76-100 124-108" fill="none" stroke="#E2001A" stroke-width="6" stroke-linecap="round"/>
          <g class="pop" style="animation-delay:1.5s"><path class="tent" d="M262 620l17 28h-34Z"/><path class="fire" d="M288 648q5-13 0-20q11 7 9 20Z"/></g>
          <g class="pop" style="animation-delay:2.3s"><path class="tent" d="M498 712l17 28h-34Z"/><path class="fire" d="M524 740q5-13 0-20q11 7 9 20Z"/></g>
          <g class="pop" style="animation-delay:3.3s"><path d="M622 634v-62" stroke="#133C6A" stroke-width="5" stroke-linecap="round"/><path class="flag" d="M624 572h46l-12 15 12 15h-46Z"/></g></g>
        <g data-d="54"><path class="c-snow" d="M-60 850c150-40 320-20 480-34s260-34 440-20V960H-60Z"/></g>
      </svg>
      <button type="button" class="orbhit" id="tod" title="Сменить время суток" aria-label="Сменить время суток"></button>
      <button type="button" class="hiker" id="hiker" aria-label="Турист"><img src="/static/img/logo.png" alt="Турклуб «Восхождение»"></button>
      <div class="bubble" id="bubble" hidden></div>
    </div>
    <div class="login-side">
      <div class="club">турклуб «Восхождение»</div>
      <h1>Нит<i>ка</i></h1>
      <p class="lead">Карты и высотные профили для отчёта о походе — из твоего трека, за пару минут.</p>
      <form id="login">
        <label class="field"><span>Как тебя зовут?</span><input name="name" type="text" placeholder="имя, ник или телефон" autocomplete="username" ${isPhone() ? "" : "autofocus"} maxlength="40" value="${esc(localStorage.nitkaName || '')}"></label>
        ${S.cfg.need_code ? '<label class="field"><span>Кодовое слово клуба</span><input name="code" type="text" autocomplete="off"></label>' : ''}
        <button class="btn go big" type="submit">Войти →</button>
        <div class="fine">Паролей нет. По имени мы находим твои походы, поэтому вводи его всегда одинаково.<br>Забыл, как вводил? Напиши <a href="https://t.me/brekotis" target="_blank" rel="noopener">@brekotis</a> в Телеграме — подскажет.</div>
      </form>
      <ol class="how">
        <li>Загрузи GPX, KML или ссылку nakarte.me — хоть одним файлом, хоть по дням.</li>
        <li>Проверь дни, ночёвки, перевалы и другие точки — их можно править на карте.</li>
        <li>Забери обзорную карту, карты дней и профили в PNG, JPG или PDF.</li>
      </ol>
    </div></div>`;
  sceneLive();
  $('#login').onsubmit = async e => {
    e.preventDefault(); const f = e.target; const name = f.name.value.trim();
    try {
      const d = await api('POST', '/api/login', { name, code: f.code ? f.code.value : '' }, false, 'Вхожу…');
      localStorage.nitkaName = name; S.user = d.user; S.isNew = d.new;
      toast(d.new ? `Приятно познакомиться, ${d.user.name}!` : `С возвращением, ${d.user.name}!`);
      route();
    } catch (err) { fail(err); f.name.focus(); }
  };
}
// Один вопрос после первого входа: откуда человек (турклуб, город…). Тем, кто пришёл раньше, — тот же вопрос, но можно отложить.
const needOrigin = () => S.user && !S.user.origin && !S.originLater;
function renderOrigin() {
  document.title = 'Нитка — откуда ты?';
  const quick = ['Турклуб «Восхождение»', 'Другой турклуб', 'Сам по себе'];
  app.innerHTML = topBar(`<span class="sp"></span><span class="who">${esc(S.user.name)}</span>`) + `
    <main class="home origin"><div class="home-head"><div><h1>Откуда ты, ${esc(S.user.name)}?</h1>
      <p>Турклуб, секция, город, компания друзей — что угодно. Просто чтобы понимать, кто пользуется «Ниткой».</p></div></div>
      <form id="originf" class="sec" style="max-width:520px">
        <div class="chips">${quick.map(q => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>
        <label class="field"><span>Откуда</span><input name="origin" type="text" maxlength="80" placeholder="например: турклуб «Восхождение», Москва" ${isPhone() ? '' : 'autofocus'}></label>
        <div class="acts" style="display:flex;gap:10px;align-items:center"><button class="btn go big">Готово →</button>${S.isNew ? '' : '<button type="button" class="btn ghost" data-later>Не сейчас</button>'}</div>
      </form></main>`;
  const f = $('#originf');
  $$('.chip', f).forEach(c => c.onclick = () => { const v = c.dataset.q; f.origin.value = v === 'Другой турклуб' ? 'Турклуб ' : v; f.origin.focus(); if (v !== 'Другой турклуб') f.requestSubmit(); });
  const later = $('[data-later]', f); if (later) later.onclick = () => { S.originLater = true; route(); };
  f.onsubmit = async e => {
    e.preventDefault();
    try { const d = await api('POST', '/api/profile', { origin: f.origin.value }); S.user.origin = d.origin; toast('Спасибо!'); route(); } catch (err) { fail(err); f.origin.focus(); }
  };
}

// ---------------------------------------------------------------- мои походы
async function loadProjects() { try { S.projects = (await api('GET', '/api/projects', undefined, false, 'Загружаю походы…')).projects; } catch (e) { fail(e); } }
function topBar(inner = '') {
  return `<header class="top"><a class="brand" data-a="home" title="Мои походы"><span class="badge"><img src="/static/img/logo-s.png" alt=""></span><span>Нитка</span></a>${inner}</header>`;
}
function fmtDates(c) {
  if (!c.start || !c.last) return '';
  const a = new Date(c.start + 'T00:00'), b = new Date(a); b.setDate(a.getDate() + c.last - 1);
  const f = d => String(d.getDate()).padStart(2, '0') + '.' + String(d.getMonth() + 1).padStart(2, '0');
  return `${f(a)}–${f(b)}.${b.getFullYear()}`;
}
function thumbSvg(c) {
  if (!c.thumb || !c.thumb.length) return '<div class="empty">трека пока нет</div>';
  const d = l => 'M' + l.map(p => p.join(' ')).join('L');
  const n = c.thumb.reduce((a, l) => a + l.length, 0);
  return `<svg viewBox="0 0 200 100" preserveAspectRatio="xMidYMid meet">${c.thumb.map(l => `<path class="under" d="${d(l)}"/>`).join('')}${c.thumb.map((l, i) => `<path pathLength="1" class="${i % 2 ? 'b' : ''}" d="${d(l)}"/>`).join('')}
    <circle class="walker" r="3.6"><animateMotion dur="${Math.min(9, Math.max(3.5, n / 22)).toFixed(1)}s" repeatCount="indefinite" begin="indefinite" path="${c.thumb.map(d).join('')}"/></circle></svg>`;
}
function renderHome() {
  document.title = 'Нитка — мои походы';
  const P = S.projects;
  app.innerHTML = topBar(`<span class="sp"></span>${S.user.admin ? '<button class="btn ghost sm" data-a="admin">Кто пользуется</button>' : ''}<span class="who">${esc(S.user.name)}</span><button class="btn ghost sm" data-a="logout">Выйти</button>`) + `
  <main class="home">
    <div class="home-head"><div><h1>${P.length ? 'Мои походы' : `Привет, ${esc(S.user.name)}!`}</h1>
      <p>${P.length ? 'Сами карты хранятся ' + S.cfg.keep_days + ' дней, а поход со всеми настройками — всегда: пересоберёшь в один клик.' : 'Здесь будут твои походы. Начни с первого — понадобится трек: GPX, KML или ссылка nakarte.me.'}</p></div></div>
    ${P.some(p => p.card && p.card.geo && p.card.geo.length) ? '<div class="atlas"><div id="atlas"></div><span class="atlas-cap">Где ты ходил · наведи на поход или нажми на нитку</span></div>' : ''}
    <div class="grid">
      <button class="card new" data-a="new"><div><span class="plus">+</span>Новый поход</div></button>
      ${P.map((p, i) => { const c = p.card || {}; const b = p.build || {};
        const left = b.expires ? Math.max(0, Math.ceil((b.expires * 1000 - Date.now()) / 864e5)) : 0;
        const st = b.state === 'done' && b.n ? `<span class="tag ok">карты готовы · ещё ${left} ${plural(left, 'день', 'дня', 'дней')}</span>` : (b.state === 'running' || b.state === 'queued' ? '<span class="tag sun">собирается…</span>' : (c.pieces ? '<span class="tag">черновик</span>' : '<span class="tag warn">нет трека</span>'));
        return `<div class="card" style="animation-delay:${Math.min(i, 8) * 45}ms" data-a="open" data-id="${p.id}" tabindex="0" role="button">
          <div class="shape">${thumbSvg(c)}</div>
          <button class="icon-btn more" data-a="menu" data-id="${p.id}" title="Ещё">${I.dots}</button>
          <div class="info"><h3>${esc(p.title)}</h3>
            <div class="meta">${[c.area, c.kind, c.ndays ? `${c.ndays} ${plural(c.ndays, 'день', 'дня', 'дней')}` : '', c.km ? `${km(c.km)} км` : '', fmtDates(c)].filter(Boolean).map(esc).map(x => `<span>${x}</span>`).join('')}</div>
            <div>${st}</div></div></div>`; }).join('')}
    </div></main>
  <button type="button" class="mascot" id="mascot" aria-label="Турист"><img src="/static/img/logo.png" alt=""></button><div class="bubble mbubble" id="mbubble" hidden></div>`;
  homeLive();
  const withTrack = P.filter(p => p.card && p.card.km), total = withTrack.reduce((a, p) => a + p.card.km, 0), ready = P.filter(p => p.build && p.build.state === 'done' && p.build.n);
  const lines = !P.length ? ['Начнём? Жми «Новый поход».', 'Нужен только трек: GPX, KML или ссылка nakarte.', 'Первый поход — самый памятный.']
    : [`${P.length} ${plural(P.length, 'поход', 'похода', 'походов')}${total ? ` и ${km(total, 0)} км на всех` : ''}. Неплохо!`,
       ...withTrack.slice(0, 4).map(p => `«${p.title}» — ${km(p.card.km, 0)} км${p.card.ndays ? ` за ${p.card.ndays} ${plural(p.card.ndays, 'день', 'дня', 'дней')}` : ''}. Помнишь?`),
       ...(ready.length ? [`Карты «${ready[0].title}» готовы — забирай, пока лежат.`] : ['Собрать карты — пара минут. Попробуй.']),
       matchMedia('(hover: hover)').matches ? 'Наведи на поход — покажу его на карте.' : 'Нажми на нитку на карте — открою поход.',
       'Куда дальше пойдём?', ...PHRASES.slice(0, 5)];
  talker($('#mascot'), $('#mbubble'), lines, 1800);
}
// ---- главная вживую: карта со всеми нитками, карточки наклоняются за курсором, по нитке идёт «турист»
let atlas = null;
function destroyAtlas() { if (atlas) { atlas.remove(); atlas = null; } }
function homeLive() {
  const lines = {};
  const cardOf = id => $(`.card[data-id="${id}"]`);
  const hot = (id, on, fly) => {
    const c = cardOf(id); c && c.classList.toggle('hot', on);
    const l = lines[id]; if (!l || !atlas) return;
    l.top.forEach(x => x.setStyle({ weight: on ? 6 : 3.5 })); if (on) l.grp.bringToFront();
    if (on && fly) atlas.flyToBounds(l.bounds.pad(0.4), { duration: 0.7, maxZoom: 12 });
  };
  $$('.card[data-id]').forEach(c => {
    const am = $('animateMotion', c), w = $('.walker', c), id = c.dataset.id;
    c.addEventListener('pointerenter', e => { if (e.pointerType === 'touch') return; if (am) { try { am.beginElement(); } catch (err) { /* старый браузер */ } w.style.opacity = 1; } hot(id, true, true); });
    c.addEventListener('pointerleave', () => { c.style.setProperty('--rx', '0deg'); c.style.setProperty('--ry', '0deg'); if (am) { try { am.endElement(); } catch (err) { /* ок */ } w.style.opacity = 0; } hot(id, false); });
    c.addEventListener('pointermove', e => { if (e.pointerType === 'touch') return; const r = c.getBoundingClientRect(); c.style.setProperty('--ry', (((e.clientX - r.left) / r.width - 0.5) * 8).toFixed(2) + 'deg'); c.style.setProperty('--rx', ((0.5 - (e.clientY - r.top) / r.height) * 8).toFixed(2) + 'deg'); });
  });
  destroyAtlas();
  const el = $('#atlas'); if (!el) return;
  atlas = L.map(el, { zoomControl: false, attributionControl: false, scrollWheelZoom: false, zoomSnap: 0.25 });
  L.control.zoom({ position: 'bottomright' }).addTo(atlas); tiles('otm').addTo(atlas);
  let all = null;
  S.projects.forEach((p, i) => {
    const g = p.card && p.card.geo, b = p.card && p.card.bbox; if (!g || !g.length || !b) return;
    const grp = L.featureGroup().addTo(atlas); const top = [];
    g.forEach(l => { L.polyline(l, { color: '#fff', weight: 8, opacity: 0.9 }).addTo(grp); top.push(L.polyline(l, { color: i % 2 ? VIOLET : RED, weight: 3.5 }).addTo(grp)); });
    L.marker([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2], { icon: L.divIcon({ className: '', html: `<div class="apin">${esc(p.title)}</div>`, iconSize: [0, 0] }), keyboard: false }).addTo(grp);
    grp.on('click', () => go(`#/p/${p.id}/tracks`)); grp.on('mouseover', () => hot(p.id, true)); grp.on('mouseout', () => hot(p.id, false));
    const bounds = L.latLngBounds([b[0], b[1]], [b[2], b[3]]); lines[p.id] = { grp, top, bounds };
    all = all ? all.extend(bounds) : L.latLngBounds(bounds.getSouthWest(), bounds.getNorthEast());
  });
  const fitAll = fly => all && (fly ? atlas.flyToBounds(all.pad(0.3), { duration: 0.8, maxZoom: 11 }) : atlas.fitBounds(all.pad(0.3), { maxZoom: 11, animate: false }));
  fitAll(false);
  const grid = $('.grid'); grid && grid.addEventListener('pointerleave', () => fitAll(true));
}
function modal(html, onMount) {
  const v = document.createElement('div'); v.className = 'veil'; v.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  const close = () => { v.remove(); document.removeEventListener('keydown', esc_); };
  const esc_ = e => { if (e.key === 'Escape') close(); };
  v.addEventListener('mousedown', e => { if (e.target === v) close(); });
  document.addEventListener('keydown', esc_); document.body.append(v);
  onMount && onMount(v, close); const f = $('input,select,button.go', v); f && f.focus();
  return close;
}
function confirmBox(title, text, yes, fn) {
  modal(`<h2>${esc(title)}</h2><div>${text}</div><div class="acts"><button class="btn ghost" data-x>Отмена</button><button class="btn warn" data-y>${esc(yes)}</button></div>`, (v, close) => {
    $('[data-x]', v).onclick = close; $('[data-y]', v).onclick = () => { close(); fn(); };
  });
}
function newProject() {
  modal(`<h2>Новый поход</h2>
    <form class="sec" id="np"><label class="field"><span>Название</span><input name="title" type="text" placeholder="например: Сванетия, август 2026" maxlength="100" required></label>
    <div class="field"><span>Вид похода</span><div class="chips">${KINDS.map((k, i) => `<label class="chip${i ? '' : ' on'}"><input type="radio" name="kind" value="${k}" ${i ? '' : 'checked'} hidden>${k}</label>`).join('')}</div>
    <div class="hint">У горного километраж сразу считается с коэффициентом 1,2 — это можно поменять.</div></div>
    <div class="acts"><button type="button" class="btn ghost" data-x>Отмена</button><button class="btn go">Создать</button></div></form>`, (v, close) => {
    $('[data-x]', v).onclick = close;
    $$('.chip', v).forEach(c => c.onclick = () => { $$('.chip', v).forEach(x => x.classList.remove('on')); c.classList.add('on'); });
    $('#np', v).onsubmit = async e => {
      e.preventDefault();
      try { const d = await api('POST', '/api/projects', { title: e.target.title.value, kind: e.target.kind.value }, false, 'Создаю поход…'); close(); go(`#/p/${d.id}/tracks`); } catch (err) { fail(err); }
    };
  });
}

// ---------------------------------------------------------------- редактор: данные
let saveT = null, saving = null, dirtyFlag = false;
function dirty() { dirtyFlag = true; setSaved('busy', 'сохраняю…'); clearTimeout(saveT); saveT = setTimeout(flush, 700); }
function setSaved(cls, text) { const el = $('.saved'); if (el) { el.className = 'saved ' + cls; el.textContent = text; } }
async function flush() {
  clearTimeout(saveT);
  if (saving) await saving;
  if (!dirtyFlag || !S.proj) return;
  dirtyFlag = false;
  const body = { ...S.proj, pieces: S.proj.pieces.map(({ pts, ...p }) => p) };
  saving = api('PUT', `/api/projects/${S.pid}`, body, false, false).then(() => { if (!dirtyFlag) setSaved('', 'сохранено'); }, e => { dirtyFlag = true; setSaved('bad', 'не сохранилось'); fail(e); }).finally(() => { saving = null; });
  await saving;
}
window.addEventListener('beforeunload', e => { if (dirtyFlag) { flush(); e.preventDefault(); e.returnValue = ''; } });
async function openProject(pid, step) {
  await flush();
  try {
    const d = await api('GET', `/api/projects/${pid}`, undefined, false, 'Открываю поход…');
    S.pid = pid; S.proj = d.project; S.build = d.build; S.step = step; S.selPiece = S.selPoint = null; S.filter = 'all'; S.folder = ''; S.q = ''; S.lastInfo = null; S.mode = null; S.selBand = null; S.bandA = null; S.ptab = 'points';
    if (!S.proj.pieces.length) S.step = 'tracks';
    destroyAtlas(); renderEditor();
    if (['running', 'queued'].includes(S.build.state)) startPoll();
  } catch (e) { fail(e); S.pid = null; go('#/'); }
}
const P = () => S.proj;
const startDate = () => P().meta.start_date ? new Date(P().meta.start_date + 'T00:00') : null;
function dateOf(n) { const s = startDate(); if (!s) return null; const d = new Date(s); d.setDate(s.getDate() + n - 1); return d; }
const dm = d => d ? String(d.getDate()).padStart(2, '0') + '.' + String(d.getMonth() + 1).padStart(2, '0') : '';
const wd = d => d ? ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'][d.getDay()] : '';
function daysModel() {
  const used = P().pieces.filter(p => p.role !== 'skip').sort((a, b) => a.order - b.order);
  const max = Math.max(0, ...used.map(p => p.day || 0));
  const days = []; for (let n = 1; n <= max; n++) days.push({ n, pieces: used.filter(p => p.day === n) });
  return { days, loose: used.filter(p => !p.day), skipped: P().pieces.filter(p => p.role === 'skip'), max };
}
function dayKm(pieces) {
  const k = P().meta.kcoef || 1, one = P().meta.radial_one_way !== false, hasMain = pieces.some(p => p.role === 'main');
  return pieces.reduce((s, p) => s + (p.km || 0) * (p.role === 'radial' && hasMain && !one ? 2 : 1), 0) * k;
}

// ---------------------------------------------------------------- редактор: каркас
function renderEditor() {
  document.title = (P().meta.title || 'Поход') + ' — Нитка';
  const stepsHtml = STEPS.map(([id, t], i) => `<button class="step" data-a="step" data-step="${id}"><span class="dot">${i + 1}</span><span>${t}</span></button>`).join('');
  app.innerHTML = `<div class="ed">` + topBar(`<input class="title" data-c="title" value="${esc(P().meta.title)}" maxlength="100" aria-label="Название похода" spellcheck="false">
      <span class="saved">сохранено</span><a class="btn sm go dlall" id="dlall" hidden download title="Все готовые листы одним архивом">${I.dl}<span>Скачать всё</span></a><span class="sp"></span><nav class="steps">${stepsHtml}</nav><span class="sp"></span><span class="who">${esc(S.user.name)}</span>`) + `
    <div class="ed-body"><section class="panel" id="panel"></section>
      <div class="mapwrap"><div id="map"></div><div class="mapbar">${[['otm', 'Топо'], ['gsh100', 'Генштаб'], ['esri', 'Спутник']].map(([id, t]) => `<button class="chip" data-a="base" data-base="${id}">${t}</button>`).join('')}</div></div>
      <div class="maphandle" id="maphandle"></div>
    </div><nav class="tabbar">${stepsHtml}</nav></div>`;
  initMap(); applyMapSize(); paintEditor(true);
}
// На телефоне карта сворачивается: 'off' — скрыта, 'small' — полоска, 'big' — на пол-экрана
const isPhone = () => matchMedia('(max-width: 880px)').matches;
function mapSize() { return S.mapTemp || localStorage.nitkaMap || 'small'; }
function applyMapSize(size) {
  if (size) { localStorage.nitkaMap = size; S.mapTemp = null; }
  const body = $('.ed-body'), h = $('#maphandle'); if (!body || !h) return; const s = mapSize();
  body.classList.toggle('map-off', s === 'off'); body.classList.toggle('map-big', s === 'big');
  h.innerHTML = s === 'off' ? '<button data-a="mapsize" data-s="small">▼ Показать карту</button>'
    : s === 'big' ? '<button data-a="mapsize" data-s="small">▲ Карту поменьше</button><button data-a="mapsize" data-s="off">Скрыть</button>'
    : '<button data-a="mapsize" data-s="off">▲ Скрыть карту</button><button data-a="mapsize" data-s="big">▼ Побольше</button>';
  setTimeout(() => { map && map.invalidateSize(); updMore(); }, 260);
}
// Подсказка «ещё ниже»: видна, пока под краем экрана остаётся содержимое
function scroller() { return S.step === 'maps' ? (isPhone() ? $('.build') : $('.out')) : $('#panel'); }
function updMore() {
  const b = $('#more'); if (!b) return; const el = S.proj ? scroller() : null;
  if (!el) { b.hidden = true; return; }
  const left = el.scrollHeight - el.scrollTop - el.clientHeight; const r = el.getBoundingClientRect();
  b.hidden = left < 48 || r.height < 60; if (b.hidden) return;
  const bar = $('.next', el); const lift = bar && getComputedStyle(bar).position === 'sticky' ? bar.offsetHeight + 4 : 12;
  b.style.left = r.left + r.width / 2 + 'px'; b.style.top = r.bottom - lift - 34 + 'px';
}
document.addEventListener('scroll', () => updMore(), true);
window.addEventListener('resize', () => updMore());
function paintEditor(fit) {
  if (!S.proj) return;
  const body = $('.ed-body'); const wide = S.step === 'maps';
  body.classList.toggle('wide', wide);
  const idx = STEPS.findIndex(s => s[0] === S.step);
  $$('.step').forEach(b => { const i = STEPS.findIndex(s => s[0] === b.dataset.step); b.classList.toggle('on', i === idx); b.classList.toggle('done', i < idx); });
  $$('.mapbar .chip').forEach(c => c.classList.toggle('on', c.dataset.base === S.base));
  syncDl(); paintPanel(true);
  if (!wide) { setTimeout(() => { if (!map) return; map.invalidateSize(); drawMap(fit || !mapFitted); }, 0); }
}
function paintPanel(top) {
  const el = $('#panel'); if (!el) return;
  const y = el.scrollTop; const out = $('.out'); const y2 = out ? out.scrollTop : 0;
  el.className = S.step === 'maps' ? 'build-host' : 'panel';
  el.style.cssText = S.step === 'maps' ? 'overflow:hidden;min-height:0;display:block;height:100%' : '';
  el.innerHTML = ({ tracks: stepTracks, days: stepDays, points: stepPoints, maps: stepMaps })[S.step]();
  if (!top) { el.scrollTop = y; const o = $('.out'); if (o) o.scrollTop = y2; } else el.scrollTop = 0;
  if (S.step === 'tracks') bindDrop();
  markSteps();
  setTimeout(updMore, 320);
}
function syncDl() {
  const el = $('#dlall'); if (!el) return; const b = S.build || {};
  el.hidden = !(b.state === 'done' && (b.sheets || []).some(s => s.state === 'ok')); el.href = `/api/projects/${S.pid}/zip?fmt=png`;
}
const nextBtn = (step, text) => `<div class="next"><span></span><button class="btn go" data-a="step" data-step="${step}">${text} →</button></div>`;

// ---------------------------------------------------------------- шаг 1: треки
function infoText(i) {
  const names = { camp: ['ночёвка', 'ночёвки', 'ночёвок'], pass: ['перевал', 'перевала', 'перевалов'], peak: ['вершина', 'вершины', 'вершин'], photo: ['фототочка', 'фототочки', 'фототочек'], water: ['источник воды', 'источника воды', 'источников воды'] };
  const parts = [`${i.pieces} ${plural(i.pieces, 'трек', 'трека', 'треков')}`];
  let other = 0;
  for (const [t, n] of Object.entries(i.points || {})) { if (t === 'skip') continue; if (names[t]) parts.push(`${n} ${plural(n, ...names[t])}`); else other += n; }
  if (other) parts.push(`ещё ${other} ${plural(other, 'точка', 'точки', 'точек')}`);
  return parts.join(', ');
}
function stepTracks() {
  const pr = P(); const has = pr.pieces.length > 0; const i = S.lastInfo;
  return `<h2>Треки</h2><p>Загрузи нитку маршрута — одним файлом, по дням или ссылкой. Ночёвки, перевалы и другие точки подтянутся вместе с ней.</p>
    ${i ? `<div class="banner ok"><div><b>Нашёл:</b> ${esc(infoText(i))}.</div><div class="hint">${i.assigned === i.pieces - i.skipped ? 'Все треки разложились по дням сами.' : (i.unassigned ? `По дням не разложились: ${i.unassigned}. Это правится на шаге «Дни».` : '')}${i.skipped ? ` Дубли (${i.skipped}) отложены в сторону.` : ''}</div></div>` : ''}
    <label class="drop" id="drop">${I.upload}<b>Перетащи файлы сюда</b><span>GPX, KML, KMZ, GeoJSON или ZIP с ними — или нажми, чтобы выбрать</span><input id="file" type="file" multiple hidden accept=".gpx,.kml,.kmz,.geojson,.json,.zip,.nktk"></label>
    <form class="linkrow" id="linkf"><input type="url" name="link" placeholder="…или вставь ссылку nakarte.me с треком" autocomplete="off"><button class="btn">Добавить</button></form>
    <details class="help" open><summary>Как назвать треки и точки, чтобы всё разложилось само</summary><div class="in">
      <div><b>Треки по дням.</b> В названии — дата или номер дня: <code>5 августа</code>, <code>05.08</code>, <code>День 3</code>, <code>3 день</code>. Радиальный выход — слово «радиально»: <code>9 августа радиально</code>.</div>
      <div><b>GPX с часов или навигатора</b> с отметками времени сам делится по суткам. Один нарисованный трек без дат можно разрезать по ночёвкам.</div>
      <div><b>Ночёвки</b> — точки с датой: <code>05.08</code>, <code>16-17.08</code> (днёвка). <b>Перевалы и вершины</b>: <code>пер. Долра 2А, 3500</code>, <code>гора Тетнульди 2Б, 4858 м</code>.</div>
      <div><b>Фототочки</b> — «день-номер» в папке со словом «Фото»: <code>3-12</code>, <code>3-12,13</code>. <b>Остальное</b> узнаётся по словам: родник, брод, магазин, заброска, пещера…</div>
      <div class="hint">Не угадалось — не страшно: тип любой точки меняется на шаге «Точки».</div></div></details>
    ${has ? `<div class="sec"><h3>Загружено</h3><div class="list">${pr.sources.map(s => `<div class="row" style="cursor:default"><span class="ico">${I.upload.replace('viewBox', 'width="26" height="26" viewBox')}</span><span><div class="t">${esc(s.name)}</div><div class="s">${s.pieces} ${plural(s.pieces, 'трек', 'трека', 'треков')} · ${s.points} ${plural(s.points, 'точка', 'точки', 'точек')}</div></span><button class="icon-btn" data-a="src-del" data-id="${s.id}" title="Убрать этот файл">${I.trash}</button></div>`).join('')}</div></div>
    ${nextBtn('days', 'Дальше: дни')}` : ''}`;
}
function bindDrop() {
  const d = $('#drop'), f = $('#file'); if (!d) return;
  f.onchange = () => { upload([...f.files]); f.value = ''; };
  ['dragenter', 'dragover'].forEach(n => d.addEventListener(n, e => { e.preventDefault(); d.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(n => d.addEventListener(n, e => { e.preventDefault(); d.classList.remove('over'); }));
  d.addEventListener('drop', e => upload([...e.dataTransfer.files]));
  $('#linkf').onsubmit = async e => { e.preventDefault(); const v = e.target.link.value.trim(); if (!v) return; S.lastInfo = null;
    if (await importOne(() => api('POST', `/api/projects/${S.pid}/import`, { link: v }, false, 'Скачиваю трек с nakarte.me и разбираю его…'))) { toast('Трек по ссылке загружен'); paintPanel(true); drawMap(true); } };
}
async function importOne(call) {
  await flush(); const d = $('#drop'); d && d.classList.add('busy');
  try {
    const r = await call(); S.proj = r.project;
    const i = S.lastInfo;
    S.lastInfo = i ? { ...r.info, pieces: i.pieces + r.info.pieces, assigned: i.assigned + r.info.assigned, skipped: i.skipped + r.info.skipped, points: Object.fromEntries([...new Set([...Object.keys(i.points), ...Object.keys(r.info.points)])].map(k => [k, (i.points[k] || 0) + (r.info.points[k] || 0)])) } : r.info;
    return true;
  } catch (e) { fail(e); return false; } finally { d && d.classList.remove('busy'); }
}
async function upload(files) {
  if (!files.length) return; S.lastInfo = null; let ok = 0;
  files.sort((a, b) => a.name.localeCompare(b.name, 'ru', { numeric: true }));
  for (const [i, f] of files.entries()) {
    const msg = `Читаю «${f.name}»${files.length > 1 ? ` — ${i + 1} из ${files.length}` : ''}${f.size > 3e6 ? ' (файл большой, это может занять полминуты)' : ''}…`;
    if (await importOne(() => api('POST', `/api/projects/${S.pid}/import?name=${encodeURIComponent(f.name)}`, f, true, msg))) ok++;
  }
  if (ok) { toast(ok > 1 ? `Загружено файлов: ${ok}` : 'Трек загружен'); paintPanel(true); drawMap(true); }
}

// ---------------------------------------------------------------- шаг 2: дни
function pieceRow(p, maxDay) {
  return `<div class="pc${S.selPiece === p.id ? ' sel' : ''}" data-a="piece" data-id="${p.id}"><span class="nm">${esc(p.name)}</span><span class="num hint">${km(p.km || 0)} км</span>
    <span class="ctl"><span class="hint">день</span><input type="number" min="1" max="${maxDay + 30}" value="${p.day || ''}" data-c="pday" data-id="${p.id}" aria-label="День">
    <select data-c="prole" data-id="${p.id}" aria-label="Что это"><option value="main"${p.role === 'main' ? ' selected' : ''}>основная нитка</option><option value="radial"${p.role === 'radial' ? ' selected' : ''}>радиальный выход</option><option value="skip"${p.role === 'skip' ? ' selected' : ''}>не использовать</option></select>
    <span class="sp" style="flex:1"></span><button class="icon-btn" data-a="pmove" data-id="${p.id}" data-d="-1" title="Раньше">${I.up}</button><button class="icon-btn" data-a="pmove" data-id="${p.id}" data-d="1" title="Позже">${I.down}</button></span></div>`;
}
function stepDays() {
  const pr = P(); const M = daysModel(); const k = pr.meta.kcoef || 1;
  if (!pr.pieces.length) return `<h2>Дни</h2><p>Сначала загрузи трек.</p><div class="next"><button class="btn" data-a="step" data-step="tracks">← К трекам</button></div>`;
  const total = M.days.reduce((s, d) => s + dayKm(d.pieces), 0) + dayKm(M.loose);
  const single = !M.max && M.loose.length;
  return `<h2>Дни</h2><p>Какой кусок нитки — какой день. Всего ≈ <b class="num">${km(total)} км</b>${k !== 1 ? ` (с коэффициентом ${km(k)})` : ''}; точные цифры и набор высоты появятся после сборки.</p>
    <div class="sec">
      <label class="field"><span>Первый день похода</span><input type="date" data-c="start" value="${pr.meta.start_date || ''}"></label>
      <div class="field"><span>Коэффициент к километражу</span><div class="chips">${[[1, '1,0 — как на карте'], [1.2, '1,2 — горный рельеф']].map(([v, t]) => `<button class="chip${Math.abs(k - v) < .001 ? ' on' : ''}" data-a="kcoef" data-v="${v}">${t}</button>`).join('')}
        <input type="number" step="0.05" min="1" max="3" value="${k}" data-c="kcoef" style="width:84px;min-height:32px;padding:2px 8px" aria-label="Свой коэффициент"></div></div>
      <label class="switch"><input type="checkbox" data-c="oneway" ${pr.meta.radial_one_way !== false ? 'checked' : ''}>Радиальные выходы считать в одну сторону</label>
    </div>
    <div class="banner blue"><div><b>Нужно разделить кусок на два?</b> Например, один трек на два дня или радиалка, нарисованная вместе с основной ниткой.</div>
      <div class="chips"><button class="btn sm" data-a="cutmode">✂ Разрезать нитку на карте</button></div></div>
    ${(() => { const f = M.days.find(d => d.pieces.length); return f && f.n > 1 ? `<div class="banner"><div><b>Первые ${f.n - 1} ${plural(f.n - 1, 'день', 'дня', 'дней')} без треков.</b> Похоже, числа в названиях треков — это числа месяца, а не номера дней.</div>
      <div class="chips"><button class="btn sm go" data-a="shiftdays" data-n="${f.n - 1}">Сделать день ${f.n} первым</button></div></div>` : ''; })()}
    ${single ? `<div class="banner"><div><b>Трек не разбит по дням.</b> Так тоже можно — получится один общий день. Если поход многодневный, отметь ночёвки с датами (шаг «Точки») и разрежь нитку по ним.</div>
      <div class="chips"><button class="btn sm" data-a="split-camps">Разрезать по ночёвкам</button><button class="btn sm" data-a="each-day">Каждый кусок — свой день</button></div></div>` : ''}
    ${M.loose.length && !single ? `<div class="day loose"><header>Без дня <small>${M.loose.length} ${plural(M.loose.length, 'кусок', 'куска', 'кусков')} — впиши номер дня</small></header>${M.loose.map(p => pieceRow(p, M.max)).join('')}
      <div class="pc" style="cursor:default"><span class="ctl"><button class="btn sm" data-a="split-camps">Разрезать по ночёвкам</button><button class="btn sm" data-a="each-day">Каждый — свой день</button></span></div></div>` : ''}
    ${single ? `<div class="day"><header>День 1 <small>${km(dayKm(M.loose))} км</small></header>${M.loose.map(p => pieceRow(p, 1)).join('')}</div>` : ''}
    ${M.days.map(d => { const dt = dateOf(d.n);
      if (!d.pieces.length) return `<div class="day rest"><header>День ${d.n}${dt ? ' · ' + dm(dt) : ''} <small>днёвка — трека нет</small></header></div>`;
      return `<div class="day${d.n % 2 ? '' : ' even'}"><header>День ${d.n}${dt ? ` · ${dm(dt)}, ${wd(dt)}` : ''} <small class="num">≈ ${km(dayKm(d.pieces))} км</small></header>${d.pieces.map(p => pieceRow(p, M.max)).join('')}</div>`; }).join('')}
    ${M.skipped.length ? `<details class="day skip"><summary style="list-style:none;cursor:pointer"><header>Не используются <small>${M.skipped.length} · нажми, чтобы показать</small></header></summary>${M.skipped.map(p => pieceRow(p, M.max)).join('')}</details>` : ''}
    <div class="next"><button class="btn" data-a="step" data-step="tracks">← Треки</button><button class="btn go" data-a="step" data-step="points">Дальше: точки →</button></div>`;
}

// ---------------------------------------------------------------- шаг 3: точки
function ptTitle(q) {
  const t = typeOf(q.type);
  if (q.type === 'camp') { const d = q.date ? q.date.slice(8, 10) + '.' + q.date.slice(5, 7) : (q.after_day ? `после дня ${q.after_day}` : ''); return ['Ночёвка', d + (q.nights > 1 ? ` · ${q.nights} ночи` : ''), q.name].filter(Boolean).join(' · '); }
  if (q.type === 'photo') return `Фото ${q.day || '?'}-${(q.nums || []).join(',') || '?'}`;
  return q.name || t.title;
}
const ptSub = q => [typeOf(q.type).title, q.grade, q.h ? q.h + ' м' : ''].filter(Boolean).join(' · ');
function shownPoints() {
  const ql = S.q.trim().toLowerCase();
  return P().points.filter(q => (S.filter === 'all' ? q.type !== 'skip' : S.filter === 'guess' ? q.guess && q.type !== 'skip' : q.type === S.filter)
    && (!S.folder || q.folder === S.folder) && (!ql || (ptTitle(q) + ' ' + (q.raw || '')).toLowerCase().includes(ql)));
}
function typeSelect(q) {
  const groups = {}; allTypes().forEach(t => (groups[t.group || '—'] = groups[t.group || '—'] || []).push(t));
  return `<select data-c="ptype" data-id="${q.id}">${Object.entries(groups).map(([g, ts]) => `<optgroup label="${esc(g)}">${ts.map(t => `<option value="${t.id}"${t.id === q.type ? ' selected' : ''}>${esc(t.title)}</option>`).join('')}</optgroup>`).join('')}<optgroup label="…"><option value="__new">＋ свой тип точки…</option></optgroup></select>`;
}
function pointEditor(q) {
  const t = typeOf(q.type); const graded = S.types.find(x => x.id === q.type)?.graded; const isObj = S.obj.includes(q.type);
  return `<div class="pe" data-pe="${q.id}">
    <div class="two"><label class="field"><span>Что это</span>${typeSelect(q)}</label>
      ${q.type === 'camp' ? `<label class="field"><span>Дата ночёвки</span><input type="date" data-c="pf" data-f="date" data-id="${q.id}" value="${q.date || ''}"></label>` :
        q.type === 'photo' ? `<label class="field"><span>День</span><input type="number" min="1" data-c="pf" data-f="day" data-id="${q.id}" value="${q.day || ''}"></label>` :
        `<label class="field"><span>Высота, м</span><input type="number" min="0" max="8900" data-c="pf" data-f="h" data-id="${q.id}" value="${q.h || ''}" placeholder="${isObj ? 'по рельефу' : '—'}"></label>`}</div>
    ${q.type === 'photo' ? `<label class="field"><span>Номера фото через запятую</span><input type="text" data-c="pf" data-f="nums" data-id="${q.id}" value="${esc((q.nums || []).join(', '))}" placeholder="12, 13, 14"></label>` :
      `<label class="field"><span>${q.type === 'camp' ? 'Название места (необязательно)' : 'Подпись на карте'}</span><input type="text" data-c="pf" data-f="name" data-id="${q.id}" value="${esc(q.name || '')}" maxlength="100" placeholder="${esc(q.type === 'camp' ? 'например: р. Долра' : t.title)}"></label>`}
    ${q.type === 'camp' ? `<div class="two"><label class="field"><span>Сколько ночей здесь</span><input type="number" min="1" max="9" data-c="pf" data-f="nights" data-id="${q.id}" value="${q.nights || 1}"></label><label class="field"><span>Высота, м</span><input type="number" min="0" max="8900" data-c="pf" data-f="h" data-id="${q.id}" value="${q.h || ''}" placeholder="по рельефу"></label></div>
      ${!P().meta.start_date && !q.date ? `<label class="field"><span>После какого дня</span><input type="number" min="0" data-c="pf" data-f="after_day" data-id="${q.id}" value="${q.after_day ?? ''}" placeholder="сама найдётся по нитке"></label>` : ''}` : ''}
    ${graded ? `<div class="field"><span>Категория трудности</span><div class="chips">${['', ...S.grades].map(g => `<button class="chip gr${(q.grade || '') === g ? ' on' : ''}" style="--g:${S.gcol[g] || '#56677D'}" data-a="grade" data-id="${q.id}" data-g="${g}">${g || 'нет'}</button>`).join('')}</div></div>
      <label class="switch"><input type="checkbox" data-c="pflag" data-f="key" data-id="${q.id}" ${q.key ? 'checked' : ''}><span><b>Определяющее препятствие</b><div class="hint">попадёт в сводку для графика движения, в списке на обзорной карте — жирным</div></span></label>` : ''}
    ${q.type !== 'photo' ? `<div class="field"><span>С какой стороны от знака ставить подпись</span><div class="compass">${[['NW', '↖'], ['N', '↑'], ['NE', '↗'], ['W', '←'], ['', 'сама'], ['E', '→'], ['SW', '↙'], ['S', '↓'], ['SE', '↘']]
      .map(([v, t]) => `<button class="${(q.label_pos || '') === v ? 'on' : ''}" data-a="lpos" data-id="${q.id}" data-v="${v}" title="${v ? 'Подпись — с этой стороны' : 'Программа выберет сама, где свободнее'}">${t}</button>`).join('')}</div></div>` : ''}
    <div class="chips" style="gap:14px"><label class="switch"><input type="checkbox" data-c="pflag" data-f="on_map" data-id="${q.id}" ${q.on_map !== false ? 'checked' : ''}>на картах</label>
      ${q.type !== 'photo' ? `<label class="switch"><input type="checkbox" data-c="pflag" data-f="on_profile" data-id="${q.id}" ${q.on_profile ? 'checked' : ''}>на профиле высот</label>` : ''}</div>
    <div class="raw">${q.raw ? `В треке: «${esc(q.raw)}»${q.folder ? ` · ${esc(q.folder)}` : ''}. ` : ''}Точку можно перетащить на карте. Правки сохраняются сами.</div></div>`;
}
function stepPoints() {
  const pr = P(); const pts = pr.points; const counts = {}; pts.forEach(q => counts[q.type] = (counts[q.type] || 0) + 1);
  const guess = pts.filter(q => q.guess && q.type !== 'skip').length;
  const folders = [...new Set(pts.map(q => q.folder).filter(Boolean))];
  const list = shownPoints(); const order = allTypes().map(t => t.id);
  list.sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type) || (a.date || '').localeCompare(b.date || '') || (a.day || 0) - (b.day || 0) || ((a.nums || [0])[0] - (b.nums || [0])[0]) || ptTitle(a).localeCompare(ptTitle(b), 'ru'));
  const cap = 300; const nb = (pr.bands || []).length;
  const td = todos(); const bang = tab => td.some(t => t.step === 'points' && t.tab === tab) ? '<i class="bang">!</i>' : '';
  const head = `<h2>Точки и участки</h2><div class="seg"><button class="${S.ptab === 'points' ? 'on' : ''}" data-a="ptab" data-t="points">Точки <span>${pts.length - (counts.skip || 0)}</span>${bang('points')}</button><button class="${S.ptab === 'bands' ? 'on' : ''}" data-a="ptab" data-t="bands">Участки <span>${nb}</span></button>${showObst() ? `<button class="${S.ptab === 'obst' ? 'on' : ''}" data-a="ptab" data-t="obst">Препятствия <span>${pts.filter(q => S.obj.includes(q.type)).length}</span>${bang('obst')}</button>` : ''}</div>
    ${S.selPoint || S.selBand ? '' : todoBlock(td.filter(t => t.step === 'points'), 'Стоит сделать')}`;
  if (S.ptab === 'bands') return head + stepBands();
  if (S.ptab === 'obst' && showObst()) return head + stepObst();
  return `${head}<p>Ночёвки, перевалы, вода, достопримечательности — всё, что будет на картах. Нажми на точку, чтобы поправить.</p>
    <div class="chips"><button class="btn sm go" data-a="padd">＋ Точка на карте</button><input type="text" data-c="q" value="${esc(S.q)}" placeholder="поиск" style="flex:1;min-width:120px;min-height:32px;padding:3px 10px"></div>
    ${daysModel().max > 1 && pts.some(q => q.type === 'camp') ? `<div class="chips"><button class="btn sm" data-a="sugg" data-w="camps">Ночёвки по концам дней…</button></div>` : ''}
    <div class="chips"><button class="chip${S.filter === 'all' ? ' on' : ''}" data-a="filter" data-f="all">Все <span class="cnt">${pts.length - (counts.skip || 0)}</span></button>
      ${guess ? `<button class="chip${S.filter === 'guess' ? ' on' : ''}" data-a="filter" data-f="guess">проверить <span class="cnt">${guess}</span></button>` : ''}
      ${allTypes().filter(t => counts[t.id]).map(t => `<button class="chip${S.filter === t.id ? ' on' : ''}" data-a="filter" data-f="${t.id}">${sym(t.id)}${esc(t.title.split(/[:,]/)[0])} <span class="cnt">${counts[t.id]}</span></button>`).join('')}</div>
    ${folders.length > 1 ? `<div class="sec"><label class="field"><span>Папка в треке</span><select data-c="folder"><option value="">все папки</option>${folders.map(f => `<option${f === S.folder ? ' selected' : ''} value="${esc(f)}">${esc(f)} (${pts.filter(q => q.folder === f).length})</option>`).join('')}</select></label>
      ${S.folder ? `<div class="chips"><button class="btn sm" data-a="bulk" data-m="hide">Скрыть все из папки</button><button class="btn sm" data-a="bulk" data-m="show">Показать все</button><button class="btn sm warn" data-a="bulk" data-m="del">Удалить все</button></div>` : ''}</div>` : ''}
    ${!pts.length ? '<div class="banner blue">Точек пока нет. Они приходят вместе с треком — или поставь первую кнопкой «＋ Точка на карте».</div>' : ''}
    <div class="list" id="plist">${list.slice(0, cap).map(q => `<button class="row${S.selPoint === q.id ? ' sel' : ''}${q.on_map === false || q.type === 'skip' ? ' off' : ''}" data-a="psel" data-id="${q.id}" id="pt-${q.id}">
      <span class="ico">${sym(q.type)}</span><span><div class="t">${esc(ptTitle(q))}${q.guess ? ' <span class="tag warn">проверь тип</span>' : ''}</div><div class="s">${esc(ptSub(q))}</div></span>
      <span class="icon-btn" data-a="peye" data-id="${q.id}" title="${q.on_map === false ? 'Показать на картах' : 'Скрыть с карт'}">${q.on_map === false ? I.eyeoff : I.eye}</span></button>${S.selPoint === q.id ? pointEditor(q) : ''}`).join('')}
      ${list.length > cap ? `<div class="hint">Показаны первые ${cap} из ${list.length} — сузь поиском или фильтром.</div>` : ''}</div>
    ${S.selPoint && pointById(S.selPoint) ? `<div class="next edit"><button class="btn warn" data-a="pdel" data-id="${S.selPoint}">Удалить точку</button><button class="btn go" data-a="psel" data-id="">Сохранить ✓</button></div>`
      : `<div class="next"><button class="btn" data-a="step" data-step="days">← Дни</button><button class="btn go" data-a="step" data-step="maps">Дальше: карты →</button></div>`}`;
}
// ---- определяющие препятствия: перевалы, вершины и прочее по ходу маршрута, с категориями
// ---- «стоит сделать»: то, что требует внимания, показывается там, где человек сейчас, а не прячется по вкладкам
function todos() {
  const pr = P(); const out = []; if (!pr || !pr.pieces.some(p => p.role !== 'skip')) return out;
  const M = daysModel(); const pts = pr.points; const objs = pts.filter(q => S.obj.includes(q.type));
  const first = M.days.find(d => d.pieces.length);
  if (M.loose.length && M.max) out.push({ step: 'days', text: `${M.loose.length} ${plural(M.loose.length, 'кусок', 'куска', 'кусков')} нитки без дня`, btn: 'К дням' });
  if (first && first.n > 1) out.push({ step: 'days', text: `Первые ${first.n - 1} ${plural(first.n - 1, 'день', 'дня', 'дней')} без треков — похоже, сбита нумерация`, btn: 'Поправить' });
  const bad = badOrderDays();
  if (bad.length) out.push({ step: 'days', fix: 'order', text: `${bad.length > 1 ? 'В днях' : 'В дне'} ${bad.join(', ')} куски нитки стоят не по ходу движения — из-за этого появляются ложные переезды`, btn: 'Расставить по ходу' });
  if (!pr.meta.start_date) out.push({ step: 'days', text: 'Не указана дата первого дня — на картах не будет дат', btn: 'Указать' });
  if (M.max > 1 && !pts.some(q => q.type === 'camp')) out.push({ step: 'points', tab: 'points', run: 'camps', text: 'Ночёвки не отмечены — могу поставить их в концах дней', btn: 'Предложить ночёвки' });
  if (askPasses() && !objs.length) out.push({ step: 'points', tab: 'obst', run: 'passes', text: 'Перевалы и вершины не отмечены — найду их по каталогу, с категориями и высотами', btn: 'Найти перевалы' });
  const ng = objs.filter(q => q.type === 'pass' && !q.grade && q.on_map !== false).length;
  if (ng) out.push({ step: 'points', tab: 'obst', text: `У ${ng} ${plural(ng, 'перевала', 'перевалов', 'перевалов')} не указана категория`, btn: 'Проставить' });
  const cs = pts.filter(q => q.type === 'campsite' && campDate(q));
  if (cs.length) out.unshift({ step: 'points', tab: 'points', fix: 'camps', text: `${cs.length} ${plural(cs.length, 'точка', 'точки', 'точек')} вида «${cs[0].raw || cs[0].name}» — это ночёвки с датами, а записаны как места под стоянку`, btn: 'Сделать ночёвками' });
  const y0 = pr.meta.start_date ? +pr.meta.start_date.slice(0, 4) : 0;
  const yb = y0 ? pts.filter(q => q.type === 'camp' && q.date && Math.abs(+q.date.slice(0, 4) - y0) > 1) : [];
  if (yb.length) out.unshift({ step: 'points', tab: 'points', fix: 'campyear', text: `У ${yb.length > 1 ? yb.length + ' ночёвок' : 'ночёвки'} дата ${yb[0].date.slice(8, 10)}.${yb[0].date.slice(5, 7)}.${yb[0].date.slice(0, 4)} — похоже, опечатка в годе: из-за неё ночёвки пронумерованы не по порядку`, btn: `Исправить на ${y0}` });
  const g = pts.filter(q => q.guess && q.type !== 'skip').length;
  if (g) out.push({ step: 'points', tab: 'points', filter: 'guess', text: `${g} ${plural(g, 'точка', 'точки', 'точек')}: тип угадан неуверенно`, btn: 'Проверить' });
  out.forEach((t, i) => { t.i = i; });
  return out;
}
// Дата ночёвки из названия точки: «22.07», «26-27.07», «31.07 - 01.08» (без lookbehind — он не работает в старом Safari)
function campDate(q) {
  const s = ' ' + (q.raw || q.name || ''); const y = (P().meta.start_date || '').slice(0, 4) || new Date().getFullYear();
  const iso = (d, m) => `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`; const ok = (d, m) => +m >= 1 && +m <= 12 && +d >= 1 && +d <= 31;
  let m = s.match(/[^\d.](\d{1,2})\.(\d{1,2})\s*[-–—]\s*(\d{1,2})\.(\d{1,2})(?!\d)/);
  if (m && ok(m[1], m[2]) && ok(m[3], m[4])) { const n = Math.round((new Date(iso(m[3], m[4])) - new Date(iso(m[1], m[2]))) / 864e5) + 1; if (n >= 1 && n <= 15) return { date: iso(m[1], m[2]), nights: n }; }
  m = s.match(/[^\d.а-яёa-z](\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\.(\d{1,2})(?!\d)/i);
  if (m && ok(m[1], m[3])) return { date: iso(m[1], m[3]), nights: m[2] ? Math.max(1, m[2] - m[1] + 1) : 1 };
  return null;
}
// «Лагерь 22.07» записан как место под стоянку → делаем ночёвкой; подсказанные рядом ночёвки-дубли убираем
// Дни, где другой порядок кусков основной нитки делает её заметно связнее (в файлах сегменты часто лежат не по ходу)
function badOrderDays() {
  const by = {}; P().pieces.filter(p => p.role === 'main' && p.day && p.pts.length > 1).sort((a, b) => a.order - b.order).forEach(p => (by[p.day] = by[p.day] || []).push(p));
  const ds = Object.keys(by).map(Number).sort((a, b) => a - b); const D = (a, b) => L.latLng(a[0], a[1]).distanceTo([b[0], b[1]]);
  const last = p => p.pts[p.pts.length - 1]; const perms = a => a.length < 2 ? [a] : a.flatMap((x, i) => perms([...a.slice(0, i), ...a.slice(i + 1)]).map(r => [x, ...r]));
  const out = []; let prev = null;
  ds.forEach((d, k) => {
    let cur = by[d];
    if (cur.length > 1 && cur.length <= 6) {
      const nxt = k + 1 < ds.length ? by[ds[k + 1]].map(q => q.pts[0]) : [];
      const cost = pm => (prev ? D(prev, pm[0].pts[0]) : 0) + pm.slice(1).reduce((s, b, i) => s + D(last(pm[i]), b.pts[0]), 0) + (nxt.length ? Math.min(...nxt.map(q => D(last(pm[pm.length - 1]), q))) : 0);
      const best = perms(cur).reduce((a, b) => cost(b) < cost(a) ? b : a, cur);
      if (cost(best) < cost(cur) - 50) { out.push(d); cur = best; }
    }
    prev = last(cur[cur.length - 1]);
  });
  return out;
}
async function fixOrder() {
  await flush();
  try { const r = await api('POST', `/api/projects/${S.pid}/chain-order`, undefined, false, 'Расставляю куски по ходу…'); S.proj = r.project; toast(r.changed ? 'Куски расставлены по ходу движения. Пересобери карты — профиль и листы изменятся.' : 'Порядок уже верный'); paintPanel(); drawMap(); } catch (e) { fail(e); }
}
function fixCamps() {
  const pts = P().points; const made = [];
  for (const q of pts) {
    if (q.type !== 'campsite') continue; const d = campDate(q); if (!d) continue;
    q.type = 'camp'; q.date = d.date; q.nights = d.nights; q.on_profile = true; delete q.guess;
    q.name = (q.raw || q.name || '').replace(/\d{1,2}\.\d{1,2}\s*[-–—]\s*\d{1,2}\.\d{1,2}|\d{1,2}(\s*[-–—]\s*\d{1,2})?\.\d{1,2}(\.\d{2,4})?/, '').replace(/лагерь|стоянка|бивак|бивуак/i, '').replace(/^[\s,.;:–-]+|[\s,.;:–-]+$/g, '');
    made.push(q);
  }
  const dup = q => q.type === 'camp' && q.folder === 'подсказка: концы дней' && made.some(c => c.date === q.date || L.latLng(c.lat, c.lon).distanceTo([q.lat, q.lon]) < 500);
  const before = pts.length; P().points = pts.filter(q => !dup(q)); const gone = before - P().points.length;
  S.selPoint = null; dirty(); paintPanel(true); drawPoints();
  toast(`Ночёвок с датами: ${made.length}` + (gone ? `. Убрано дублей: ${gone}` : ''));
}
function todoBlock(list, title) {
  if (!list.length) return '';
  return `<div class="todo"><h3>${title}</h3>${list.map(t => `<div class="todo-i"><i class="bang">!</i><span>${esc(t.text)}</span><button class="btn sm go" data-a="todo" data-i="${t.i}">${esc(t.btn)}</button></div>`).join('')}</div>`;
}
function markSteps() { const st = new Set(todos().map(t => t.step)); $$('.step').forEach(b => b.classList.toggle('att', st.has(b.dataset.step) && b.dataset.step !== S.step)); }
// препятствия бывают в любом походе (перевалы — и в лыжном, и в вело), поэтому вкладка есть всегда;
// подсказку «найти перевалы» не показываем только на воде и после поиска, который ничего не нашёл
const showObst = () => true;
const askPasses = () => P().meta.kind !== 'водный' && !P().meta.no_passes;
const gbadge = g => `<span class="gb" style="--g:${S.gcol[g] || '#8A97A8'}">${g || '—'}</span>`;
function routeOrder(list) {
  const chains = dayChains(); const days = Object.keys(chains).map(Number).sort((a, b) => a - b);
  const pos = q => { let best = [1e9, 999, 0];
    for (const d of days) { const pts = chains[d]; const k = Math.cos(q.lat * Math.PI / 180);
      for (let i = 0; i < pts.length; i++) { const dd = (pts[i][0] - q.lat) ** 2 + ((pts[i][1] - q.lon) * k) ** 2; if (dd < best[0] - 1e-12) best = [dd, d, i]; } }
    return best; };
  return list.map(q => { const p = pos(q); return { q, day: p[0] < 4e-6 ? p[1] : 0, i: p[2], off: p[0] >= 4e-6 }; }).sort((a, b) => (a.off - b.off) || a.day - b.day || a.i - b.i);
}
function stepObst() {
  const objs = P().points.filter(q => S.obj.includes(q.type)); const rows = routeOrder(objs);
  const cnt = {}; objs.forEach(q => { if (q.on_map !== false) cnt[q.grade || ''] = (cnt[q.grade || ''] || 0) + 1; });
  const top = [...S.grades].reverse().find(g => cnt[g]); const keys = objs.filter(q => q.key).length;
  return `<p>Перевалы, вершины, переправы и другие препятствия — по ходу маршрута. Категория задаёт цвет знака и подписи на картах и профиле.</p>
    ${objs.length ? `<div class="chips">${[...S.grades].reverse().filter(g => cnt[g]).map(g => `<span class="gsum" style="--g:${S.gcol[g]}"><b>${g}</b> × ${cnt[g]}</span>`).join('')}${cnt[''] ? `<span class="gsum" style="--g:#8A97A8"><b>без кат.</b> × ${cnt['']}</span>` : ''}</div>
      <div class="hint">${top ? `Старшая категория на маршруте — <b>${top}</b>. ` : ''}${keys ? `Определяющих отмечено: ${keys}.` : 'Звёздочкой отметь определяющие препятствия — они пойдут в сводку для графика движения.'}</div>` : ''}
    <div class="chips"><button class="btn sm go" data-a="oadd">＋ Препятствие на карте</button><button class="btn sm" data-a="sugg" data-w="passes">Найти по каталогу перевалов</button></div>
    ${!objs.length ? '<div class="banner blue">Препятствий пока нет. Они подтягиваются из трека (точки вида «пер. Долра 2А, 3500»), находятся по каталогу перевалов — тому же, что на nakarte.me, — или ставятся на карте вручную.</div>' : ''}
    <div class="list">${rows.map(({ q, day, off }) => `<button class="row${S.selPoint === q.id ? ' sel' : ''}${q.on_map === false ? ' off' : ''}" data-a="psel" data-id="${q.id}" id="pt-${q.id}">
      <span class="ico">${gbadge(q.grade)}</span><span><div class="t">${esc(ptTitle(q))}</div><div class="s">${esc([typeOf(q.type).title, q.h ? q.h + ' м' : '', off ? 'в стороне от нитки' : (day ? 'день ' + day : '')].filter(Boolean).join(' · '))}</div></span>
      <span class="icon-btn star${q.key ? ' on' : ''}" data-a="okey" data-id="${q.id}" title="${q.key ? 'Определяющее препятствие' : 'Отметить как определяющее'}">★</span></button>${S.selPoint === q.id ? pointEditor(q) : ''}`).join('')}</div>
    ${S.selPoint && pointById(S.selPoint) ? `<div class="next edit"><button class="btn warn" data-a="pdel" data-id="${S.selPoint}">Удалить</button><button class="btn go" data-a="psel" data-id="">Сохранить ✓</button></div>`
      : `<div class="next"><button class="btn" data-a="step" data-step="days">← Дни</button><button class="btn go" data-a="step" data-step="maps">Дальше: карты →</button></div>`}`;
}
const BCOLS = ['#E6007A', '#C62828', '#EF6C00', '#F9A825', '#2E7D32', '#00838F', '#1565C0', '#4527A0', '#5D4037', '#37474F'];
const swatch = st => `<span class="bsw${st.hatch ? ' h' : ''}" style="--c:${st.color}"></span>`;
function bandEditor(b) {
  const groups = {}; S.bands.forEach(t => (groups[t.group] = groups[t.group] || []).push(t));
  return `<div class="pe"><label class="field"><span>Что за участок</span><select data-c="bkind" data-id="${b.id}">
      ${Object.entries(groups).map(([g, ts]) => `<optgroup label="${esc(g)}">${ts.map(t => `<option value="${t.id}"${t.id === b.kind ? ' selected' : ''}>${esc(t.title)}</option>`).join('')}</optgroup>`).join('')}
      <optgroup label="…"><option value="custom"${b.kind === 'custom' ? ' selected' : ''}>своё название и цвет</option></optgroup></select></label>
    ${b.kind === 'custom' ? `<label class="field"><span>Как подписать в легенде</span><input type="text" data-c="btitle" data-id="${b.id}" value="${esc(b.title || '')}" maxlength="60" placeholder="например: ягодник"></label>
      <div class="field"><span>Цвет полосы</span><div class="swatches">${BCOLS.map(c => `<button class="${(b.color || BCOLS[0]).toLowerCase() === c.toLowerCase() ? 'on' : ''}" data-a="bcol" data-id="${b.id}" data-col="${c}" style="background:${c}" aria-label="${c}"></button>`).join('')}</div></div>` : ''}
    <div class="raw">На картах — цветная полоса под ниткой и строка в легенде, на профиле — полоска вдоль оси. Чтобы сдвинуть границы, удали участок и отметь заново.</div></div>`;
}
function stepBands() {
  const bands = P().bands || []; const chains = dayChains();
  const rows = bands.map(b => ({ b, st: bandStyle(b), info: bandInfo(b, chains) })).sort((x, y) => x.info.day - y.info.day);
  return `<p>Где путь особенный: ледник, осыпь, бурелом, перила, болото. Отметь начало и конец прямо на нитке.</p>
    <div class="chips"><button class="btn sm go" data-a="badd">＋ Участок на нитке</button></div>
    ${!bands.length ? '<div class="banner blue">Участков пока нет. Нажми «＋ Участок на нитке», потом два раза по нитке на карте: где участок начинается и где кончается.</div>' : ''}
    <div class="list">${rows.map(({ b, st, info }) => `<button class="row${S.selBand === b.id ? ' sel' : ''}" data-a="bsel" data-id="${b.id}" id="bd-${b.id}"><span class="ico">${swatch(st)}</span>
      <span><div class="t">${esc(st.title)}</div><div class="s">${info.path.length ? `${info.day ? 'день ' + info.day + ' · ' : ''}${km(info.km)} км` : 'не лежит на нитке — отметь заново'}</div></span><span></span></button>${S.selBand === b.id ? bandEditor(b) : ''}`).join('')}</div>
    ${S.selBand && bands.some(b => b.id === S.selBand) ? `<div class="next edit"><button class="btn warn" data-a="bdel" data-id="${S.selBand}">Удалить участок</button><button class="btn go" data-a="bsel" data-id="">Сохранить ✓</button></div>`
      : `<div class="next"><button class="btn" data-a="step" data-step="days">← Дни</button><button class="btn go" data-a="step" data-step="maps">Дальше: карты →</button></div>`}`;
}
// ---- рамка листа: сдвиг и масштаб
function frameEditor(s) {
  const f = s && s.frame; if (!f) return toast('У этого листа нет рамки — это график', true);
  const sheets = P().sheets; if (!sheets.adjust) sheets.adjust = {};
  const cur = sheets.adjust[s.id] || {}; let lat = cur.lat ?? f.lat, lon = cur.lon ?? f.lon, scale = cur.scale || 0, moved = cur.lat != null;
  const scales = s.id === 'region' ? [500000, 1000000, 2000000, 5000000] : s.id === 'overview' ? [50000, 100000, 200000, 300000, 500000, 1000000, 2000000] : s.id.startsWith('day-') ? [25000, 50000, 100000, 200000, 500000] : [];
  const fmt = v => '1:' + String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  let fm = null;
  modal(`<h2>Рамка листа</h2><div class="hint">«${esc(s.title)}». Перетащи рамку за жёлтую метку в центре${scales.length ? ' или поменяй масштаб' : ''} — пересоберётся только этот лист.</div>
    <div class="fmap" id="fmap"></div>
    ${scales.length ? `<label class="field"><span>Масштаб</span><select id="fscale"><option value="0">сам подберётся (сейчас ${fmt(f.scale)})</option>${scales.map(v => `<option value="${v}"${v === scale ? ' selected' : ''}>${fmt(v)}</option>`).join('')}</select></label>`
      : '<div class="hint">Масштаб рабочей карты общий для всех листов — он в настройках слева.</div>'}
    <div class="acts"><button class="btn ghost" data-auto ${f.manual || cur.lat != null || cur.scale ? '' : 'disabled'}>Вернуть как было</button><span style="flex:1"></span><button class="btn ghost" data-x>Отмена</button><button class="btn go" data-ok>Сохранить и пересобрать</button></div>`, (v, close) => {
    $('.modal', v).classList.add('wide');
    const box = () => { const sc = scale || f.scale, hw = f.fw * sc / 100000 / 2, hh = f.fh * sc / 100000 / 2, dla = hh / 111.13, dlo = hw / (111.32 * Math.cos(lat * Math.PI / 180)); return [[lat - dla, lon - dlo], [lat + dla, lon + dlo]]; };
    fm = L.map($('#fmap', v), { attributionControl: false });
    tiles(S.base).addTo(fm);
    P().pieces.filter(p => p.role !== 'skip').forEach(p => { const ll = p.pts.map(q => [q[0], q[1]]); L.polyline(ll, { color: '#fff', weight: 6, interactive: false }).addTo(fm); L.polyline(ll, { color: (p.day || 1) % 2 ? RED : VIOLET, weight: 3, interactive: false }).addTo(fm); });
    const rect = L.rectangle(box(), { color: '#133C6A', weight: 3, fillColor: '#FEBC0E', fillOpacity: .12, interactive: false }).addTo(fm);
    const mk = L.marker([lat, lon], { draggable: true, icon: L.divIcon({ className: '', html: '<div class="fhandle">✥</div>', iconSize: [34, 34], iconAnchor: [17, 17] }) }).addTo(fm);
    mk.on('drag', () => { const p = mk.getLatLng(); lat = +p.lat.toFixed(6); lon = +p.lng.toFixed(6); moved = true; rect.setBounds(box()); });
    fm.fitBounds(L.latLngBounds(box()).pad(0.45), { animate: false }); setTimeout(() => fm.invalidateSize(), 60);
    const sel = $('#fscale', v); if (sel) sel.onchange = () => { scale = +sel.value; rect.setBounds(box()); fm.fitBounds(L.latLngBounds(box()).pad(0.45)); };
    const done = async adj => { fm.remove(); close(); if (adj) sheets.adjust[s.id] = adj; else delete sheets.adjust[s.id]; dirty(); $$('.light').forEach(x => x.remove()); await build([s.id]); toast('Пересобираю лист с новой рамкой'); };
    $('[data-x]', v).onclick = () => { fm.remove(); close(); };
    $('[data-auto]', v).onclick = () => done(null);
    $('[data-ok]', v).onclick = () => { const a = {}; if (moved) { a.lat = lat; a.lon = lon; } if (scale) a.scale = scale; done(Object.keys(a).length ? a : null); };
  });
}
function newType(q) {
  const cols = ['#C62828', '#EF6C00', '#F9A825', '#2E7D32', '#00838F', '#1565C0', '#4527A0', '#AD1457', '#5D4037', '#37474F'];
  modal(`<h2>Свой тип точки</h2><form class="sec" id="nt"><label class="field"><span>Как назвать (так будет в легенде)</span><input name="title" type="text" maxlength="50" required placeholder="например: ягодник"></label>
    <label class="field"><span>Буква или знак на карте</span><input name="letter" type="text" maxlength="2" required placeholder="Я" style="width:90px"></label>
    <div class="field"><span>Цвет</span><div class="swatches">${cols.map((c, i) => `<button type="button" class="${i === 5 ? 'on' : ''}" data-col="${c}" style="background:${c}" aria-label="${c}"></button>`).join('')}</div></div>
    <div class="acts"><button type="button" class="btn ghost" data-x>Отмена</button><button class="btn go">Добавить</button></div></form>`, (v, close) => {
    let col = cols[5];
    $$('.swatches button', v).forEach(b => b.onclick = () => { $$('.swatches button', v).forEach(x => x.classList.remove('on')); b.classList.add('on'); col = b.dataset.col; });
    $('[data-x]', v).onclick = () => { close(); paintPanel(); };
    $('#nt', v).onsubmit = e => {
      e.preventDefault(); const id = 'c_' + Math.random().toString(16).slice(2, 10).padEnd(8, '0');
      P().custom_types.push({ id, title: e.target.title.value.trim(), letter: e.target.letter.value.trim(), color: col });
      q.type = id; delete q.guess; dirty(); close(); paintPanel(); drawPoints();
    };
  });
}

// ---------------------------------------------------------------- шаг 4: карты
const SHEET_OPTS = [['region', 'Район похода', 'мелкая карта: где это вообще'], ['overview', 'Обзорная карта', 'вся нитка, ночёвки, перевалы по номерам'], ['profile', 'Высотный профиль маршрута', 'все дни на одном графике'],
  ['work', 'Рабочая карта', 'листы по ходу маршрута с сеткой'], ['days', 'Карты дней с профилем', 'по листу на каждый ходовой день']];
function stepMaps() {
  const pr = P(); const sh = pr.sheets || {}; const b = S.build || { state: 'none', sheets: [] };
  const running = ['running', 'queued'].includes(b.state); const sheets = b.sheets || []; const ok = sheets.filter(s => s.state === 'ok');
  const hasTrack = pr.pieces.some(p => p.role !== 'skip');
  const left = b.expires ? Math.max(0, Math.ceil((b.expires * 1000 - Date.now()) / 864e5)) : 0;
  const pct = b.total ? Math.round(100 * b.done / b.total) : (b.state === 'running' ? 3 : 0);
  const sm = b.summary;
  return `<div class="build"><section class="panel">
      <h2>Карты</h2><p>Что собрать и как подписать. Всё рисуется в 300 dpi — для печати в масштабе 100 %.</p>
      <label class="field"><span>Район — для заголовков карт</span><input type="text" data-c="area" value="${esc(pr.meta.area || '')}" maxlength="80" placeholder="например: Сванетия, Центральный Кавказ"></label>
      <div class="sec"><h3>Листы</h3>${SHEET_OPTS.map(([id, t, s]) => `<label class="switch"><input type="checkbox" data-c="sheet" data-f="${id}" ${sh[id] !== false ? 'checked' : ''}><span><b>${t}</b><div class="hint">${s}</div></span></label>`).join('')}</div>
      <div class="field"><span>Масштаб рабочей карты</span><div class="chips">${[[100000, '1:100 000'], [50000, '1:50 000']].map(([v, t]) => `<button class="chip${(sh.work_scale || 100000) === v ? ' on' : ''}" data-a="wscale" data-v="${v}">${t}</button>`).join('')}</div></div>
      <div class="field"><span>Основа</span><div class="chips">${[['otm', 'OpenTopoMap'], ['gsh', 'Генштаб и ГГЦ, где есть']].map(([v, t]) => `<button class="chip${(sh.base || 'otm') === v ? ' on' : ''}" data-a="sbase" data-v="${v}">${t}</button>`).join('')}</div>
        <div class="hint">OpenTopoMap есть везде. Генштаб подробнее в горах, но покрывает не всё — где его нет, подставится OpenTopoMap.</div></div>
      ${pr.points.some(q => q.type === 'photo') ? `<label class="switch"><input type="checkbox" data-c="sheet" data-f="photos" ${sh.photos !== false ? 'checked' : ''}>Фототочки на картах дней и рабочих листах</label>` : ''}
      <label class="switch logo-opt"><input type="checkbox" data-c="sheet" data-f="logo" ${sh.logo !== false ? 'checked' : ''}><img src="/static/img/logo-s.png" alt=""><span><b>Логотип «Восхождения»</b><div class="hint">в правом нижнем углу каждого листа</div></span></label>
      <div class="next" style="position:static"><button class="btn" data-a="step" data-step="points">← Точки</button></div>
    </section>
    <section class="out">
      <h2 class="out-title">${running ? 'Собираю…' : (ok.length ? 'Готовые листы' : 'Сборка')}</h2>
      <div class="out-acts">${ok.length && !running ? `<a class="btn go big" href="/api/projects/${S.pid}/zip?fmt=png" download>${I.dl}Скачать всё архивом</a>
          <a class="btn sm" href="/api/projects/${S.pid}/zip?fmt=jpg" download title="То же, но в JPG — файлы легче">Архив в JPG</a><a class="btn sm" href="/files/${S.pid}/karty.pdf?dl=1" title="Все листы одним PDF">PDF</a><span class="sp"></span>
          <button class="btn" data-a="build" ${!hasTrack ? 'disabled' : ''}>Пересобрать</button>`
        : `<button class="btn go big" data-a="build" ${running || !hasTrack ? 'disabled' : ''}>${running ? 'Собираю…' : 'Собрать карты'}</button>`}
        <button class="btn sm only-m" data-a="tosettings">Настройки листов ↓</button></div>
      ${!hasTrack ? '<div class="banner">Сначала загрузи трек — без нитки рисовать нечего.</div>' : ''}
      ${running ? '' : todoBlock(todos(), 'Перед сборкой стоит проверить')}
      ${running ? `<div class="prog"><div class="stage">${esc(b.state === 'queued' ? (b.queue ? `В очереди, перед тобой ${b.queue}` : 'Вот-вот начну') : (b.stage || 'Начинаю'))}</div>
        <div class="hint">${b.total ? `${b.done} из ${b.total} · обычно это минута-две, страницу можно закрыть — сборка не прервётся` : 'Считаю высоты вдоль нитки…'}</div>
        <div class="trail"><div class="ln"></div><div class="fill" style="width:${pct}%"></div>${sheets.filter(s => s.state !== 'skip').map((s, i, a) => `<i class="tent ${s.state === 'ok' ? 'ok' : s.state === 'err' ? 'err' : ''}" style="left:${(i + 1) / a.length * 100}%"></i>`).join('')}
          <div class="walker" style="left:${pct}%"><img src="/static/img/logo-s.png" alt=""></div></div></div>` : ''}
      ${b.state === 'error' ? `<div class="banner"><b>Не получилось собрать.</b> ${esc(b.error || '')}</div>` : ''}
      ${b.state === 'done' && ok.length ? `<div class="hint">Файлы хранятся ${S.cfg.keep_days} дней — осталось ${left} ${plural(left, 'день', 'дня', 'дней')}. Сам поход остаётся, пересобрать можно когда угодно. В архиве — все листы в PNG (без потерь, для вставки в отчёт) и общий PDF.</div>` : ''}
      ${b.state === 'none' && hasTrack ? `<div class="banner blue"><b>Всё готово к сборке.</b> Нажми «Собрать карты» — листы будут появляться здесь по одному.</div>` : ''}
      <div class="gal">${sheets.map((s, i) => s.state === 'ok' ? `<div class="sheet ok" data-a="view" data-i="${i}" style="animation-delay:${running ? 0 : Math.min(i, 12) * 35}ms"><div class="pic"><img loading="lazy" src="/files/${S.pid}/${s.file.replace('.png', '.thumb.jpg')}?v=${s.ver || 0}" alt=""></div>
          <div class="cap"><b>${esc(s.title)}</b><div class="s">${km(s.w_cm)} × ${km(s.h_cm)} см${s.note ? ' · ' + esc(s.note) : ''}</div>
          <div class="dl"><a href="/files/${S.pid}/${s.file}?dl=1" data-stop>PNG</a><a href="/files/${S.pid}/${s.file.replace('.png', '.jpg')}?dl=1" data-stop>JPG</a>${s.frame ? `<a href="#" data-stop data-a="frame" data-i="${i}" title="Сдвинуть рамку или поменять масштаб этого листа">Рамка</a>` : ''}</div></div></div>`
        : `<div class="sheet ${s.state === 'run' ? 'run' : ''}"><div class="pic"><div class="wait">${s.state === 'run' ? 'рисую' : s.state === 'err' ? 'ошибка' : 'ждёт'}</div></div><div class="cap"><b>${esc(s.title)}</b><div class="s">${s.state === 'err' ? esc(s.error || '') : '&nbsp;'}</div></div></div>`).join('')}</div>
      ${sm && sm.days ? `<div class="sec"><h3>Сводка по дням <span class="hint" style="text-transform:none;letter-spacing:0;font:400 13.5px var(--body)">— для графика движения</span></h3>
        <div class="tblwrap"><table class="sum"><thead><tr><th>День</th><th>Дата</th><th class="n">Км</th><th class="n">Набор</th><th class="n">Сброс</th><th class="n">Высоты, м</th><th class="obj">${sm.days.some(d => (d.key_objects || []).length) ? 'Определяющие препятствия' : 'Препятствия'}</th></tr></thead><tbody>
        ${sm.days.map(d => `<tr><td>${d.n}</td><td>${d.date}</td><td class="n">${km(d.km)}</td><td class="n">+${d.up}</td><td class="n">−${d.dn}</td><td class="n">${d.zmin}–${d.zmax}</td><td>${esc(((d.key_objects || []).length ? d.key_objects : d.objects).join('; '))}</td></tr>`).join('')}
        <tr><td colspan="2"><b>Всего</b></td><td class="n"><b>${km(sm.km)}</b></td><td class="n"><b>+${sm.up}</b></td><td class="n"><b>−${sm.dn}</b></td><td></td><td class="hint">${sm.kcoef !== 1 ? `км с коэффициентом ${km(sm.kcoef)}; ` : ''}высоты — ${sm.elev === 'track' ? 'из трека' : 'по модели рельефа'}</td></tr></tbody></table></div>
        ${sm.camps.length ? `<div class="hint"><b>Ночёвки:</b> ${sm.camps.map(c => `${c.lab} — ${[c.date, c.name, c.h + ' м'].filter(Boolean).map(esc).join(', ')}`).join('; ')}</div>` : ''}</div>` : ''}
    </section></div>`;
}
let pollT = null;
function stopPoll() { clearInterval(pollT); pollT = null; }
function startPoll() {
  stopPoll();
  pollT = setInterval(async () => {
    if (!S.pid) return stopPoll();
    try {
      const b = await api('GET', `/api/projects/${S.pid}/status`, undefined, false, false); const was = JSON.stringify(S.build); S.build = b;
      if (!['running', 'queued'].includes(b.state)) { stopPoll(); if (b.state === 'done') toast('Карты готовы!'); }
      syncDl(); if (S.step === 'maps' && JSON.stringify(b) !== was) updateBuild();
    } catch (e) { /* подождём */ }
  }, 1300);
}
function updateBuild() {
  // перерисовываем только правую часть, чтобы не сбивать поля настроек
  const tmp = document.createElement('div'); tmp.innerHTML = stepMaps(); const o = $('.out'); const y = o ? o.scrollTop : 0;
  if (o) { o.replaceWith($('.out', tmp)); $('.out').scrollTop = y; } else paintPanel();
}
function lightbox(i) {
  const sheets = (S.build.sheets || []); const oks = sheets.map((s, k) => [s, k]).filter(([s]) => s.state === 'ok'); let pos = oks.findIndex(([, k]) => k === i);
  const el = document.createElement('div'); el.className = 'light';
  const show = () => { const s = oks[pos][0];
    el.innerHTML = `<header><b>${esc(s.title)}</b><span class="hint" style="color:#cfe0f2">${pos + 1} / ${oks.length}</span>${s.frame ? '<button class="btn ghost sm" data-l="frame">Рамка</button>' : ''}<button class="btn ghost sm" data-l="rebuild">Пересобрать этот лист</button><a class="btn ghost sm" href="/files/${S.pid}/${s.file}?dl=1">PNG</a><a class="btn ghost sm" href="/files/${S.pid}/${s.file.replace('.png', '.jpg')}?dl=1">JPG</a>
      <button class="icon-btn" data-l="prev" title="Назад">${I.back}</button><button class="icon-btn" data-l="next" title="Вперёд" style="transform:scaleX(-1)">${I.back}</button><button class="icon-btn" data-l="close" title="Закрыть">${I.x}</button></header>
      <div class="view"><img src="/files/${S.pid}/${s.file.replace('.png', '.prev.jpg')}?v=${s.ver || 0}" alt="${esc(s.title)}"></div>`; };
  const close = () => { el.remove(); document.removeEventListener('keydown', key); };
  const step = d => { pos = (pos + d + oks.length) % oks.length; show(); };
  const key = e => { if (e.key === 'Escape') close(); if (e.key === 'ArrowRight') step(1); if (e.key === 'ArrowLeft') step(-1); };
  el.addEventListener('click', e => { const b = e.target.closest('[data-l]');
    if (b) { const a = b.dataset.l; if (a === 'frame') { close(); frameEditor(oks[pos][0]); return; } if (a === 'close') close(); if (a === 'prev') step(-1); if (a === 'next') step(1); if (a === 'rebuild') { close(); build([oks[pos][0].id]); } return; }
    const v = e.target.closest('.view'); if (v && !e.target.closest('a')) v.classList.toggle('zoom'); });
  document.addEventListener('keydown', key); show(); document.body.append(el);
}
async function build(only) {
  await flush();
  try { const r = await api('POST', `/api/projects/${S.pid}/build`, { only }, false, 'Ставлю сборку в очередь…'); S.build = r.build; syncDl(); updateBuild(); startPoll(); } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- карта
let map = null, baseLayer = null, bandLayer = null, pieceLayer = null, pointLayer = null, markers = {}, lines = {};
let mapFitted = false;
function destroyMap() { if (map) { map.remove(); map = null; } markers = {}; lines = {}; mapFitted = false; }
// Слой карты. Если плитка не пришла (внешний сервер карты не ответил), просим её ещё до четырёх раз с паузами;
// на последней попытке сервер отдаёт увеличенный кусок более мелкого зума — лучше мутно, чем дыра.
function tiles(base, opts = {}) {
  const l = L.tileLayer(`/tiles/${base}/{z}/{x}/{y}.png?v=5`, { maxNativeZoom: { otm: 17, gsh100: 15, esri: 17 }[base] || 17, maxZoom: 18, minZoom: 3, ...opts });
  // тайла пока нет (OpenTopoMap дорисовывает крупный зум, сервер карты не ответил): сразу показываем увеличенный кусок
  // более мелкого зума, а сам тайл спрашиваем ещё раз через 5, 15 и 30 секунд
  l.on('tileerror', e => {
    const t = e.tile, src = t.getAttribute('src') || '';
    if (src.endsWith('&r=oz')) return;      // не нашлось и замены — ждём повтора
    const n = t._retry || 0; if (n >= 3) return; t._retry = n + 1;
    const url = src.replace(/&r=\w+$/, '');
    t.src = url + '&r=oz';
    setTimeout(() => { if (t.parentNode) t.src = url + '&r=' + (n + 1); }, [5000, 15000, 30000][n]);
  });
  return l;
}
function setBase() {
  if (baseLayer) baseLayer.remove();
  baseLayer = tiles(S.base, { attribution: S.base === 'otm' ? '© OpenTopoMap, OpenStreetMap' : S.base === 'esri' ? '© Esri' : 'Генштаб · nakarte.me' }).addTo(map);
}
function initMap() {
  destroyMap();
  map = L.map('map', { zoomControl: false, attributionControl: true }).setView([55.7, 37.6], 5);
  map.attributionControl.setPrefix(false);
  L.control.zoom({ position: 'bottomright' }).addTo(map);
  L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map);
  setBase(); bandLayer = L.layerGroup().addTo(map); pieceLayer = L.layerGroup().addTo(map); pointLayer = L.layerGroup().addTo(map);
  map.on('click', e => mapClick(e.latlng));
}
function hintOff() { const h = $('.maphint'); h && h.remove(); const m = $('#map'); if (m) m.style.cursor = ''; }
// Режимы карты: 'add' — поставить точку, 'cut' — разрезать нитку, 'band1'/'band2' — начало и конец участка
function setMode(m, text) {
  S.mode = m; hintOff(); if (m !== 'band2') S.bandA = m === 'band1' ? null : S.bandA;
  if (!m) { S.bandA = null; if (S.mapTemp) { S.mapTemp = null; applyMapSize(); } return; }
  const h = document.createElement('div'); h.className = 'maphint'; h.innerHTML = `${esc(text)} <button class="icon-btn" data-a="modeoff" title="Отмена">${I.x}</button>`;
  $('.mapwrap').append(h); $('#map').style.cursor = 'crosshair';
  if (isPhone() && mapSize() !== 'big') { S.mapTemp = 'big'; applyMapSize(); }   // на телефоне для клика по карте она разворачивается сама
}
function nearestOnTrack(latlng) {
  const cp = map.latLngToContainerPoint(latlng); let best = null;
  for (const p of P().pieces) {
    if (p.role === 'skip') continue;
    let a = map.latLngToContainerPoint([p.pts[0][0], p.pts[0][1]]);
    for (let i = 1; i < p.pts.length; i++) {
      const b = map.latLngToContainerPoint([p.pts[i][0], p.pts[i][1]]);
      const dx = b.x - a.x, dy = b.y - a.y, L2 = dx * dx + dy * dy;
      const t = L2 ? Math.max(0, Math.min(1, ((cp.x - a.x) * dx + (cp.y - a.y) * dy) / L2)) : 0;
      const x = a.x + dx * t, y = a.y + dy * t, d = (x - cp.x) ** 2 + (y - cp.y) ** 2;
      if (!best || d < best.d) best = { d, piece: p, x, y };
      a = b;
    }
  }
  if (!best || best.d > 42 * 42) return null;
  const ll = map.containerPointToLatLng([best.x, best.y]);
  return { piece: best.piece, lat: +ll.lat.toFixed(6), lon: +ll.lng.toFixed(6) };
}
async function mapClick(latlng) {
  if (!S.mode) return;
  if (S.mode === 'add') {
    const obst = S.newType === 'pass'; S.newType = null;
    const q = { id: Math.random().toString(16).slice(2, 10), type: obst ? 'pass' : 'mark', name: '', lat: +latlng.lat.toFixed(6), lon: +latlng.lng.toFixed(6), on_map: true, on_profile: obst, nights: 1 };
    P().points.push(q); setMode(null); S.selPoint = q.id; S.ptab = obst ? 'obst' : 'points'; S.filter = 'all'; S.q = ''; dirty(); paintPanel(); drawPoints(); scrollToPoint();
    return;
  }
  const n = nearestOnTrack(latlng);
  if (!n) return toast('Нажми прямо на нитку маршрута — мимо не считается', true);
  if (S.mode === 'cut') {
    setMode(null); await flush();
    try {
      const r = await api('POST', `/api/projects/${S.pid}/cut`, { piece: n.piece.id, lat: n.lat, lon: n.lon }, false, 'Режу нитку…');
      S.proj = r.project; toast(r.msg, !r.ok); if (r.ok) S.selPiece = r.new;
      paintPanel(); drawMap(); const row = $(`.pc[data-id="${S.selPiece}"]`); row && row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    } catch (e) { fail(e); }
  } else if (S.mode === 'band1') {
    S.bandA = [n.lat, n.lon]; setMode('band2', 'Теперь нажми на нитку в конце участка'); drawBands();
  } else if (S.mode === 'band2') {
    const b = { id: Math.random().toString(16).slice(2, 10), kind: 'nopath', a: S.bandA, b: [n.lat, n.lon] };
    if (!P().bands) P().bands = [];
    if (!bandInfo(b).path.length) { toast('Начало и конец должны быть в одном дне — попробуй ещё раз', true); return setMode('band1', 'Нажми на нитку в начале участка'); }
    P().bands.push(b); S.selBand = b.id; setMode(null); dirty(); paintPanel(); drawBands();
  }
}
// ---- участки с особым характером пути
const bandStyle = b => { const s = S.bands.find(x => x.id === b.kind); return s ? { title: s.title, color: s.color, hatch: s.hatch } : { title: b.title || 'особый участок', color: b.color || '#E6007A', hatch: false }; };
function dayChains() {
  const by = {};
  P().pieces.filter(p => p.role !== 'skip').sort((a, b) => a.order - b.order).forEach(p => { (by[p.day || 0] = by[p.day || 0] || []).push(...p.pts); });
  return by;
}
function bandInfo(b, chains = dayChains()) {
  const d2 = (p, q) => { const k = Math.cos(p[0] * Math.PI / 180); return (p[0] - q[0]) ** 2 + ((p[1] - q[1]) * k) ** 2; };
  const near = (pts, q) => { let bi = 0, bd = 1e9; for (let i = 0; i < pts.length; i++) { const d = d2(pts[i], q); if (d < bd) { bd = d; bi = i; } } return [bi, bd]; };
  let best = null;
  for (const [day, pts] of Object.entries(chains)) {
    const [i, di] = near(pts, b.a), [j, dj] = near(pts, b.b);
    if (!best || di + dj < best.cost) best = { cost: di + dj, day: +day, pts, i, j };
  }
  if (!best || best.cost > 2e-5 || best.i === best.j) return { path: [], day: 0, km: 0 };
  const fwd = best.i < best.j; const lo = Math.min(best.i, best.j), hi = Math.max(best.i, best.j);
  const path = [fwd ? b.a : b.b, ...best.pts.slice(lo + 1, hi).map(q => [q[0], q[1]]), fwd ? b.b : b.a];
  let len = 0; for (let i = 1; i < path.length; i++) len += L.latLng(path[i - 1]).distanceTo(path[i]);
  return { path, day: best.day, km: len / 1000 * (P().meta.kcoef || 1) };
}
function drawBands() {
  if (!bandLayer) return; bandLayer.clearLayers();
  const chains = dayChains();
  for (const b of P().bands || []) {
    const info = bandInfo(b, chains); if (!info.path.length) continue; const st = bandStyle(b); const sel = S.selBand === b.id;
    const l = L.polyline(info.path, { color: st.color, weight: sel ? 22 : 16, opacity: sel ? .75 : .5, lineCap: 'butt', dashArray: st.hatch ? '10 3' : null }).addTo(bandLayer);
    l.bindTooltip(`${st.title} · ${km(info.km)} км`, { sticky: true, className: 'tt' });
    l.on('click', ev => { L.DomEvent.stopPropagation(ev); if (S.mode) return mapClick(ev.latlng); S.selBand = b.id; S.selPoint = null; S.ptab = 'bands'; if (S.step !== 'points') go(`#/p/${S.pid}/points`); else paintPanel(); drawBands(); });
  }
  if (S.bandA) L.circleMarker(S.bandA, { radius: 8, color: '#133C6A', weight: 3, fillColor: '#FEBC0E', fillOpacity: 1, interactive: false }).addTo(bandLayer);
}
function drawMap(fit) { if (!map) return; drawBands(); drawPieces(); drawPoints(); if (fit) fitAll(); }
function fitAll() {
  const pts = P().pieces.filter(p => p.role !== 'skip').flatMap(p => p.pts.map(q => [q[0], q[1]]));
  const all = pts.length ? pts : P().points.map(q => [q.lat, q.lon]);
  if (all.length) { map.fitBounds(L.latLngBounds(all).pad(0.08), { animate: false }); mapFitted = true; }
}
function drawPieces() {
  pieceLayer.clearLayers(); lines = {};
  const draw = (p, style) => {
    const ll = p.pts.map(q => [q[0], q[1]]);
    L.polyline(ll, { color: '#fff', weight: (style.weight || 4) + 3.5, opacity: .9, interactive: false }).addTo(pieceLayer);
    const l = L.polyline(ll, style).addTo(pieceLayer);
    l.on('click', ev => { L.DomEvent.stopPropagation(ev); if (S.mode) return mapClick(ev.latlng); S.selPiece = p.id; if (S.step !== 'days') go(`#/p/${S.pid}/days`); else { paintPanel(); } drawPieces(); const r = $(`.pc[data-id="${p.id}"]`); r && r.scrollIntoView({ block: 'center', behavior: 'smooth' }); });
    l.bindTooltip(`${p.day ? 'День ' + p.day + ' · ' : ''}${p.name} · ${km(p.km || 0)} км`, { sticky: true, className: 'tt' });
    lines[p.id] = l;
  };
  const ps = P().pieces;
  ps.filter(p => p.role === 'skip').forEach(p => draw(p, { color: '#8A97A8', weight: 2, dashArray: '2 6', opacity: .7 }));
  ps.filter(p => p.role !== 'skip').forEach(p => draw(p, { color: !p.day && ps.some(x => x.day) ? '#F68A1D' : ((p.day || 1) % 2 ? RED : VIOLET), weight: S.selPiece === p.id ? 6.5 : 4, dashArray: p.role === 'radial' ? '8 7' : null }));
}
function drawPoints() {
  if (!pointLayer) return; pointLayer.clearLayers(); markers = {};
  const many = P().points.length > 400;
  for (const q of P().points) {
    if (q.type === 'skip' && S.selPoint !== q.id) continue;
    if (many && q.type === 'photo' && S.step !== 'points') continue;
    const sel = S.selPoint === q.id;
    const m = L.marker([q.lat, q.lon], { icon: L.divIcon({ className: '', html: `<div class="pm${sel ? ' sel' : ''}${q.on_map === false ? ' off' : ''}">${sym(q.type)}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] }), draggable: sel, zIndexOffset: sel ? 1000 : (q.type === 'photo' ? -100 : 0), keyboard: false });
    m.bindTooltip(ptTitle(q), { direction: 'top', offset: [0, -10], className: 'tt' });
    m.on('click', ev => { L.DomEvent.stopPropagation(ev); if (S.mode) return mapClick(ev.latlng); S.selPoint = q.id; S.selBand = null; if (!(S.ptab === 'obst' && S.obj.includes(q.type))) S.ptab = 'points'; if (S.step !== 'points') { go(`#/p/${S.pid}/points`); setTimeout(scrollToPoint, 60); } else { showPoint(q); paintPanel(); drawPoints(); scrollToPoint(); } });
    m.on('dragend', () => { const p = m.getLatLng(); q.lat = +p.lat.toFixed(6); q.lon = +p.lng.toFixed(6); dirty(); });
    m.addTo(pointLayer); markers[q.id] = m;
  }
}
function showPoint(q) {
  if ((S.filter !== 'all' && q.type !== S.filter && !(S.filter === 'guess' && q.guess)) || (S.folder && q.folder !== S.folder) || S.q) { S.filter = 'all'; S.folder = ''; S.q = ''; }
  const order = shownPoints(); if (order.indexOf(q) > 290) S.filter = q.type;
}
function scrollToPoint() { const r = $(`#pt-${S.selPoint}`); r && r.scrollIntoView({ block: 'start', behavior: 'smooth' }); }

// ---------------------------------------------------------------- действия
const pieceById = id => P().pieces.find(p => p.id === id);
const pointById = id => P().points.find(p => p.id === id);
const A = {
  home() { go('#/'); },
  admin() { go('#/admin'); },
  async logout() { await flush(); try { await api('POST', '/api/logout'); } catch (e) { /* ок */ } S.user = null; S.proj = null; S.pid = null; go('#/'); render(); },
  new: newProject,
  open(el) { go(`#/p/${el.dataset.id}/tracks`); },
  menu(el, ev) {
    ev.stopPropagation(); $$('.menu').forEach(m => m.remove());
    const id = el.dataset.id, p = S.projects.find(x => x.id === id); const m = document.createElement('div'); m.className = 'menu';
    m.innerHTML = '<button data-m="open">Открыть</button>' + (p.build && p.build.state === 'done' && p.build.n ? '<button data-m="zip">Скачать всё архивом</button>' : '') + '<button data-m="maps">Сразу к картам</button><button data-m="copy">Сделать копию</button><button data-m="del" class="warn">Удалить</button>';
    const r = el.getBoundingClientRect(); m.style.top = r.bottom + 6 + 'px'; m.style.left = Math.min(r.left, innerWidth - 210) + 'px'; document.body.append(m);
    const off = () => { m.remove(); document.removeEventListener('click', off); }; setTimeout(() => document.addEventListener('click', off), 0);
    m.onclick = async e => { const a = e.target.dataset.m; if (!a) return;
      if (a === 'zip') location.href = `/api/projects/${id}/zip?fmt=png`;
      if (a === 'open') go(`#/p/${id}/tracks`); if (a === 'maps') go(`#/p/${id}/maps`);
      if (a === 'copy') { try { await api('POST', `/api/projects/${id}/copy`, undefined, false, 'Делаю копию…'); await loadProjects(); renderHome(); toast('Копия готова'); } catch (er) { fail(er); } }
      if (a === 'del') confirmBox('Удалить поход?', `«${esc(p.title)}» исчезнет вместе с треками и собранными картами. Вернуть будет нельзя.`, 'Удалить', async () => { try { await api('DELETE', `/api/projects/${id}`); await loadProjects(); renderHome(); toast('Поход удалён'); } catch (er) { fail(er); } }); };
  },
  step(el) { go(`#/p/${S.pid}/${el.dataset.step}`); },
  base(el) {
    const order = ['otm', 'gsh100', 'esri'];
    S.base = el.dataset.base === S.base && isPhone() ? order[(order.indexOf(S.base) + 1) % order.length] : el.dataset.base; localStorage.nitkaBase = S.base; setBase(); $$('.mapbar .chip').forEach(c => c.classList.toggle('on', c.dataset.base === S.base)); },
  'src-del'(el) { const s = P().sources.find(x => x.id === el.dataset.id);
    confirmBox('Убрать файл?', `Все треки и точки из «${esc(s.name)}» уйдут из похода — вместе с правками, которые ты в них сделал.`, 'Убрать', async () => { await flush(); try { S.proj = (await api('POST', `/api/projects/${S.pid}/source-delete`, { id: s.id })).project; S.lastInfo = null; paintPanel(); drawMap(true); } catch (e) { fail(e); } }); },
  piece(el, ev) { if (ev.target.closest('input,select,button')) return; S.selPiece = el.dataset.id; $$('.pc').forEach(r => r.classList.toggle('sel', r.dataset.id === S.selPiece)); drawPieces(); const l = lines[S.selPiece]; l && map.fitBounds(l.getBounds().pad(0.25)); },
  pmove(el) { const p = pieceById(el.dataset.id); const sib = P().pieces.filter(x => x.day === p.day && (x.role === 'skip') === (p.role === 'skip')).sort((a, b) => a.order - b.order); const i = sib.indexOf(p), j = i + +el.dataset.d;
    if (j < 0 || j >= sib.length) return; [p.order, sib[j].order] = [sib[j].order, p.order]; if (p.order === sib[j].order) p.order += +el.dataset.d * .5; dirty(); paintPanel(); },
  kcoef(el) { P().meta.kcoef = +el.dataset.v; dirty(); paintPanel(); },
  async 'split-camps'() { await flush(); try { const r = await api('POST', `/api/projects/${S.pid}/split-by-camps`, undefined, false, 'Режу нитку по ночёвкам…'); S.proj = r.project; toast(r.msg, !r.ok); paintPanel(); drawMap(); } catch (e) { fail(e); } },
  'each-day'() { const M = daysModel(); let n = M.max; M.loose.forEach(p => { p.day = ++n; }); dirty(); paintPanel(); drawPieces(); },
  filter(el) { S.filter = el.dataset.f; S.selPoint = null; paintPanel(true); drawPoints(); },
  psel(el, ev) { if (ev.target.closest('[data-a="peye"]')) return A.peye(ev.target.closest('[data-a="peye"]'));
    const id = el.dataset.id; const was = S.selPoint; S.selPoint = S.selPoint === id ? null : (id || null); paintPanel(); drawPoints(); const q = pointById(S.selPoint);
    if (q) { map.setView([q.lat, q.lon], Math.max(map.getZoom(), 13)); scrollToPoint(); } else if (was && !id) toast('Сохранено'); },
  peye(el) { const q = pointById(el.dataset.id); q.on_map = q.on_map === false; dirty(); paintPanel(); drawPoints(); },
  pdel(el) { P().points = P().points.filter(q => q.id !== el.dataset.id); S.selPoint = null; dirty(); paintPanel(); drawPoints(); },
  padd() { S.newType = null; setMode('add', 'Нажми на карту — там появится точка'); },
  modeoff() { setMode(null); drawBands(); },
  mapsize(el) { applyMapSize(el.dataset.s); },
  shiftdays(el) { const n = +el.dataset.n; P().pieces.forEach(p => { if (p.day) p.day = Math.max(1, p.day - n); });
    P().points.forEach(q => { if (q.after_day != null) q.after_day = Math.max(0, q.after_day - n); if (q.type === 'photo' && q.day) q.day = Math.max(1, q.day - n); });
    dirty(); paintPanel(true); drawPieces(); toast(`Нумерация сдвинута: день ${n + 1} стал первым`); },
  cutmode() { setMode('cut','Нажми на нитку там, где её разрезать'); },
  ptab(el) { S.ptab = el.dataset.t; S.selPoint = null; S.selBand = null; paintPanel(true); drawPoints(); drawBands(); },
  badd() { S.selBand = null; setMode('band1', 'Нажми на нитку в начале участка'); paintPanel(); drawBands(); },
  bsel(el) { const id = el.dataset.id; const was = S.selBand; S.selBand = S.selBand === id ? null : (id || null); paintPanel(); drawBands();
    const b = (P().bands || []).find(x => x.id === S.selBand);
    if (b) { const i = bandInfo(b); if (i.path.length) map.fitBounds(L.latLngBounds(i.path).pad(0.6)); const r = $(`#bd-${b.id}`); r && r.scrollIntoView({ block: 'start', behavior: 'smooth' }); } else if (was && !id) toast('Сохранено'); },
  bdel(el) { P().bands = (P().bands || []).filter(b => b.id !== el.dataset.id); S.selBand = null; dirty(); paintPanel(); drawBands(); },
  bcol(el) { const b = P().bands.find(x => x.id === el.dataset.id); b.color = el.dataset.col; dirty(); paintPanel(); drawBands(); },
  lpos(el) { const q = pointById(el.dataset.id); q.label_pos = el.dataset.v || null; dirty(); $$('.compass button').forEach(b => b.classList.toggle('on', (b.dataset.v || '') === (q.label_pos || ''))); },
  frame(el, ev) { ev.stopPropagation(); frameEditor(S.build.sheets[+el.dataset.i]); },
  grade(el) { const q = pointById(el.dataset.id); q.grade = el.dataset.g || null; dirty(); paintPanel(); },
  okey(el) { const q = pointById(el.dataset.id); q.key = !q.key; dirty(); paintPanel(); },
  todo(el) {
    const t = todos()[+el.dataset.i]; if (!t) return;
    if (t.fix === 'camps') return fixCamps();
    if (t.fix === 'campyear') {      // год ночёвки сильно не совпадает с годом похода — ставим год похода (а если даты на стыке лет — ближайший)
      const y0 = +P().meta.start_date.slice(0, 4); let n = 0;
      for (const q of P().points) if (q.type === 'camp' && q.date && Math.abs(+q.date.slice(0, 4) - y0) > 1) {
        const md = q.date.slice(4), alt = [y0, y0 + 1].map(y => y + md).sort((a, b) => Math.abs(Date.parse(a) - Date.parse(P().meta.start_date)) - Math.abs(Date.parse(b) - Date.parse(P().meta.start_date)));
        q.date = alt[0]; n++;
      }
      dirty(); paintPanel(); drawMap(); return toast(`Год поправлен у ${n} ${plural(n, 'ночёвки', 'ночёвок', 'ночёвок')}. Пересобери карты, чтобы номера ночёвок встали по порядку`);
    }
    if (t.fix === 'order') return fixOrder();
    const jump = S.step !== t.step; S.selPoint = null; S.selBand = null;
    if (t.tab) S.ptab = t.tab; if (t.filter) S.filter = t.filter;
    if (jump) go(`#/p/${S.pid}/${t.step}`); else { paintPanel(true); drawPoints(); }
    if (t.run) setTimeout(() => A.sugg({ dataset: { w: t.run } }), jump ? 600 : 60);
  },
  async sugg(el) {
    const what = el.dataset.w; await flush(); el.disabled = true;
    let items;
    try { items = (await api('POST', `/api/projects/${S.pid}/suggest`, { what }, false, what === 'passes' ? 'Сверяю нитку с каталогом перевалов…' : 'Смотрю, где заканчиваются дни…')).items; } catch (e) { el.disabled = false; return fail(e); }
    el.disabled = false;
    if (!items.length) {
      if (what === 'passes' && !P().meta.no_passes) { P().meta.no_passes = true; dirty(); paintPanel(); }      // искали — рядом с ниткой ничего нет: больше не напоминаем
      return toast(what === 'camps' ? 'Все ночёвки в концах дней уже отмечены' : 'В каталоге нет перевалов у нитки, которых ещё нет в походе');
    }
    const dmy = d => d ? d.slice(8, 10) + '.' + d.slice(5, 7) : '';
    const row = (o, i) => what === 'camps'
      ? `<label class="srow"><input type="checkbox" data-i="${i}" checked><span class="ico">${sym('camp')}</span><span><div class="t">${o.date ? 'Ночёвка ' + dmy(o.date) : 'Ночёвка после дня ' + o.after_day}${o.nights > 1 ? ` · ${o.nights} ночи` : ''}</div><div class="s">${esc(o.note)}</div></span></label>`
      : `<label class="srow"><input type="checkbox" data-i="${i}" ${o.sure ? 'checked' : ''}><span class="ico">${gbadge(o.grade)}</span><span><div class="t">${esc(o.name)}${o.h ? `, ${o.h} м` : ''}</div><div class="s">день ${o.day} · ${o.sure ? 'на нитке' : 'в ' + o.dist + ' м от нитки'}${o.alt ? ' · ' + esc(o.alt) : ''}${o.note ? ' · ' + esc(o.note) : ''}</div></span></label>`;
    modal(`<h2>${what === 'camps' ? 'Ночёвки по концам дней' : 'Перевалы и вершины по маршруту'}</h2>
      <div class="hint">${what === 'camps' ? 'Ночёвка ставится там, где кончается трек дня. Потом её можно подвинуть на карте и подписать.' : 'По каталогу перевалов Вестры. Отмечены те, через которые нитка проходит; те, что в стороне, — на твой выбор. Названия, категории и высоты потом можно поправить.'}</div>
      <div class="slist">${items.map(row).join('')}</div>
      <div class="acts"><button class="btn ghost" data-all>Снять все</button><span style="flex:1"></span><button class="btn ghost" data-x>Отмена</button><button class="btn go" data-ok>Добавить</button></div>`, (v, close) => {
      $('.modal', v).classList.add('wide');
      const boxes = () => $$('.slist input', v); const upd = () => { const n = boxes().filter(b => b.checked).length; $('[data-ok]', v).textContent = n ? `Добавить (${n})` : 'Добавить'; $('[data-ok]', v).disabled = !n; $('[data-all]', v).textContent = n ? 'Снять все' : 'Отметить все'; };
      v.addEventListener('change', upd); upd();
      $('[data-all]', v).onclick = () => { const any = boxes().some(b => b.checked); boxes().forEach(b => { b.checked = !any; }); upd(); };
      $('[data-x]', v).onclick = close;
      $('[data-ok]', v).onclick = () => {
        const chosen = boxes().filter(b => b.checked).map(b => items[+b.dataset.i]);
        for (const o of chosen) {
          const id = Math.random().toString(16).slice(2, 10);
          if (what === 'camps') P().points.push({ id, type: 'camp', name: '', lat: o.lat, lon: o.lon, date: o.date, nights: o.nights, after_day: o.date ? null : o.after_day, on_map: true, on_profile: true, folder: 'подсказка: концы дней' });
          else P().points.push({ id, type: o.type, name: o.name, grade: o.grade || null, h: o.h || null, lat: o.lat, lon: o.lon, on_map: true, on_profile: true, nights: 1, note: o.note, folder: 'каталог перевалов', raw: o.name });
        }
        close(); dirty(); paintPanel(); drawPoints(); toast(`Добавлено: ${chosen.length}. Проверь названия и высоты.`);
      };
    });
  },
  oadd() { S.newType = 'pass'; setMode('add', 'Нажми на карту — там появится препятствие'); },
  bulk(el) { const m = el.dataset.m; const inF = q => q.folder === S.folder;
    if (m === 'del') return confirmBox('Удалить все точки папки?', `Из похода уйдут все точки папки «${esc(S.folder)}».`, 'Удалить', () => { P().points = P().points.filter(q => !inF(q)); S.folder = ''; dirty(); paintPanel(); drawPoints(); });
    P().points.filter(inF).forEach(q => { q.on_map = m === 'show'; }); dirty(); paintPanel(); drawPoints(); },
  wscale(el) { P().sheets.work_scale = +el.dataset.v; dirty(); paintPanel(); },
  sbase(el) { P().sheets.base = el.dataset.v; dirty(); paintPanel(); },
  build() { build(); },
  tosettings() { const p = $('.build .panel'); p && p.scrollIntoView({ behavior: 'smooth' }); },
  view(el, ev) { if (ev.target.closest('[data-stop]')) return; lightbox(+el.dataset.i); },
};
const C = {
  title(el) { P().meta.title = el.value; document.title = (el.value || 'Поход') + ' — Нитка'; dirty(); },
  start(el) { P().meta.start_date = el.value || null; dirty(); paintPanel(); },
  kcoef(el, final) { const v = Math.min(3, Math.max(1, +String(el.value).replace(',', '.') || 1)); P().meta.kcoef = v; dirty(); if (final) paintPanel(); },
  oneway(el) { P().meta.radial_one_way = el.checked; dirty(); paintPanel(); },
  pday(el, final) { const p = pieceById(el.dataset.id); const v = parseInt(el.value, 10); p.day = v >= 1 ? v : null; dirty(); if (final) { paintPanel(); drawPieces(); } },
  prole(el) { pieceById(el.dataset.id).role = el.value; dirty(); paintPanel(); drawPieces(); },
  q(el) { S.q = el.value; const y = $('#panel').scrollTop; const list = $('#plist'); const tmp = document.createElement('div'); tmp.innerHTML = stepPoints(); list.replaceWith($('#plist', tmp)); $('#panel').scrollTop = y; },
  folder(el) { S.folder = el.value; paintPanel(); },
  ptype(el) { const q = pointById(el.dataset.id); if (el.value === '__new') return newType(q);
    q.type = el.value; delete q.guess; if (q.type === 'camp' && q.on_profile === undefined) q.on_profile = true; if (S.obj.includes(q.type) || q.type === 'camp') q.on_profile = true;
    if (S.filter !== 'all' && S.filter !== q.type) S.filter = 'all'; dirty(); paintPanel(); drawPoints(); },
  pf(el, final) { const q = pointById(el.dataset.id); const f = el.dataset.f; let v = el.value;
    if (['h', 'day', 'nights', 'after_day'].includes(f)) v = v === '' ? null : parseInt(v, 10); if (f === 'nights') v = Math.max(1, v || 1);
    if (f === 'nums') v = v.split(/[,;\s]+/).map(x => parseInt(x, 10)).filter(x => x > 0); if (f === 'date') v = v || null;
    q[f] = v; dirty(); if (final) { const r = $(`#pt-${q.id}`); if (r) { $('.t', r).innerHTML = esc(ptTitle(q)); $('.s', r).textContent = ptSub(q); } const m = markers[q.id]; m && m.setTooltipContent(ptTitle(q)); } },
  pflag(el) { const q = pointById(el.dataset.id); q[el.dataset.f] = el.checked; dirty(); if (el.dataset.f === 'on_map') { drawPoints(); const r = $(`#pt-${q.id}`); r && r.classList.toggle('off', !el.checked); } },
  bkind(el) { const b = P().bands.find(x => x.id === el.dataset.id); b.kind = el.value; if (b.kind === 'custom' && !b.color) b.color = BCOLS[0]; dirty(); paintPanel(); drawBands(); },
  btitle(el, final) { const b = P().bands.find(x => x.id === el.dataset.id); b.title = el.value; dirty(); if (final) { paintPanel(); drawBands(); } },
  area(el) { P().meta.area = el.value; dirty(); },
  sheet(el) { P().sheets[el.dataset.f] = el.checked; dirty(); },
};
document.addEventListener('click', e => {
  const b = e.target.closest('button, .btn, .chip'); if (b) { lastBtn = b; lastBtnAt = Date.now(); }
  const a = e.target.closest('a[href]'); const h = a ? a.getAttribute('href') : '';
  if (h.includes('/zip?')) toast('Собираю архив — скачивание начнётся через несколько секунд, не закрывай страницу');
  else if (/\.jpg\?dl=1$/.test(h)) toast('Готовлю JPG — скачивание начнётся через пару секунд');
}, true);
document.addEventListener('submit', e => { const b = e.target.querySelector('button:not([type=button])'); if (b) { lastBtn = b; lastBtnAt = Date.now(); } }, true);
app.addEventListener('click', e => { const el = e.target.closest('[data-a]'); if (el && A[el.dataset.a]) { if (el.tagName === 'A') e.preventDefault(); A[el.dataset.a](el, e); } });
app.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('.card[data-a]')) A.open(e.target); if (e.key === 'Enter' && e.target.matches('input.title')) e.target.blur(); });
app.addEventListener('input', e => { const el = e.target.closest('[data-c]'); if (el && C[el.dataset.c] && el.matches('input[type=text],input[type=number],input.title,input:not([type])')) C[el.dataset.c](el, false); });
app.addEventListener('change', e => { const el = e.target.closest('[data-c]'); if (el && C[el.dataset.c]) C[el.dataset.c](el, true); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && S.mode) { setMode(null); drawBands(); } });
boot();
// кнопка-подсказка «ещё ниже» живёт вне приложения, чтобы не перерисовываться вместе с панелью
{
  const ld = document.createElement('div'); ld.id = 'load'; ld.hidden = true; ld.setAttribute('role', 'status'); ld.setAttribute('aria-live', 'polite');
  ld.innerHTML = '<i class="lbar"></i><div class="lpill"><img src="/static/img/logo-s.png" alt=""><span>Секунду…</span></div>'; document.body.append(ld);
  const b = document.createElement('button'); b.id = 'more'; b.hidden = true; b.type = 'button'; b.textContent = 'ещё ниже ↓';
  b.addEventListener('click', () => { const el = scroller(); el && el.scrollBy({ top: el.clientHeight * 0.75, behavior: 'smooth' }); });
  document.body.append(b);
}
