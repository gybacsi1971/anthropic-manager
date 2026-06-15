/* Beállítások (ütemező) */
window.pageInit = async function () {
  const NUM_KEYS = [
    'scheduler.usage_interval_min', 'scheduler.cost_interval_min',
    'scheduler.claude_code_interval_min', 'scheduler.metadata_interval_min',
    'scheduler.rolling_window_days',
  ];
  document.getElementById('save').addEventListener('click', save);
  await load();

  async function load() {
    const s = await api.getSettings();
    document.getElementById('scheduler.enabled').value = String(!!s['scheduler.enabled']);
    for (const k of NUM_KEYS) {
      const el = document.getElementById(k);
      if (el && s[k] !== undefined) el.value = s[k];
    }
  }

  async function save() {
    const values = { 'scheduler.enabled': document.getElementById('scheduler.enabled').value === 'true' };
    for (const k of NUM_KEYS) {
      const el = document.getElementById(k);
      const n = parseInt(el.value, 10);
      if (Number.isNaN(n) || n < 0) { toast('Érvénytelen érték: ' + k, 'error'); return; }
      values[k] = n;
    }
    try { await api.updateSettings(values); toast('Beállítások mentve', 'success'); }
    catch (e) { toast(e.message, 'error'); }
  }
};
