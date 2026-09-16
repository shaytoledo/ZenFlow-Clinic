// Treatment page — 24h follow-up results and therapist-entered patient feedback.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Follow-up results renderer ─────────────────────────────────────────────────

function renderFollowupResults(data) {
  const card = document.getElementById('followup-card');
  const body = document.getElementById('followup-body');
  if (!data) return;

  const improvementColors = {
    1: '#DC2626', 2: '#EA580C', 3: '#D97706', 4: '#16A34A', 5: '#0D9488'
  };
  const improvementLabels = {
    1: 'Much worse', 2: 'Slightly worse', 3: 'About the same',
    4: 'Noticeably better', 5: 'Much better'
  };

  let metricsHtml = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px;">';
  if (data.pain_level != null) {
    const painColor = data.pain_level <= 3 ? '#16A34A' : data.pain_level <= 6 ? '#D97706' : '#DC2626';
    metricsHtml += `<div class="zf-metric-card">
      <div>
        <div class="zf-metric-num" style="color:${painColor};">${escHtml(data.pain_level)}<span style="font-size:13px;color:#9CA3AF;">/10</span></div>
        <div class="zf-metric-label">Pain level<br><span style="font-size:10px;text-transform:uppercase;letter-spacing:0.5px;">1=none · 10=severe</span></div>
      </div>
    </div>`;
  }
  if (data.improvement_rating != null) {
    const iColor = improvementColors[data.improvement_rating] || '#6B7280';
    metricsHtml += `<div class="zf-metric-card">
      <div>
        <div class="zf-metric-num" style="color:${iColor};">${escHtml(data.improvement_rating)}<span style="font-size:13px;color:#9CA3AF;">/5</span></div>
        <div class="zf-metric-label">${escHtml(improvementLabels[data.improvement_rating] || '')}<br><span style="font-size:10px;text-transform:uppercase;letter-spacing:0.5px;">Improvement</span></div>
      </div>
    </div>`;
  }
  metricsHtml += '</div>';

  let notesHtml = '';
  if (data.notes) {
    notesHtml = `<div style="background:#F0FDF4;border-radius:8px;padding:10px 12px;margin-bottom:12px;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#059669;letter-spacing:0.6px;margin-bottom:4px;">Patient Notes</div>
      <p style="font-size:13px;color:#065F46;line-height:1.6;margin:0;">${escHtml(data.notes)}</p>
    </div>`;
  }

  let convHtml = '';
  if (Array.isArray(data.conversation) && data.conversation.length > 0) {
    convHtml = `<details style="margin-top:6px;">
      <summary style="cursor:pointer;font-size:12px;font-weight:600;color:#6B7280;padding:4px 0;user-select:none;">View full conversation ▸</summary>
      <div style="margin-top:10px;display:flex;flex-direction:column;gap:6px;">
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
  btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" style="animation:spin 1s linear infinite"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Saving…';

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
