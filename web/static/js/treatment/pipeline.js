// Treatment page — AI generation: diagnosis/points requests, progress, following a run, cancel.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Dynamic re-diagnosis ───────────────────────────────────────────────────────

let _progressTimer = null;

function _startPointsProgress(label) {
  const wrap  = document.getElementById('points-progress');
  const lbl   = document.getElementById('points-progress-label');
  const bar   = document.getElementById('points-progress-bar');
  const badge = document.getElementById('points-ai-badge');
  if (!wrap) return;
  wrap.style.display = 'block';
  if (badge) badge.style.display = 'none';
  lbl.textContent = label || 'Diagnosing patient…';
  bar.style.width = '0%';

  // Animate the bar through sub-stage milestones; real progress updates override these
  const stages = [
    { pct: 10, delay: 400,  text: 'Analysing intake…' },
    { pct: 25, delay: 2200, text: 'Request 1 of 3 — generating TCM diagnosis…' },
    { pct: 55, delay: 5000, text: 'Request 2 of 3 — selecting first batch (5–7 points)…' },
    { pct: 82, delay: 9000, text: 'Request 3 of 3 — selecting second batch (5–7 points)…' },
  ];
  stages.forEach(({ pct, delay, text }) => {
    setTimeout(() => {
      if (wrap.style.display === 'none') return;  // already done
      bar.style.width = pct + '%';
      lbl.textContent = text;
    }, delay);
  });
}

function _finishPointsProgress(success) {
  const wrap  = document.getElementById('points-progress');
  const bar   = document.getElementById('points-progress-bar');
  const badge = document.getElementById('points-ai-badge');
  if (!wrap) return;
  if (success) {
    bar.style.width = '100%';
    bar.style.background = 'linear-gradient(90deg,#059669,#0D9488)';
    setTimeout(() => {
      wrap.style.display = 'none';
      bar.style.width = '0%';
      bar.style.background = 'linear-gradient(90deg,#2563EB,#0D9488)';
      if (badge) badge.style.display = '';
    }, 600);
  } else {
    bar.style.background = '#DC2626';
    setTimeout(() => {
      wrap.style.display = 'none';
      bar.style.background = 'linear-gradient(90deg,#2563EB,#0D9488)';
    }, 1500);
  }
}

// ── Shared helper: apply a completed rediagnose API response to the UI ────────

// Advance the progress bar to a specific percentage + label.
// Safe to call even if the bar is not visible (no-op in that case).
function _updateProgress(pct, label) {
  const wrap = document.getElementById('points-progress');
  if (!wrap || wrap.style.display === 'none') return;
  const bar = document.getElementById('points-progress-bar');
  const lbl = document.getElementById('points-progress-label');
  if (bar) bar.style.width = pct + '%';
  if (lbl) lbl.textContent = label;
}

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

// ── "Generate diagnosis & points" — only ever called from its button ──────────
// Two-phase: Phase 1 = rediagnose (diagnosis), Phase 2 = generate-points (points).
// Progress bar advances through real milestones, not a fake timer.
// `force` is set only by the button offered after a run stopped reporting progress.

async function generateDiagnosisAndPoints(force) {
  const q = force ? '?force=true' : '';
  _startPointsProgress('Generating AI diagnosis…');
  try {
    // ── Phase 1: diagnosis ────────────────────────────────────────────────────
    const r1 = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/rediagnose${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tongue_observation: document.getElementById('tongue-input').value.trim(),
                             pulse_observation:  document.getElementById('pulse-input').value.trim() }),
    });
    if (r1.status === 409) { _followRunningGeneration(); return; }
    if (!r1.ok) throw new Error('Diagnosis request failed');
    const diag = await r1.json();

    // Render Clinical Summary + diagnosis block immediately
    renderDiagnosisBlock(diag, '');
    _applyRecommendations(diag.recommendations);
    _updateProgress(52, 'Diagnosis ready — selecting acupuncture points…');

    if (!diag.tcm_pattern) {
      _finishPointsProgress(false);
      return;
    }

    // ── Phase 2: points ───────────────────────────────────────────────────────
    const r2 = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/generate-points${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    if (r2.status === 409) { _followRunningGeneration(); return; }
    if (!r2.ok) throw new Error('Point generation failed');
    const pts = await r2.json();

    aiPointRationale = {};
    (pts.ai_suggested_points || []).forEach(p => {
      if (typeof p === 'object' && p.code) aiPointRationale[p.code] = p.rationale || '';
    });

    _finishPointsProgress((pts.ai_suggested_points || []).length > 0);
    if ((pts.ai_suggested_points || []).length > 0) {
      renderSuggestedPoints(pts, null);
    } else {
      _showRetryButton();
    }
  } catch (e) {
    console.warn('generateDiagnosisAndPoints:', e.message);
    _finishPointsProgress(false);
    _showRetryButton();
  }
}

// A 409 means another run (the queued pipeline, or another tab) is generating right now:
// show its progress instead of starting a second one.
function _followRunningGeneration() {
  _pollForPoints(true);
  _updateProgress(20, 'AI is already working on this session…');
}

// Shown when a session has no points and nothing is generating. Never auto-fires.
function _showGenerateButton(opts) {
  const div = document.getElementById('suggested-points');
  if (!div) return;
  const failed = opts && opts.failed;
  const cancelled = opts && opts.cancelled;
  const msg = cancelled
    ? 'AI generation was cancelled.'
    : failed
    ? 'The last AI generation did not complete.'
    : (opts && opts.hasInput
        ? 'No AI diagnosis or points yet.'
        : 'No intake on file — add tongue and pulse findings, then generate.');
  div.innerHTML = `
    <div style="display:flex;flex-direction:column;align-items:center;gap:10px;padding:16px 0;">
      <span style="font-size:12px;color:#9CA3AF;">${msg}</span>
      <button data-action="generate" class="zf-btn zf-btn-outline"
        style="font-size:12px;padding:6px 16px;color:#0D9488;border-color:#0D9488;">
        ✨ Generate diagnosis &amp; points
      </button>
    </div>`;
}

// ── Update Diagnosis button — only called by explicit therapist click ──────────
// Animates the button and re-runs diagnosis with updated tongue/pulse values.

async function triggerRediagnosis(force) {
  const q = force ? '?force=true' : '';
  const tongue = document.getElementById('tongue-input').value.trim();
  const pulse  = document.getElementById('pulse-input').value.trim();

  const btn     = document.getElementById('rediag-btn');
  const ICON_SPIN = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" style="animation:spin 1s linear infinite"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
  const ICON_IDLE = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';

  // Stop any DB poll that may be running for the bot pipeline
  _stopFollowing();

  btn.disabled = true;
  btn.innerHTML = ICON_SPIN + ' Diagnosing…';
  document.getElementById('suggested-points').innerHTML = '';
  _startPointsProgress('Analysing intake and findings…');

  try {
    // ── Phase 1: diagnosis ────────────────────────────────────────────────────
    const r1 = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/rediagnose${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tongue_observation: tongue, pulse_observation: pulse }),
    });
    if (r1.status === 409) {
      btn.innerHTML = ICON_IDLE + ' Update Diagnosis';
      btn.disabled = false;
      _followRunningGeneration();
      return;
    }
    if (!r1.ok) throw new Error((await r1.json()).detail || 'Diagnosis failed');
    const diag = await r1.json();

    // Render Clinical Summary + TCM diagnosis block immediately
    renderDiagnosisBlock(diag, '');
    _applyRecommendations(diag.recommendations);
    _updateProgress(52, 'Diagnosis ready — selecting acupuncture points…');
    btn.innerHTML = ICON_SPIN + ' Selecting points…';

    if (!diag.tcm_pattern) {
      throw new Error('AI could not produce a TCM pattern — add more clinical findings');
    }

    // ── Phase 2: points ───────────────────────────────────────────────────────
    const r2 = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/generate-points${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    if (r2.status === 409) {
      btn.innerHTML = ICON_IDLE + ' Update Diagnosis';
      btn.disabled = false;
      _followRunningGeneration();
      return;
    }
    if (!r2.ok) throw new Error((await r2.json()).detail || 'Point generation failed');
    const pts = await r2.json();

    aiPointRationale = {};
    (pts.ai_suggested_points || []).forEach(p => {
      if (typeof p === 'object' && p.code) aiPointRationale[p.code] = p.rationale || '';
    });

    const hasPoints = (pts.ai_suggested_points || []).length > 0;
    _finishPointsProgress(hasPoints);
    if (hasPoints) {
      renderSuggestedPoints(pts, null);
    } else {
      _showRetryButton();
    }

    btn.innerHTML = '✓ Updated';
    btn.style.borderColor = '#0D9488'; btn.style.color = '#0D9488';
    setTimeout(() => {
      btn.innerHTML = ICON_IDLE + ' Update Diagnosis';
      btn.style.borderColor = ''; btn.style.color = '';
      btn.disabled = false;
    }, 2500);

  } catch (e) {
    console.error('triggerRediagnosis:', e);
    _finishPointsProgress(false);
    btn.innerHTML = '⚠ ' + e.message;
    btn.style.borderColor = '#DC2626'; btn.style.color = '#DC2626';
    setTimeout(() => {
      btn.innerHTML = ICON_IDLE + ' Update Diagnosis';
      btn.style.borderColor = ''; btn.style.color = '';
      btn.disabled = false;
    }, 3500);
  }
}

// ── Silent DB poller — used when the background pipeline is still running ─────
// Polls every 5 s and renders each stage as it lands in the DB:
//   Stage 1 arrival → diagnosis block renders (no points yet, bar keeps going)
//   Stage 2 arrival → point cards render, bar completes

let _pollHandle = null;
let _eventSource = null;

function _stopFollowing() {
  if (_pollHandle !== null) { clearInterval(_pollHandle); _pollHandle = null; }
  if (_eventSource) { _eventSource.close(); _eventSource = null; }
}

async function _pollForPoints(skipProgress) {
  if (_pollHandle || _eventSource) return;  // already following
  if (!skipProgress) _startPointsProgress('AI is preparing your diagnosis…');

  let stage1Rendered = false;

  let stage2aRendered = false;

  const apply = async (notes) => {
    try {

      const status      = notes.points_status;
      const hasDiagnosis = Boolean(notes.tcm_pattern);
      const hasPoints    = Array.isArray(notes.ai_suggested_points) && notes.ai_suggested_points.length > 0;

      // Stage 1 arrived: render diagnosis block + summary, keep bar moving toward stage 2.
      if (hasDiagnosis && !stage1Rendered) {
        stage1Rendered = true;
        // Fetch the latest appointment summary so the Clinical Summary block populates
        let latestSummary = '';
        try {
          const aptR = await fetch(`/api/appointment/${patientId}/${aptDate}/${aptTimeSlug}`);
          if (aptR.ok) {
            const aptData = await aptR.json();
            latestSummary = aptData.summary || '';
          }
        } catch (_) {}
        renderDiagnosisBlock(notes, latestSummary);
        _updateProgress(45, 'Request 2 of 3 — selecting first batch (5–7 points)…');

        const rec = notes.ai_recommendations;
        if (rec && (rec.diet || rec.sleep || rec.exercise || rec.stress)) {
          advice = [];
          if (rec.sleep)    advice.push({ id: 'sleep',    icon: '🌙', category: 'Sleep',    text: rec.sleep,    enabled: true });
          if (rec.diet)     advice.push({ id: 'diet',     icon: '🥗', category: 'Diet',     text: rec.diet,     enabled: true });
          if (rec.stress)   advice.push({ id: 'stress',   icon: '🧘', category: 'Stress',   text: rec.stress,   enabled: true });
          if (rec.exercise) advice.push({ id: 'exercise', icon: '🏃', category: 'Exercise', text: rec.exercise, enabled: false });
          renderAdvice();
        }
      }

      // First batch arrived (any moment points appear while still GENERATING):
      // render immediately, advance progress to "Request 2 of 2", keep polling.
      const stillGenerating = status && status.startsWith('GENERATING');
      if (hasPoints && stillGenerating && !stage2aRendered) {
        stage2aRendered = true;
        aiPointRationale = {};
        (notes.ai_suggested_points || []).forEach(p => {
          if (typeof p === 'object' && p.code) aiPointRationale[p.code] = p.rationale || '';
        });
        renderSuggestedPoints(notes, null);
        const count = (notes.ai_suggested_points || []).length;
        _updateProgress(75, `Request 3 of 3 — selecting second batch (${count} points so far)…`);
        return;
      }

      // Second batch arrived while still GENERATING (rare timing) — re-render with merged list
      if (hasPoints && stillGenerating && stage2aRendered) {
        const newCount = (notes.ai_suggested_points || []).length;
        const cachedCount = Object.keys(aiPointRationale).length;
        if (newCount > cachedCount) {
          aiPointRationale = {};
          (notes.ai_suggested_points || []).forEach(p => {
            if (typeof p === 'object' && p.code) aiPointRationale[p.code] = p.rationale || '';
          });
          renderSuggestedPoints(notes, null);
          _updateProgress(95, `Almost done — ${newCount} points selected…`);
        }
      }

      // Stage 2B COMPLETED: render full point list and stop polling
      if (status === 'COMPLETED') {
        _stopFollowing();
        aiPointRationale = {};
        (notes.ai_suggested_points || []).forEach(p => {
          if (typeof p === 'object' && p.code) aiPointRationale[p.code] = p.rationale || '';
        });
        if (!stage1Rendered) {
          // Pull the latest summary so Clinical Summary populates without a refresh
          let latestSummary = '';
          try {
            const aptR = await fetch(`/api/appointment/${patientId}/${aptDate}/${aptTimeSlug}`);
            if (aptR.ok) latestSummary = (await aptR.json()).summary || '';
          } catch (_) {}
          renderDiagnosisBlock(notes, latestSummary);
        }
        _finishPointsProgress(hasPoints);
        if (hasPoints) {
          renderSuggestedPoints(notes, null);
        } else {
          _showRetryButton();
        }
        return;
      }

      // Legacy COMPLETED without sub-stages (rediagnose path)
      if (hasPoints && !status) {
        _stopFollowing();
        aiPointRationale = {};
        (notes.ai_suggested_points || []).forEach(p => {
          if (typeof p === 'object' && p.code) aiPointRationale[p.code] = p.rationale || '';
        });
        if (!stage1Rendered) renderDiagnosisBlock(notes, '');
        _finishPointsProgress(true);
        renderSuggestedPoints(notes, null);
        return;
      }

      // Cancelled by the therapist: stop and offer a fresh run
      if (status === 'CANCELLED') {
        _stopFollowing();
        _finishPointsProgress(false);
        _showGenerateButton({ cancelled: true });
        return;
      }

      // Stage 2 FAILED: stop polling and offer retry
      if (status === 'FAILED') {
        _stopFollowing();
        _finishPointsProgress(false);
        _showRetryButton();
        return;
      }

      // Update progress label based on current sub-stage
      if (status === 'GENERATING_STAGE_0')       _updateProgress(10, 'Queued — summarising the intake…');
      else if (status === 'GENERATING_STAGE_1')  _updateProgress(25, 'Request 1 of 3 — generating TCM diagnosis…');
      else if (status === 'GENERATING_STAGE_2A') _updateProgress(50, 'Request 2 of 3 — selecting first batch (5–7 points)…');
      else if (status === 'GENERATING_STAGE_2B') _updateProgress(78, 'Request 3 of 3 — selecting second batch (5–7 points)…');

      // Nothing is generating and nothing exists: stop and offer the explicit button
      // (never start a run from here — plan 3.2).
      if (!stillGenerating && !hasDiagnosis && !hasPoints) {
        _stopFollowing();
        _finishPointsProgress(false);
        _showGenerateButton({ failed: false, hasInput: true });
        return;
      }
      // still generating → keep following
    } catch (_) {}
  };

  const poll = () => {
    _pollHandle = setInterval(async () => {
      try {
        const nr = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}`);
        if (nr.ok) await apply(await nr.json());
      } catch (_) {}
    }, 2000);
  };

  if (ZF_SSE_UPDATES && window.EventSource) {
    // One stream instead of a request every 2 s. If it fails for any reason, poll instead.
    _eventSource = new EventSource(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/stream`);
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

  // Safety timeout: a queued run can take several minutes (each stage may retry with backoff),
  // so stop watching after 15 minutes and let the therapist decide. The retry offered here may
  // override a status a crashed run left behind; the server still refuses while a job is live.
  setTimeout(() => {
    if (_pollHandle || _eventSource) {
      _stopFollowing();
      _finishPointsProgress(false);
      _showRetryButton(true);
    }
  }, 900000);
}

// ── Regenerate points (Stage 2A + 2B re-run, keeps existing diagnosis) ────────

async function regeneratePoints() {
  const btn = document.getElementById('regen-points-btn');
  if (!btn) return;
  const originalHtml = btn.innerHTML;
  const restore = () => { btn.innerHTML = originalHtml; btn.style.borderColor = ''; btn.style.color = ''; btn.disabled = false; };
  btn.disabled = true;
  btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" style="animation:spin 1s linear infinite"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Working…';

  try {
    // The server answers 202 at once and queues the work (plan 3.3). The page renders only what
    // the status says afterwards — never this response — so nothing can render twice.
    const r = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/regenerate-points`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    if (r.status === 409) { restore(); _followRunningGeneration(); return; }
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error((typeof err.detail === 'string' && err.detail) || `Regenerate failed (${r.status})`);
    }
    _stopFollowing();
    _clearRenderedPoints();  // the server cleared them; don't keep showing the old formula
    _startPointsProgress('Request 2 of 3 — selecting first batch (5–7 points)…');
    _updateProgress(40, 'Request 2 of 3 — selecting first batch (5–7 points)…');
    _pollForPoints(true);
    setTimeout(restore, 1500);
  } catch (e) {
    console.error('regeneratePoints:', e);
    btn.innerHTML = '⚠ ' + (e.message || 'Failed');
    btn.style.borderColor = '#DC2626'; btn.style.color = '#DC2626';
    setTimeout(restore, 3500);
  }
}

// Remove every rendering of the current AI points (chips and the detailed card grid).
function _clearRenderedPoints() {
  const chips = document.getElementById('suggested-points');
  if (chips) chips.innerHTML = '';
  const grid = document.getElementById('ai-points-grid');
  if (grid) grid.innerHTML = '';
  const section = document.getElementById('ai-points-section');
  if (section) section.style.display = 'none';
  aiPointRationale = {};
}

// ── Cancel the running generation (queued runs stop; a result that lands later is discarded) ──

async function cancelGeneration() {
  const btn = document.getElementById('cancel-generation-btn');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/cancel-generation`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    if (r.ok) {
      _stopFollowing();
      _finishPointsProgress(false);
      _showGenerateButton({ cancelled: true, hasInput: true });
    }
    // 409: nothing was generating any more — the poller (if any) renders the real final state.
  } catch (e) {
    console.error('cancelGeneration:', e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function _showRetryButton(force) {
  const div = document.getElementById('suggested-points');
  if (!div) return;
  const msg = force
    ? 'Still no result from the AI after 15 minutes.'
    : 'Point generation did not complete.';
  div.innerHTML = `
    <div style="display:flex;flex-direction:column;align-items:center;gap:10px;padding:16px 0;">
      <span style="font-size:12px;color:#9CA3AF;">${msg}</span>
      <button data-action="rediagnose" data-force="${force ? 'true' : 'false'}" class="zf-btn zf-btn-outline"
        style="font-size:12px;padding:6px 16px;color:#0D9488;border-color:#0D9488;">
        ↺ Retry Point Generation
      </button>
    </div>`;
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
