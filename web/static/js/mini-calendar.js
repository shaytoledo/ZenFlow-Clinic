/* ZenFlow — Mini calendar (sidebar date picker) */

let miniDate = new Date();
const MONTHS = ['January','February','March','April','May','June',
                'July','August','September','October','November','December'];
const DOWS   = ['S','M','T','W','T','F','S'];

function renderMiniCal(selected) {
  const yr = miniDate.getFullYear(), mo = miniDate.getMonth();
  $('mini-cal-title').textContent = `${MONTHS[mo]} ${yr}`;

  const grid = $('mini-cal-grid');
  grid.innerHTML = '';

  const hdr = document.createElement('div');
  hdr.className = 'mini-dow-row';
  DOWS.forEach(d => {
    const c = document.createElement('div');
    c.className = 'mini-dow';
    c.textContent = d;
    hdr.appendChild(c);
  });
  grid.appendChild(hdr);

  const firstDay = new Date(yr, mo, 1).getDay();
  const daysInMo = new Date(yr, mo + 1, 0).getDate();
  const prevDays = new Date(yr, mo, 0).getDate();
  const today    = new Date();

  let cells = [];
  for (let i = firstDay - 1; i >= 0; i--)
    cells.push({ d: prevDays - i, m: mo - 1, y: yr, other: true });
  for (let d = 1; d <= daysInMo; d++)
    cells.push({ d, m: mo, y: yr, other: false });
  while (cells.length % 7) cells.push({ d: cells.length - daysInMo - firstDay + 1, m: mo + 1, y: yr, other: true });

  for (let r = 0; r < cells.length / 7; r++) {
    const row = document.createElement('div');
    row.className = 'mini-week-row';

    const firstCell = cells[r * 7];
    const weekStart = new Date(firstCell.y, firstCell.m, firstCell.d);

    row.addEventListener('click', () => {
      mainCal.gotoDate(weekStart);
      mainCal.changeView('timeGridWeek');
      renderMiniCal(weekStart);
    });

    for (let c = 0; c < 7; c++) {
      const cell = cells[r * 7 + c];
      const date = new Date(cell.y, cell.m, cell.d);
      const el   = document.createElement('div');
      el.className = 'mini-day';
      el.textContent = cell.d;
      if (cell.other) el.classList.add('other-month');
      if (date.toDateString() === today.toDateString()) el.classList.add('today');
      if (selected && date.toDateString() === selected.toDateString()) el.classList.add('selected');
      row.appendChild(el);
    }
    grid.appendChild(row);
  }
}
