// Treatment page — every user action, wired by event delegation (Phase 4.1b).
// Classic script: shares globals with the other treatment/*.js files, loaded in order.
//
// No markup on this page carries on*="…" attributes (a strict CSP forbids them, Phase 9).
// Elements declare what they do instead:
//   data-action="name"        + data-* arguments   → CLICK_ACTIONS[name](element, event)
//   data-input-action="name"                       → INPUT_ACTIONS[name](element, event)
//   data-advice-edit="id"     (contenteditable)    → saved when it loses focus
// Hover and focus feedback is plain CSS (static/css/treatment.css), like every other style on
// the page: no markup carries a style attribute either (Phase 4.1c).

const CLICK_ACTIONS = {
  'toggle-intake': () => toggleIntake(),
  'rediagnose': (el) => triggerRediagnosis(el.dataset.force === 'true'),
  'generate': () => generateDiagnosisAndPoints(),
  'regenerate-points': () => regeneratePoints(),
  'cancel-generation': () => cancelGeneration(),
  'add-point': () => addPoint(),
  'toggle-point': (el, e) => {
    // A whole card toggles too, except clicks meant for its details or a text selection.
    if (el.tagName === 'ARTICLE' && (e.target.closest('details') || String(window.getSelection()))) return;
    togglePoint(el.dataset.code);
  },
  'undo-point-removal': () => undoPointRemoval(),
  'remove-point': (el) => removePoint(el.dataset.code),
  'open-point-panel': (el) => openPointPanel(el.dataset.code, el),
  'close-point-panel': () => closePointPanel(),
  'send-advice': () => sendAdvice(),
  'send-advice-later': () => sendAdviceLater(),
  'toggle-advice': (el) => toggleAdvice(el.dataset.adviceId),
  'complete-session': () => completeSession(),
  'set-mf-rating': (el) => setMfRating(Number(el.dataset.val)),
  'save-manual-feedback': () => saveManualFeedback(),
  'close-email-fallback': () => closeEmailFallback(),
  'submit-email-fallback': () => submitEmailFallback(),
  'copy-smtp-text': () => copySmtpText(),
};

const INPUT_ACTIONS = {
  'therapist-change': () => onTherapistChange(),
  'mf-change': () => onMfChange(),
  'notes-change': () => onNotesChange(),
};

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  const run = el && CLICK_ACTIONS[el.dataset.action];
  if (run) run(el, e);
});

document.addEventListener('input', (e) => {
  const el = e.target.closest('[data-input-action]');
  const run = el && INPUT_ACTIONS[el.dataset.inputAction];
  if (run) run(el, e);
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closePointPanel();
});

document.addEventListener('focusout', (e) => {
  const el = e.target.closest('[data-advice-edit]');
  if (el) updateAdviceText(el.dataset.adviceEdit, el.textContent);
});
