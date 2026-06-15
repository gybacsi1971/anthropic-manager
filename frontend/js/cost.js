/* Költség (USD) explorer — tényleges + becsült (a mai/nyitott napokra) */
window.pageInit = async function () {
  // A néző csak BECSÜLT költséget lát (a tényleges cost_facts kulcsra nem szűrhető → 403).
  const viewer = currentUser && currentUser.role !== 'admin';
  if (viewer) {
    // A becslésre nem értelmezhető csoportosításokat (token-forgalomból nem jönnek) elrejtjük.
    const sel = document.getElementById('group_by');
    ['cost_type', 'token_type', 'description'].forEach((v) => {
      const opt = sel && sel.querySelector(`option[value="${v}"]`);
      if (opt) opt.remove();
    });
  }

  // Azok a dimenziók, amelyekre TÉNYLEGES (cost_facts) bontás nem létezik → csak BECSÜLT.
  // (A cost_facts-ban nincs api_key_id oszlop, ezért kulcsra csak az árlistából becsülhetünk.)
  const ESTIMATE_ONLY = new Set(['api_key_id']);

  const getRange = setupRangeBar(load, 30);
  document.getElementById('group_by').addEventListener('change', load);
  await load();

  async function load() {
    const { start, end } = getRange();
    const group_by = document.getElementById('group_by').value;
    const p = { start, end };
    const bdGroup = group_by === 'none' ? 'model' : group_by;
    const estOnly = ESTIMATE_ONLY.has(group_by);  // csak-becsült dim (pl. API kulcs)

    if (viewer) { await loadViewer(p, group_by, bdGroup); return; }

    let summary, ts, bd, cache;
    try {
      if (estOnly) {
        // Csak-becsült dimenzió: a tényleges breakdown végpont nem hívható (cost_facts-ban
        // nincs ilyen oszlop → 400). A megoszlást a kombinált idősorból származtatjuk.
        [summary, ts, cache] = await Promise.all([
          api.costSummary(p),
          api.costCombinedTimeseries({ ...p, group_by }),
          api.costCacheSavings(p),
        ]);
        bd = breakdownFromTimeseries(ts);
      } else {
        [summary, ts, bd, cache] = await Promise.all([
          api.costSummary(p),
          api.costCombinedTimeseries({ ...p, group_by }),
          api.costBreakdown({ ...p, group_by: bdGroup }),
          api.costCacheSavings(p),
        ]);
      }
    } catch (e) { toast(e.message, 'error'); return; }

    renderKpis(summary, ts, estOnly);
    renderCacheSavings(cache);
    // Mindig oszlopdiagram: a becsült napokat a modell színének 40%-os változata jelzi.
    renderStackedBar('ts-chart', ts.labels, ts.series, { format: 'usd', estimatedDays: ts.estimated_days });
    renderLegend('ts-legend', ts.series);
    renderEstimateNote(ts);
    renderDoughnut('bd-doughnut', bd.slice(0, 10).map((x) => ({ label: x.label, value: x.value })), { format: 'usd' });
    renderLegend('bd-legend', bd.slice(0, 10).map((x) => ({ label: x.label })));
    renderTable(bd);
    markBreakdownEstimated(estOnly);
  }

  // Néző-ág: csak becsült költség (kombinált idősor, scope-olt) + cache-haszon.
  async function loadViewer(p, group_by, bdGroup) {
    let ts, bdTs, cache;
    try {
      [ts, bdTs, cache] = await Promise.all([
        api.costCombinedTimeseries({ ...p, group_by }),
        api.costCombinedTimeseries({ ...p, group_by: bdGroup }),
        api.costCacheSavings(p),
      ]);
    } catch (e) { toast(e.message, 'error'); return; }

    document.getElementById('kpis').innerHTML =
      `<div class="kpi" style="border-left:3px solid #d09a3c"><div class="label"><span class="material-icons">trending_up</span>Becsült költség</div>`
      + `<div class="value" style="color:#9a6a2f">${fmtUSD(ts.estimated_total_usd || 0)}</div>`
      + `<div class="sub">az árlistából, a hatókörödben — a tényleges költség nézőként nem érhető el</div></div>`;
    renderCacheSavings(cache);
    renderStackedBar('ts-chart', ts.labels, ts.series, { format: 'usd', estimatedDays: ts.estimated_days });
    renderLegend('ts-legend', ts.series);
    renderEstimateNote(ts);
    const bd = breakdownFromTimeseries(bdTs);
    renderDoughnut('bd-doughnut', bd.slice(0, 10).map((x) => ({ label: x.label, value: x.value })), { format: 'usd' });
    renderLegend('bd-legend', bd.slice(0, 10).map((x) => ({ label: x.label })));
    renderTable(bd);
  }

  function kpi(icon, label, value, sub = '') {
    return `<div class="kpi"><div class="label"><span class="material-icons">${icon}</span>${label}</div><div class="value">${value}</div>${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ''}</div>`;
  }

  function kpiEst(value) {
    return `<div class="kpi" style="border-left:3px solid #d09a3c"><div class="label"><span class="material-icons">trending_up</span>Becsült (nyitott napok)</div><div class="value" style="color:#9a6a2f">${value}</div><div class="sub">a mai/nyitott napok token-forgalmából</div></div>`;
  }

  function renderKpis(s, ts, estOnly) {
    const types = { tokens: 'Tokenek', web_search: 'Web keresés', code_execution: 'Kódfuttatás', session_usage: 'Munkamenet' };
    let html = kpi('payments', estOnly ? 'Tényleges költség (összes)' : 'Tényleges költség', fmtUSD(s.total_usd));
    let slots = 3;
    if (estOnly && ts && ts.estimate_supported) {
      // Csak-becsült bontás (pl. API kulcs): a TELJES idősor becsült, nem csak a nyitott napok.
      html += `<div class="kpi" style="border-left:3px solid #d09a3c"><div class="label">`
        + `<span class="material-icons">trending_up</span>Becsült összes (árlistából)</div>`
        + `<div class="value" style="color:#9a6a2f">${fmtUSD(ts.estimated_total_usd || 0)}</div>`
        + `<div class="sub">API kulcsra a tényleges költség nem bontható — a bontás teljes egészében becsült</div></div>`;
      slots = 2;
    } else if (ts && ts.estimate_supported && ts.estimated_total_usd > 0) {
      html += kpiEst(fmtUSD(ts.estimated_total_usd));
      slots = 2;
    }
    for (const t of (s.by_cost_type || []).slice(0, slots)) {
      html += kpi('sell', types[t.cost_type] || t.cost_type, fmtUSD(t.usd));
    }
    document.getElementById('kpis').innerHTML = html;
  }

  function renderCacheSavings(cs) {
    const net = cs.net_benefit_usd;
    const netColor = net >= 0 ? '#2e7d57' : '#c0392b';
    let html = '';
    html += `<div class="kpi"><div class="label"><span class="material-icons">cached</span>Cache találati arány</div><div class="value">${fmtPct(cs.cache_hit_ratio)}</div><div class="sub">${fmtTokens(cs.cache_read_tokens)} olvasás · ${fmtTokens(cs.uncached_input_tokens)} uncached</div></div>`;
    html += `<div class="kpi" style="border-left:3px solid ${netColor}"><div class="label"><span class="material-icons">savings</span>Nettó cache-haszon</div><div class="value" style="color:${netColor}">${fmtUSD(net)}</div><div class="sub">megtakarítás − írás-felár</div></div>`;
    html += `<div class="kpi"><div class="label"><span class="material-icons">trending_down</span>Olvasás-megtakarítás</div><div class="value">${fmtUSD(cs.read_savings_usd)}</div><div class="sub">vs. teljes beviteli ár</div></div>`;
    html += `<div class="kpi"><div class="label"><span class="material-icons">trending_up</span>Cache-írás felár</div><div class="value">${fmtUSD(cs.write_overhead_usd)}</div><div class="sub">${fmtTokens(cs.cache_write_tokens)} write token</div></div>`;
    document.getElementById('cache-kpis').innerHTML = html;
    const note = document.getElementById('cache-note');
    if (note) note.innerHTML = 'A cache-olvasás a beviteli ár 10%-áért megy (90% megtakarítás); a cache-írás felára +25% (5m) / +100% (1h). A nettó haszon az olvasás-megtakarítás és az írás-felár különbsége — a token-arányok a teljes forgalomra, a USD az árazott modellekre vonatkoznak (<a href="/pricing">Árjegyzék</a>).';
  }

  function renderEstimateNote(ts) {
    const el = document.getElementById('ts-note');
    if (!el) return;
    let html = '';
    if (ts.estimate_supported && (ts.estimated_days || []).some(Boolean)) {
      html += `<span style="display:inline-block;width:11px;height:11px;border-radius:2px;background:#cc785c66;vertical-align:-1px"></span> A halványabb (40%) oszlop = <b>becsült</b> költség (a Cost API még nem zárta le a napot); a tömör szín = tényleges, lezárt nap.`;
    } else if (!ts.estimate_supported) {
      html += 'Ebben a bontásban nincs becslés (a token-forgalom nem bontható cost_type / token_type / leírás szerint) — csak a tényleges, lezárt napok látszanak.';
    }
    if ((ts.unpriced_models || []).length) {
      html += `<br><span class="material-icons" style="font-size:14px;vertical-align:-3px;color:#c0392b">warning</span> Árazatlan modell(ek) — a becslésből kimaradnak: <code>${ts.unpriced_models.map(escapeHtml).join('</code>, <code>')}</code>. Add meg az árukat az <a href="/pricing">Árjegyzék</a> oldalon.`;
    }
    el.innerHTML = html;
  }

  // Becsült megoszlás a kombinált idősorból: kulcsonként összegzi a napi értékeket.
  // Olyan dimenziókra kell, amelyekre TÉNYLEGES (cost_facts) bontás nem létezik (pl. API kulcs),
  // illetve a néző-ághoz, ahol egyáltalán nincs tényleges költség.
  function breakdownFromTimeseries(ts) {
    return (ts.series || [])
      .map((s) => ({ key: s.key, label: s.label, value: (s.data || []).reduce((a, b) => a + b, 0) }))
      .sort((a, b) => b.value - a.value);
  }

  // A "Megoszlás"/"Részletek" kártyák jelzése: a megjelenített bontás BECSÜLT-e.
  // (A pageInit egyszer fut, a load többször — ezért estOnly=false esetén vissza is állítjuk.)
  function markBreakdownEstimated(estOnly) {
    const heads = document.querySelectorAll('.grid.cols-2 .card .card-head h2');
    if (heads[0]) heads[0].textContent = estOnly ? 'Megoszlás (becsült)' : 'Megoszlás';
    if (heads[1]) heads[1].textContent = estOnly ? 'Részletek (becsült)' : 'Részletek';
  }

  function renderTable(bd) {
    const total = bd.reduce((a, x) => a + x.value, 0) || 1;
    let html = '<thead><tr><th>Csoport</th><th class="num">Költség (USD)</th><th class="num">Arány</th></tr></thead><tbody>';
    for (const x of bd) {
      html += `<tr><td>${escapeHtml(x.label)}</td><td class="num">${fmtUSD(x.value)}</td><td class="num muted">${((x.value / total) * 100).toFixed(1)}%</td></tr>`;
    }
    html += '</tbody>';
    document.getElementById('bd-table').innerHTML = html;
  }
};
