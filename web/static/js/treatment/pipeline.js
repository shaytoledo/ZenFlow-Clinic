// Treatment page — AI generation: diagnosis/points requests, following a run, cancel.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.
// What the AI points area shows is always one showPointState() call (point-states.js).

const NOTES_URL = () => `/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}`;

// Apply lifestyle recommendations from a recommendations object.
function _applyRecommendations(rec) {
  if (!rec) return;
  if (!(rec.diet || rec.sleep || rec.exercise || rec.stress)) return;
  advice = [];
  if (rec.sleep)    advice.push({ id: 'sleep',    icon: '🌙', category: 'Sleep',    text: rec.sleep,    enabled: true });
  if (rec.diet)     advice.push({ id: 'diet',     icon: '🥗', category: 'Diet',     text: rec.diet,     enabled: true });
  if (rec.stress)   advice.push({ id: 'stress',   icon: '🧘', category: 'Stress',   text: rec.stress,   enabled: true });
  if (rec.exercise) advice.push({ id: 'exercise', icon: '🏃', category: 'Exercise', text: rec.exercise, enabled: false });
  renderAdvice();
}

// The appointment's intake summary as it is now ('' when unavailable).
async function _latestSummary() {
  try {
    const r = await fetch(`/api/appointment/${patientId}/${aptDate}/${aptTimeSlug}`);
    return r.ok ? (await r.json()).summary || '' : '';
  } catch (_) {
    return '';
  }
}

// ── "Generate diagnosis & points" — only ever called from its button ──────────
// Two requests: diagnosis, then points. `force` is set only by the Retry offered after a run
// stopped reporting progress.

async function generateDiagnosisAndPoints(force) {
  const q = force ? '?force=true' : '';
  showPointState('loading', { stage: 'diagnosis' });
  try {
    const r1 = await fetch(`${NOTES_URL()}/rediagnose${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tongue_observation: document.getElementById('tongue-input').value.trim(),
                             pulse_observation:  document.getElementById('pulse-input').value.trim() }),
    });
    if (r1.status === 409) { _followRunningGeneration(); return; }
    if (!r1.ok) throw new Error('Diagnosis request failed');
    const diag = await r1.json();
    renderDiagnosisBlock(diag, '');
    _applyRecommendations(diag.recommendations);
    if (!diag.tcm_pattern) { showPointState('failed'); return; }

    setPointStage('batch-a');
    const r2 = await fetch(`${NOTES_URL()}/generate-points${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    if (r2.status === 409) { _followRunningGeneration(); return; }
    if (!r2.ok) throw new Error('Point generation failed');
    const pts = await r2.json();
    rememberRationales(pts);
    const points = pointsOf(pts);
    showPointState(points.length ? 'ready' : 'failed', { points });
  } catch (e) {
    console.warn('generateDiagnosisAndPoints:', e.message);
    showPointState('failed');
  }
}

// A 409 means another run (the queued pipeline, or another tab) is generating right now:
// follow it instead of starting a second one.
function _followRunningGeneration() {
  showPointState('loading', { stage: _pointStage || 'diagnosis' });
  _pollForPoints(null);
}

// ── Update Diagnosis button — only called by an explicit therapist action ─────
// Animates the button and re-runs diagnosis with updated tongue/pulse values.

async function triggerRediagnosis(force) {
  const q = force ? '?force=true' : '';
  const tongue = document.getElementById('tongue-input').value.trim();
  const pulse  = document.getElementById('pulse-input').value.trim();

  const btn     = document.getElementById('rediag-btn');
  const ICON_SPIN = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" class="tp-spin"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
  const ICON_IDLE = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
  const resetButton = () => {
    btn.innerHTML = ICON_IDLE + ' Update Diagnosis';
    btn.style.borderColor = ''; btn.style.color = '';
    btn.disabled = false;
  };

  // Stop following a queued run; this request replaces it.
  _stopFollowing();

  btn.disabled = true;
  btn.innerHTML = ICON_SPIN + ' Diagnosing…';
  showPointState('loading', { stage: 'diagnosis' });

  try {
    const r1 = await fetch(`${NOTES_URL()}/rediagnose${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tongue_observation: tongue, pulse_observation: pulse }),
    });
    if (r1.status === 409) {
      resetButton();
      _followRunningGeneration();
      return;
    }
    if (!r1.ok) throw new Error((await r1.json()).detail || 'Diagnosis failed');
    const diag = await r1.json();

    renderDiagnosisBlock(diag, '');
    _applyRecommendations(diag.recommendations);
    setPointStage('batch-a');
    btn.innerHTML = ICON_SPIN + ' Selecting points…';

    if (!diag.tcm_pattern) {
      throw new Error('AI could not produce a TCM pattern — add more clinical findings');
    }

    const r2 = await fetch(`${NOTES_URL()}/generate-points${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    if (r2.status === 409) {
      resetButton();
      _followRunningGeneration();
      return;
    }
    if (!r2.ok) throw new Error((await r2.json()).detail || 'Point generation failed');
    const pts = await r2.json();
    rememberRationales(pts);
    const points = pointsOf(pts);
    showPointState(points.length ? 'ready' : 'failed', { points });

    btn.innerHTML = '✓ Updated';
    btn.style.borderColor = '#0D9488'; btn.style.color = '#0D9488';
    setTimeout(resetButton, 2500);
  } catch (e) {
    console.error('triggerRediagnosis:', e);
    showPointState('failed');
    btn.innerHTML = '⚠ ' + e.message;
    btn.style.borderColor = '#DC2626'; btn.style.color = '#DC2626';
    setTimeout(resetButton, 3500);
  }
}

// ── Following a run (the queued pipeline, a regeneration, another tab) ────────
// Every status the server reports becomes one designed state: loading → partial → ready,
// or failed / cancelled. Polls every 2 s, or listens to the SSE stream when enabled.

let _pollHandle = null;
let _eventSource = null;
let _followTimeout = null;
let _followToken = 0;  // bumped whenever following stops; a response for an older token is dropped

function _stopFollowing() {
  _followToken += 1;
  if (_pollHandle !== null) { clearInterval(_pollHandle); _pollHandle = null; }
  if (_eventSource) { _eventSource.close(); _eventSource = null; }
  if (_followTimeout !== null) { clearTimeout(_followTimeout); _followTimeout = null; }
}

// `initialNotes`: notes already loaded by the caller (applied at once), or null to fetch them.
async function _pollForPoints(initialNotes) {
  if (_pollHandle || _eventSource || _followTimeout) return;  // already following
  const token = ++_followToken;

  let diagnosisRendered = false;
  let lastShown = '';
  const show = (state, opts = {}) => {
    const key = JSON.stringify([state, opts.stage || null, (opts.points || []).map((p) => [p.code, p.rationale || ''])]);
    if (key === lastShown) return;  // unchanged: keep open details and focus where they are
    lastShown = key;
    showPointState(state, opts);
  };

  // Returns true while the run is still going. A run that ends — COMPLETED, FAILED, 'CANCELLED'
  // or nothing at all — stops the follower; nothing here ever starts a run (plan 3.2).
  const apply = async (notes) => {
    try {
      if (token !== _followToken) return false;  // cancelled or replaced while this was in flight
      if (notes.tcm_pattern && !diagnosisRendered) {
        diagnosisRendered = true;
        const summary = await _latestSummary();
        if (token !== _followToken) return false;
        renderDiagnosisBlock(notes, summary);
        _applyRecommendations(notes.ai_recommendations);
      }
      rememberRationales(notes);

      const next = stateOfNotes(notes);
      if (!next.generating) _stopFollowing();
      show(next.state, next);
      return next.generating;
    } catch (e) {
      console.warn('_pollForPoints:', e);
      return true;
    }
  };

  // A run can take several minutes (each stage may retry with backoff), so stop watching after
  // 15 minutes and let the therapist decide. That Retry may override a status a crashed run left
  // behind; the server still refuses while a job is live.
  _followTimeout = setTimeout(() => {
    _stopFollowing();
    showPointState('stalled', { points: _shownPoints });
  }, 900000);

  if (initialNotes && !(await apply(initialNotes))) return;

  const tick = async () => {
    try {
      const nr = await fetch(NOTES_URL());
      if (nr.ok) await apply(await nr.json());
    } catch (_) {}
  };
  const poll = () => {
    if (!initialNotes) tick();
    _pollHandle = setInterval(tick, 2000);
  };

  if (ZF_SSE_UPDATES && window.EventSource) {
    // One stream instead of a request every 2 s. If it fails for any reason, poll instead.
    _eventSource = new EventSource(`${NOTES_URL()}/stream`);
    _eventSource.addEventListener('notes', (e) => { apply(JSON.parse(e.data)); });
    _eventSource.addEventListener('done', () => {
      // Close before the server does, or the browser would reconnect.
      if (_eventSource) { _eventSource.close(); _eventSource = null; }
    });
    _eventSource.onerror = () => {
      if (!_eventSource) return;
      _eventSource.close();
      _eventSource = null;
      poll();
    };
  } else {
    poll();
  }
}

// ── Regenerate points (batch A + B re-run, keeps the diagnosis) ───────────────

async function regeneratePoints() {
  const btn = document.getElementById('regen-points-btn');
  if (!btn) return;
  const originalHtml = btn.innerHTML;
  const restore = () => { btn.innerHTML = originalHtml; btn.style.borderColor = ''; btn.style.color = ''; btn.disabled = false; };
  btn.disabled = true;
  btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" class="tp-spin"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Working…';

  try {
    // The server answers 202 at once and queues the work (plan 3.3). The page shows only what
    // the status says afterwards — never this response — so nothing can render twice.
    const r = await fetch(`${NOTES_URL()}/regenerate-points`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    if (r.status === 409) { restore(); _followRunningGeneration(); return; }
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error((typeof err.detail === 'string' && err.detail) || `Regenerate failed (${r.status})`);
    }
    _stopFollowing();
    rememberRationales(null);
    // The server cleared the old formula; show the new run from its first batch.
    showPointState('loading', { stage: 'batch-a' });
    _pollForPoints(null);
    setTimeout(restore, 1500);
  } catch (e) {
    console.error('regeneratePoints:', e);
    btn.innerHTML = '⚠ ' + (e.message || 'Failed');
    btn.style.borderColor = '#DC2626'; btn.style.color = '#DC2626';
    setTimeout(restore, 3500);
  }
}

// ── Cancel the running generation (queued runs stop; a result that lands later is discarded) ──

async function cancelGeneration() {
  const btn = document.getElementById('cancel-generation-btn');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`${NOTES_URL()}/cancel-generation`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    if (r.ok) {
      _stopFollowing();
      // Points that arrived before the cancel are kept by the server; show what it has.
      const nr = await fetch(NOTES_URL());
      const notes = nr.ok ? await nr.json() : null;
      showPointState('cancelled', { points: pointsOf(notes), hasInput: true });
    }
    // 409: nothing was generating any more — the follower (if any) shows the real final state.
  } catch (e) {
    console.error('cancelGeneration:', e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Clinical Summary auto-refresh ──────────────────────────────────────────────
// Polls the appointment endpoint every 4 s until the bot's Stage 0 writes the
// clinical summary to the DB.  Once it arrives, re-renders the diagnosis block
// with the full summary text and stops polling.

let _summaryPollHandle = null;

function _startSummaryPoller() {
  if (_summaryPollHandle) return;

  _summaryPollHandle = setInterval(async () => {
    try {
      const r = await fetch(`/api/appointment/${patientId}/${aptDate}/${aptTimeSlug}`);
      if (!r.ok) return;
      const aptData = await r.json();
      if (!aptData.summary) return;

      // Summary has arrived — stop polling and re-render with it
      clearInterval(_summaryPollHandle);
      _summaryPollHandle = null;

      // Fetch the latest notes so we render the full diagnosis + summary together
      const nr = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}`);
      const notes = nr.ok ? await nr.json() : null;
      renderDiagnosisBlock(notes, aptData.summary);
    } catch (_) {}
  }, 4000);

  // Stop unconditionally after 5 minutes (summary won't arrive after that)
  setTimeout(() => {
    if (_summaryPollHandle) {
      clearInterval(_summaryPollHandle);
      _summaryPollHandle = null;
    }
  }, 300000);
}
