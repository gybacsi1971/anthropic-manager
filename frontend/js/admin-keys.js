/* Admin API kulcsok kezelése */
window.pageInit = async function () {
  document.getElementById('add-key').addEventListener('click', openAdd);
  document.getElementById('keys-table').addEventListener('click', onAction);
  await load();

  async function load() {
    const keys = await api.listAdminKeys();
    renderTable(keys);
  }

  function renderTable(keys) {
    const tbl = document.getElementById('keys-table');
    if (!keys.length) {
      tbl.innerHTML = '<tbody><tr><td><div class="empty"><span class="material-icons">vpn_key</span>Még nincs Admin API kulcs. Vegyél fel egyet a gyűjtés indításához.</div></td></tr></tbody>';
      return;
    }
    let html = '<thead><tr><th>Címke</th><th>Kulcs</th><th>Szervezet</th><th>Állapot</th><th>Utolsó teszt</th><th></th></tr></thead><tbody>';
    for (const k of keys) {
      const active = k.is_active
        ? '<span class="badge green"><span class="dot green"></span>Aktív</span>'
        : '<span class="badge gray"><span class="dot gray"></span>Inaktív</span>';
      const test = k.last_tested_at
        ? (k.last_test_ok ? `<span class="badge green">OK</span>` : `<span class="badge red">Hiba</span>`) + ` <span class="muted">${fmtDateTime(k.last_tested_at)}</span>`
        : '<span class="muted">–</span>';
      html += `<tr>
        <td>${escapeHtml(k.label)}</td>
        <td class="mono muted">${escapeHtml(k.masked_preview)}</td>
        <td>${escapeHtml(k.organization_name || '–')}</td>
        <td>${active}</td>
        <td>${test}</td>
        <td class="num">
          <button class="btn btn-sm" data-action="test" data-id="${k.id}"><span class="material-icons">wifi_tethering</span> Teszt</button>
          <button class="btn btn-sm" data-action="toggle" data-id="${k.id}" data-active="${k.is_active}">${k.is_active ? 'Inaktiválás' : 'Aktiválás'}</button>
          <button class="btn btn-sm btn-danger" data-action="delete" data-id="${k.id}"><span class="material-icons">delete</span></button>
        </td></tr>`;
    }
    html += '</tbody>';
    tbl.innerHTML = html;
  }

  function openAdd() {
    showModal('Új Admin API kulcs', `
      <div class="form-row"><label>Címke</label><input type="text" id="k-label" placeholder="pl. Fő szervezet"></div>
      <div class="form-row"><label>Kulcs értéke (sk-ant-admin…)</label><input type="password" id="k-value" placeholder="sk-ant-admin..."></div>
      <p class="muted" style="font-size:12px">A kulcs Fernet-titkosítással tárolódik; a teljes érték nem jelenik meg többé.</p>
    `, async () => {
      const label = document.getElementById('k-label').value.trim();
      const value = document.getElementById('k-value').value.trim();
      if (!label || !value) throw new Error('A címke és a kulcs is kötelező');
      await api.createAdminKey(label, value);
      toast('Kulcs hozzáadva', 'success');
      await load();
    }, 'Hozzáadás');
  }

  async function onAction(e) {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    const action = btn.dataset.action;
    try {
      if (action === 'test') {
        btn.disabled = true;
        const res = await api.testAdminKey(id);
        toast(`Kapcsolat OK: ${res.organization?.name || 'szervezet'}`, 'success');
        await load();
      } else if (action === 'toggle') {
        const active = btn.dataset.active === 'true';
        await api.updateAdminKey(id, { is_active: !active });
        await load();
      } else if (action === 'delete') {
        if (await confirmAction('Biztosan törlöd ezt a kulcsot?')) {
          await api.deleteAdminKey(id);
          toast('Kulcs törölve', 'success');
          await load();
        }
      }
    } catch (ex) {
      toast(ex.message, 'error');
      btn.disabled = false;
    }
  }
};
