// Treatment page — Page state, URL parts, server config and notes auto-save.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

const defaultAdvice = [
  { id: 'sleep', icon: '🌙', category: 'Sleep', text: 'Establish a wind-down routine — dim lights 1 hour before bed, avoid screens, try 4-7-8 breathing.', enabled: true },
  { id: 'diet',  icon: '🥗', category: 'Diet',  text: 'Reduce greasy and cold-raw foods. Favour warm, cooked meals with bitter greens and ginger tea.', enabled: true },
  { id: 'stress',icon: '🧘', category: 'Stress',text: 'Practice 10 min of Liver Qi-moving stretches daily. Consider journaling.', enabled: true },
  { id: 'walk',  icon: '🚶', category: 'Movement', text: 'Gentle daily movement — 30-minute walks preferred over intense workouts during treatment.', enabled: false },
];

let advice = JSON.parse(JSON.stringify(defaultAdvice));
let usedPoints = [];
let aiPointRationale = {};
let _mfRating = null;
let _isManual = false;

const pathParts = window.location.pathname.split('/').filter(Boolean);
const patientId = pathParts[1];
const aptDate   = pathParts[2];
const aptTimeSlug = pathParts[3] || '';
const aptTime   = aptTimeSlug.replace('-', ':');
// Live updates (Phase 3.4): stream status changes instead of polling when the server offers it.
const ZF_SSE_UPDATES = Boolean(JSON.parse(document.getElementById('treatment-config').textContent || '{}').sse_updates);

// ── Auto-save ─────────────────────────────────────────────────────────────────

let _autoSaveTimer;

async function autoSave() {
  if (!patientId || !aptDate || !aptTimeSlug) return;
  try {
    await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tongue_observation:   document.getElementById('tongue-input').value,
        pulse_observation:    document.getElementById('pulse-input').value,
        session_notes:        document.getElementById('session-notes').value,
        used_points:          usedPoints,
        therapist_diagnosis:  document.getElementById('therapist-diagnosis')?.value || '',
        therapist_notes:      document.getElementById('therapist-notes')?.value || '',
      }),
    });
    document.getElementById('notes-status').textContent = 'Auto-saved ✓';
    const tdStatus = document.getElementById('td-status');
    if (tdStatus) tdStatus.textContent = 'Saved ✓';
  } catch (e) {
    document.getElementById('notes-status').textContent = 'Save failed';
    console.error('autoSave error:', e);
  }
}

function scheduleAutoSave() {
  document.getElementById('notes-status').textContent = 'Unsaved…';
  clearTimeout(_autoSaveTimer);
  _autoSaveTimer = setTimeout(autoSave, 1500);
}
