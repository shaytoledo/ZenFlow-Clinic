/* ZenFlow — Event click popover */

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
  closeEventPopover();
  const props = event.extendedProps;
  const type  = props.type || 'busy';

  $('pop-color-bar').style.background = event.backgroundColor || '#4285f4';
  $('pop-title').textContent = event.title;

  const dateStr = event.start.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
  const timeStr = event.end ? `${fmt(event.start)} – ${fmt(event.end)}` : fmt(event.start);
  $('pop-time').textContent = `${dateStr}  ·  ${timeStr}`;

  if (type === 'available') {
    _popEventId = event.id;
    _popCalId   = props.calendarId;
    $('pop-cal-row').classList.add('hidden');
    $('pop-confirm-msg').classList.remove('hidden');
    $('pop-delete-btn').classList.remove('hidden');
    $('pop-close-btn').textContent = 'Keep';
  } else {
    $('pop-cal-name').textContent = props.calendarName || 'Calendar';
    $('pop-cal-row').classList.remove('hidden');
    $('pop-confirm-msg').classList.add('hidden');
    $('pop-delete-btn').classList.add('hidden');
    $('pop-close-btn').textContent = 'Close';
  }

  $('overlay').classList.remove('hidden');
  $('event-popover').classList.remove('hidden');
  requestAnimationFrame(() => _positionPopover(anchorEl));

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
