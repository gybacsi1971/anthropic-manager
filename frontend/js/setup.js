/* Első beállítás (setup wizard) */
(async function () {
  try {
    const s = await api.authStatus();
    if (!s.needs_setup) { window.location.href = s.authenticated ? '/' : '/login'; }
  } catch {}
})();

document.getElementById('setup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = document.getElementById('err');
  err.textContent = '';
  const name = document.getElementById('name').value.trim();
  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  const password2 = document.getElementById('password2').value;
  if (password !== password2) { err.textContent = 'A két jelszó nem egyezik'; return; }
  if (password.length < 12) { err.textContent = 'A jelszónak legalább 12 karakter hosszúnak kell lennie'; return; }
  try {
    await api.setup({ name, email, password });
    window.location.href = '/';
  } catch (ex) {
    err.textContent = ex.message;
  }
});
