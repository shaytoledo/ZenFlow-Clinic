/* ZenFlow — Availability slot creation */

async function saveSlot(start, end) {
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
    optimistic.remove();
    mainCal.refetchEvents();
    showToast('Slot saved — patients can now book this time', '✅');
  } catch {
    optimistic.remove();
    showToast('Could not save slot', '⚠️');
  }
}
