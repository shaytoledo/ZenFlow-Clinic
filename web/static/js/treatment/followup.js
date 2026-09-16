// Treatment page — 24h follow-up results and therapist-entered patient feedback.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Follow-up results renderer ─────────────────────────────────────────────────

function renderFollowupResults(data) {
  const card = document.getElementById('followup-card');
  const body = document.getElementById('followup-body');
  if (!data) return;

  const improvementTones = { 1: 'red', 2: 'orange', 3: 'amber', 4: 'green', 5: 'teal' };
  const improvementLabels = {
    1: 'Much worse', 2: 'Slightly worse', 3: 'About the same',
    4: 'Noticeably better', 5: 'Much better'
  };

  let metricsHtml = '<div class="tp-metrics">';
  if (data.pain_level != null) {
    const painTone = data.pain_level <= 3 ? 'green' : data.pain_level <= 6 ? 'amber' : 'red';
    metricsHtml += `<div class="zf-metric-card">
      <div>
        <div class="zf-metric-num tp-metric-value tp-tone-${painTone}">${escHtml(data.pain_level)}<span class="tp-muted">/10</span></div>
        <div class="zf-metric-label">Pain level<br><span class="tp-metric-hint">1=none · 10=severe</span></div>
      </div>
    </div>`;
  }
  if (data.improvement_rating != null) {
    const improvementTone = improvementTones[data.improvement_rating] || 'grey';
    metricsHtml += `<div class="zf-metric-card">
      <div>
        <div class="zf-metric-num tp-metric-value tp-tone-${improvementTone}">${escHtml(data.improvement_rating)}<span class="tp-muted">/5</span></div>
        <div class="zf-metric-label">${escHtml(improvementLabels[data.improvement_rating] || '')}<br><span class="tp-metric-hint">Improvement</span></div>
      </div>
    </div>`;
  }
  metricsHtml += '</div>';

  let notesHtml = '';
  if (data.notes) {
    notesHtml = `<div class="tp-followup-notes">
      <div class="tp-followup-notes-label">Patient Notes</div>
      <p class="tp-note-text">${escHtml(data.notes)}</p>
    </div>`;
  }

  let convHtml = '';
  if (Array.isArray(data.conversation) && data.conversation.length > 0) {
    convHtml = `<details class="tp-mt-6">
      <summary class="tp-conversation-toggle">View full conversation ▸</summary>
      <div class="tp-conversation">
        ${data.conversation.map(m => {
          const isPatient = m.role === 'user';
          return `<div class="zf-followup-msg ${isPatient ? 'patient' : 'ai'}">
            <div class="zf-intake-role">${isPatient ? '👤 Patient' : '🤖 ZenFlow Bot'}</div>
            <div class="zf-intake-text">${escHtml(m.content)}</div>
          </div>`;
        }).join('')}
      </div>
    </details>`;
  }

  body.innerHTML = metricsHtml + notesHtml + convHtml;
  card.style.display = 'block';
}

// ── Manual Feedback ────────────────────────────────────────────────────────────

const MF_RATING_LABELS = {
  1: 'Much worse',
  2: 'Slightly worse',
  3: 'About the same',
  4: 'Noticeably better',
  5: 'Much better',
};

function highlightMfStar(val) {
  document.querySelectorAll('.zf-star-btn').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.val) === val);
  });
  document.getElementById('mf-rating-label').textContent = MF_RATING_LABELS[val] || '';
}

function setMfRating(val) {
  _mfRating = val;
  highlightMfStar(val);
}

function onMfChange() {
  document.getElementById('mf-status').textContent = 'Unsaved…';
}

async function saveManualFeedback() {
  const notes = document.getElementById('mf-notes').value.trim();
  if (!_mfRating && !notes) { alert('Please add a rating or notes before saving.'); return; }

  const btn = document.getElementById('mf-save-btn');
  const origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" class="tp-spin"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Saving…';

  try {
    const r = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/manual-feedback`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating: _mfRating, notes }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || 'Save failed');
    btn.innerHTML = '✓ Feedback Saved';
    btn.style.background = '#16A34A';
    document.getElementById('mf-status').textContent = 'Saved ✓';
    setTimeout(() => { btn.disabled = false; btn.innerHTML = origText; btn.style.background = ''; }, 3000);
  } catch (e) {
    alert('Could not save feedback: ' + e.message);
    btn.disabled = false; btn.innerHTML = origText;
  }
}
