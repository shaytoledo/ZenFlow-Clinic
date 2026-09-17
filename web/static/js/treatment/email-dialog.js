// Treatment page — sending recommendations by email (Phase 5.3, docs/GOOGLE_CONNECTION_UX.md §3):
// the address dialog, the "connect Google" explanation, and the copy-the-text fallback.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.
//
// Email goes out through the therapist's own Gmail. The page knows up front whether that works
// (`google` in #treatment-config). While it does not, the email controls of an email-only patient
// are marked disabled with the reason; trying anyway explains it instead of failing. "Connect
// Google" comes back to this session and reopens the send with the same address and items.

const EMAIL_TEXT = ZF_CONFIG.email_text || {};
// What was being sent, kept only across the round trip to Google: this tab, this page, 30 min.
const EMAIL_PENDING_KEY = 'zf:pending-email';
const EMAIL_PENDING_TTL_MS = 30 * 60 * 1000;

let googleState = {
  connected: ZF_CONFIG.google ? ZF_CONFIG.google.connected : null,
  reason: ZF_CONFIG.google ? ZF_CONFIG.google.reason : null,
};
let _emailKind = 'email';      // 'email' (the address dialog) or 'later' (the 24h queue)
let _emailAddress = '';
let _emailCopyText = '';

const ICON_MAIL = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>';
const ICON_COPY = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" focusable="false"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

// Plain text (callers escape it). The server fills every key; the key itself is the fallback.
function emailText(key) {
  return EMAIL_TEXT[key] || key;
}

function isEmailOnly() {
  return Boolean(_isManual) || Number(patientId) < 0;
}

function isGoogleBlocked() {
  return googleState.connected === false;
}

function connectGoogleUrl() {
  return '/auth/login?' + new URLSearchParams({ next: window.location.pathname }).toString();
}

// Heading, explanation and button label for why email cannot go out.
function googleCopy(reason) {
  const expired = reason === 'token_invalid';
  return {
    title: emailText(expired ? 'email_token_expired_title' : 'email_not_connected_title'),
    message: emailText(expired ? 'email_token_expired_body' : 'email_not_connected_body'),
    connect: emailText(expired ? 'email_reconnect_google_btn' : 'email_connect_google_btn'),
  };
}

// ── Markup ─────────────────────────────────────────────────────────────────────

function connectLinkHtml(label, kind) {
  return `<a class="zf-btn zf-btn-primary ed-btn" href="${escHtml(connectGoogleUrl())}" data-action="connect-google" data-kind="${escHtml(kind)}">${escHtml(label)}</a>`;
}

// Shown inside the address dialog while Google is not connected.
function googleNoticeHtml(reason) {
  const copy = googleCopy(reason);
  const linkHtml = connectLinkHtml(copy.connect, 'email');
  return `<div class="ed-notice" role="note">
    <p class="ed-notice-title">${escHtml(copy.title)}</p>
    <p class="ed-notice-text" id="email-blocked">${escHtml(copy.message)}</p>
    <div class="ed-notice-actions">
      ${linkHtml}
      <button type="button" class="zf-btn zf-btn-outline ed-btn" data-action="show-email-copy">${escHtml(emailText('email_copy_instead_btn'))}</button>
    </div>
  </div>`;
}

// The address form. `blocked` keeps Send focusable but marked disabled: pressing it still asks
// the server, which answers with the explanation (and the text to copy).
function emailAskHtml({ message = '', email = '', status = '', blocked = false, reason = null } = {}) {
  const statusHtml = status ? `<p class="ed-status" role="status">${escHtml(status)}</p>` : '';
  const noticeHtml = blocked ? googleNoticeHtml(reason) : '';
  const blockedHtml = blocked ? ' aria-disabled="true" aria-describedby="email-blocked"' : '';
  return `<form class="ed-card" novalidate>
    <header class="ed-head">
      <span class="ed-icon">${ICON_MAIL}</span>
      <h2 class="ed-title" id="email-title">${escHtml(emailText('email_dialog_no_telegram_title'))}</h2>
    </header>
    <p class="ed-text">${escHtml(message || emailText('email_dialog_ask_text'))}</p>
    ${statusHtml}
    <label class="ed-label" for="email-address">${escHtml(emailText('email_dialog_address_label'))}</label>
    <input id="email-address" type="email" class="zf-input" autocomplete="off" placeholder="patient@example.com" value="${escHtml(email)}">
    <p id="email-error" class="ed-error" role="alert" hidden></p>
    ${noticeHtml}
    <div class="ed-actions">
      <button type="button" class="zf-btn zf-btn-outline ed-btn" data-action="close-email-dialog">${escHtml(emailText('btn_cancel'))}</button>
      <button type="submit" id="email-send" class="zf-btn zf-btn-primary ed-btn"${blockedHtml}>${escHtml(emailText('email_dialog_send'))}</button>
    </div>
  </form>`;
}

// The server refused because of Google (409 google_not_connected); `refusal` is its body.
function emailGoogleHtml(refusal = {}) {
  const copy = googleCopy(refusal.reason);
  const linkHtml = connectLinkHtml(copy.connect, _emailKind);
  return `<div class="ed-card">
    <header class="ed-head">
      <span class="ed-icon ed-icon-caution">${ICON_MAIL}</span>
      <h2 class="ed-title" id="email-title">${escHtml(refusal.title || copy.title)}</h2>
    </header>
    <p class="ed-text">${escHtml(refusal.message || copy.message)}</p>
    <p class="ed-hint">${escHtml(emailText('email_google_hint'))}</p>
    <p id="email-error" class="ed-error" role="alert" hidden></p>
    <div class="ed-actions">
      <button type="button" class="zf-btn zf-btn-outline ed-btn" data-action="close-email-dialog">${escHtml(emailText('btn_cancel'))}</button>
      <button type="button" class="zf-btn zf-btn-outline ed-btn" data-action="show-email-copy">${escHtml(emailText('email_copy_instead_btn'))}</button>
      ${linkHtml}
    </div>
  </div>`;
}

function emailCopyHtml(text) {
  return `<div class="ed-card">
    <header class="ed-head">
      <span class="ed-icon">${ICON_COPY}</span>
      <h2 class="ed-title" id="email-title">${escHtml(emailText('email_dialog_copy_title'))}</h2>
    </header>
    <p class="ed-hint" id="email-copy-hint">${escHtml(emailText('email_dialog_copy_hint'))}</p>
    <textarea id="email-copy-text" class="ed-copy" rows="10" readonly aria-labelledby="email-title" aria-describedby="email-copy-hint">${escHtml(text)}</textarea>
    <div class="ed-actions">
      <button type="button" class="zf-btn zf-btn-outline ed-btn" data-action="close-email-dialog">${escHtml(emailText('btn_close'))}</button>
      <button type="button" class="zf-btn zf-btn-primary ed-btn" data-action="copy-email-text">${escHtml(emailText('email_dialog_copy_btn'))}</button>
    </div>
  </div>`;
}

// Under the send buttons of an email-only patient while Google is not connected.
function googleHintHtml(reason) {
  const copy = googleCopy(reason);
  const linkHtml = `<a class="tp-google-hint-link" href="${escHtml(connectGoogleUrl())}" data-action="connect-google" data-kind="later">${escHtml(copy.connect)}</a>`;
  return `${escHtml(emailText('email_only_patient_hint'))} ${escHtml(copy.title)}. ${linkHtml}`;
}

// ── The dialog ─────────────────────────────────────────────────────────────────

function emailDialog() {
  return document.getElementById('email-dialog');
}

function showEmailDialog(html, focusSelector) {
  const dialog = emailDialog();
  if (!dialog) return;
  dialog.innerHTML = html;
  if (!dialog.open) dialog.showModal();
  dialog.querySelector(focusSelector)?.focus();
}

function showEmailError(message) {
  const box = document.getElementById('email-error');
  if (!box) return;
  box.textContent = message;
  box.hidden = false;
}

// Asked for when a patient has no Telegram (422 needs_email) or after coming back from Google.
function openEmailDialog({ message = '', email = '', status = '' } = {}) {
  _emailKind = 'email';
  _emailCopyText = '';
  showEmailDialog(
    emailAskHtml({ message, email, status, blocked: isGoogleBlocked(), reason: googleState.reason }),
    '#email-address',
  );
}

function closeEmailDialog() {
  const dialog = emailDialog();
  if (dialog && dialog.open) dialog.close();
}

function enabledAdvice() {
  return advice.filter((a) => a.enabled);
}

async function submitEmailDialog() {
  const input = document.getElementById('email-address');
  const button = document.getElementById('email-send');
  if (!input || !button || button.dataset.busy) return;
  const email = input.value.trim();
  document.getElementById('email-error').hidden = true;
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    showEmailError(emailText('email_dialog_invalid'));
    input.focus();
    return;
  }
  _emailAddress = email;
  button.dataset.busy = '1';
  button.textContent = emailText('email_dialog_sending');
  try {
    const r = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/send-recommendations`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: enabledAdvice(), schedule_hours: 2, email }),
    });
    const result = await r.json();
    if (handleGoogleRefusal(result, 'email')) return;
    if (!r.ok) throw new Error(result.message || result.detail || emailText('email_dialog_failed'));
    googleState = { connected: true, reason: null };  // it just worked
    applyGooglePreflight();
    closeEmailDialog();
    const sendBtn = document.getElementById('send-advice-btn');
    const orig = sendBtn.innerHTML;
    sendBtn.innerHTML = '✓ Sent via email'; sendBtn.style.background = '#16A34A';
    setTimeout(() => { sendBtn.innerHTML = orig; sendBtn.style.background = ''; }, 3000);
  } catch (e) {
    showEmailError(e.message || emailText('email_dialog_failed'));
  } finally {
    delete button.dataset.busy;
    button.textContent = emailText('email_dialog_send');
  }
}

// A send answered 409 google_not_connected: explain it. Returns true when it did.
function handleGoogleRefusal(result, kind) {
  if (!result || result.code !== 'google_not_connected') return false;
  googleState = { connected: false, reason: result.reason || 'not_connected' };
  applyGooglePreflight();
  _emailKind = kind;
  _emailCopyText = result.text || '';
  showEmailDialog(emailGoogleHtml(result), '[data-action="connect-google"]');
  return true;
}

async function showEmailCopy() {
  if (!_emailCopyText) {
    try {
      const r = await fetch(`/api/treatment-notes/${patientId}/${aptDate}/${aptTimeSlug}/recommendations-text`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: enabledAdvice() }),
      });
      const result = await r.json();
      if (!r.ok || !result.text) throw new Error(result.detail || '');
      _emailCopyText = result.text;
    } catch (_) {
      showEmailError(emailText('email_dialog_copy_failed'));
      return;
    }
  }
  showEmailDialog(emailCopyHtml(_emailCopyText), '#email-copy-text');
  document.getElementById('email-copy-text')?.select();
}

async function copyEmailText(button) {
  const area = document.getElementById('email-copy-text');
  if (!area) return;
  area.select();
  let copied = false;
  try {
    await navigator.clipboard.writeText(area.value);
    copied = true;
  } catch (_) {
    try { copied = document.execCommand('copy'); } catch (_) { copied = false; }
  }
  if (!copied) return;  // the text stays selected for Ctrl+C
  button.textContent = emailText('email_dialog_copied');
  setTimeout(() => { button.textContent = emailText('email_dialog_copy_btn'); }, 2000);
}

// The link navigates by itself; first remember what was being sent.
function connectGoogle(link) {
  const typed = document.getElementById('email-address')?.value.trim();
  savePendingEmail({ kind: link.dataset.kind || _emailKind, email: typed || _emailAddress });
}

// ── Before the click: mark the email controls ──────────────────────────────────

function applyGooglePreflight() {
  const blocked = isGoogleBlocked() && isEmailOnly();
  for (const id of ['send-advice-btn', 'send-later-btn']) {
    const button = document.getElementById(id);
    if (!button) continue;
    if (blocked) {
      if (!('titleBefore' in button.dataset)) button.dataset.titleBefore = button.getAttribute('title') || '';
      button.setAttribute('aria-disabled', 'true');
      button.setAttribute('aria-describedby', 'google-hint');
      button.setAttribute('title', googleCopy(googleState.reason).message);
    } else if (button.getAttribute('aria-disabled') === 'true') {
      button.removeAttribute('aria-disabled');
      button.removeAttribute('aria-describedby');
      if (button.dataset.titleBefore) button.setAttribute('title', button.dataset.titleBefore);
      else button.removeAttribute('title');
      delete button.dataset.titleBefore;
    }
  }
  const hint = document.getElementById('google-hint');
  if (!hint) return;
  hint.innerHTML = blocked ? googleHintHtml(googleState.reason) : '';
  hint.hidden = !blocked;
}

// ── The round trip to Google ───────────────────────────────────────────────────

function savePendingEmail({ kind, email }) {
  const pending = {
    path: window.location.pathname,
    at: Date.now(),
    kind: kind === 'later' ? 'later' : 'email',
    email: email || '',
    advice: advice.map(({ id, enabled, text }) => ({ id, enabled, text })),
  };
  try {
    sessionStorage.setItem(EMAIL_PENDING_KEY, JSON.stringify(pending));
  } catch (_) { /* storage blocked: the send is simply not restored */ }
}

// The saved send for this page, if it is recent; it is removed either way.
function takePendingEmail(now = Date.now()) {
  let raw = null;
  try {
    raw = sessionStorage.getItem(EMAIL_PENDING_KEY);
    sessionStorage.removeItem(EMAIL_PENDING_KEY);
  } catch (_) {
    return null;
  }
  let pending = null;
  try {
    pending = JSON.parse(raw || 'null');
  } catch (_) {
    return null;
  }
  if (!pending || typeof pending !== 'object') return null;
  if (pending.path !== window.location.pathname) return null;
  if (!(now - Number(pending.at) <= EMAIL_PENDING_TTL_MS)) return null;
  return pending;
}

// `current` with the saved on/off switches and edited texts; unknown ids are ignored.
function restoredAdvice(current, saved) {
  if (!Array.isArray(saved)) return current;
  const byId = new Map(saved.filter((item) => item && item.id).map((item) => [item.id, item]));
  return current.map((item) => {
    const s = byId.get(item.id);
    if (!s) return item;
    const text = typeof s.text === 'string' && s.text.trim() ? s.text : item.text;
    return { ...item, enabled: Boolean(s.enabled), text };
  });
}

// Back from /auth/login?next=… with ?google=connected|cancelled (called once the advice is loaded).
function resumeAfterGoogle() {
  const url = new URL(window.location.href);
  const outcome = url.searchParams.get('google');
  if (!outcome) return;
  url.searchParams.delete('google');
  window.history.replaceState(null, '', url.pathname + url.search + url.hash);
  const status = emailText(outcome === 'connected' ? 'email_google_connected_banner' : 'email_google_cancelled_banner');
  const pending = takePendingEmail();
  if (pending) {
    advice = restoredAdvice(advice, pending.advice);
    renderAdvice();
  }
  if (pending && pending.kind === 'email') {
    openEmailDialog({ email: pending.email, status });
    return;
  }
  const statusEl = document.getElementById('google-status');
  if (statusEl) {
    statusEl.textContent = status;
    statusEl.hidden = false;
  }
  if (pending) document.getElementById('send-later-btn')?.focus();
}

document.addEventListener('DOMContentLoaded', () => {
  const dialog = emailDialog();
  if (!dialog) return;
  dialog.addEventListener('submit', (e) => {
    e.preventDefault();
    submitEmailDialog();
  });
  // A click on the backdrop (the dialog element itself, outside its card) closes it.
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) dialog.close();
  });
  dialog.addEventListener('close', () => {
    dialog.innerHTML = '';
  });
});
