/* Áttekintő dashboard */
window.pageInit = async function () {
  // A néző nem látja a szervezet egyenlegét (org-szintű), és csak BECSÜLT költséget lát.
  const viewer = currentUser && currentUser.role !== 'admin';
  const getRange = setupRangeBar(load, 30);
  if (viewer) {
    hideCard('balance-body');  // egyenleg-kártya elrejtése
  } else {
    await loadBalance();
  }
  await load();

  // Egy mező legközelebbi .card konténerének elrejtése (a kártya teljes eltüntetéséhez).
  function hideCard(elementId) {
    const el = document.getElementById(elementId);
    const card = el && el.closest('.card');
    if (card) card.style.display = 'none';
    else if (el) el.style.display = 'none';
  }

  // ---- Szervezet egyenlege (kézi horgony, tartomány-független) ----
  async function loadBalance() {
    let b;
    try { b = await api.getBalance(); }
    catch (e) { document.getElementById('balance-body').innerHTML = `<div class="muted">${escapeHtml(e.message)}</div>`; return; }
    renderBalance(b);
  }

  function renderBalance(b) {
    const isAdmin = currentUser && currentUser.role === 'admin';
    const editBtn = isAdmin
      ? `<button class="btn btn-sm" id="balance-edit"><span class="material-icons">edit</span> ${b.configured ? 'Módosítás' : 'Beállítás'}</button>`
      : '';
    let html;
    if (!b.configured) {
      html = `<div class="row" style="justify-content:space-between;align-items:center;gap:16px">
          <div><div class="label" style="font-size:13px"><span class="material-icons">account_balance_wallet</span> Szervezet egyenlege</div>
            <div class="muted" style="margin-top:6px">Nincs beállítva. ${isAdmin ? 'Add meg az aktuális egyenleget a Console → Billing oldalról.' : 'Kérd meg egy adminisztrátort a beállításra.'}</div>
            <div class="muted" style="font-size:12px;margin-top:4px">Az API nem ad kredit-egyenleget; ez kézi horgony, amiből az app levonja az azóta felmerült költséget.</div></div>
          ${editBtn}
        </div>`;
    } else {
      const bal = b.balance_usd;
      const color = bal <= 0 ? '#c0392b' : bal < 10 ? '#c77b1f' : '#2e7d57';
      const unpriced = (b.unpriced_models || []).length
        ? `<div class="muted" style="font-size:12px;margin-top:4px;color:#c0392b"><span class="material-icons" style="font-size:14px;vertical-align:-3px">warning</span> Árazatlan modell(ek) a mai forgalomban — a becsült költés (és így az egyenleg) optimista lehet: <code>${b.unpriced_models.map(escapeHtml).join('</code>, <code>')}</code> (l. <a href="/pricing">Árjegyzék</a>).</div>`
        : '';
      const prorated = b.anchor_day_prorated
        ? ' <span class="muted" title="A horgony napjára nincs órás adat, ezért időarányosan becsültük">· a horgony napja időarányos</span>'
        : '';
      html = `<div class="row" style="justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap">
          <div>
            <div class="label" style="font-size:13px"><span class="material-icons">account_balance_wallet</span> Szervezet egyenlege <span class="muted" style="font-weight:400">(becsült)</span></div>
            <div style="font-size:42px;font-weight:700;line-height:1.1;color:${color};margin-top:2px">${fmtUSD(bal)}</div>
            <div class="muted" style="font-size:12.5px;margin-top:6px">
              Horgony: <b>${fmtUSD(b.anchor_usd)}</b> (${escapeHtml(fmtDateTime(b.anchor_ts))}) − elköltve <b>${fmtUSD(b.spent_usd)}</b>
              <span style="opacity:.8">(${fmtUSD(b.actual_spent_usd)} tényleges + ${fmtUSD(b.estimated_open_usd)} becsült)</span>${prorated}
            </div>
            ${unpriced}
          </div>
          ${editBtn}
        </div>`;
    }
    document.getElementById('balance-body').innerHTML = html;
    const eb = document.getElementById('balance-edit');
    if (eb) eb.addEventListener('click', () => editBalance(b));
  }

  // UTC ISO → helyi "YYYY-MM-DDTHH:MM" (datetime-local inputhoz). Üres iso → most.
  function toLocalInput(iso) {
    const d = iso ? new Date(iso) : new Date();
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function editBalance(b) {
    showModal('Szervezet egyenlege', `
      <p class="muted" style="font-size:12.5px;margin-top:0">Másold ki az aktuális egyenleget a <b>Console → Billing</b> oldalról, és add meg a <b>pontos időponttal</b> (amikor leolvastad). Az app ettől az időponttól vonja le a felmerült költséget — a horgony napján csak az utána eső órákat.</p>
      <div class="form-row"><label>Egyenleg (USD)</label><input type="number" step="0.01" min="0" id="bal-amount" value="${b.configured ? b.anchor_usd : ''}" placeholder="pl. 27.42"></div>
      <div class="form-row"><label>Érvényes ettől az időponttól (helyi idő)</label><input type="datetime-local" id="bal-ts" value="${toLocalInput(null)}"></div>
    `, async () => {
      const amount = parseFloat(document.getElementById('bal-amount').value);
      const local = document.getElementById('bal-ts').value;
      if (!isFinite(amount) || amount < 0) throw new Error('Érvénytelen egyenleg');
      if (!local) throw new Error('Az időpont kötelező');
      const anchorTs = new Date(local).toISOString();  // helyi → UTC ISO
      await api.setBalance(amount, anchorTs);
      toast('Egyenleg mentve', 'success');
      await loadBalance();
    }, 'Mentés');
  }

  async function load() {
    const { start, end } = getRange();
    const p = { start, end };
    if (viewer) { await loadViewer(p); return; }

    let cost, usage, cc, costTs, estModelTs, tokTs, status;
    try {
      [cost, usage, cc, costTs, estModelTs, tokTs, status] = await Promise.all([
        api.costSummary(p), api.usageSummary(p), api.ccSummary(p),
        // A folyó napot (pl. ma) a Cost API nem adja → combined (tényleges + becsült)
        // idősor, hogy a mai nap becsült oszlopként megjelenjen (a néző ággal egyezően).
        api.costCombinedTimeseries({ ...p, group_by: 'none' }),
        api.costCombinedTimeseries({ ...p, group_by: 'model' }),
        api.usageTimeseries({ ...p, group_by: 'model', metric: 'total_tokens' }),
        api.syncStatus(),
      ]);
    } catch (e) { toast(e.message, 'error'); return; }

    renderKpis(cost, usage, cc, costTs);
    renderLineChart('cost-chart', costTs.labels, costTs.series, { format: 'usd' });
    renderStackedBar('token-chart', tokTs.labels, tokTs.series, { format: 'tokens' });
    renderLegend('token-legend', tokTs.series);
    const top = (estModelTs.series || [])
      .map((s) => ({ label: s.label, value: (s.data || []).reduce((a, b) => a + b, 0) }))
      .sort((a, b) => b.value - a.value).slice(0, 8);
    renderDoughnut('model-doughnut', top.map((x) => ({ label: x.label, value: x.value })), { format: 'usd' });
    renderLegend('model-legend', top.map((x) => ({ label: x.label })));
    renderFreshness(status);
  }

  // Néző-ág: scope-olt használat + becsült költség (a tényleges/sync admin-only → kihagyjuk).
  async function loadViewer(p) {
    let usage, cc, estTs, estModelTs, tokTs;
    try {
      [usage, cc, estTs, estModelTs, tokTs] = await Promise.all([
        api.usageSummary(p), api.ccSummary(p),
        api.costCombinedTimeseries({ ...p, group_by: 'none' }),
        api.costCombinedTimeseries({ ...p, group_by: 'model' }),
        api.usageTimeseries({ ...p, group_by: 'model', metric: 'total_tokens' }),
      ]);
    } catch (e) { toast(e.message, 'error'); return; }

    document.getElementById('kpis').innerHTML =
      kpi('trending_up', 'Becsült költség', fmtUSD(estTs.estimated_total_usd || 0), 'az árlistából, a hatókörödben') +
      kpi('data_usage', 'Összes token', fmtTokens(usage.total_tokens), `${fmtTokens(usage.input)} be · ${fmtTokens(usage.output)} ki`) +
      kpi('terminal', 'Claude Code költség', fmtUSD(cc.cost_usd), cc.actors > 0 ? `${cc.actors} fejlesztő` : 'nincs Claude Code telemetria') +
      kpi('travel_explore', 'Web keresés', fmtInt(usage.web_search), 'szerveroldali kérés');
    renderLineChart('cost-chart', estTs.labels, estTs.series, { format: 'usd' });
    renderStackedBar('token-chart', tokTs.labels, tokTs.series, { format: 'tokens' });
    renderLegend('token-legend', tokTs.series);
    const top = (estModelTs.series || [])
      .map((s) => ({ label: s.label, value: (s.data || []).reduce((a, b) => a + b, 0) }))
      .sort((a, b) => b.value - a.value).slice(0, 8);
    renderDoughnut('model-doughnut', top.map((x) => ({ label: x.label, value: x.value })), { format: 'usd' });
    renderLegend('model-legend', top.map((x) => ({ label: x.label })));
    hideCard('freshness');  // adatfrissesség + sync admin-only
  }

  function kpi(icon, label, value, sub = '') {
    return `<div class="kpi"><div class="label"><span class="material-icons">${icon}</span>${label}</div>
            <div class="value">${value}</div>${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ''}</div>`;
  }

  function renderKpis(cost, usage, cc, est) {
    // "Összes költség" = tényleges (cost_facts). A combined idősorból hozzávesszük a
    // nyitott napok (pl. ma) becsült összegét — külön feltüntetve (nincs csendes 0).
    const estOpen = (est && est.estimated_total_usd) || 0;
    const unpriced = (est && est.unpriced_models) || [];
    let costSub = estOpen > 0 ? `+ ${fmtUSD(estOpen)} becsült (nyitott nap)` : '';
    if (unpriced.length) costSub += `${costSub ? ' · ' : ''}⚠ árazatlan: ${unpriced.join(', ')}`;
    document.getElementById('kpis').innerHTML =
      kpi('payments', 'Összes költség', fmtUSD(cost.total_usd), costSub) +
      kpi('data_usage', 'Összes token', fmtTokens(usage.total_tokens), `${fmtTokens(usage.input)} be · ${fmtTokens(usage.output)} ki`) +
      kpi('terminal', 'Claude Code költség', fmtUSD(cc.cost_usd), cc.actors > 0 ? `${cc.actors} fejlesztő` : 'nincs Claude Code telemetria') +
      kpi('travel_explore', 'Web keresés', fmtInt(usage.web_search), 'szerveroldali kérés');
  }

  function renderFreshness(status) {
    const map = {};
    (status.last_runs || []).forEach((r) => { map[r.source] = r; });
    const names = { usage: 'Használat', cost: 'Költség', claude_code: 'Claude Code', metadata: 'Metaadat' };
    let html = '<table class="data"><tbody>';
    for (const src of ['usage', 'cost', 'claude_code', 'metadata']) {
      const r = map[src];
      const badge = !r ? '<span class="badge gray">nincs adat</span>'
        : r.status === 'ok' ? '<span class="badge green">OK</span>'
        : r.status === 'running' ? '<span class="badge amber">fut</span>'
        : '<span class="badge red">hiba</span>';
      html += `<tr><td>${names[src]}</td><td>${badge}</td><td class="muted">${r ? fmtDateTime(r.finished_at || r.started_at) : '–'}</td></tr>`;
    }
    html += '</tbody></table>';
    if (!status.active_key) {
      html += '<p class="mt-16"><span class="badge amber">Figyelem</span> Nincs aktív Admin API kulcs — <a href="/admin-keys">állíts be egyet</a> az adatgyűjtéshez.</p>';
    }
    document.getElementById('freshness').innerHTML = html;
  }
};
