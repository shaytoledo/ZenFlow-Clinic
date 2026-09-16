// Treatment page — The used-points input and tags.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Points input ───────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const inp = document.getElementById('point-input');
  if (inp) {
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); addPoint(); }
      if (e.key === 'Backspace' && inp.value === '' && usedPoints.length > 0) {
        usedPoints.pop(); renderPoints(); scheduleAutoSave();
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
  const style = document.createElement('style');
  style.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
  document.head.appendChild(style);
});

function addPoint() {
  const inp = document.getElementById('point-input');
  const val = inp.value.trim().toUpperCase();
  if (val && !usedPoints.includes(val)) { usedPoints.push(val); inp.value = ''; renderPoints(); scheduleAutoSave(); }
}

// Add a suggested point directly to the used-points list (from card grid or chip)
function quickAddPoint(code) {
  const c = String(code).toUpperCase().trim();
  if (!c) return;
  if (!usedPoints.includes(c)) {
    usedPoints.push(c);
    renderPoints();
    scheduleAutoSave();
  }
  // Brief visual feedback on the clicked button
  const btns = document.querySelectorAll(`[data-action="quick-add-point"][data-code="${CSS.escape(c)}"]`);
  btns.forEach(btn => {
    const orig = btn.innerHTML;
    btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#16A34A" stroke-width="2.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>';
    btn.style.borderColor = '#16A34A'; btn.style.background = '#DCFCE7';
    setTimeout(() => { btn.innerHTML = orig; btn.style.borderColor = ''; btn.style.background = ''; }, 1200);
  });
}

function removePoint(p) { usedPoints = usedPoints.filter(x => x !== p); renderPoints(); scheduleAutoSave(); }

function renderPoints() {
  document.getElementById('points-tags').innerHTML = usedPoints.map(p =>
    `<span class="zf-point-tag">${escHtml(p)}<button data-action="remove-point" data-code="${escHtml(p)}" style="background:none;border:none;cursor:pointer;color:rgba(255,255,255,0.7);padding:0 0 0 4px;line-height:1;">&times;</button></span>`
  ).join('');
  document.getElementById('point-count').textContent = `${usedPoints.length} point${usedPoints.length !== 1 ? 's' : ''} selected · Press Enter to add, Backspace to remove last`;
}

function openPointPanel(code) {
  const panel = document.getElementById('point-panel');
  const info  = getPointInfo(code);
  const rationale = aiPointRationale[code];
  const isHe = _ZF_LANG === 'he';
  document.getElementById('panel-code').textContent = code;
  let html = '';
  if (rationale) {
    html += `<div style="background:#CCFBF1;border-radius:8px;padding:10px 12px;margin-bottom:14px;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#0D9488;letter-spacing:0.6px;margin-bottom:5px;">${isHe ? 'נימוק AI לטיפול זה' : 'AI Rationale for This Session'}</div>
      <p style="font-size:13px;color:#065F46;line-height:1.6;margin:0;">${escHtml(rationale)}</p>
    </div>`;
  }
  if (info && info.name) {
    const channelLabel = isHe ? 'ערוץ' : 'Channel';
    const locationLabel = isHe ? 'מיקום' : 'Location';
    const actionsLabel = isHe ? 'פעולות' : 'Actions';
    html += `
      <div style="margin-bottom:10px;"><span class="zf-badge zf-badge-teal">${info.channel}${isHe ? '' : ' ' + channelLabel}</span></div>
      <div style="font-size:15px;font-weight:600;color:#111827;margin-bottom:8px;">${info.name}</div>
      <div style="margin-bottom:12px;">
        <span style="font-size:11px;font-weight:600;text-transform:uppercase;color:#9CA3AF;letter-spacing:0.5px;">${locationLabel}</span>
        <p style="font-size:13px;color:#374151;margin-top:4px;line-height:1.6;">${info.location}</p>
      </div>
      <div>
        <span style="font-size:11px;font-weight:600;text-transform:uppercase;color:#9CA3AF;letter-spacing:0.5px;">${actionsLabel}</span>
        <p style="font-size:13px;color:#374151;margin-top:4px;line-height:1.6;">${info.actions}</p>
      </div>`;
  } else {
    html += `<p style="font-size:13px;color:#9CA3AF;">${isHe ? 'אין נתוני עזר עבור' : 'No reference data for'} <strong>${escHtml(code)}</strong>.</p>`;
  }
  document.getElementById('panel-body').innerHTML = html;
  panel.classList.remove('hidden');
}

function closePointPanel() { document.getElementById('point-panel').classList.add('hidden'); }

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
