// Treatment page — AI-suggested point cards and the compact chips (Phase 4.2 design:
// docs/POINT_CARD_DESIGN.md). Classic script: shares globals with the other treatment/*.js
// files, loaded in order (point-info.js first).

// SVG icons by channel (decorative: the channel name is always shown next to them)
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

// Channel colour theme: a `tp-ch-*` class on a card or chip sets the --ch-soft / --ch-border /
// --ch-ink variables its children use (static/css/treatment.css, palette in css/tokens.css).
// Looked up by the English channel name (pointChannel), so Hebrew pages are coloured too.
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

const POINT_TEXT = {
  en: {
    add: 'Add',
    added: 'Added',
    forPatient: 'For this patient',
    more: 'Actions & needling',
    actions: 'Actions',
    needling: 'Needling',
    location: 'Location',
    pregnancy: 'Traditionally avoided in pregnancy',
    selected: '{n} of {total} selected',
    chipsIntro: 'AI formula — click a point to add or remove it:',
    removed: 'Removed {code}',
    undo: 'Undo',
    remove: 'Remove {code}',
    showInfo: 'Show {code} details',
    density: 'Card detail',
    detailed: 'Detailed',
    compact: 'Compact',
  },
  he: {
    add: 'הוסף',
    added: 'נוסף',
    forPatient: 'עבור מטופל זה',
    more: 'פעולות ודיקור',
    actions: 'פעולות',
    needling: 'דיקור',
    location: 'מיקום',
    pregnancy: 'נמנעת באופן מסורתי בהריון',
    selected: '{n} מתוך {total} נבחרו',
    chipsIntro: 'פורמולת AI — לחץ על נקודה כדי להוסיף או להסיר:',
    removed: '{code} הוסרה',
    undo: 'בטל',
    remove: 'הסר את {code}',
    showInfo: 'פרטי {code}',
    density: 'רמת פירוט',
    detailed: 'מפורט',
    compact: 'תמציתי',
  },
};

// Plain text (callers escape it): POINT_TEXT[lang][key] with {placeholders} filled in.
function pointText(key, vars = {}) {
  const table = POINT_TEXT[_ZF_LANG === 'he' ? 'he' : 'en'];
  return table[key].replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ''));
}

const ICON_ADD = '<svg class="pc-icon-add" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true" focusable="false"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
const ICON_DONE = '<svg class="pc-icon-done" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polyline points="20 6 9 17 4 12"/></svg>';
const ICON_PIN = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>';
const ICON_CAUTION = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';

// The AI's list (objects or bare codes), or — for old notes without one — the reference points
// the summary mentions.
function normalizeSuggestedPoints(notes, rawSummary) {
  if (notes && Array.isArray(notes.ai_suggested_points) && notes.ai_suggested_points.length > 0) {
    return notes.ai_suggested_points
      .map((p) => (p && typeof p === 'object' ? p : { code: p == null ? '' : String(p), rationale: '' }))
      .filter((p) => normPointCode(p.code));
  }
  const summary = String(rawSummary || '').toUpperCase();
  if (!summary) return [];
  return Object.keys(POINT_INFO)
    .filter((code) => summary.includes(code))
    .map((code) => ({ code, rationale: '' }));
}

function pointToggleHtml(code, selected) {
  return `<button type="button" class="pc-toggle" data-action="toggle-point" data-code="${escHtml(code)}" aria-pressed="${selected ? 'true' : 'false'}">
      ${ICON_ADD}${ICON_DONE}<span class="pc-toggle-text">${escHtml(pointText(selected ? 'added' : 'add'))}</span><span class="tp-sr-only"> ${escHtml(code)}</span>
    </button>`;
}

// One suggested point as a card (docs/POINT_CARD_DESIGN.md §3). Every value is escaped.
function pointCardHtml(pt, selected) {
  const code = normPointCode(pt.code);
  const info = getPointInfo(code);
  const channel = info.channel || '';
  const themeClass = channelThemeClass(pointChannel(code));
  const icon = CHANNEL_ICONS[pointChannel(code)] || '';
  const name = info.name || '';
  const location = pt.location || info.location || '';
  const rationale = pt.rationale || '';
  const details = [
    [pointText('actions'), info.actions],
    [pointText('needling'), pt.needle_technique],
    [pointText('location'), location],
  ].filter(([, value]) => value);

  const parts = [];
  if (channel) {
    parts.push(`<p class="pc-channel"><span class="pc-channel-icon" aria-hidden="true">${icon}</span>${escHtml(channel)}</p>`);
  }
  // A caution is shown on the card face, never behind the disclosure.
  if (hasPregnancyCaution(code)) {
    parts.push(`<p class="pc-caution">${ICON_CAUTION}<span>${escHtml(pointText('pregnancy'))}</span></p>`);
  }
  if (location) {
    parts.push(`<p class="pc-location" title="${escHtml(location)}">${ICON_PIN}<span>${escHtml(location)}</span></p>`);
  }
  if (rationale) {
    parts.push(`<section class="pc-why"><h4 class="pc-why-label">${escHtml(pointText('forPatient'))}</h4><p>${escHtml(rationale)}</p></section>`);
  }
  if (details.length) {
    const rows = details.map(([label, value]) => `<dt>${escHtml(label)}</dt><dd>${escHtml(value)}</dd>`).join('');
    parts.push(`<details class="pc-more"><summary>${escHtml(pointText('more'))}</summary><dl>${rows}</dl></details>`);
  }

  const label = name ? code + ' ' + name : code;
  const body = parts.join('\n    ');
  return `<article class="pc ${themeClass}${selected ? ' is-selected' : ''}" data-point-card="${escHtml(code)}"
      data-action="toggle-point" data-code="${escHtml(code)}" aria-label="${escHtml(label)}">
    <header class="pc-head">
      <button type="button" class="pc-code" data-action="open-point-lightbox" data-code="${escHtml(code)}" aria-haspopup="dialog">${escHtml(code)}</button>
      ${name ? `<h3 class="pc-name">${escHtml(name)}</h3>` : ''}
      ${pointToggleHtml(code, selected)}
    </header>
    ${body}
  </article>`;
}

// One suggested point as a compact chip in the "Points used" card.
function pointChipHtml(pt, selected) {
  const code = normPointCode(pt.code);
  const themeClass = channelThemeClass(pointChannel(code));
  return `<button type="button" class="zf-point-chip pc-chip ${themeClass}${selected ? ' is-selected' : ''}" data-point-chip="${escHtml(code)}"
      data-action="toggle-point" data-code="${escHtml(code)}" aria-pressed="${selected ? 'true' : 'false'}">${ICON_ADD}${ICON_DONE}${escHtml(code)}</button>`;
}

// Reflect `usedPoints` on every card and chip without re-rendering them (open details stay open).
function syncPointSelection() {
  const chosen = new Set(usedPoints.map(normPointCode));
  const cards = [...document.querySelectorAll('[data-point-card]')];
  cards.forEach((card) => {
    const on = chosen.has(card.dataset.pointCard);
    card.classList.toggle('is-selected', on);
    const toggle = card.querySelector('.pc-toggle');
    if (!toggle) return;
    toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
    toggle.querySelector('.pc-toggle-text').textContent = pointText(on ? 'added' : 'add');
  });
  document.querySelectorAll('[data-point-chip]').forEach((chip) => {
    const on = chosen.has(chip.dataset.pointChip);
    chip.classList.toggle('is-selected', on);
    chip.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  const countEl = document.getElementById('ai-points-count');
  if (countEl && cards.length) {
    const n = cards.filter((card) => chosen.has(card.dataset.pointCard)).length;
    countEl.textContent = pointText('selected', { n, total: cards.length });
  }
}

// ── Density (Phase 4.2d): detailed or compact cards, saved per therapist ───────

function applyPointDensity(density) {
  pointDensity = density === 'compact' ? 'compact' : 'detailed';
  const section = document.getElementById('ai-points-section');
  if (!section) return;
  section.dataset.density = pointDensity;
  const group = section.querySelector('.pc-density');
  group.setAttribute('aria-label', pointText('density'));
  group.querySelectorAll('[data-density]').forEach((button) => {
    button.setAttribute('aria-pressed', button.dataset.density === pointDensity ? 'true' : 'false');
    button.textContent = pointText(button.dataset.density);
  });
}

async function setPointDensity(density) {
  if (density === pointDensity) return;
  applyPointDensity(density);
  try {
    const r = await fetch('/api/my/preferences', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ point_density: pointDensity }),
    });
    if (!r.ok) throw new Error(`saving the density failed (${r.status})`);
  } catch (e) {
    console.warn('setPointDensity:', e.message);  // the choice still applies to this page
  }
}
