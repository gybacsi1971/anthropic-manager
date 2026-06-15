/* Bejelentkezés */
function nextUrl() {
  const p = new URLSearchParams(location.search).get('next');
  return p && p.startsWith('/') ? p : '/';
}

(async function () {
  try {
    const s = await api.authStatus();
    if (s.needs_setup) { window.location.href = '/setup'; return; }
    if (s.authenticated) { window.location.href = nextUrl(); }
  } catch {}
})();

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = document.getElementById('err');
  err.textContent = '';
  try {
    await api.login(document.getElementById('email').value, document.getElementById('password').value);
    window.location.href = nextUrl();
  } catch (ex) {
    err.textContent = ex.message;
  }
});
