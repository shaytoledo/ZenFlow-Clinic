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

// ── Calendar list + visibility ─────────────────────────────────────────────────

const _hiddenCals = new Set();

// ── Calendar rename (localStorage, display-only) ───────────────────────────────

const _calRenames = JSON.parse(localStorage.getItem('zf_cal_renames') || '{}');

function _saveCalRenames() {
  localStorage.setItem('zf_cal_renames', JSON.stringify(_calRenames));
}

function _showCalCtxMenu(e, calId, originalName) {
  e.preventDefault();
  e.stopPropagation();
  // Remove any existing menu
  const old = document.getElementById('zf-cal-ctx');
  if (old) old.remove();

  const menu = document.createElement('div');
  menu.id = 'zf-cal-ctx';
  menu.style.cssText = `
    position:fixed;left:${e.clientX}px;top:${e.clientY}px;
    background:white;border:1px solid #E5E7EB;border-radius:8px;
    box-shadow:0 4px 16px rgba(0,0,0,0.12);z-index:9999;
    min-width:148px;padding:4px 0;font-family:inherit;`;

  const rename = document.createElement('button');
  rename.textContent = '✏️  Rename';
  rename.style.cssText = 'display:block;width:100%;text-align:left;padding:8px 14px;border:none;background:none;cursor:pointer;font-size:13px;color:#374151;';
  rename.onmouseenter = () => { rename.style.background = '#F3F4F6'; };
  rename.onmouseleave = () => { rename.style.background = ''; };
  rename.onclick = () => {
    menu.remove();
    const current = _calRenames[calId] || originalName;
    const newName = prompt(`Rename "${originalName}":`, current);
    if (newName && newName.trim() && newName.trim() !== originalName) {
      _calRenames[calId] = newName.trim();
    } else if (newName !== null && newName.trim() === originalName) {
      delete _calRenames[calId];  // reset to original
    }
    _saveCalRenames();
    loadCalendarList();  // re-render with updated name
  };
  menu.appendChild(rename);

  const reset = document.createElement('button');
  reset.textContent = '↩  Reset name';
  reset.style.cssText = rename.style.cssText + 'color:#9CA3AF;';
  reset.onmouseenter = () => { reset.style.background = '#F3F4F6'; };
  reset.onmouseleave = () => { reset.style.background = ''; };
  reset.onclick = () => {
    menu.remove();
    delete _calRenames[calId];
    _saveCalRenames();
    loadCalendarList();
  };
  if (_calRenames[calId]) menu.appendChild(reset);

  document.body.appendChild(menu);
  // Close on next click anywhere
  setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }), 0);
}

function _showLocalCalendarBadge() {
  $('cal-items').innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;padding:4px 0 6px;">
      <span style="width:10px;height:10px;background:#27ae60;border-radius:50%;flex-shrink:0;"></span>
      <span style="font-size:12px;color:#374151;font-weight:500;">Local Calendar</span>
    </div>
    <p style="font-size:11px;color:#9CA3AF;line-height:1.5;margin-top:2px;">
      Slots saved here are visible to patients.<br>
      <a href="/settings" style="color:#0D9488;">Connect Google</a> to sync.
    </p>`;
}

async function loadCalendarList() {
  try {
    const r = await fetch('/api/calendars');
    if (!r.ok) { _showLocalCalendarBadge(); return; }
    const data = await r.json();
    if (!data.length) { _showLocalCalendarBadge(); return; }
    const el = $('cal-items');
    el.innerHTML = '';
    data.forEach(c => {
      const row = document.createElement('label');
      row.className = 'cal-item';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      cb.className = 'cal-checkbox';
      cb.style.accentColor = c.color;
      cb.addEventListener('change', () => {
        if (cb.checked) {
          _hiddenCals.delete(c.id);
          row.classList.remove('cal-hidden');
          mainCal.refetchEvents();
        } else {
          _hiddenCals.add(c.id);
          row.classList.add('cal-hidden');
          // Immediately remove matching events from the rendered calendar
          mainCal.getEvents().forEach(ev => {
            if (ev.extendedProps?.calendarId === c.id) ev.remove();
          });
        }
      });

      const name = document.createElement('span');
      name.className = 'cal-name';
      name.textContent = _calRenames[c.id] || c.name;

      row.appendChild(cb);
      row.appendChild(name);
      row.addEventListener('contextmenu', e => _showCalCtxMenu(e, c.id, c.name));
      el.appendChild(row);
    });
  } catch (_) { _showLocalCalendarBadge(); }
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

const _menuBtn = $('menu-btn');
if (_menuBtn) _menuBtn.addEventListener('click', () => { const sb = $('sidebar'); if (sb) sb.classList.toggle('collapsed'); });

async function saveSlot(start, end) {
  // Optimistic: render the event immediately before the API responds
  const optimistic = mainCal.addEvent({
    title: '✅ Available',
    start,
    end,
    backgroundColor: '#27ae60',
    borderColor: '#1e8449',
    extendedProps: { type: 'available', calendarId: 'optimistic' },
  });
  try {
    const r = await fetch('/api/availability', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start: start.toISOString(), end: end.toISOString() }),
    });
    if (!r.ok) throw new Error();
    optimistic.remove();  // remove temporary event
    mainCal.refetchEvents();  // reload confirmed events
    showToast('Slot saved — patients can now book this time', '✅');
  } catch {
    optimistic.remove();  // revert on failure
    showToast('Could not save slot', '⚠️');
  }
}

// ── Event popover ──────────────────────────────────────────────────────────────

let _popEventId = null;
let _popCalId   = null;
let _autoCloseTimer = null;

function closeEventPopover() {
  $('event-popover').classList.add('hidden');
  $('overlay').classList.add('hidden');
  if (_autoCloseTimer) { clearTimeout(_autoCloseTimer); _autoCloseTimer = null; }
  _popEventId = null;
  _popCalId   = null;
}

function _positionPopover(anchorEl) {
  const pop  = $('event-popover');
  const rect = anchorEl.getBoundingClientRect();
  const pw   = pop.offsetWidth  || 300;
  const ph   = pop.offsetHeight || 240;
  const vw   = window.innerWidth;
  const vh   = window.innerHeight;

  let left = rect.right + 12;
  let top  = rect.top;

  if (left + pw > vw - 12) left = Math.max(12, rect.left - pw - 12);
  if (top  + ph > vh - 12) top  = Math.max(8,  vh - ph  - 12);

  pop.style.left = `${left}px`;
  pop.style.top  = `${top}px`;
}

function showEventPopover(event, anchorEl) {
  closeEventPopover();          // dismiss any previously open popover first
  const props = event.extendedProps;
  const type  = props.type || 'busy';

  // Color bar
  $('pop-color-bar').style.background = event.backgroundColor || '#4285f4';

  // Title
  $('pop-title').textContent = event.title;

  // Date + time
  const dateStr = event.start.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
  const timeStr = event.end ? `${fmt(event.start)} – ${fmt(event.end)}` : fmt(event.start);
  $('pop-time').textContent = `${dateStr}  ·  ${timeStr}`;

  if (type === 'available') {
    // Confirmation mode — ask before removing
    _popEventId = event.id;
    _popCalId   = props.calendarId;
    $('pop-cal-row').classList.add('hidden');
    $('pop-confirm-msg').classList.remove('hidden');
    $('pop-delete-btn').classList.remove('hidden');
    $('pop-close-btn').textContent = 'Keep';
  } else {
    // Info mode — read-only busy event
    $('pop-cal-name').textContent = props.calendarName || 'Calendar';
    $('pop-cal-row').classList.remove('hidden');
    $('pop-confirm-msg').classList.add('hidden');
    $('pop-delete-btn').classList.add('hidden');
    $('pop-close-btn').textContent = 'Close';
  }

  $('overlay').classList.remove('hidden');
  $('event-popover').classList.remove('hidden');
  requestAnimationFrame(() => _positionPopover(anchorEl));

  // Auto-close after 5 seconds
  _autoCloseTimer = setTimeout(closeEventPopover, 5000);
}

async function deleteSlot() {
  if (!_popEventId || !_popCalId) return;
  try {
    const r = await fetch(
      `/api/availability/${encodeURIComponent(_popEventId)}?calendarId=${encodeURIComponent(_popCalId)}`,
      { method: 'DELETE' },
    );
    if (!r.ok) throw new Error(await r.text());
    closeEventPopover();
    mainCal.refetchEvents();
    showToast('Slot removed', '🗑️');
  } catch (err) {
    console.error('deleteSlot error:', err);
    showToast('Could not remove slot', '⚠️');
  }
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
  $('pop-close').addEventListener('click', closeEventPopover);
  $('pop-close-btn').addEventListener('click', closeEventPopover);
  $('pop-delete-btn').addEventListener('click', deleteSlot);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeEventPopover(); });

  $('overlay').addEventListener('click', closeEventPopover);

  // Close when any button outside the popover is clicked
  document.addEventListener('click', e => {
    if ($('event-popover').classList.contains('hidden')) return;
    const btn = e.target.closest('button, [role="button"], .fc-button, .mini-week-row, .cal-item');
    if (btn && !$('event-popover').contains(btn)) closeEventPopover();
  });

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
        .then(events => ok(
          _hiddenCals.size === 0
            ? events
            : events.filter(e => !_hiddenCals.has(e.extendedProps?.calendarId))
        ))
        .catch(err => { showToast('Could not load events', '⚠️'); fail(err); });
    },

    // selectMinDistance > 0 means a plain click does NOT trigger select —
    // the user must drag to create a slot. This prevents accidental slot creation
    // and stops single-clicks from triggering a refetchEvents that disrupts the popover.
    selectMinDistance: 5,

    select(info) {
      mainCal.unselect();
      saveSlot(info.start, info.end);
    },

    // Use eventClick (FullCalendar's own hook) so stopPropagation() fully
    // prevents FullCalendar's internal click processing and any resulting
    // re-renders that would close the popover.
    eventClick(info) {
      info.jsEvent.stopPropagation();
      info.jsEvent.preventDefault();
      showEventPopover(info.event, info.el);
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
