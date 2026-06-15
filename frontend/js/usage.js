/* Használat (token) explorer */
window.pageInit = async function () {
  const getRange = setupRangeBar(load, 30);
  document.getElementById('group_by').addEventListener('change', load);
  document.getElementById('metric').addEventListener('change', load);
  await load();

  async function load() {
    const { start, end } = getRange();
    const group_by = document.getElementById('group_by').value;
    const metric = document.getElementById('metric').value;
    const p = { start, end };
    const bdGroup = group_by === 'none' ? 'model' : group_by;
    let summary, ts, bd, cacheBd;
    try {
      [summary, ts, bd, cacheBd] = await Promise.all([
        api.usageSummary(p),
        api.usageTimeseries({ ...p, group_by, metric }),
        api.usageBreakdown({ ...p, group_by: bdGroup, metric }),
        api.usageCacheBreakdown(p),
      ]);
    } catch (e) { toast(e.message, 'error'); return; }

    renderKpis(summary);
    document.getElementById('ts-title').textContent = metricLabel(metric) + ' – napi idősor';
    if (ts.series.length > 1) {
      renderStackedBar('ts-chart', ts.labels, ts.series, { format: 'tokens' });
    } else {
      renderLineChart('ts-chart', ts.labels, ts.series, { format: 'tokens' });
    }
    renderLegend('ts-legend', ts.series);
    renderDoughnut('bd-doughnut', bd.slice(0, 10).map((x) => ({ label: x.label, value: x.value })), { format: 'tokens' });
    renderLegend('bd-legend', bd.slice(0, 10).map((x) => ({ label: x.label })));
    renderTable(bd, metric);
    renderCacheTable(cacheBd);
  }

  function renderCacheTable(rows) {
    let html = '<thead><tr><th>Modell</th><th class="num">Cache olvasás</th><th class="num">Cache írás (5m)</th><th class="num">Cache írás (1h)</th><th class="num">Uncached input</th><th class="num">Találati arány</th></tr></thead><tbody>';
    if (!rows.length) {
      html += '<tr><td colspan="6"><div class="empty"><span class="material-icons">cached</span>Nincs adat a tartományban.</div></td></tr>';
    }
    for (const r of rows) {
      html += `<tr><td>${escapeHtml(r.model)}</td><td class="num">${fmtInt(r.cache_read)}</td><td class="num">${fmtInt(r.cache_write_5m)}</td><td class="num">${fmtInt(r.cache_write_1h)}</td><td class="num">${fmtInt(r.uncached_input)}</td><td class="num">${fmtPct(r.cache_hit_ratio)}</td></tr>`;
    }
    html += '</tbody>';
    document.getElementById('cache-table').innerHTML = html;
  }

  function metricLabel(m) {
    return { total_tokens: 'Összes token', input: 'Bemeneti token', output: 'Kimeneti token',
      cache_read: 'Cache olvasás', cache_creation: 'Cache létrehozás', web_search: 'Web keresés' }[m] || m;
  }

  function kpi(icon, label, value, sub = '') {
    return `<div class="kpi"><div class="label"><span class="material-icons">${icon}</span>${label}</div><div class="value">${value}</div>${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ''}</div>`;
  }

  function renderKpis(s) {
    document.getElementById('kpis').innerHTML =
      kpi('data_usage', 'Összes token', fmtTokens(s.total_tokens)) +
      kpi('login', 'Bemenet (uncached)', fmtTokens(s.input)) +
      kpi('logout', 'Kimenet', fmtTokens(s.output)) +
      kpi('cached', 'Cache', fmtTokens(s.cache_read + s.cache_creation), `${fmtTokens(s.cache_read)} olvasás · ${fmtTokens(s.cache_creation)} létrehozás`);
  }

  function renderTable(bd, metric) {
    const total = bd.reduce((a, x) => a + x.value, 0) || 1;
    let html = `<thead><tr><th>${escapeHtml(document.getElementById('group_by').value === 'none' ? 'Modell' : 'Csoport')}</th><th class="num">${escapeHtml(metricLabel(metric))}</th><th class="num">Arány</th></tr></thead><tbody>`;
    for (const x of bd) {
      html += `<tr><td>${escapeHtml(x.label)}</td><td class="num">${fmtInt(x.value)}</td><td class="num muted">${((x.value / total) * 100).toFixed(1)}%</td></tr>`;
    }
    html += '</tbody>';
    document.getElementById('bd-table').innerHTML = html;
  }
};
