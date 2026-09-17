// Treatment page — Lifestyle advice toggles, sending, and the email fallback popup.
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
      openEmailFallback(result.phone || '', result.detail || '', result.patient_name || '');
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
      openEmailFallback(result.phone || '', result.detail || '', result.patient_name || '');
      return;
    }
    // 409 google_not_connected: an email-only patient cannot be scheduled yet (Phase 5.2)
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

// ── Email fallback popup ──────────────────────────────────────────────────────

function openEmailFallback(phone, message, patientName) {
  closeEmailFallback();
  const overlay = document.createElement('div');
  overlay.id = 'zf-email-overlay';
  overlay.className = 'tp tp-overlay';  // outside #treatment-root, so it carries the page scope itself
  overlay.onclick = (e) => { if (e.target === overlay) closeEmailFallback(); };
  overlay.innerHTML = `
    <div class="tp-dialog">
      <div class="tp-dialog-head">
        <div class="tp-dialog-icon-red">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        </div>
        <h3 class="tp-dialog-title">No Telegram account found</h3>
      </div>
      <p class="tp-dialog-text">${escHtml(message || `No Telegram account is linked to ${phone}.`)}</p>
      <label class="zf-field-label">Patient email address</label>
      <input id="zf-email-input" type="email" class="zf-input" placeholder="patient@example.com" />
      <div id="zf-email-error" class="tp-dialog-error"></div>
      <div class="tp-dialog-actions">
        <button class="zf-btn zf-btn-outline tp-dialog-btn" data-action="close-email-fallback">Cancel</button>
        <button id="zf-email-send" class="zf-btn zf-btn-primary tp-dialog-btn-main" data-action="submit-email-fallback">Send by Email</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('zf-email-input')?.focus(), 50);
}

function closeEmailFallback() { document.getElementById('zf-email-overlay')?.remove(); }

async function submitEmailFallback() {
  const input = document.getElementById('zf-email-input');
  const errBox = document.getElementById('zf-email-error');
  const email = (input?.value || '').trim();
  errBox.style.display = 'none';
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    errBox.textContent = 'Please enter a valid email address.'; errBox.style.display = 'block'; return;
  }
  const btn = document.getElementById('zf-email-send');
  btn.disabled = true; btn.textContent = 'Sending…';
  try {
    const r = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/send-recommendations`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: advice.filter(a => a.enabled), schedule_hours: 2, email }),
    });
    const result = await r.json();

    // 409 google_not_connected — say why, and offer the text to copy instead (Phase 5.2)
    if (result.code === 'google_not_connected') {
      showSmtpCopyFallback(result.text, result.message);
      return;
    }

    if (!r.ok) throw new Error(result.detail || 'Email send failed');
    closeEmailFallback();
    const sendBtn = document.getElementById('send-advice-btn');
    const orig = sendBtn.innerHTML;
    sendBtn.innerHTML = '✓ Sent via email'; sendBtn.style.background = '#16A34A';
    setTimeout(() => { sendBtn.innerHTML = orig; sendBtn.style.background = ''; }, 3000);
  } catch (e) {
    errBox.textContent = e.message || String(e); errBox.style.display = 'block';
    btn.disabled = false; btn.textContent = 'Send by Email';
  }
}

function showSmtpCopyFallback(text, notice) {
  // Replace the email popup contents with a copy-paste panel
  const overlay = document.getElementById('zf-email-overlay');
  if (!overlay) return;
  overlay.querySelector('div').innerHTML = `
    <div class="tp-dialog-head">
      <div class="tp-dialog-icon-amber">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/></svg>
      </div>
      <h3 class="tp-dialog-title">Copy &amp; send manually</h3>
    </div>
    <p class="tp-dialog-note">${escHtml(notice || 'Email service not configured. Copy the text below and send it yourself.')}</p>
    <textarea id="zf-copy-text" readonly class="tp-copy-text">${escHtml(text || '')}</textarea>
    <div class="tp-dialog-actions-tight">
      <button class="zf-btn zf-btn-outline tp-dialog-btn" data-action="close-email-fallback">Close</button>
      <button class="zf-btn zf-btn-primary tp-dialog-btn-main" data-action="copy-smtp-text" id="zf-copy-btn">Copy Text</button>
    </div>`;
}

function copySmtpText() {
  const ta = document.getElementById('zf-copy-text');
  if (!ta) return;
  ta.select();
  try { document.execCommand('copy'); } catch (_) { navigator.clipboard?.writeText(ta.value); }
  const btn = document.getElementById('zf-copy-btn');
  if (btn) { btn.textContent = 'Copied ✓'; btn.style.background = '#16A34A'; setTimeout(() => { btn.textContent = 'Copy Text'; btn.style.background = ''; }, 2000); }
}
