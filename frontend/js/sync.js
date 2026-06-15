/* Gyűjtés vezérlése */
window.pageInit = async function () {
  document.getElementById('sources').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-sync]');
    if (b) runSync(b.dataset.sync);
  });
  document.getElementById('backfill-btn').addEventListener('click', backfill);
  document.getElementById('bf-start').value = daysAgo(30);
  document.getElementById('bf-end').value = today();

  const SOURCES = {
    usage: { name: 'Használat', icon: 'data_usage', cov: 'usage_facts' },
    cost: { name: 'Költség', icon: 'payments', cov: 'cost_facts' },
    claude_code: { name: 'Claude Code', icon: 'terminal', cov: 'claude_code_facts' },
    metadata: { name: 'Metaadat', icon: 'inventory_2', cov: null },
  };

  await refresh();
  const timer = setInterval(refresh, 8000);
  window.addEventListener('beforeunload', () => clearInterval(timer));

  async function refresh() {
    let status, runs;
    try { [status, runs] = await Promise.all([api.syncStatus(), api.syncRuns(40)]); }
    catch (e) { return; }
    renderWarning(status);
    renderSources(status);
    renderRuns(runs);
  }

  function renderWarning(status) {
    document.getElementById('key-warning').innerHTML = status.active_key ? '' :
      '<div class="card mb-16"><div class="card-body"><span class="badge amber">Figyelem</span> Nincs aktív Admin API kulcs. A gyűjtés nem indul, amíg nem állítasz be egyet az <a href="/admin-keys">Admin kulcsok</a> oldalon.</div></div>';
  }

  function renderSources(status) {
    const last = {};
    (status.last_runs || []).forEach((r) => { last[r.source] = r; });
    let html = '';
    for (const [src, cfg] of Object.entries(SOURCES)) {
      const r = last[src];
      const badge = !r ? '<span class="badge gray">nincs futás</span>'
        : r.status === 'ok' ? '<span class="badge green">OK</span>'
        : r.status === 'running' ? '<span class="badge amber">fut…</span>'
        : '<span class="badge red">hiba</span>';
      let cov = '';
      if (cfg.cov && status.coverage[cfg.cov]) {
        const c = status.coverage[cfg.cov];
        const rows = c.rows || 0;
        let range = '';
        if (cfg.cov === 'claude_code_facts') range = c.min_day ? `${c.min_day} – ${c.max_day}` : '';
        else range = c.min_ts ? `${String(c.min_ts).slice(0, 10)} – ${String(c.max_ts).slice(0, 10)}` : '';
        cov = `<div class="sub">${fmtInt(rows)} sor${range ? ' · ' + range : ''}</div>`;
      } else if (src === 'metadata') {
        const w = status.metadata.workspaces?.rows || 0, k = status.metadata.org_api_keys?.rows || 0, m = status.metadata.org_members?.rows || 0;
        cov = `<div class="sub">${w} ws · ${k} kulcs · ${m} tag</div>`;
      }
      html += `<div class="kpi">
        <div class="label"><span class="material-icons">${cfg.icon}</span>${cfg.name} ${badge}</div>
        ${cov}
        <div class="sub muted">${r ? 'Utolsó: ' + fmtDateTime(r.finished_at || r.started_at) : ''}</div>
        ${r && r.error ? `<div class="error-text" style="font-size:11px">${escapeHtml(r.error).slice(0, 120)}</div>` : ''}
        <button class="btn btn-sm btn-primary mt-16" data-sync="${src}" ${status.active_key ? '' : 'disabled'}><span class="material-icons">sync</span> Szinkronizálás</button>
      </div>`;
    }
    document.getElementById('sources').innerHTML = html;
  }

  function renderRuns(runs) {
    if (!runs.length) { document.getElementById('runs-table').innerHTML = '<tbody><tr><td class="empty">Még nincs futás</td></tr></tbody>'; return; }
    let html = '<thead><tr><th>Forrás</th><th>Indító</th><th>Állapot</th><th class="num">Sorok</th><th>Indítva</th><th>Befejezve</th><th>Hiba</th></tr></thead><tbody>';
    const names = { usage: 'Használat', cost: 'Költség', claude_code: 'Claude Code', metadata: 'Metaadat' };
    const triggers = { scheduler: 'ütemező', manual: 'kézi', backfill: 'backfill' };
    for (const r of runs) {
      const badge = r.status === 'ok' ? '<span class="badge green">OK</span>'
        : r.status === 'running' ? '<span class="badge amber">fut</span>'
        : '<span class="badge red">hiba</span>';
      html += `<tr>
        <td>${names[r.source] || r.source}</td>
        <td class="muted">${triggers[r.trigger] || r.trigger}</td>
        <td>${badge}</td>
        <td class="num">${fmtInt(r.rows_upserted)}</td>
        <td class="muted">${fmtDateTime(r.started_at)}</td>
        <td class="muted">${r.finished_at ? fmtDateTime(r.finished_at) : '–'}</td>
        <td class="error-text" style="font-size:12px">${r.error ? escapeHtml(r.error).slice(0, 80) : ''}</td>
      </tr>`;
    }
    html += '</tbody>';
    document.getElementById('runs-table').innerHTML = html;
  }

  async function runSync(source) {
    try { await api.syncRun(source); toast('Szinkronizálás elindítva', 'success'); setTimeout(refresh, 1200); }
    catch (e) { toast(e.message, 'error'); }
  }

  async function backfill() {
    const source = document.getElementById('bf-source').value;
    const start = document.getElementById('bf-start').value;
    const end = document.getElementById('bf-end').value;
    if (!start || !end) { toast('Add meg a dátumtartományt', 'error'); return; }
    try { await api.syncBackfill(source, start, end); toast('Backfill elindítva', 'success'); setTimeout(refresh, 1200); }
    catch (e) { toast(e.message, 'error'); }
  }
};
