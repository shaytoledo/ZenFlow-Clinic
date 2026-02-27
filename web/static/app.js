/* ZenFlow — Therapist Availability Calendar */

// ── Helpers ───────────────────────────────────────────────────────────────────

const $  = id => document.getElementById(id);
let _toastTimer;

function showToast(msg, icon = '✓') {
  clearTimeout(_toastTimer);
  $('toast-icon').textContent = icon;
  $('toast-msg').textContent  = msg;
  $('toast').classList.add('show');
  _toastTimer = setTimeout(() => $('toast').classList.remove('show'), 3000);
}

function fmt(d) {
  return new Date(d).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

// ── Calendar list ──────────────────────────────────────────────────────────────

const calColors = {};

async function loadCalendarList() {
  try {
    const data = await fetch('/api/calendars').then(r => r.json());
    const el = $('cal-items');
    el.innerHTML = '';
    data.forEach(c => {
      calColors[c.id] = c.color;
      const row = document.createElement('div');
      row.className = 'cal-item';
      row.innerHTML = `<span class="cal-dot" style="background:${c.color}"></span><span>${c.name}</span>`;
      el.appendChild(row);
    });
  } catch (_) {}
}

// ── Mini calendar ──────────────────────────────────────────────────────────────

let miniDate = new Date();
const MONTHS = ['January','February','March','April','May','June',
                'July','August','September','October','November','December'];
const DOWS   = ['S','M','T','W','T','F','S'];

function renderMiniCal(selected) {
  const yr = miniDate.getFullYear(), mo = miniDate.getMonth();
  $('mini-cal-title').textContent = `${MONTHS[mo]} ${yr}`;

  const grid = $('mini-cal-grid');
  grid.innerHTML = '';

  // Headers
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

    // First cell of this row — used for week navigation
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


// ── Sidebar toggle ─────────────────────────────────────────────────────────────

$('menu-btn').addEventListener('click', () => $('sidebar').classList.toggle('collapsed'));

async function saveSlot(start, end) {
  try {
    const r = await fetch('/api/availability', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start: start.toISOString(), end: end.toISOString() }),
    });
    if (!r.ok) throw new Error();
    mainCal.refetchEvents();
    showToast('Slot saved — patients can now book this time', '✅');
  } catch { showToast('Could not save slot', '⚠️'); }
}

// ── Event content renderer ─────────────────────────────────────────────────────

function renderEvent(info) {
  const props = info.event.extendedProps;
  const type  = props.type || 'busy';
  const start = fmt(info.event.start);
  const end   = info.event.end ? fmt(info.event.end) : '';

  const el = document.createElement('div');
  el.className = type === 'available' ? 'ev-available' : 'ev-busy';

  el.innerHTML = `
    <div class="ev-time">${start}${end ? ` – ${end}` : ''}</div>
    <div class="ev-title">${info.event.title}</div>
  `;
  return { domNodes: [el] };
}

// ── Main calendar ──────────────────────────────────────────────────────────────

let mainCal;

document.addEventListener('DOMContentLoaded', () => {
  loadCalendarList();
  renderMiniCal(new Date());

  mainCal = new FullCalendar.Calendar($('calendar'), {
    initialView: 'timeGridWeek',
    headerToolbar: {
      left:   'prev,next today',
      center: 'title',
      right:  'dayGridMonth,timeGridWeek,timeGridDay',
    },
    buttonText: { month: 'Month', week: 'Week', day: 'Day', today: 'Today' },
    slotMinTime: '07:00:00',
    slotMaxTime: '22:00:00',
    slotDuration: '01:00:00',
    snapDuration: '01:00:00',
    allDaySlot: false,
    nowIndicator: true,
    selectable: true,
    selectMirror: true,
    unselectAuto: false,
    scrollTime: '08:00:00',

    events(info, ok, fail) {
      fetch(`/api/events?start=${encodeURIComponent(info.startStr)}&end=${encodeURIComponent(info.endStr)}`)
        .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
        .then(ok)
        .catch(err => { showToast('Could not load events', '⚠️'); fail(err); });
    },

    select(info) {
      mainCal.unselect();
      saveSlot(info.start, info.end);
    },

    eventContent: renderEvent,

    datesSet(info) {
      const mid = new Date((info.start.getTime() + info.end.getTime()) / 2);
      miniDate = new Date(mid.getFullYear(), mid.getMonth(), 1);
      renderMiniCal(mid);
    },
  });

  mainCal.render();
});
