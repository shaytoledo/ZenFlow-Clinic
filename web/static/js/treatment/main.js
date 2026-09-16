// Treatment page — Complete session, utilities, and page start-up (must load last).
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Complete Session ───────────────────────────────────────────────────────────

async function completeSession() {
  const btn = document.getElementById('complete-btn');
  if (!confirm('Mark this session as complete and return to the dashboard?')) return;
  clearTimeout(_autoSaveTimer);
  btn.disabled = true;
  btn.innerHTML = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" style="animation:spin 1s linear infinite"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Saving…';
  try {
    const r = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/complete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tongue_observation:  document.getElementById('tongue-input').value,
        pulse_observation:   document.getElementById('pulse-input').value,
        session_notes:       document.getElementById('session-notes').value,
        used_points:         usedPoints,
        therapist_diagnosis: document.getElementById('therapist-diagnosis')?.value || '',
        therapist_notes:     document.getElementById('therapist-notes')?.value || '',
      }),
    });
    const result = await r.json();
    if (!r.ok) throw new Error(result.detail || 'Failed to complete session');
    document.getElementById('notes-status').textContent = 'Saved ✓';
    btn.innerHTML = '✓ Session Complete — Redirecting…';
    btn.style.background = '#16A34A';
    setTimeout(() => { window.location.href = result.redirect || '/'; }, 1200);
  } catch (e) {
    btn.innerHTML = '⚠ ' + e.message;
    btn.style.background = '#DC2626';
    setTimeout(() => {
      btn.innerHTML = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg> Complete Session';
      btn.style.background = ''; btn.disabled = false;
    }, 3000);
  }
}

// ── Utility ────────────────────────────────────────────────────────────────────

function escHtml(str) {
  return (str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Init
renderAdvice();
loadTreatment();
