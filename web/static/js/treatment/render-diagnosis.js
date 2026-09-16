// Treatment page — Clinical summary and TCM diagnosis rendering.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Clinical summary + diagnosis rendering ─────────────────────────────────────

function _certaintyColor(pct) {
  if (pct >= 70) return '#0D9488';
  if (pct >= 40) return '#D97706';
  return '#DC2626';
}

// The same bands as a tp-tone-* class name (static/css/treatment.css).
function _certaintyTone(pct) {
  if (pct >= 70) return 'teal';
  if (pct >= 40) return 'amber';
  return 'red';
}

function _certaintyLabel(pct) {
  if (pct >= 80) return 'High confidence';
  if (pct >= 60) return 'Likely';
  if (pct >= 40) return 'Tentative';
  return 'Low confidence — gather more data';
}

function renderDiagnosisBlock(notes, rawSummary) {
  const body = document.getElementById('summary-body');
  const tcmPattern   = notes?.tcm_pattern;
  const treatPrincip = notes?.treatment_principles;
  // A number or null — never a string that could reach innerHTML (F10).
  const certainty    = notes?.diagnosis_certainty != null ? (Number(notes.diagnosis_certainty) || 0) : null;

  let html = '';

  if (tcmPattern) {
    html += `<div class="tp-pattern">
      <div class="tp-pattern-label">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#0D9488" stroke-width="2.5" stroke-linecap="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        Primary TCM Pattern
      </div>
      <div class="tp-pattern-name">${escHtml(tcmPattern)}</div>
      ${treatPrincip ? `<div class="tp-pattern-principles">
        <span class="tp-strong">Treatment principles:</span> ${escHtml(treatPrincip)}
      </div>` : ''}
    </div>`;
  }

  if (certainty !== null && certainty > 0) {
    const col = _certaintyColor(certainty);
    const tone = _certaintyTone(certainty);
    const lbl = _certaintyLabel(certainty);
    // Update badge in card header
    const badge = document.getElementById('diag-confidence-badge');
    if (badge) {
      badge.textContent = `${certainty}% confidence`;
      badge.style.background = certainty >= 70 ? '#DCFCE7' : certainty >= 40 ? '#FEF3C7' : '#FEE2E2';
      badge.style.color = col;
      badge.style.display = 'inline';  // hidden by .tp-confidence-badge until there is a value
    }
    html += `<div class="tp-mb-14">
      <div class="tp-confidence-head">
        <div class="tp-confidence-title">AI Diagnostic Confidence · <span class="tp-confidence-level tp-tone-${tone}">${lbl}</span></div>
        <span class="tp-confidence-value tp-tone-${tone}">${certainty}%</span>
      </div>
      <div class="tp-confidence-track">
        <div class="tp-confidence-fill tp-tone-${tone}"></div>
      </div>
    </div>`;
  }

  if (rawSummary) {
    const bulletLines = rawSummary.split('\n').filter(l => l.trim());
    // Parse structured key:value lines (e.g. "Chief complaint: ...") vs plain bullets
    const structured = [];
    const plain = [];
    const kvRe = /^([A-Za-z][A-Za-z\s\/]{2,30}):\s*(.+)$/;
    bulletLines.forEach(l => {
      const clean = l.trim().replace(/^[-•*]\s*/, '');
      const m = clean.match(kvRe);
      if (m) {
        structured.push({ key: m[1].trim(), value: m[2].trim() });
      } else if (clean) {
        plain.push(clean);
      }
    });

    // Icon map for common clinical keys
    const keyIcons = {
      'chief complaint': '🩺', 'main complaint': '🩺', 'complaint': '🩺',
      'symptoms': '📋', 'key symptoms': '📋', 'symptom': '📋',
      'tcm pattern': '🔬', 'pattern': '🔬', 'diagnosis': '🔬',
      'focus': '🎯', 'suggested focus': '🎯', 'focus areas': '🎯',
      'duration': '⏱', 'onset': '⏱',
      'sleep': '🌙', 'diet': '🥗', 'stress': '🧘', 'energy': '⚡',
      'tongue': '👅', 'pulse': '💓',
    };
    const getIcon = key => {
      const k = key.toLowerCase();
      return Object.entries(keyIcons).find(([pat]) => k.includes(pat))?.[1] || '◆';
    };

    html += `<div class="tp-summary">
      <div class="tp-summary-label">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        Intake Summary
      </div>`;

    if (structured.length > 0) {
      html += `<div class="tp-kv-list">`;
      structured.forEach(({ key, value }) => {
        const icon = getIcon(key);
        html += `<div class="tp-kv-row">
          <span class="tp-kv-key">
            <span class="tp-kv-icon">${icon}</span>
            ${escHtml(key)}
          </span>
          <span class="tp-kv-value">${escHtml(value)}</span>
        </div>`;
      });
      html += `</div>`;
    }

    if (plain.length > 0) {
      if (structured.length > 0) html += `<div class="tp-mt-8">`;
      else html += `<div>`;
      plain.forEach(line => {
        html += `<div class="tp-bullet">
          <span class="tp-bullet-dot">•</span>
          <p class="tp-bullet-text">${escHtml(line)}</p>
        </div>`;
      });
      html += `</div>`;
    }

    html += `</div>`;
  }

  if (!html) {
    html = `<p class="tp-muted">No clinical summary recorded.</p>`;
  }
  body.innerHTML = html;
  // A width is data, not style: set through the CSSOM, which a strict CSP allows (Phase 4.1c).
  const fill = body.querySelector('.tp-confidence-fill');
  if (fill) fill.style.width = `${certainty}%`;
}
