/* Anthropic Manager — közös API-kliens és UI segédfüggvények */

const API_BASE = '/api';

function buildQuery(params) {
  if (!params) return '';
  const parts = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) {
      v.forEach((item) => parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(item)}`));
    } else {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
    }
  }
  return parts.length ? '?' + parts.join('&') : '';
}

async function apiRequest(url, options = {}) {
  const fullUrl = url.startsWith('http') ? url : `${API_BASE}${url}`;
  const response = await fetch(fullUrl, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    credentials: 'same-origin',
    ...options,
  });

  if (response.status === 401 && !url.includes('/auth/')) {
    const path = window.location.pathname;
    if (path !== '/login' && path !== '/setup') {
      window.location.href = '/login?next=' + encodeURIComponent(path);
      throw new Error('Bejelentkezés szükséges');
    }
  }

  if (!response.ok) {
    let msg = `Hiba (${response.status})`;
    try { const err = await response.json(); msg = err.detail || msg; } catch {}
    throw new Error(msg);
  }

  const ct = response.headers.get('content-type') || '';
  return ct.includes('application/json') ? response.json() : response;
}

const api = {
  // Auth
  authStatus: () => apiRequest('/auth/status'),
  login: (email, password) => apiRequest('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => apiRequest('/auth/logout', { method: 'POST' }),
  setup: (data) => apiRequest('/auth/setup', { method: 'POST', body: JSON.stringify(data) }),
  changePassword: (oldP, newP) => apiRequest('/auth/change-password', { method: 'POST', body: JSON.stringify({ old_password: oldP, new_password: newP }) }),
  version: () => apiRequest('/version'),
  versionHistory: () => apiRequest('/version-history'),

  // Felhasználók
  listUsers: () => apiRequest('/users'),
  createUser: (data) => apiRequest('/users', { method: 'POST', body: JSON.stringify(data) }),
  updateUser: (id, data) => apiRequest(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  resetUserPassword: (id, newP) => apiRequest(`/users/${id}/reset-password`, { method: 'POST', body: JSON.stringify({ new_password: newP }) }),
  deleteUser: (id) => apiRequest(`/users/${id}`, { method: 'DELETE' }),
  getUserScope: (id) => apiRequest(`/users/${id}/scope`),
  setUserScope: (id, data) => apiRequest(`/users/${id}/scope`, { method: 'PUT', body: JSON.stringify(data) }),

  // Admin kulcsok
  listAdminKeys: () => apiRequest('/admin-keys'),
  createAdminKey: (label, value) => apiRequest('/admin-keys', { method: 'POST', body: JSON.stringify({ label, value }) }),
  testAdminKey: (id) => apiRequest(`/admin-keys/${id}/test`, { method: 'POST' }),
  updateAdminKey: (id, data) => apiRequest(`/admin-keys/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteAdminKey: (id) => apiRequest(`/admin-keys/${id}`, { method: 'DELETE' }),

  // Sync
  syncRun: (source) => apiRequest('/sync/run', { method: 'POST', body: JSON.stringify({ source }) }),
  syncBackfill: (source, start, end) => apiRequest('/sync/backfill', { method: 'POST', body: JSON.stringify({ source, start, end }) }),
  syncRuns: (limit = 50) => apiRequest(`/sync/runs${buildQuery({ limit })}`),
  syncStatus: () => apiRequest('/sync/status'),
  syncRunRows: (runId, p) => apiRequest(`/sync/runs/${runId}/rows${buildQuery(p)}`),

  // Usage
  usageSummary: (p) => apiRequest(`/usage/summary${buildQuery(p)}`),
  usageTimeseries: (p) => apiRequest(`/usage/timeseries${buildQuery(p)}`),
  usageBreakdown: (p) => apiRequest(`/usage/breakdown${buildQuery(p)}`),
  usageCacheBreakdown: (p) => apiRequest(`/usage/cache-breakdown${buildQuery(p)}`),

  // Cost
  costSummary: (p) => apiRequest(`/cost/summary${buildQuery(p)}`),
  costTimeseries: (p) => apiRequest(`/cost/timeseries${buildQuery(p)}`),
  costCombinedTimeseries: (p) => apiRequest(`/cost/combined-timeseries${buildQuery(p)}`),
  costBreakdown: (p) => apiRequest(`/cost/breakdown${buildQuery(p)}`),
  costCacheSavings: (p) => apiRequest(`/cost/cache-savings${buildQuery(p)}`),

  // Szervezet egyenlege (kézi horgony, pontos időpont)
  getBalance: () => apiRequest('/balance'),
  setBalance: (amount_usd, anchor_ts) => apiRequest('/balance', { method: 'PUT', body: JSON.stringify({ amount_usd, anchor_ts }) }),

  // Modell-árazás (becsült költség)
  listPricing: () => apiRequest('/pricing'),
  savePricing: (items) => apiRequest('/pricing', { method: 'PUT', body: JSON.stringify({ items }) }),
  refreshPricing: () => apiRequest('/pricing/refresh', { method: 'POST' }),

  // Claude Code
  ccSummary: (p) => apiRequest(`/claude-code/summary${buildQuery(p)}`),
  ccTimeseries: (p) => apiRequest(`/claude-code/timeseries${buildQuery(p)}`),
  ccLeaderboard: (p) => apiRequest(`/claude-code/leaderboard${buildQuery(p)}`),
  ccAcceptance: (p) => apiRequest(`/claude-code/acceptance${buildQuery(p)}`),

  // Metaadat
  metaWorkspaces: () => apiRequest('/metadata/workspaces'),
  metaApiKeys: () => apiRequest('/metadata/api-keys'),
  metaMembers: () => apiRequest('/metadata/members'),
  metaModels: () => apiRequest('/metadata/models'),

  // Beállítások / napló
  getSettings: () => apiRequest('/settings'),
  updateSettings: (values) => apiRequest('/settings', { method: 'PUT', body: JSON.stringify({ values }) }),
  activity: (p) => apiRequest(`/activity${buildQuery(p)}`),
};

// ============================================================
// FORMÁZÓK
// ============================================================

function fmtInt(n) {
  if (n === null || n === undefined) return '–';
  return Math.round(n).toLocaleString('hu-HU');
}

function fmtTokens(n) {
  if (n === null || n === undefined) return '–';
  n = Number(n);
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + ' Mrd';
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + ' M';
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + ' e';
  return fmtInt(n);
}

function fmtUSD(n, digits = 2) {
  if (n === null || n === undefined) return '–';
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtPct(x) {
  if (x === null || x === undefined) return '–';
  return (x * 100).toFixed(1) + '%';
}

function fmtDateTime(iso) {
  if (!iso) return '–';
  try { return new Date(iso).toLocaleString('hu-HU', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
  catch { return iso; }
}

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Dátum-segédek (YYYY-MM-DD, UTC alapú)
function isoDate(d) { return d.toISOString().slice(0, 10); }
function today() { return isoDate(new Date()); }
function daysAgo(n) { const d = new Date(); d.setUTCDate(d.getUTCDate() - n); return isoDate(d); }

// ============================================================
// TOAST
// ============================================================

function toast(message, type = 'info', timeout = 3500) {
  const container = document.getElementById('toast-container');
  if (!container) { console.log(`[${type}] ${message}`); return; }
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  const icon = { success: 'check_circle', error: 'error', warning: 'warning', info: 'info' }[type] || 'info';
  el.innerHTML = `<span class="material-icons">${icon}</span><span>${escapeHtml(message)}</span>`;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 200); }, timeout);
}
