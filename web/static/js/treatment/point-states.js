// Treatment page — the AI points area as one set of designed states (Phase 4.2b,
// docs/POINT_CARD_DESIGN.md §4). Classic script: shares globals with the other treatment/*.js
// files, loaded in order (after render-points.js, before pipeline.js).
//
//   idle       nothing generated yet                        → empty state + Generate
//   loading    a run is working and no point has arrived    → step list + skeleton cards
//   partial    the first batch is in, the second is working → cards + skeletons
//   ready      the formula is complete                      → cards
//   failed     the run stopped (points it saved are kept)   → cards if any + Retry
//   cancelled  the therapist cancelled                      → cards if any + Generate
//   stalled    no result after 15 minutes                   → Retry that may override a stale status
//
// Every path that changes the AI points calls showPointState(); nothing else writes to
// #ai-points-grid, #ai-points-message or #suggested-points. Progress is the run's real stage
// (points_status), never a timer.

const POINT_STEPS = ['summary', 'diagnosis', 'batch-a', 'batch-b'];
const STAGE_BY_STATUS = {
  GENERATING_STAGE_0: 'summary',
  GENERATING_STAGE_1: 'diagnosis',
  GENERATING_STAGE_2A: 'batch-a',
  GENERATING_STAGE_2B: 'batch-b',
};

const STATE_TEXT = {
  en: {
    summary: 'Intake summary',
    diagnosis: 'TCM diagnosis',
    'batch-a': 'First points',
    'batch-b': 'More points',
    stepOf: 'Step {n} of {total}: {name}…',
    cancel: 'Cancel',
    idleTitle: 'No AI formula yet',
    idleBody: 'Generate a TCM diagnosis and a point formula from the intake and your findings.',
    noInputTitle: 'No intake on file',
    noInputBody: 'Add tongue and pulse findings, then generate.',
    generate: 'Generate diagnosis & points',
    cancelledTitle: 'Generation cancelled',
    cancelledBody: 'Nothing from the cancelled run was saved.',
    cancelledPartialBody: 'The points above arrived before you cancelled.',
    failedTitle: "The AI didn't finish",
    failedBody: 'Point generation did not complete. Your notes are safe.',
    failedPartialBody: 'Only the first batch arrived. The points above are kept.',
    stalledTitle: 'Still no result after 15 minutes',
    stalledBody: 'The run may have stopped. Retrying replaces it.',
    retry: 'Retry point generation',
    moreComing: 'Selecting more points…',
    chipsWaiting: 'The AI formula will appear here.',
    chipsNone: 'No AI formula yet — add points manually below.',
  },
  he: {
    summary: 'סיכום שאלון',
    diagnosis: 'אבחנה סינית',
    'batch-a': 'נקודות ראשונות',
    'batch-b': 'נקודות נוספות',
    stepOf: 'שלב {n} מתוך {total}: {name}…',
    cancel: 'ביטול',
    idleTitle: 'אין עדיין פורמולת AI',
    idleBody: 'צור אבחנה סינית ופורמולת נקודות מהשאלון ומהממצאים שלך.',
    noInputTitle: 'אין שאלון בתיק',
    noInputBody: 'הוסף ממצאי לשון ודופק, ואז צור.',
    generate: 'צור אבחנה ונקודות',
    cancelledTitle: 'היצירה בוטלה',
    cancelledBody: 'דבר מהריצה שבוטלה לא נשמר.',
    cancelledPartialBody: 'הנקודות שלמעלה הגיעו לפני הביטול.',
    failedTitle: 'ה-AI לא סיים',
    failedBody: 'בחירת הנקודות לא הושלמה. הרשומות שלך שמורות.',
    failedPartialBody: 'רק הקבוצה הראשונה הגיעה. הנקודות שלמעלה נשמרו.',
    stalledTitle: 'אין תוצאה אחרי 15 דקות',
    stalledBody: 'ייתכן שהריצה נעצרה. ניסיון חוזר מחליף אותה.',
    retry: 'נסה שוב',
    moreComing: 'בוחר נקודות נוספות…',
    chipsWaiting: 'פורמולת ה-AI תופיע כאן.',
    chipsNone: 'אין עדיין פורמולת AI — הוסף נקודות ידנית למטה.',
  },
};

// Plain text (callers escape it).
function stateText(key, vars = {}) {
  const table = STATE_TEXT[_ZF_LANG === 'he' ? 'he' : 'en'];
  return table[key].replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ''));
}

const ICON_SPARK = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></svg>';
const ICON_ALERT = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';

let _pointStage = null;
let _shownPoints = [];  // what the grid shows now (a stalled run keeps it)

// The run's stage for a points_status, or null when nothing is generating.
function stageOfStatus(status) {
  return STAGE_BY_STATUS[status] || null;
}

// The state for a status nothing is generating under.
function restingState(status, hasPoints) {
  if (status === 'FAILED') return 'failed';
  if (status === 'CANCELLED') return 'cancelled';
  if (hasPoints) return 'ready';
  return status === 'COMPLETED' ? 'failed' : 'idle';  // a finished run that produced nothing
}

// The designed state for notes as the server reports them: { state, points, stage, generating }.
function stateOfNotes(notes) {
  const status = String((notes && notes.points_status) || '');
  const points = pointsOf(notes);
  if (status.startsWith('GENERATING')) {
    const stage = stageOfStatus(status) || 'diagnosis';
    return { state: points.length ? 'partial' : 'loading', points, stage, generating: true };
  }
  return { state: restingState(status, points.length > 0), points, stage: null, generating: false };
}

function skeletonCardHtml(label) {
  const labelHtml = label ? `<p class="ps-skeleton-label">${escHtml(label)}</p>` : '';
  return `<div class="ps-skeleton" aria-hidden="true">${labelHtml}<span class="ps-bar ps-bar-code"></span><span class="ps-bar"></span><span class="ps-bar ps-bar-short"></span><span class="ps-bar ps-bar-block"></span></div>`;
}

function pointMessageHtml(state, hasPoints, hasInput) {
  const specs = {
    idle: hasInput ? ['idleTitle', 'idleBody', 'generate'] : ['noInputTitle', 'noInputBody', 'generate'],
    cancelled: ['cancelledTitle', hasPoints ? 'cancelledPartialBody' : 'cancelledBody', 'generate'],
    failed: ['failedTitle', hasPoints ? 'failedPartialBody' : 'failedBody', 'retry'],
    stalled: ['stalledTitle', 'stalledBody', 'retry'],
  };
  const spec = specs[state];
  if (!spec) return '';
  const [title, body, action] = spec;
  const alert = state === 'failed' || state === 'stalled';
  const force = state === 'stalled';
  const icon = alert ? ICON_ALERT : ICON_SPARK;
  const actionHtml = action === 'generate'
    ? `<button type="button" class="zf-btn zf-btn-primary ps-action" data-action="generate">${escHtml(stateText('generate'))}</button>`
    : `<button type="button" class="zf-btn zf-btn-outline ps-action" data-action="rediagnose" data-force="${force ? 'true' : 'false'}">${escHtml(stateText('retry'))}</button>`;
  return `<div class="ps-panel${alert ? ' ps-panel-alert' : ''}"${alert ? ' role="alert"' : ''}>
      <span class="ps-icon">${icon}</span>
      <div class="ps-text">
        <h3 class="ps-title">${escHtml(stateText(title))}</h3>
        <p class="ps-body">${escHtml(stateText(body))}</p>
      </div>
      ${actionHtml}
    </div>`;
}

// Mark the step list for a stage (null hides it).
function setPointStage(stage) {
  _pointStage = stage;
  const status = document.getElementById('ai-points-status');
  if (!status) return;
  status.hidden = !stage;
  if (!stage) return;
  const at = POINT_STEPS.indexOf(stage);
  status.querySelectorAll('[data-step]').forEach((step) => {
    const i = POINT_STEPS.indexOf(step.dataset.step);
    step.textContent = stateText(step.dataset.step);
    step.dataset.state = i < at ? 'done' : i === at ? 'current' : 'todo';
  });
  document.getElementById('ai-points-step').textContent = stateText('stepOf', {
    n: at + 1,
    total: POINT_STEPS.length,
    name: stateText(stage),
  });
  document.getElementById('cancel-generation-btn').textContent = stateText('cancel');
}

// Render the AI points area. `points` are normalised (normalizeSuggestedPoints / pointsOf).
function showPointState(state, { points = [], stage = null, hasInput = true } = {}) {
  const section = document.getElementById('ai-points-section');
  const grid = document.getElementById('ai-points-grid');
  const message = document.getElementById('ai-points-message');
  const chips = document.getElementById('suggested-points');
  const badge = document.getElementById('points-ai-badge');
  const busy = state === 'loading' || state === 'partial';
  const chosen = new Set(usedPoints.map(normPointCode));
  _shownPoints = points;

  section.style.display = 'block';  // hidden by .tp-ai-section until the page knows its state
  section.dataset.state = state;
  section.setAttribute('aria-busy', busy ? 'true' : 'false');
  setPointStage(busy ? stage || _pointStage || 'diagnosis' : null);

  const cells = points.map((pt) => pointCardHtml(pt, chosen.has(normPointCode(pt.code))));
  if (state === 'loading') for (let i = 0; i < 6; i++) cells.push(skeletonCardHtml(''));
  if (state === 'partial') {
    cells.push(skeletonCardHtml(stateText('moreComing')));
    cells.push(skeletonCardHtml(''));
  }
  grid.innerHTML = cells.join('');
  grid.hidden = cells.length === 0;

  message.innerHTML = pointMessageHtml(state, points.length > 0, hasInput);
  message.hidden = !message.innerHTML;

  chips.innerHTML = points.length
    ? `<span class="tp-chips-intro">${escHtml(pointText('chipsIntro'))}</span>`
      + points.map((pt) => pointChipHtml(pt, chosen.has(normPointCode(pt.code)))).join('')
    : `<span class="tp-chips-empty">${escHtml(stateText(busy ? 'chipsWaiting' : 'chipsNone'))}</span>`;

  if (badge) badge.style.display = state === 'ready' ? 'inline' : 'none';
  const count = document.getElementById('ai-points-count');
  if (count && !points.length) count.textContent = '';
  syncPointSelection();
}

// The suggested points stored on a notes object (the AI's list only).
function pointsOf(notes) {
  return normalizeSuggestedPoints(notes, null);
}

// Remember each suggested point's rationale for the point panel.
function rememberRationales(notes) {
  aiPointRationale = {};
  ((notes && notes.ai_suggested_points) || []).forEach((p) => {
    if (p && typeof p === 'object' && p.code) aiPointRationale[p.code] = p.rationale || '';
  });
}
