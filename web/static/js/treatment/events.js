// Treatment page — every user action, wired by event delegation (Phase 4.1b).
// Classic script: shares globals with the other treatment/*.js files, loaded in order.
//
// No markup on this page carries on*="…" attributes (a strict CSP forbids them, Phase 9).
// Elements declare what they do instead:
//   data-action="name"        + data-* arguments   → CLICK_ACTIONS[name](element, event)
//   data-input-action="name"                       → INPUT_ACTIONS[name](element, event)
//   data-advice-edit="id"     (contenteditable)    → saved when it loses focus
//   data-hover="preset"                            → HOVER / UNHOVER inline styles

const CLICK_ACTIONS = {
  'toggle-intake': () => toggleIntake(),
  'rediagnose': (el) => triggerRediagnosis(el.dataset.force === 'true'),
  'generate': () => generateDiagnosisAndPoints(),
  'regenerate-points': () => regeneratePoints(),
  'cancel-generation': () => cancelGeneration(),
  'add-point': () => addPoint(),
  'quick-add-point': (el) => quickAddPoint(el.dataset.code),
  'remove-point': (el) => removePoint(el.dataset.code),
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

// Visual hover feedback, kept exactly as the inline handlers set it (4.1c moves it to CSS).
const HOVER = {
  'link-teal': { color: '#0D9488' },
  'card-row': { background: '#FAFAFA' },
  'add-point-btn': { borderColor: '#0D9488', color: '#0D9488', background: '#F0FDFA' },
};
const UNHOVER = {
  'link-teal': { color: '' },
  'card-row': { background: '' },
  'add-point-btn': { borderColor: '#E5E7EB', color: '#9CA3AF', background: '#fff' },
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

document.addEventListener('focusin', (e) => {
  const el = e.target.closest('[data-advice-edit]');
  if (el) el.style.background = '#F9FAFB';
});

document.addEventListener('focusout', (e) => {
  const el = e.target.closest('[data-advice-edit]');
  if (el) updateAdviceText(el.dataset.adviceEdit, el.textContent);
});

// mouseenter/mouseleave do not bubble: emulate them for every [data-hover] ancestor the pointer
// actually entered or left (not ones it merely moved inside of).
function _hoverChain(e) {
  const chain = [];
  let el = e.target.closest ? e.target.closest('[data-hover]') : null;
  while (el) {
    if (!el.contains(e.relatedTarget)) chain.push(el);
    el = el.parentElement ? el.parentElement.closest('[data-hover]') : null;
  }
  return chain;
}

document.addEventListener('mouseover', (e) => {
  _hoverChain(e).forEach((el) => Object.assign(el.style, HOVER[el.dataset.hover] || {}));
});

document.addEventListener('mouseout', (e) => {
  _hoverChain(e).forEach((el) => Object.assign(el.style, UNHOVER[el.dataset.hover] || {}));
});
