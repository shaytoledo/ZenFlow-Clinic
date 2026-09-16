// Treatment page — Suggested-point cards and chips.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.

// ── Suggested points renderer ──────────────────────────────────────────────────

// SVG icons by channel category
const CHANNEL_ICONS = {
  'Stomach':           '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2a9 9 0 0 1 9 9 9 9 0 0 1-9 9 9 9 0 0 1-9-9 9 9 0 0 1 9-9z"/><path d="M8 12h8"/></svg>',
  'Large Intestine':   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6c0 0 2-2 8-2s8 2 8 2"/><path d="M4 18s2 2 8 2 8-2 8-2"/><line x1="4" y1="6" x2="4" y2="18"/><line x1="20" y1="6" x2="20" y2="18"/></svg>',
  'Pericardium':       '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78z"/></svg>',
  'Liver':             '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><ellipse cx="12" cy="12" rx="10" ry="7"/><path d="M7 12s2-4 5-4 5 4 5 4"/></svg>',
  'Spleen':            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M8 12c0-2 1.5-4 4-4s4 2 4 4-1.5 4-4 4"/></svg>',
  'Governing Vessel':  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="2" x2="12" y2="22"/><path d="M6 6l6-4 6 4"/><path d="M6 18l6 4 6-4"/></svg>',
  'Heart':             '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78z"/></svg>',
  'Kidney':            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3c-4 0-7 3-7 7 0 3 1.5 5 3 7 1 1.2 2 2 2 4h4c0-2 1-2.8 2-4 1.5-2 3-4 3-7 0-4-3-7-7-7z"/></svg>',
  'Gallbladder':       '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>',
  'Triple Energizer':  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.22 4.22l2.12 2.12M17.66 17.66l2.12 2.12M2 12h3M19 12h3"/></svg>',
  'Bladder':           '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><ellipse cx="12" cy="13" rx="7" ry="8"/><path d="M12 5V2"/></svg>',
  'Conception Vessel': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="2" x2="12" y2="22"/><circle cx="12" cy="8" r="2"/><circle cx="12" cy="16" r="2"/></svg>',
  'Lung':              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 12c0-3 2-6 6-8 4 2 6 5 6 8 0 4-2 6-6 6-4 0-6-2-6-6z"/></svg>',
  'Extra Point':       '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
};

// Channel colour theme: a `tp-ch-*` class on a card or chip sets the --ch-bg / --ch-border /
// --ch-code / --ch-icon variables its children use (static/css/treatment.css).
const CHANNEL_THEMES = {
  'Stomach': 'stomach',
  'Large Intestine': 'large-intestine',
  'Pericardium': 'pericardium',
  'Liver': 'liver',
  'Spleen': 'spleen',
  'Governing Vessel': 'governing-vessel',
  'Heart': 'heart',
  'Kidney': 'kidney',
  'Gallbladder': 'gallbladder',
  'Triple Energizer': 'triple-energizer',
  'Bladder': 'bladder',
  'Conception Vessel': 'conception-vessel',
  'Lung': 'lung',
  'Extra Point': 'extra-point',
};

function channelThemeClass(channel) {
  return 'tp-ch-' + (CHANNEL_THEMES[channel] || 'default');
}

function renderSuggestedPoints(notes, rawSummary) {
  const div = document.getElementById('suggested-points');
  let pointObjects = [];

  if (notes && Array.isArray(notes.ai_suggested_points) && notes.ai_suggested_points.length > 0) {
    pointObjects = notes.ai_suggested_points.map(p =>
      typeof p === 'object' ? p : { code: String(p), rationale: '' }
    ).filter(p => p.code);
  } else {
    const known = Object.keys(POINT_INFO);
    pointObjects = known
      .filter(p => rawSummary && rawSummary.toUpperCase().includes(p))
      .map(p => ({ code: p, rationale: '' }));
  }

  // ── 1. Full-width card grid (above two columns) ───────────────────────────────
  const section  = document.getElementById('ai-points-section');
  const grid     = document.getElementById('ai-points-grid');
  const countEl  = document.getElementById('ai-points-count');

  if (pointObjects.length === 0) {
    if (section) section.style.display = 'none';
    div.innerHTML = `<span class="tp-chips-empty">No specific points detected — add manually below.</span>`;
    return;
  }

  // Needle SVG icon for the card header
  const needleSVG = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
    <line x1="12" y1="2" x2="12" y2="17"/>
    <line x1="9" y1="14" x2="12" y2="17"/><line x1="15" y1="14" x2="12" y2="17"/>
    <circle cx="12" cy="20" r="2"/>
  </svg>`;

  if (grid && section) {
    if (countEl) countEl.textContent = `${pointObjects.length} point${pointObjects.length !== 1 ? 's' : ''}`;

    grid.innerHTML = pointObjects.map((pt, idx) => {
      const code            = typeof pt === 'object' ? (pt.code || '') : String(pt);
      const rationale       = (typeof pt === 'object' && pt.rationale)        || '';
      const aiLocation      = (typeof pt === 'object' && pt.location)         || '';
      const needleTechnique = (typeof pt === 'object' && pt.needle_technique) || '';

      const info   = getPointInfo(code);
      const themeClass = channelThemeClass(info.channel);
      const icon   = CHANNEL_ICONS[info.channel]  || CHANNEL_ICONS['Extra Point'];
      const name   = info.name     || code;
      const ch     = info.channel  || '';
      const loc    = aiLocation    || info.location || '';
      const action = info.actions  || '';

      return `<div class="tp-pt-card ${themeClass}">

        <!-- Code badge + quick-add -->
        <div class="tp-pt-head">
          <div class="tp-pt-code-badge">
            <span class="tp-pt-needle">${needleSVG}</span>
            <span class="tp-pt-code">${escHtml(code)}</span>
          </div>
          <button data-action="quick-add-point" data-code="${escHtml(code)}" title="Add ${escHtml(code)} to used points"
            class="tp-pt-add">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          </button>
        </div>

        <!-- Name + channel badge -->
        <div>
          <div class="tp-pt-name">${escHtml(name)}</div>
          ${ch ? `<div class="tp-pt-channel">
            <span class="tp-pt-channel-icon">${icon}</span>
            <span class="tp-pt-channel-name">${escHtml(ch)}</span>
          </div>` : ''}
        </div>

        <!-- Location -->
        ${loc ? `<div class="tp-pt-line">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2.5" stroke-linecap="round" class="tp-pt-line-icon"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
          <span class="tp-pt-location">${escHtml(loc)}</span>
        </div>` : ''}

        <!-- Actions -->
        ${action ? `<div class="tp-pt-line">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2.5" stroke-linecap="round" class="tp-pt-line-icon"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
          <span class="tp-pt-actions">${escHtml(action)}</span>
        </div>` : ''}

        ${needleTechnique ? `<div class="tp-pt-technique">
          <span>🪡</span><span>${escHtml(needleTechnique)}</span>
        </div>` : ''}

        <!-- AI rationale for this patient -->
        ${rationale ? `<div class="tp-pt-rationale">
          <div class="tp-pt-rationale-label">${_ZF_LANG === 'he' ? 'עבור מטופל זה' : 'For this patient'}</div>
          <p class="tp-pt-rationale-text">${escHtml(rationale)}</p>
        </div>` : ''}
      </div>`;
    }).join('');

    section.style.display = 'block';  // hidden by .tp-ai-section until there are points
  }

  // ── 2. Compact chip row inside "Acupuncture Points Used" card ─────────────────
  div.innerHTML =
    `<span class="tp-chips-intro">${_ZF_LANG === 'he' ? 'פורמולת AI — לחץ <strong>+</strong> להוספה:' : 'AI formula — click <strong>+</strong> to add:'}</span>` +
    pointObjects.map(pt => {
      const code = typeof pt === 'object' ? (pt.code || '') : String(pt);
      const info  = getPointInfo(code);
      return `<button class="zf-point-chip tp-chip ${channelThemeClass(info.channel)}" data-action="quick-add-point" data-code="${escHtml(code)}"
        title="Add ${escHtml(code)} to used points">${escHtml(code)} <span class="tp-chip-plus">+</span></button>`;
    }).join('');
}
