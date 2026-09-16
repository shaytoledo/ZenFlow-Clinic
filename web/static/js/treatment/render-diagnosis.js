// Treatment page — Clinical summary and TCM diagnosis rendering.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Clinical summary + diagnosis rendering ─────────────────────────────────────

function _certaintyColor(pct) {
  if (pct >= 70) return '#0D9488';
  if (pct >= 40) return '#D97706';
  return '#DC2626';
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
    html += `<div style="background:linear-gradient(135deg,#CCFBF1 0%,#ECFDF5 100%);border-radius:12px;padding:14px 16px;margin-bottom:12px;border:1px solid #A7F3D0;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#0D9488;letter-spacing:0.8px;margin-bottom:6px;display:flex;align-items:center;gap:6px;">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#0D9488" stroke-width="2.5" stroke-linecap="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        Primary TCM Pattern
      </div>
      <div style="font-size:16px;font-weight:700;color:#111827;line-height:1.35;">${escHtml(tcmPattern)}</div>
      ${treatPrincip ? `<div style="font-size:12px;color:#065F46;margin-top:8px;line-height:1.55;border-top:1px dashed #A7F3D0;padding-top:8px;">
        <span style="font-weight:600;">Treatment principles:</span> ${escHtml(treatPrincip)}
      </div>` : ''}
    </div>`;
  }

  if (certainty !== null && certainty > 0) {
    const col = _certaintyColor(certainty);
    const lbl = _certaintyLabel(certainty);
    // Update badge in card header
    const badge = document.getElementById('diag-confidence-badge');
    if (badge) {
      badge.textContent = `${certainty}% confidence`;
      badge.style.background = certainty >= 70 ? '#DCFCE7' : certainty >= 40 ? '#FEF3C7' : '#FEE2E2';
      badge.style.color = col;
      badge.style.display = '';
    }
    html += `<div style="margin-bottom:14px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:5px;">
        <div style="font-size:11px;font-weight:600;text-transform:uppercase;color:#6B7280;letter-spacing:0.5px;">AI Diagnostic Confidence · <span style="color:${col};font-weight:600;">${lbl}</span></div>
        <span style="font-size:17px;font-weight:800;color:${col};">${certainty}%</span>
      </div>
      <div style="background:#F3F4F6;border-radius:999px;height:8px;overflow:hidden;">
        <div style="height:100%;border-radius:999px;background:linear-gradient(90deg,${col} 0%,${col}99 100%);width:${certainty}%;transition:width .7s ease;"></div>
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

    html += `<div style="border-top:1px solid #E5E7EB;padding-top:14px;margin-top:4px;">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#6B7280;letter-spacing:0.7px;margin-bottom:10px;display:flex;align-items:center;gap:6px;">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        Intake Summary
      </div>`;

    if (structured.length > 0) {
      html += `<div style="display:flex;flex-direction:column;gap:6px;">`;
      structured.forEach(({ key, value }) => {
        const icon = getIcon(key);
        html += `<div style="display:grid;grid-template-columns:auto 1fr;gap:8px;align-items:baseline;background:#FAFAFA;border-radius:8px;padding:8px 10px;border:1px solid #F0F0F0;">
          <span style="font-size:11px;font-weight:700;color:#6B7280;white-space:nowrap;display:flex;align-items:center;gap:5px;">
            <span style="font-size:13px;">${icon}</span>
            ${escHtml(key)}
          </span>
          <span style="font-size:13px;color:#111827;line-height:1.5;">${escHtml(value)}</span>
        </div>`;
      });
      html += `</div>`;
    }

    if (plain.length > 0) {
      if (structured.length > 0) html += `<div style="margin-top:8px;">`;
      else html += `<div>`;
      plain.forEach(line => {
        html += `<div style="display:flex;gap:8px;align-items:flex-start;margin-bottom:5px;">
          <span style="color:#0D9488;font-size:13px;flex-shrink:0;margin-top:2px;">•</span>
          <p style="font-size:13px;color:#374151;line-height:1.6;margin:0;">${escHtml(line)}</p>
        </div>`;
      });
      html += `</div>`;
    }

    html += `</div>`;
  }

  if (!html) {
    html = `<p style="font-size:13px;color:#9CA3AF;">No clinical summary recorded.</p>`;
  }
  body.innerHTML = html;
}
