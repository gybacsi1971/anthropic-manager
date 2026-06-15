/* Felhasználók kezelése */
window.pageInit = async function () {
  document.getElementById('add-user').addEventListener('click', openAdd);
  document.getElementById('users-table').addEventListener('click', onAction);
  await load();

  async function load() {
    const users = await api.listUsers();
    renderTable(users);
  }

  function roleBadge(role) {
    return role === 'admin' ? '<span class="badge blue">admin</span>' : '<span class="badge gray">néző</span>';
  }

  function renderTable(users) {
    let html = '<thead><tr><th>Név</th><th>Email</th><th>Szerepkör</th><th>Állapot</th><th>Utolsó belépés</th><th></th></tr></thead><tbody>';
    for (const u of users) {
      const active = u.is_active ? '<span class="badge green">aktív</span>' : '<span class="badge gray">inaktív</span>';
      html += `<tr>
        <td>${escapeHtml(u.name)}${u.id === currentUser.id ? ' <span class="muted">(te)</span>' : ''}</td>
        <td class="muted">${escapeHtml(u.email)}</td>
        <td>${roleBadge(u.role)}</td>
        <td>${active}</td>
        <td class="muted">${u.last_login_at ? fmtDateTime(u.last_login_at) : '–'}</td>
        <td class="num">
          <button class="btn btn-sm" data-action="edit" data-id="${u.id}"><span class="material-icons">edit</span></button>
          ${u.role === 'viewer' ? `<button class="btn btn-sm" data-action="scope" data-id="${u.id}" title="Hatókör (API kulcsok / workspace-ek)"><span class="material-icons">filter_alt</span></button>` : ''}
          <button class="btn btn-sm" data-action="reset" data-id="${u.id}">Jelszó</button>
          ${u.id === currentUser.id ? '' : `<button class="btn btn-sm btn-danger" data-action="delete" data-id="${u.id}"><span class="material-icons">delete</span></button>`}
        </td></tr>`;
    }
    html += '</tbody>';
    document.getElementById('users-table').innerHTML = html;
  }

  function openAdd() {
    showModal('Új felhasználó', `
      <div class="form-row"><label>Teljes név</label><input type="text" id="u-name"></div>
      <div class="form-row"><label>Email-cím</label><input type="email" id="u-email"></div>
      <div class="form-row"><label>Jelszó (min. 12 karakter)</label><input type="password" id="u-pass"></div>
      <div class="form-row"><label>Szerepkör</label><select id="u-role"><option value="viewer">Néző</option><option value="admin">Admin</option></select></div>
    `, async () => {
      const data = {
        name: document.getElementById('u-name').value.trim(),
        email: document.getElementById('u-email').value.trim(),
        password: document.getElementById('u-pass').value,
        role: document.getElementById('u-role').value,
      };
      await api.createUser(data);
      toast('Felhasználó létrehozva', 'success');
      await load();
    }, 'Létrehozás');
  }

  async function onAction(e) {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = parseInt(btn.dataset.id, 10);
    const action = btn.dataset.action;
    const users = await api.listUsers();
    const u = users.find((x) => x.id === id);
    if (!u) return;
    try {
      if (action === 'edit') {
        showModal('Felhasználó szerkesztése', `
          <div class="form-row"><label>Teljes név</label><input type="text" id="e-name" value="${escapeHtml(u.name)}"></div>
          <div class="form-row"><label>Szerepkör</label><select id="e-role"><option value="viewer"${u.role === 'viewer' ? ' selected' : ''}>Néző</option><option value="admin"${u.role === 'admin' ? ' selected' : ''}>Admin</option></select></div>
          <div class="form-row"><label>Aktív</label><select id="e-active"><option value="true"${u.is_active ? ' selected' : ''}>Igen</option><option value="false"${!u.is_active ? ' selected' : ''}>Nem</option></select></div>
        `, async () => {
          await api.updateUser(id, {
            name: document.getElementById('e-name').value.trim(),
            role: document.getElementById('e-role').value,
            is_active: document.getElementById('e-active').value === 'true',
          });
          toast('Mentve', 'success');
          await load();
        });
      } else if (action === 'reset') {
        showModal('Jelszó visszaállítása', `
          <p class="muted">${escapeHtml(u.email)}</p>
          <div class="form-row"><label>Új jelszó (min. 12 karakter)</label><input type="password" id="r-pass"></div>
        `, async () => {
          await api.resetUserPassword(id, document.getElementById('r-pass').value);
          toast('Jelszó visszaállítva', 'success');
        });
      } else if (action === 'scope') {
        await openScope(u);
      } else if (action === 'delete') {
        if (await confirmAction(`Biztosan törlöd: ${u.email}?`)) {
          await api.deleteUser(id);
          toast('Felhasználó törölve', 'success');
          await load();
        }
      }
    } catch (ex) {
      toast(ex.message, 'error');
    }
  }

  // ---- Hatókör (viewer → API kulcs / workspace) ----
  async function openScope(u) {
    let keys, workspaces, scope;
    try {
      [keys, workspaces, scope] = await Promise.all([
        api.metaApiKeys(), api.metaWorkspaces(), api.getUserScope(u.id),
      ]);
    } catch (e) { toast(e.message, 'error'); return; }

    const selKeys = new Set(scope.api_key_ids || []);
    const selWs = new Set(scope.workspace_ids || []);
    const wsName = {};
    workspaces.forEach((w) => { wsName[w.id] = w.name || w.id; });

    const listStyle = 'max-height:190px;overflow:auto;border:1px solid var(--border,#e3e3e3);border-radius:8px;padding:8px;display:flex;flex-direction:column;gap:5px';
    const itemStyle = 'display:flex;align-items:center;gap:8px;font-weight:400;cursor:pointer;margin:0';
    const groupStyle = 'font-weight:600;font-size:12px;color:var(--muted);margin-top:6px';

    let wsHtml = `<div class="form-row"><label>Workspace-ek (a teljes workspace forgalma)</label><div style="${listStyle}">`;
    if (!workspaces.length) wsHtml += '<div class="muted">Nincs workspace adat (futtass metaadat-gyűjtést).</div>';
    for (const w of workspaces) {
      wsHtml += `<label style="${itemStyle}"><input type="checkbox" data-ws="${escapeHtml(w.id)}"${selWs.has(w.id) ? ' checked' : ''}> ${escapeHtml(w.name || w.id)}</label>`;
    }
    wsHtml += '</div></div>';

    const byWs = {};
    for (const k of keys) { (byWs[k.workspace_id || ''] = byWs[k.workspace_id || ''] || []).push(k); }
    let keyHtml = `<div class="form-row"><label>API kulcsok</label><div style="${listStyle}">`;
    if (!keys.length) keyHtml += '<div class="muted">Nincs API kulcs adat (futtass metaadat-gyűjtést).</div>';
    for (const [wsId, list] of Object.entries(byWs)) {
      keyHtml += `<div style="${groupStyle}">${escapeHtml(wsName[wsId] || wsId || '(nincs workspace)')}</div>`;
      for (const k of list) {
        const st = k.status && k.status !== 'active' ? ` <span class="muted">(${escapeHtml(k.status)})</span>` : '';
        keyHtml += `<label style="${itemStyle}"><input type="checkbox" data-key="${escapeHtml(k.id)}"${selKeys.has(k.id) ? ' checked' : ''}> ${escapeHtml(k.name || k.id)}${st}</label>`;
      }
    }
    keyHtml += '</div></div>';

    const note = '<p class="muted" style="font-size:12.5px;margin-top:0">A néző csak a kijelölt kulcsok/workspace-ek <b>token-használatát</b>, <b>becsült költségét</b> és <b>Claude Code</b> aktivitását látja. Hatókör nélkül semmilyen használati adat nem jelenik meg. Az admin szerepkör mindig korlátlan.</p>';

    showModal(`Hatókör — ${u.email}`, note + wsHtml + keyHtml, async () => {
      const overlay = document.getElementById('modal-overlay');
      const api_key_ids = [...overlay.querySelectorAll('input[data-key]:checked')].map((el) => el.dataset.key);
      const workspace_ids = [...overlay.querySelectorAll('input[data-ws]:checked')].map((el) => el.dataset.ws);
      await api.setUserScope(u.id, { api_key_ids, workspace_ids });
      toast('Hatókör mentve', 'success');
    }, 'Mentés');
  }
};
