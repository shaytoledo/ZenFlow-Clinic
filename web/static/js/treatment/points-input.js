// Treatment page — The used-points input and tags, the selection model, the point info panel.
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
    `<span class="zf-point-tag"><button type="button" class="pc-tag-label" data-action="open-point-panel" data-code="${escHtml(p)}" title="${escHtml(pointText('showInfo', { code: p }))}">${escHtml(p)}</button><button type="button" data-action="remove-point" data-code="${escHtml(p)}" class="tp-tag-remove" aria-label="${escHtml(pointText('remove', { code: p }))}">&times;</button></span>`
  ).join('');
  document.getElementById('point-count').textContent = `${usedPoints.length} point${usedPoints.length !== 1 ? 's' : ''} selected · Press Enter to add, Backspace to remove last`;
  syncPointSelection();
}

// ── Point info panel (opened from a tag in "Points used") ─────────────────────

let _panelOpener = null;

function rationaleFor(code) {
  const key = Object.keys(aiPointRationale).find((k) => normPointCode(k) === code);
  return key ? aiPointRationale[key] : '';
}

function openPointPanel(code, opener) {
  const c = normPointCode(code);
  const panel = document.getElementById('point-panel');
  const info = getPointInfo(c);
  const rationale = rationaleFor(c);
  const isHe = _ZF_LANG === 'he';
  document.getElementById('panel-code').textContent = c;
  let html = '';
  if (rationale) {
    html += `<div class="tp-panel-note">
      <div class="tp-panel-note-label">${isHe ? 'נימוק AI לטיפול זה' : 'AI Rationale for This Session'}</div>
      <p class="tp-note-text">${escHtml(rationale)}</p>
    </div>`;
  }
  if (info && info.name) {
    const themeClass = channelThemeClass(pointChannel(c));
    html += `
      <div class="tp-mb-10 ${themeClass}"><p class="pc-channel">${escHtml(info.channel)}</p></div>
      <div class="tp-panel-name">${escHtml(info.name)}</div>
      ${hasPregnancyCaution(c) ? `<p class="pc-caution tp-mb-12">${ICON_CAUTION}<span>${escHtml(pointText('pregnancy'))}</span></p>` : ''}
      <div class="tp-mb-12">
        <span class="tp-panel-label">${escHtml(pointText('location'))}</span>
        <p class="tp-panel-text">${escHtml(info.location)}</p>
      </div>
      <div>
        <span class="tp-panel-label">${escHtml(pointText('actions'))}</span>
        <p class="tp-panel-text">${escHtml(info.actions)}</p>
      </div>`;
  } else {
    html += `<p class="tp-muted">${isHe ? 'אין נתוני עזר עבור' : 'No reference data for'} <strong>${escHtml(c)}</strong>.</p>`;
  }
  document.getElementById('panel-body').innerHTML = html;
  panel.classList.remove('hidden');
  _panelOpener = opener || null;
  panel.querySelector('[data-action="close-point-panel"]').focus();
}

function closePointPanel() {
  const panel = document.getElementById('point-panel');
  if (panel.classList.contains('hidden')) return;
  panel.classList.add('hidden');
  if (_panelOpener && document.contains(_panelOpener)) _panelOpener.focus();
  _panelOpener = null;
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
