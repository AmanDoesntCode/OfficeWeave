const root = element.querySelector('.recipient-grid');
const search = root.querySelector('.grid-search');
const head = root.querySelector('thead');
const body = root.querySelector('tbody');
let data = {}, page = 0;
function read() { try { data = JSON.parse(props.value || '{}'); } catch { data = {}; } draw(); }
function publish() { props.value = JSON.stringify(data); trigger('input'); }
function draw() {
  const query = search.value.toLowerCase();
  const rows = (data.rows || []).filter(row => Object.values(row.values).some(value => String(value).toLowerCase().includes(query)) || row.id.includes(query));
  const pages = Math.max(1, Math.ceil(rows.length / 50)); page = Math.min(page, pages - 1);
  head.replaceChildren(); body.replaceChildren();
  const heading = document.createElement('tr');
  for (const label of ['Select', 'Row', ...(data.headers || []), ...(data.alternate_column ? ['Alternate delivery'] : []), 'Status']) { const th = document.createElement('th'); th.textContent = label; heading.append(th); }
  head.append(heading);
  for (const row of rows.slice(page * 50, (page + 1) * 50)) {
    const tr = document.createElement('tr'); const td = document.createElement('td'); const checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.checked = !!row.selected; checkbox.disabled = !!data.locked;
    checkbox.setAttribute('aria-label', 'Select row ' + row.id);
    checkbox.addEventListener('change', () => { row.selected = checkbox.checked; publish(); }); td.append(checkbox); tr.append(td);
    for (const value of [row.id, ...(data.headers || []).map(h => row.values[h] || '')]) {
      const cell = document.createElement('td'); cell.textContent = value; cell.title = value;
      tr.append(cell);
    }
    if (data.alternate_column) {
      const cell = document.createElement('td'), delivery = document.createElement('select');
      delivery.setAttribute('aria-label', 'Alternate delivery for row ' + row.id);
      const hasAlternate = !!row.values[data.alternate_column]?.trim();
      for (const mode of hasAlternate ? ['cc','bcc'] : ['']) {
        const option = document.createElement('option'); option.value = mode;
        option.textContent = hasAlternate ? mode.toUpperCase() : 'No alternate address'; delivery.append(option);
      }
      delivery.value = hasAlternate ? (row.alternate_mode || 'cc') : '';
      delivery.disabled = !!data.locked || !hasAlternate;
      delivery.addEventListener('change', () => { row.alternate_mode = delivery.value; publish(); });
      cell.append(delivery); tr.append(cell);
    }
    const status = document.createElement('td'); status.textContent = row.warning || 'Ready';
    if (row.warning) status.className = 'warning-cell'; tr.append(status);
    body.append(tr);
  }
  root.querySelector('.grid-count').textContent = (data.rows || []).filter(r => r.selected).length + ' selected / ' + (data.rows || []).length + ' recipients';
  root.querySelector('.page-label').textContent = rows.length ? 'Page ' + (page + 1) + ' of ' + pages : 'No recipients yet';
  root.querySelector('.previous').disabled = page === 0;
  root.querySelector('.next').disabled = page >= pages - 1;
}
search.addEventListener('input', () => { page = 0; draw(); });
root.querySelector('.previous').addEventListener('click', () => { page--; draw(); });
root.querySelector('.next').addEventListener('click', () => { page++; draw(); });
root.querySelector('.select-all').addEventListener('click', () => {
  if (data.locked) return;
  for (const row of data.rows || []) row.selected = !row.invalid && (!data.exclude_duplicate_emails || !row.duplicate);
  publish(); draw();
});
root.querySelector('.clear-all').addEventListener('click', () => {
  if (data.locked) return;
  for (const row of data.rows || []) row.selected = false;
  publish(); draw();
});
read(); watch('value', read);
