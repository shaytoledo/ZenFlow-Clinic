// Treatment page — The used-points input and tags, and the selection model.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Points input ───────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const inp = document.getElementById('point-input');
  if (inp) {
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); addPoint(); }
      if (e.key === 'Backspace' && inp.value === '' && usedPoints.length > 0) {
        removePoint(usedPoints[usedPoints.length - 1]);
      }
    });
  }
  ['tongue-input', 'pulse-input'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); triggerRediagnosis(); } });
      el.addEventListener('change', scheduleAutoSave);
    }
  });
});

function addPoint() {
  const inp = document.getElementById('point-input');
  const val = normPointCode(inp.value);
  if (val && !usedPoints.includes(val)) { usedPoints.push(val); inp.value = ''; renderPoints(); scheduleAutoSave(); }
}

// ── Selection model (Phase 4.2): cards and chips toggle a point; any removal can be undone ──

// Pure list helpers — they return new arrays and never touch the page (tests run them in node).
function withPointToggled(list, code) {
  const c = normPointCode(code);
  const at = list.indexOf(c);
  if (at < 0) return { list: [...list, c], removedAt: -1 };
  return { list: [...list.slice(0, at), ...list.slice(at + 1)], removedAt: at };
}

function withPointRestored(list, code, at) {
  if (list.includes(code)) return list;
  const i = Math.min(Math.max(at, 0), list.length);
  return [...list.slice(0, i), code, ...list.slice(i)];
}

function togglePoint(code) {
  const c = normPointCode(code);
  if (!c) return;
  const { list, removedAt } = withPointToggled(usedPoints, c);
  usedPoints = list;
  renderPoints();
  scheduleAutoSave();
  if (removedAt >= 0) offerUndo(c, removedAt);
}

function removePoint(p) {
  const at = usedPoints.indexOf(p);
  if (at < 0) return;
  usedPoints = [...usedPoints.slice(0, at), ...usedPoints.slice(at + 1)];
  renderPoints();
  scheduleAutoSave();
  offerUndo(p, at);
}

let _undo = null;
let _undoTimer = null;

function offerUndo(code, at) {
  const bar = document.getElementById('points-undo');
  if (!bar) return;
  _undo = { code, at };
  document.getElementById('points-undo-text').textContent = pointText('removed', { code });
  document.getElementById('points-undo-btn').textContent = pointText('undo');
  bar.hidden = false;
  clearTimeout(_undoTimer);
  _undoTimer = setTimeout(dismissUndo, 6000);
}

function undoPointRemoval() {
  if (!_undo) return;
  usedPoints = withPointRestored(usedPoints, _undo.code, _undo.at);
  dismissUndo();
  renderPoints();
  scheduleAutoSave();
}

function dismissUndo() {
  _undo = null;
  clearTimeout(_undoTimer);
  const bar = document.getElementById('points-undo');
  if (bar) bar.hidden = true;
}

function renderPoints() {
  document.getElementById('points-tags').innerHTML = usedPoints.map((p) =>
    `<span class="zf-point-tag"><button type="button" class="pc-tag-label" data-action="open-point-lightbox" data-code="${escHtml(p)}" title="${escHtml(pointText('showInfo', { code: p }))}">${escHtml(p)}</button><button type="button" data-action="remove-point" data-code="${escHtml(p)}" class="tp-tag-remove" aria-label="${escHtml(pointText('remove', { code: p }))}">&times;</button></span>`
  ).join('');
  document.getElementById('point-count').textContent = `${usedPoints.length} point${usedPoints.length !== 1 ? 's' : ''} selected · Press Enter to add, Backspace to remove last`;
  syncPointSelection();
}

function toggleIntake() {
  const body = document.getElementById('intake-body');
  const chevron = document.getElementById('intake-chevron');
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  chevron.setAttribute('points', open ? '6 9 12 15 18 9' : '18 15 12 9 6 15');
}

function onNotesChange() { scheduleAutoSave(); }

function onTherapistChange() {
  const tdStatus = document.getElementById('td-status');
  if (tdStatus) tdStatus.textContent = 'Unsaved…';
  scheduleAutoSave();
}
