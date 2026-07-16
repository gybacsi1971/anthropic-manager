/* Gyűjtés vezérlése */
window.pageInit = async function () {
  const viewer = currentUser && currentUser.role !== 'admin';
  const runsById = {};

  if (viewer) {
    document.getElementById('sources').style.display = 'none';
    document.getElementById('backfill-card').style.display = 'none';
    document.getElementById('key-warning').style.display = 'none';
  } else {
    document.getElementById('sources').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-sync]');
      if (b) runSync(b.dataset.sync);
    });
    document.getElementById('backfill-btn').addEventListener('click', backfill);
    document.getElementById('bf-start').value = daysAgo(30);
    document.getElementById('bf-end').value = today();
  }

  document.getElementById('runs-table').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-run-id]');
    if (tr && runsById[tr.dataset.runId]) openRunDetail(runsById[tr.dataset.runId]);
  });

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
    let runs;
    try {
      if (viewer) {
        runs = await api.syncRuns(40);
      } else {
        let status;
        [status, runs] = await Promise.all([api.syncStatus(), api.syncRuns(40)]);
        renderWarning(status);
        renderSources(status);
      }
    } catch (e) { return; }
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
      const badge = statusBadge(r && r.status);
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
    for (const k of Object.keys(runsById)) delete runsById[k];
    if (!runs.length) { document.getElementById('runs-table').innerHTML = '<tbody><tr><td class="empty">Még nincs futás</td></tr></tbody>'; return; }
    let html = '<thead><tr><th>Forrás</th><th>Indító</th><th>Állapot</th><th class="num">Sorok</th><th>Indítva</th><th>Befejezve</th><th>Hiba</th></tr></thead><tbody>';
    const names = { usage: 'Használat', cost: 'Költség', claude_code: 'Claude Code', metadata: 'Metaadat' };
    const triggers = { scheduler: 'ütemező', manual: 'kézi', backfill: 'backfill' };
    for (const r of runs) {
      runsById[r.id] = r;
      html += `<tr data-run-id="${r.id}" style="cursor:pointer">
        <td>${names[r.source] || r.source}</td>
        <td class="muted">${triggers[r.trigger] || r.trigger}</td>
        <td>${statusBadge(r.status)}</td>
        <td class="num">${fmtInt(r.rows_upserted)}</td>
        <td class="muted">${fmtDateTime(r.started_at)}</td>
        <td class="muted">${r.finished_at ? fmtDateTime(r.finished_at) : '–'}</td>
        <td class="error-text" style="font-size:12px">${r.error ? escapeHtml(r.error).slice(0, 80) : ''}</td>
      </tr>`;
    }
    html += '</tbody>';
    document.getElementById('runs-table').innerHTML = html;
  }

  function statusBadge(status) {
    if (status === 'ok') return '<span class="badge green">OK</span>';
    if (status === 'running') return '<span class="badge amber">fut…</span>';
    if (status === 'partial') return '<span class="badge amber">részleges</span>';
    if (!status) return '<span class="badge gray">nincs futás</span>';
    return '<span class="badge red">hiba</span>';
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

  // ---- Futás-részletek modal (95%-os, táblázat/JSON váltással) ----

  const DETAIL_COLUMNS = {
    usage: [
      ['bucket_start', 'Kezdet', fmtDateTime], ['model', 'Modell'],
      ['workspace_id', 'Workspace'], ['api_key_id', 'API kulcs'], ['service_tier', 'Tier'],
      ['uncached_input_tokens', 'Input token', fmtInt], ['output_tokens', 'Output token', fmtInt],
      ['cache_read_input_tokens', 'Cache-olvasás', fmtInt], ['web_search_requests', 'Web keresés', fmtInt],
    ],
    cost: [
      ['bucket_start', 'Kezdet', fmtDateTime], ['workspace_id', 'Workspace'], ['model', 'Modell'],
      ['cost_type', 'Típus'], ['amount_cents', 'Összeg', (v) => fmtUSD((v || 0) / 100)],
    ],
    claude_code: [
      ['day', 'Nap'], ['actor_email', 'Aktor (email)'], ['actor_api_key_name', 'Aktor (kulcs)'],
      ['num_sessions', 'Session', fmtInt], ['lines_added', 'Sor +', fmtInt], ['lines_removed', 'Sor −', fmtInt],
      ['commits', 'Commit', fmtInt], ['total_input_tokens', 'Input token', fmtInt],
      ['total_output_tokens', 'Output token', fmtInt],
    ],
    workspaces: [
      ['id', 'ID'], ['name', 'Név'], ['archived_at', 'Archiválva', fmtDateTime],
      ['created_at', 'Létrehozva', fmtDateTime],
    ],
    org_api_keys: [
      ['id', 'ID'], ['name', 'Név'], ['workspace_id', 'Workspace'], ['status', 'Státusz'],
      ['partial_key_hint', 'Kulcs-részlet'], ['created_at', 'Létrehozva', fmtDateTime],
    ],
    org_members: [
      ['id', 'ID'], ['email', 'Email'], ['name', 'Név'], ['role', 'Szerepkör'],
    ],
  };
  const SOURCE_NAMES = { usage: 'Használat', cost: 'Költség', claude_code: 'Claude Code', metadata: 'Metaadat' };
  const METADATA_TAB_LABELS = { org_api_keys: 'API kulcsok', workspaces: 'Workspace-ek', org_members: 'Tagok' };

  function openRunDetail(run) {
    const state = { runId: run.id, table: run.source === 'metadata' ? 'org_api_keys' : null,
                     view: 'table', offset: 0, limit: 50, data: null, loading: true };

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay open';
    document.body.appendChild(overlay);
    const close = () => overlay.remove();

    function cellsFor(d) {
      return DETAIL_COLUMNS[d.table || d.source] || [];
    }

    function renderTableView(d) {
      const cols = cellsFor(d);
      let html = '<div class="table-wrap"><table class="data"><thead><tr>' +
        cols.map(([, label]) => `<th>${label}</th>`).join('') + '</tr></thead><tbody>';
      for (const r of d.rows) {
        html += '<tr>' + cols.map(([key, , fmt]) => {
          const v = r[key];
          const out = v === null || v === undefined ? '–' : (fmt ? fmt(v) : escapeHtml(String(v)));
          return `<td>${out}</td>`;
        }).join('') + '</tr>';
      }
      html += '</tbody></table></div>';
      return html;
    }

    function render() {
      const d = state.data;
      const badge = statusBadge(run.status);
      const tabsHtml = run.source === 'metadata' && d
        ? Object.keys(METADATA_TAB_LABELS).map((t) => {
            const active = state.table === t ? 'btn-primary' : '';
            const count = d.table_counts ? fmtInt(d.table_counts[t] || 0) : '–';
            return `<button class="btn btn-sm ${active}" data-tab="${t}">${METADATA_TAB_LABELS[t]} (${count})</button>`;
          }).join('')
        : '';
      const toggleHtml = `
        <button class="btn toggle ${state.view === 'table' ? 'active' : ''}" data-view="table">Táblázat</button>
        <button class="btn toggle ${state.view === 'json' ? 'active' : ''}" data-view="json">JSON</button>`;

      let bodyHtml;
      if (state.loading) bodyHtml = '<p class="muted">Betöltés…</p>';
      else if (d.viewer_blocked) bodyHtml = '<p class="muted">Néző nem lát tényleges költségadatot ehhez a forráshoz.</p>';
      else if (!d.rows.length) bodyHtml = '<p class="muted">Ehhez a futáshoz nem tartozik ténysor.</p>';
      else if (state.view === 'json') bodyHtml = `<pre class="mono" style="white-space:pre-wrap;font-size:12px">${escapeHtml(JSON.stringify(d.rows, null, 2))}</pre>`;
      else bodyHtml = renderTableView(d);

      const pagerHtml = !state.loading && d.total
        ? `<div class="row" style="justify-content:space-between;align-items:center;margin-top:12px">
            <span class="muted" style="font-size:12px">${d.offset + 1}–${Math.min(d.offset + d.limit, d.total)} / ${d.total}</span>
            <div class="row" style="gap:8px">
              <button class="btn btn-sm" id="rd-prev" ${d.offset === 0 ? 'disabled' : ''}>Előző</button>
              <button class="btn btn-sm" id="rd-next" ${d.offset + d.limit >= d.total ? 'disabled' : ''}>Következő</button>
            </div></div>`
        : '';

      overlay.innerHTML = `
        <div class="modal modal-xl">
          <div class="modal-head">
            <h3>${SOURCE_NAMES[run.source] || run.source} — futás #${run.id} ${badge}</h3>
            <button class="btn btn-ghost btn-sm" id="rd-x"><span class="material-icons">close</span></button>
          </div>
          <div class="modal-body">
            <div class="row muted" style="font-size:12px;gap:16px;flex-wrap:wrap">
              <span>Indítva: ${fmtDateTime(run.started_at)}</span>
              <span>Befejezve: ${run.finished_at ? fmtDateTime(run.finished_at) : '–'}</span>
              <span>Sorok: ${fmtInt(run.rows_upserted)}</span>
              <span>Indító: ${run.trigger}</span>
            </div>
            ${run.error ? `<div class="error-text" style="font-size:12px;margin-top:6px">${escapeHtml(run.error)}</div>` : ''}
            ${tabsHtml ? `<div class="row" style="gap:8px;margin-top:16px;flex-wrap:wrap">${tabsHtml}</div>` : ''}
            <div class="row" style="gap:8px;margin-top:16px">${toggleHtml}</div>
            <div style="margin-top:12px">${bodyHtml}</div>
            ${pagerHtml}
          </div>
          <div class="modal-foot"><button class="btn" id="rd-close">Bezárás</button></div>
        </div>`;

      overlay.querySelector('#rd-x').onclick = close;
      overlay.querySelector('#rd-close').onclick = close;
      overlay.onclick = (e) => { if (e.target === overlay) close(); };
      overlay.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
        state.table = b.dataset.tab; state.offset = 0; load();
      }));
      overlay.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => {
        state.view = b.dataset.view; render();
      }));
      const prevBtn = overlay.querySelector('#rd-prev');
      const nextBtn = overlay.querySelector('#rd-next');
      if (prevBtn) prevBtn.addEventListener('click', () => { state.offset -= state.limit; load(); });
      if (nextBtn) nextBtn.addEventListener('click', () => { state.offset += state.limit; load(); });
    }

    async function load() {
      state.loading = true;
      render();
      try {
        state.data = await api.syncRunRows(state.runId, { table: state.table, limit: state.limit, offset: state.offset });
      } catch (e) { toast(e.message, 'error'); close(); return; }
      state.loading = false;
      render();
    }

    load();
  }
};
