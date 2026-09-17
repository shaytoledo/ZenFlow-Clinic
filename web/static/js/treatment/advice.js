// Treatment page — Lifestyle advice toggles and sending (email: email-dialog.js).
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Advice toggles ─────────────────────────────────────────────────────────────

function renderAdvice() {
  document.getElementById('advice-list').innerHTML = advice.map((item) => `
    <div class="zf-advice-item ${item.enabled ? 'on' : 'off'}">
      <span class="tp-advice-icon">${escHtml(item.icon)}</span>
      <div class="tp-advice-body">
        <div class="tp-advice-category">${escHtml(item.category)}</div>
        <p class="advice-text-${escHtml(item.id)} tp-advice-text" contenteditable="true"
           data-advice-edit="${escHtml(item.id)}"
           >${escHtml(item.text)}</p>
      </div>
      <button class="zf-toggle ${item.enabled ? 'on' : ''}" data-action="toggle-advice" data-advice-id="${escHtml(item.id)}"></button>
    </div>
  `).join('');
}

function updateAdviceText(id, newText) {
  const item = advice.find(a => a.id === id);
  if (item) item.text = newText.trim();
}

function toggleAdvice(id) {
  const item = advice.find(a => a.id === id);
  if (item) { item.enabled = !item.enabled; renderAdvice(); }
}

async function sendAdvice(emailOverride) {
  const enabled = advice.filter(a => a.enabled);
  if (enabled.length === 0) { alert('No advice selected.'); return; }

  const btn = document.getElementById('send-advice-btn');
  const origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = emailOverride ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" class="tp-spin"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Sending…' : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" class="tp-spin"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Sending…';

  try {
    const r = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/send-recommendations`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: enabled, schedule_hours: 2, email: emailOverride || null }),
    });
    const result = await r.json();

    if (r.status === 422 && result.status === 'needs_email') {
      btn.disabled = false; btn.innerHTML = origText;
      openEmailDialog();  // email-dialog.js (its own, translated explanation)
      return;
    }
    if (!r.ok) throw new Error(result.message || result.detail || 'Send failed');

    const channel = result.sent_via === 'email' ? 'email' : 'Telegram';
    btn.innerHTML = `✓ Sent via ${channel}`;
    btn.style.background = '#16A34A';
    setTimeout(() => { btn.disabled = false; btn.innerHTML = origText; btn.style.background = ''; }, 3000);
  } catch (e) {
    alert('Could not send: ' + e.message);
    btn.disabled = false; btn.innerHTML = origText;
  }
}

async function sendAdviceLater() {
  const enabled = advice.filter(a => a.enabled);
  if (enabled.length === 0) { alert('No advice selected.'); return; }

  const btn = document.getElementById('send-later-btn');
  const origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" class="tp-spin"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Scheduling…';

  try {
    const r = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/send-recommendations`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: enabled, schedule_hours: 24 }),
    });
    const result = await r.json();

    if (r.status === 422 && result.status === 'needs_email') {
      btn.disabled = false; btn.innerHTML = origText;
      openEmailDialog();  // email-dialog.js (its own, translated explanation)
      return;
    }
    // 409 google_not_connected: an email-only patient cannot be scheduled yet (Phase 5.2/5.3)
    if (handleGoogleRefusal(result, 'later')) { btn.disabled = false; btn.innerHTML = origText; return; }
    if (!r.ok) throw new Error(result.message || result.detail || 'Schedule failed');

    btn.innerHTML = '⏰ Scheduled in 24h ✓';
    btn.style.borderColor = '#16A34A'; btn.style.color = '#16A34A';
    const statusEl = document.getElementById('send-later-status');
    statusEl.textContent = 'Recommendations will be sent automatically 24 hours after session completion.';
    statusEl.style.display = 'block';
    setTimeout(() => { btn.disabled = false; btn.innerHTML = origText; btn.style.borderColor = ''; btn.style.color = ''; }, 4000);
  } catch (e) {
    alert('Could not schedule: ' + e.message);
    btn.disabled = false; btn.innerHTML = origText;
  }
}
