/* Anthropic Manager — app-shell: auth bootstrap, sidebar, fejléc, modal */

let currentUser = null;
let _bootstrapResolve;
const bootstrapReady = new Promise((r) => { _bootstrapResolve = r; });

const NAV = [
  { section: 'Elemzés' },
  { href: '/', icon: 'dashboard', label: 'Áttekintés' },
  { href: '/usage', icon: 'data_usage', label: 'Használat' },
  { href: '/cost', icon: 'payments', label: 'Költség' },
  { href: '/claude-code', icon: 'terminal', label: 'Claude Code' },
  { href: '/sync', icon: 'sync', label: 'Gyűjtés' },
  { section: 'Adminisztráció', admin: true },
  { href: '/admin-keys', icon: 'vpn_key', label: 'Admin kulcsok', admin: true },
  { href: '/pricing', icon: 'sell', label: 'Árjegyzék', admin: true },
  { href: '/users', icon: 'group', label: 'Felhasználók', admin: true },
  { href: '/settings', icon: 'settings', label: 'Beállítások', admin: true },
  { section: 'Egyéb' },
  { href: '/activity-log', icon: 'history', label: 'Tevékenységnapló' },
];

async function bootstrapApp() {
  try {
    const status = await api.authStatus();
    if (status.needs_setup) { window.location.href = '/setup'; return; }
    if (!status.authenticated) {
      window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
      return;
    }
    currentUser = status.user;
    renderSidebar();
    renderHeader();
    renderFooter();
    _bootstrapResolve(true);
    if (typeof window.pageInit === 'function') {
      try { await window.pageInit(); }
      catch (e) { console.error(e); toast(e.message || 'Hiba az oldal betöltésekor', 'error'); }
    }
  } catch (e) {
    console.error('Bootstrap hiba:', e);
    _bootstrapResolve(false);
  }
}

function renderSidebar() {
  const el = document.getElementById('app-sidebar');
  if (!el) return;
  const path = window.location.pathname;
  const isAdmin = currentUser && currentUser.role === 'admin';
  let html = `<div class="brand"><span class="logo">A</span><span>Anthropic Manager</span></div><nav>`;
  for (const item of NAV) {
    if (item.admin && !isAdmin) continue;
    if (item.section) { html += `<div class="nav-section">${escapeHtml(item.section)}</div>`; continue; }
    const active = path === item.href ? ' active' : '';
    html += `<a class="nav-item${active}" href="${item.href}"><span class="material-icons">${item.icon}</span><span>${escapeHtml(item.label)}</span></a>`;
  }
  html += `</nav>`;
  el.innerHTML = html;
}

function renderHeader() {
  const el = document.getElementById('app-header');
  if (!el) return;
  const title = document.querySelector('meta[name="page-title"]')?.content || 'Anthropic Manager';
  const initials = ((currentUser && currentUser.name) || '?').trim().charAt(0).toUpperCase();
  el.innerHTML = `
    <div class="row">
      <button class="menu-toggle btn btn-ghost"><span class="material-icons">menu</span></button>
      <span class="page-title">${escapeHtml(title)}</span>
    </div>
    <div class="right">
      <span id="env-badge"></span>
      <div class="user-menu" id="user-menu">
        <div class="trigger">
          <span class="avatar">${escapeHtml(initials)}</span>
          <span class="material-icons" style="font-size:18px;color:var(--muted)">expand_more</span>
        </div>
        <div class="dropdown" id="user-dropdown">
          <div class="info"><div class="name">${escapeHtml((currentUser && currentUser.name) || '')}</div><div class="email">${escapeHtml((currentUser && currentUser.email) || '')}</div></div>
          <button id="btn-change-password"><span class="material-icons" style="font-size:17px">lock</span> Jelszó módosítása</button>
          <button id="btn-logout"><span class="material-icons" style="font-size:17px">logout</span> Kijelentkezés</button>
        </div>
      </div>
    </div>`;
  loadEnvBadge();
  // Event listenerek JS-ből (NEM inline onclick) — a szigorú CSP a script-src-ben nincs
  // 'unsafe-inline', ezért az inline handlereket a böngésző blokkolná.
  el.querySelector('.menu-toggle')?.addEventListener('click',
    () => document.getElementById('app-sidebar')?.classList.toggle('open'));
  el.querySelector('#user-menu .trigger')?.addEventListener('click',
    () => document.getElementById('user-dropdown')?.classList.toggle('open'));
  el.querySelector('#btn-change-password')?.addEventListener('click', openChangePassword);
  el.querySelector('#btn-logout')?.addEventListener('click', doLogout);
  document.addEventListener('click', (e) => {
    const menu = document.getElementById('user-menu');
    if (menu && !menu.contains(e.target)) document.getElementById('user-dropdown')?.classList.remove('open');
  });
}

// Megosztott /version lekérdezés (egyetlen kérés a badge-nek és a footernek).
let _versionInfoPromise = null;
function getVersionInfo() {
  if (!_versionInfoPromise) _versionInfoPromise = api.version();
  return _versionInfoPromise;
}

async function loadEnvBadge() {
  try {
    const v = await getVersionInfo();
    if (v && v.env_type) {
      const b = document.getElementById('env-badge');
      if (!b) return;
      b.className = 'env-badge';
      b.textContent = v.env_type;
      b.style.background = v.env_color || '#888';
      b.style.color = pickTextColor(v.env_color || '#888');
    }
  } catch {}
}

function pickTextColor(bg) {
  try {
    const c = bg.replace('#', '');
    const r = parseInt(c.slice(0, 2), 16), g = parseInt(c.slice(2, 4), 16), b = parseInt(c.slice(4, 6), 16);
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? '#000' : '#fff';
  } catch { return '#fff'; }
}

async function doLogout() {
  try { await api.logout(); } catch {}
  window.location.href = '/login';
}

// ---- Footer: verzió-kijelzés + verzió-történet ----
async function renderFooter() {
  // Globális, teljes szélességű fixed footer (a body-ra, nem a .main-re).
  if (!document.querySelector('.app') || document.getElementById('app-footer')) return;
  let version = '?';
  try { version = (await getVersionInfo()).version || '?'; }
  catch (e) { console.error('Verzió lekérdezési hiba:', e); }

  const footer = document.createElement('footer');
  footer.className = 'app-footer';
  footer.id = 'app-footer';
  footer.innerHTML = `<a href="#" class="app-footer-version" id="footer-version-link">v${escapeHtml(version)}</a>`;
  document.body.appendChild(footer);

  footer.querySelector('#footer-version-link').addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      const history = await api.versionHistory();
      await showVersionHistoryModal(history);
    } catch (err) {
      console.error('Verzió-történet hiba:', err);
      toast(err.message || 'Verzió-történet nem tölthető be', 'error');
    }
  });
}

// marked.js lusta betöltése (csak a modal első megnyitásakor; self-hosted, /vendor alól).
let _markedLoading = null;
function _ensureMarked() {
  if (typeof marked !== 'undefined') return Promise.resolve();
  if (_markedLoading) return _markedLoading;
  _markedLoading = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/vendor/marked/marked.min.js';
    s.onload = () => { marked.use({ gfm: true, breaks: true }); resolve(); };
    s.onerror = () => reject(new Error('marked.js nem tölthető be'));
    document.head.appendChild(s);
  });
  return _markedLoading;
}

async function showVersionHistoryModal(history) {
  await _ensureMarked();
  // Saját overlay (nem a showModal singletonja) — nincs stale handler-átszivárgás.
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  const entriesHtml = (history || []).map((e) => `
    <div class="version-entry">
      <div class="version-entry-header"><strong>v${escapeHtml(e.version)}</strong><span class="muted">${escapeHtml(e.date)}</span></div>
      <div class="version-entry-message markdown-body">${marked.parse(e.message || '')}</div>
    </div>`).join('');
  overlay.innerHTML = `
    <div class="modal modal-wide">
      <div class="modal-head"><h3>Verzió-történet</h3><button class="btn btn-ghost btn-sm" id="vh-x"><span class="material-icons">close</span></button></div>
      <div class="modal-body version-history-body">${entriesHtml || '<p class="muted">Nincs verzió-információ.</p>'}</div>
      <div class="modal-foot"><button class="btn" id="vh-close">Bezárás</button></div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector('#vh-x').onclick = close;
  overlay.querySelector('#vh-close').onclick = close;
  overlay.onclick = (e) => { if (e.target === overlay) close(); };
}

// ---- Modal ----
function showModal(title, bodyHtml, onConfirm, confirmLabel = 'Mentés', danger = false, extraClass = '') {
  let overlay = document.getElementById('modal-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'modal-overlay';
    overlay.className = 'modal-overlay';
    document.body.appendChild(overlay);
  }
  const okClass = danger ? 'btn-danger' : 'btn-primary';
  overlay.innerHTML = `
    <div class="modal${extraClass ? ' ' + extraClass : ''}">
      <div class="modal-head"><h3>${escapeHtml(title)}</h3><button class="btn btn-ghost btn-sm" id="modal-x"><span class="material-icons">close</span></button></div>
      <div class="modal-body">${bodyHtml}</div>
      <div class="modal-foot"><button class="btn" id="modal-cancel">Mégse</button><button class="btn ${okClass}" id="modal-ok">${escapeHtml(confirmLabel)}</button></div>
    </div>`;
  overlay.classList.add('open');
  const close = () => overlay.classList.remove('open');
  overlay.querySelector('#modal-x').onclick = close;
  overlay.querySelector('#modal-cancel').onclick = close;
  overlay.querySelector('#modal-ok').onclick = async () => {
    const btn = overlay.querySelector('#modal-ok');
    btn.disabled = true;
    try { await onConfirm(); close(); }
    catch (e) { toast(e.message || 'Hiba', 'error'); btn.disabled = false; }
  };
  return overlay;
}

function confirmAction(message, confirmLabel = 'Igen', danger = true) {
  return new Promise((resolve) => {
    const overlay = showModal('Megerősítés', `<p>${escapeHtml(message)}</p>`, async () => resolve(true), confirmLabel, danger);
    overlay.querySelector('#modal-cancel').addEventListener('click', () => resolve(false), { once: true });
    overlay.querySelector('#modal-x').addEventListener('click', () => resolve(false), { once: true });
  });
}

function openChangePassword() {
  document.getElementById('user-dropdown')?.classList.remove('open');
  showModal('Jelszó módosítása', `
    <div class="form-row"><label>Jelenlegi jelszó</label><input type="password" id="cp-old"></div>
    <div class="form-row"><label>Új jelszó (min. 12 karakter)</label><input type="password" id="cp-new"></div>
  `, async () => {
    const oldP = document.getElementById('cp-old').value;
    const newP = document.getElementById('cp-new').value;
    if (!oldP || !newP) throw new Error('Mindkét mező kötelező');
    await api.changePassword(oldP, newP);
    toast('Jelszó módosítva', 'success');
  });
}

// ---- Dátumtartomány eszköztár ----
// Elvárt elemek az oldalon: #start, #end (date input), .quick-ranges [data-days], #apply
// Visszaad: getRange() → {start, end}
function setupRangeBar(onApply, defaultDays = 30) {
  const startEl = document.getElementById('start');
  const endEl = document.getElementById('end');
  if (startEl && !startEl.value) startEl.value = daysAgo(defaultDays);
  if (endEl && !endEl.value) endEl.value = today();
  document.querySelectorAll('.quick-ranges [data-days]').forEach((b) => {
    b.addEventListener('click', () => {
      if (startEl) startEl.value = daysAgo(parseInt(b.dataset.days, 10));
      if (endEl) endEl.value = today();
      onApply();
    });
  });
  const applyBtn = document.getElementById('apply');
  if (applyBtn) applyBtn.addEventListener('click', onApply);
  return () => ({ start: startEl ? startEl.value : today(), end: endEl ? endEl.value : today() });
}

document.addEventListener('DOMContentLoaded', bootstrapApp);
