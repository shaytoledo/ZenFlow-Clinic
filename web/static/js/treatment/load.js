// Treatment page — Loading the session and rendering the intake history.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Load treatment data ────────────────────────────────────────────────────────

async function loadTreatment() {
  if (!patientId || !aptDate) return;
  try {
    const data = await fetch(`/api/appointment/${patientId}/${aptDate}/${aptTimeSlug}`).then(r => r.json());

    const initials = data.patient_name.split(' ').map(n => n[0]).join('').slice(0,2).toUpperCase();
    document.getElementById('pt-initials').textContent = initials;
    const nameEl = document.getElementById('pt-name');
    nameEl.innerHTML = `<a href="/patients/${patientId}" class="tp-patient-link">${escHtml(data.patient_name)}</a>`;
    const d = new Date(data.date);
    document.getElementById('pt-meta').textContent =
      d.toLocaleDateString('en-GB', {weekday:'long',day:'numeric',month:'long',year:'numeric'}) + ' at ' + data.time;
    document.getElementById('pt-session-num').textContent = 'Session';

    // Intake history — redesigned conversation view
    renderIntakeHistory(data.intake_history, data.date);

    // Load treatment notes
    let notes = null;
    try {
      const nr = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}`);
      if (nr.ok) notes = await nr.json();
    } catch (_) {}

    // Show no-Telegram banner for manual appointments
    _isManual = notes?.is_manual || data.source === 'manual' || parseInt(patientId) < 0;
    if (_isManual) {
      const banner = document.getElementById('no-telegram-banner');
      banner.style.display = 'flex';
      document.getElementById('intake-source-badge').textContent = 'Manual Booking';
      document.getElementById('intake-source-badge').style.background = '#FEF3C7';
      document.getElementById('intake-source-badge').style.color = '#B45309';
    }

    renderDiagnosisBlock(notes, data.summary);

    rememberRationales(notes);

    if (notes) {
      if (notes.tongue_observation) document.getElementById('tongue-input').value = notes.tongue_observation;
      if (notes.pulse_observation)  document.getElementById('pulse-input').value  = notes.pulse_observation;
      if (notes.session_notes)      document.getElementById('session-notes').value = notes.session_notes;
      const tdEl = document.getElementById('therapist-diagnosis');
      const tnEl = document.getElementById('therapist-notes');
      if (tdEl && notes.therapist_diagnosis) tdEl.value = notes.therapist_diagnosis;
      if (tnEl && notes.therapist_notes)     tnEl.value = notes.therapist_notes;
      if (Array.isArray(notes.used_points) && notes.used_points.length > 0) {
        // Older notes may hold "st 36"; the cards compare normalised codes.
        usedPoints = [...new Set(notes.used_points.map(normPointCode).filter(Boolean))];
        renderPoints();
      }
      // Restore manual feedback
      if (notes.manual_feedback_rating) {
        _mfRating = notes.manual_feedback_rating;
        highlightMfStar(_mfRating);
      }
      if (notes.manual_feedback_notes) {
        document.getElementById('mf-notes').value = notes.manual_feedback_notes;
      }
    }

    // Build advice from AI recommendations
    const aiRecs = notes && notes.ai_recommendations;
    if (aiRecs && (aiRecs.diet || aiRecs.sleep || aiRecs.exercise || aiRecs.stress)) {
      advice = [];
      if (aiRecs.sleep)    advice.push({ id: 'sleep',    icon: '🌙', category: 'Sleep',    text: aiRecs.sleep,    enabled: true });
      if (aiRecs.diet)     advice.push({ id: 'diet',     icon: '🥗', category: 'Diet',     text: aiRecs.diet,     enabled: true });
      if (aiRecs.stress)   advice.push({ id: 'stress',   icon: '🧘', category: 'Stress',   text: aiRecs.stress,   enabled: true });
      if (aiRecs.exercise) advice.push({ id: 'exercise', icon: '🏃', category: 'Exercise', text: aiRecs.exercise, enabled: false });
    }
    renderAdvice();

    // Render follow-up conversation if it exists
    if (notes?.followup_conversation) {
      renderFollowupResults(notes.followup_conversation);
    }

    const hasDiagnosis = Boolean(notes?.tcm_pattern);
    const hasIntake    = Boolean(data.summary || (data.intake_history && data.intake_history.length > 0));
    const status       = String(notes?.points_status || '');
    // The AI's list, or for old notes without one, the reference points the summary names.
    const points       = normalizeSuggestedPoints(notes, data.summary);

    // Opening a session never starts a generation (plan 3.2). The queued pipeline owns the
    // automatic run; the therapist starts any other run with an explicit click.
    if (status.startsWith('GENERATING')) {
      // The pipeline is working — show where it is now, then follow it stage by stage.
      _pollForPoints(notes);
    } else {
      showPointState(restingState(status, points.length > 0), { points, hasInput: hasDiagnosis || hasIntake });
    }
    if (!data.summary && (points.length || status.startsWith('GENERATING'))) {
      _startSummaryPoller();  // the bot's intake summary is still on its way
    }

  } catch(e) {
    console.error('Treatment load error:', e);
    document.getElementById('pt-name').textContent = 'Error loading session';
  }
}

// ── Intake history renderer ───────────────────────────────────────────────────

function renderIntakeHistory(history, sessionDate) {
  const intakeBody = document.getElementById('intake-body');
  if (history && history.length > 0) {
    const msgs = history.map(msg => {
      const isUser = msg.role === 'user';
      const icon = isUser
        ? `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`
        : `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.84A2.5 2.5 0 0 1 9.5 2"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.84A2.5 2.5 0 0 0 14.5 2"/></svg>`;
      return `<div class="zf-intake-msg ${isUser ? 'user' : 'ai'}">
        <div class="zf-intake-avatar">${icon}</div>
        <div class="zf-intake-bubble">
          <div class="zf-intake-role">${isUser ? 'Patient' : 'AI Assistant'}</div>
          <div class="zf-intake-text">${escHtml(msg.content)}</div>
        </div>
      </div>`;
    }).join('');
    const dateStr = new Date(sessionDate).toLocaleDateString('en-GB', {day:'numeric', month:'long', year:'numeric'});
    intakeBody.innerHTML = `<div class="zf-intake-thread">${msgs}</div>
      <div class="tp-intake-footer">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2.2" stroke-linecap="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        ${history.length} exchange${history.length !== 1 ? 's' : ''} · Collected via Telegram Bot · ${dateStr}
      </div>`;
  } else {
    intakeBody.innerHTML = `<div class="tp-empty">
      <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#D1D5DB" stroke-width="1.5" stroke-linecap="round" class="tp-mb-10"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
      <p class="tp-empty-title">No intake questionnaire for this session.</p>
      <p class="tp-empty-hint">Patient booked without completing the AI intake flow.</p>
    </div>`;
  }
}
