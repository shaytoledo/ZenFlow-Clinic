// Treatment page — therapist-entered patient feedback (the follow-up card itself is server-rendered).
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

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
