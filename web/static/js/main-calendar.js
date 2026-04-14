/* ZenFlow — FullCalendar initialisation */

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

let mainCal;

document.addEventListener('DOMContentLoaded', () => {
  // Sidebar toggle
  const _menuBtn = $('menu-btn');
  if (_menuBtn) _menuBtn.addEventListener('click', () => {
    const sb = $('sidebar');
    if (sb) sb.classList.toggle('collapsed');
  });

  // Popover controls
  $('pop-close').addEventListener('click', closeEventPopover);
  $('pop-close-btn').addEventListener('click', closeEventPopover);
  $('pop-delete-btn').addEventListener('click', deleteSlot);
  $('overlay').addEventListener('click', closeEventPopover);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeEventPopover(); });

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

    // selectMinDistance > 0: plain click does NOT trigger select — user must drag
    selectMinDistance: 5,

    select(info) {
      mainCal.unselect();
      saveSlot(info.start, info.end);
    },

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
