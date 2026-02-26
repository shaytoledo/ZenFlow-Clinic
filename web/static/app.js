/* ZenFlow Therapist Calendar */

const toast = document.getElementById('toast');
let toastTimer;

function showToast(msg, isError = false) {
  clearTimeout(toastTimer);
  toast.textContent = msg;
  toast.className = 'show' + (isError ? ' error' : '');
  toastTimer = setTimeout(() => { toast.className = ''; }, 3000);
}

document.addEventListener('DOMContentLoaded', function () {
  const calEl = document.getElementById('calendar');

  const calendar = new FullCalendar.Calendar(calEl, {
    initialView: 'timeGridWeek',
    headerToolbar: {
      left:   'prev,next today',
      center: 'title',
      right:  'timeGridWeek,timeGridDay',
    },
    slotMinTime: '07:00:00',
    slotMaxTime: '21:00:00',
    slotDuration: '01:00:00',
    snapDuration: '01:00:00',
    allDaySlot: false,
    nowIndicator: true,
    height: 'auto',
    selectable: true,
    selectMirror: true,

    // ── Load events ──────────────────────────────────────────────────────
    events: function (info, successCb, failureCb) {
      fetch(`/api/events?start=${info.startStr}&end=${info.endStr}`)
        .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
        .then(successCb)
        .catch(err => { showToast('Failed to load events', true); failureCb(err); });
    },

    // ── Click / drag empty slot → create availability ─────────────────
    select: function (info) {
      calendar.unselect();
      fetch('/api/availability', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start: info.startStr, end: info.endStr }),
      })
        .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
        .then(() => { calendar.refetchEvents(); showToast('Slot marked as available ✅'); })
        .catch(() => showToast('Could not save slot', true));
    },

    // ── Click green event → remove availability ───────────────────────
    eventClick: function (info) {
      const props = info.event.extendedProps;
      if (props.type !== 'available') return;

      if (!confirm('Remove this available slot?')) return;

      const calId = encodeURIComponent(props.calendarId);
      fetch(`/api/availability/${info.event.id}?calendarId=${calId}`, { method: 'DELETE' })
        .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
        .then(() => { calendar.refetchEvents(); showToast('Slot removed'); })
        .catch(() => showToast('Could not remove slot', true));
    },

    // ── Cursor hint on available events ──────────────────────────────
    eventDidMount: function (info) {
      if (info.event.extendedProps.type === 'available') {
        info.el.style.cursor = 'pointer';
        info.el.title = 'Click to remove this available slot';
      } else {
        info.el.style.cursor = 'default';
      }
    },
  });

  calendar.render();
});
