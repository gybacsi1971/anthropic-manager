/* Tevékenységnapló */
window.pageInit = async function () {
  const PAGE = 50;
  let offset = 0;
  let total = 0;

  document.getElementById('apply').addEventListener('click', () => { offset = 0; load(); });
  document.getElementById('prev').addEventListener('click', () => { if (offset >= PAGE) { offset -= PAGE; load(); } });
  document.getElementById('next').addEventListener('click', () => { if (offset + PAGE < total) { offset += PAGE; load(); } });
  await load();

  async function load() {
    const action = document.getElementById('action').value.trim() || undefined;
    let res;
    try { res = await api.activity({ limit: PAGE, offset, action }); }
    catch (e) { toast(e.message, 'error'); return; }
    total = res.total;
    renderTable(res.items);
    document.getElementById('page-info').textContent =
      total ? `${offset + 1}–${Math.min(offset + PAGE, total)} / ${total}` : 'Nincs találat';
    document.getElementById('prev').disabled = offset === 0;
    document.getElementById('next').disabled = offset + PAGE >= total;
  }

  function renderTable(items) {
    if (!items.length) { document.getElementById('log-table').innerHTML = '<tbody><tr><td class="empty">Nincs napló-bejegyzés</td></tr></tbody>'; return; }
    let html = '<thead><tr><th>Időpont</th><th>Felhasználó</th><th>Művelet</th><th>Cél</th><th>Részletek</th><th>IP</th></tr></thead><tbody>';
    for (const it of items) {
      const detail = it.detail_parsed ? escapeHtml(JSON.stringify(it.detail_parsed)) : '';
      const who = it.user_email ? escapeHtml(it.user_name || it.user_email) : '<span class="muted">rendszer</span>';
      const target = it.target_type ? `${escapeHtml(it.target_type)}${it.target_id ? ' #' + escapeHtml(it.target_id) : ''}` : '';
      html += `<tr>
        <td class="muted">${fmtDateTime(it.created_at)}</td>
        <td>${who}</td>
        <td><span class="badge gray">${escapeHtml(it.action)}</span></td>
        <td class="muted">${target}</td>
        <td class="muted mono" style="font-size:12px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${detail}</td>
        <td class="muted">${escapeHtml(it.ip || '')}</td>
      </tr>`;
    }
    html += '</tbody>';
    document.getElementById('log-table').innerHTML = html;
  }
};
