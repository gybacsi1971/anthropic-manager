/* Claude Code analitika */
window.pageInit = async function () {
  const getRange = setupRangeBar(load, 30);
  document.getElementById('metric').addEventListener('change', load);
  await load();

  async function load() {
    const { start, end } = getRange();
    const metric = document.getElementById('metric').value;
    const p = { start, end };
    let summary, ts, lb, acc;
    try {
      [summary, ts, lb, acc] = await Promise.all([
        api.ccSummary(p),
        api.ccTimeseries({ ...p, metric }),
        api.ccLeaderboard({ ...p, limit: 100 }),
        api.ccAcceptance(p),
      ]);
    } catch (e) { toast(e.message, 'error'); return; }

    renderKpis(summary);
    document.getElementById('ts-title').textContent = metricLabel(metric) + ' – napi';
    renderLineChart('ts-chart', ts.labels, ts.series, { format: metric === 'cost' ? 'usd' : 'tokens' });
    renderAcceptance(acc);
    renderLeaderboard(lb);
  }

  function metricLabel(m) {
    return { sessions: 'Munkamenetek', lines_added: 'Hozzáadott sorok', lines_removed: 'Törölt sorok',
      commits: 'Commitok', pull_requests: 'Pull requestek', cost: 'Becsült költség' }[m] || m;
  }

  function kpi(icon, label, value, sub = '') {
    return `<div class="kpi"><div class="label"><span class="material-icons">${icon}</span>${label}</div><div class="value">${value}</div>${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ''}</div>`;
  }

  function renderKpis(s) {
    document.getElementById('kpis').innerHTML =
      kpi('groups', 'Fejlesztők', fmtInt(s.actors), `${fmtInt(s.sessions)} munkamenet`) +
      kpi('difference', 'Kódsorok', fmtInt(s.lines_added), `+${fmtInt(s.lines_added)} / −${fmtInt(s.lines_removed)}`) +
      kpi('commit', 'Commit / PR', fmtInt(s.commits), `${fmtInt(s.pull_requests)} pull request`) +
      kpi('payments', 'Becsült költség', fmtUSD(s.cost_usd));
  }

  function renderAcceptance(acc) {
    const names = { edit: 'Edit', multi_edit: 'MultiEdit', write: 'Write', notebook_edit: 'NotebookEdit' };
    let html = '<thead><tr><th>Eszköz</th><th class="num">Elfogadva</th><th class="num">Elutasítva</th><th class="num">Arány</th></tr></thead><tbody>';
    for (const a of acc) {
      html += `<tr><td>${names[a.tool] || a.tool}</td><td class="num">${fmtInt(a.accepted)}</td><td class="num">${fmtInt(a.rejected)}</td><td class="num">${a.acceptance_rate === null ? '–' : fmtPct(a.acceptance_rate)}</td></tr>`;
    }
    html += '</tbody>';
    document.getElementById('acc-table').innerHTML = html;
  }

  function renderLeaderboard(lb) {
    if (!lb.length) { document.getElementById('lb-table').innerHTML = '<tbody><tr><td class="empty">Nincs adat a tartományban</td></tr></tbody>'; return; }
    let html = `<thead><tr><th>Fejlesztő</th><th class="num">Munkamenet</th><th class="num">+ sorok</th><th class="num">− sorok</th><th class="num">Commit</th><th class="num">PR</th><th class="num">Elfogadás</th><th class="num">Költség</th></tr></thead><tbody>`;
    for (const r of lb) {
      html += `<tr>
        <td>${escapeHtml(r.actor)}</td>
        <td class="num">${fmtInt(r.sessions)}</td>
        <td class="num">${fmtInt(r.lines_added)}</td>
        <td class="num">${fmtInt(r.lines_removed)}</td>
        <td class="num">${fmtInt(r.commits)}</td>
        <td class="num">${fmtInt(r.pull_requests)}</td>
        <td class="num">${r.acceptance_rate === null ? '–' : fmtPct(r.acceptance_rate)}</td>
        <td class="num">${fmtUSD(r.cost_usd)}</td>
      </tr>`;
    }
    html += '</tbody>';
    document.getElementById('lb-table').innerHTML = html;
  }
};
