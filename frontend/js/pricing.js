/* Árjegyzék — szerkeszthető modell-ár rács + hivatalos frissítés (review-then-apply) */
window.pageInit = async function () {
  const NUM_FIELDS = [
    ['input_usd_per_mtok', 'Input'],
    ['cache_write_5m_usd_per_mtok', '5m cache'],
    ['cache_write_1h_usd_per_mtok', '1h cache'],
    ['cache_read_usd_per_mtok', 'Cache read'],
    ['output_usd_per_mtok', 'Output'],
  ];
  const PATTERN_RE = /^[a-z0-9][a-z0-9._-]*$/;

  let models = [];

  document.getElementById('add-row').addEventListener('click', onAdd);
  document.getElementById('save').addEventListener('click', onSave);
  document.getElementById('refresh-official').addEventListener('click', onRefresh);
  document.getElementById('pricing-table').addEventListener('click', onTableClick);

  await load();

  async function load() {
    const [list, settings] = await Promise.all([api.listPricing(), api.getSettings()]);
    models = list.map((m) => ({ ...m, _status: '' }));
    document.getElementById('ws-price').value = settings['pricing.web_search_usd_per_request'];
    render();
  }

  function render() {
    const rowBg = (s) => (s === 'new' ? 'background:#e8f5e9' : s === 'changed' ? 'background:#fff8e1' : '');
    let html = '<thead><tr><th>Modell</th><th>Minta (előtag)</th>'
      + NUM_FIELDS.map(([, lbl]) => `<th class="num">${lbl}<br><span class="muted" style="font-weight:400;font-size:11px">$/MTok</span></th>`).join('')
      + '<th>Forrás</th><th></th></tr></thead><tbody>';
    if (!models.length) {
      html += '<tr><td colspan="9"><div class="empty"><span class="material-icons">sell</span>Nincs ármodell. Vegyél fel egyet, vagy frissíts a hivatalos árlistából.</div></td></tr>';
    }
    models.forEach((m, i) => {
      const src = m._status === 'new' ? '<span class="badge green">új</span>'
        : m._status === 'changed' ? '<span class="badge" style="background:#fff3cd;color:#7a5b00">módosult</span>'
        : `<span class="badge gray">${escapeHtml(m.source || 'manual')}</span>`;
      html += `<tr data-idx="${i}" style="${rowBg(m._status)}">
        <td><input data-f="display_name" value="${escapeHtml(m.display_name || '')}" style="width:100%;min-width:140px"></td>
        <td><input data-f="model_pattern" class="mono" value="${escapeHtml(m.model_pattern || '')}" style="width:100%;min-width:150px" spellcheck="false"></td>
        ${NUM_FIELDS.map(([f]) => `<td class="num"><input type="number" step="0.01" min="0" data-f="${f}" value="${m[f]}" style="width:88px;text-align:right"></td>`).join('')}
        <td>${src}</td>
        <td class="num"><button class="btn btn-sm btn-danger" data-action="del" data-idx="${i}"><span class="material-icons">delete</span></button></td>
      </tr>`;
    });
    html += '</tbody>';
    document.getElementById('pricing-table').innerHTML = html;
  }

  // A rács aktuális (esetleg szerkesztett) tartalmát olvassa vissza állapotba.
  function readGrid() {
    const out = [];
    document.querySelectorAll('#pricing-table tbody tr[data-idx]').forEach((tr) => {
      const get = (f) => { const el = tr.querySelector(`[data-f="${f}"]`); return el ? el.value : ''; };
      const m = {
        model_pattern: get('model_pattern').trim(),
        display_name: get('display_name').trim(),
        source: models[+tr.dataset.idx]?.source || 'manual',
        _status: models[+tr.dataset.idx]?._status || '',
      };
      for (const [f] of NUM_FIELDS) m[f] = get(f);
      out.push(m);
    });
    return out;
  }

  function onAdd() {
    models = readGrid();
    models.push({ model_pattern: '', display_name: '', input_usd_per_mtok: 0, cache_write_5m_usd_per_mtok: 0,
      cache_write_1h_usd_per_mtok: 0, cache_read_usd_per_mtok: 0, output_usd_per_mtok: 0, source: 'manual', _status: 'new' });
    render();
  }

  function onTableClick(e) {
    const btn = e.target.closest('button[data-action="del"]');
    if (!btn) return;
    models = readGrid();
    models.splice(+btn.dataset.idx, 1);
    render();
  }

  async function onRefresh() {
    const btn = document.getElementById('refresh-official');
    btn.disabled = true;
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="material-icons spin">autorenew</span> Letöltés…';
    try {
      const res = await api.refreshPricing();
      const proposed = res.proposed || [];
      models = mergeProposed(readGrid(), proposed);
      render();
      const changed = models.filter((m) => m._status).length;
      toast(`${proposed.length} modell betöltve a hivatalos árlistából (${changed} eltérés). Nézd át és mentsd.`, 'success', 6000);
    } catch (e) {
      toast(e.message, 'error', 6000);
    } finally {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }

  // A javasolt (hivatalos) értékeket beolvasztja a meglévő rácsba: meglévő minta → frissítés
  // (és 'changed' jelölés, ha tényleg eltér), új minta → hozzáadás ('new'). Semmit nem ment.
  function mergeProposed(current, proposed) {
    const byPattern = new Map(current.map((m) => [m.model_pattern, m]));
    for (const p of proposed) {
      const ex = byPattern.get(p.model_pattern);
      if (ex) {
        let diff = ex.display_name !== p.display_name;
        for (const [f] of NUM_FIELDS) {
          const a = parseFloat(ex[f]);
          if (!Number.isNaN(a) && a !== parseFloat(p[f])) diff = true;  // üres cella nem jelez ál-módosulást
          ex[f] = p[f];
        }
        ex.display_name = p.display_name;
        ex.source = 'official';
        if (diff) ex._status = 'changed';
      } else {
        const row = { model_pattern: p.model_pattern, display_name: p.display_name, source: 'official', _status: 'new' };
        for (const [f] of NUM_FIELDS) row[f] = p[f];
        current.push(row);
        byPattern.set(p.model_pattern, row);
      }
    }
    return current;
  }

  async function onSave() {
    const grid = readGrid();
    const items = [];
    const seen = new Set();
    try {
      grid.forEach((m, i) => {
        if (!PATTERN_RE.test(m.model_pattern)) throw new Error(`Érvénytelen minta: "${m.model_pattern}" (csak kisbetű, szám, . _ -)`);
        if (seen.has(m.model_pattern)) throw new Error(`Ismétlődő minta: ${m.model_pattern}`);
        seen.add(m.model_pattern);
        if (!m.display_name) throw new Error(`A megjelenített név kötelező (${m.model_pattern})`);
        const item = { model_pattern: m.model_pattern, display_name: m.display_name, source: m.source || 'manual', sort_order: (i + 1) * 10 };
        for (const [f, lbl] of NUM_FIELDS) {
          const v = parseFloat(m[f]);
          if (!isFinite(v) || v < 0) throw new Error(`Érvénytelen ${lbl} ár (${m.model_pattern})`);
          item[f] = v;
        }
        items.push(item);
      });
      if (!items.length) throw new Error('Legalább egy modell kötelező');

      const wsVal = parseFloat(document.getElementById('ws-price').value);
      if (!isFinite(wsVal) || wsVal < 0) throw new Error('Érvénytelen web keresés ár');

      await api.savePricing(items);
      await api.updateSettings({ 'pricing.web_search_usd_per_request': wsVal });
      toast('Árjegyzék mentve', 'success');
      await load();
    } catch (e) {
      toast(e.message, 'error', 6000);
    }
  }
};
