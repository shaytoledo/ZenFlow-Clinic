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

// Channel-based gradient theme
const CHANNEL_COLORS = {
  'Stomach':           { bg: 'linear-gradient(135deg,#FEF3C7 0%,#FFFBEB 100%)', border: '#FDE68A', code: '#B45309', icon: '#D97706' },
  'Large Intestine':   { bg: 'linear-gradient(135deg,#E0F2FE 0%,#F0F9FF 100%)', border: '#BAE6FD', code: '#0369A1', icon: '#0284C7' },
  'Pericardium':       { bg: 'linear-gradient(135deg,#FCE7F3 0%,#FDF2F8 100%)', border: '#F9A8D4', code: '#BE185D', icon: '#DB2777' },
  'Liver':             { bg: 'linear-gradient(135deg,#DCFCE7 0%,#F0FDF4 100%)', border: '#BBF7D0', code: '#15803D', icon: '#16A34A' },
  'Spleen':            { bg: 'linear-gradient(135deg,#FEF9C3 0%,#FEFCE8 100%)', border: '#FDE047', code: '#A16207', icon: '#CA8A04' },
  'Governing Vessel':  { bg: 'linear-gradient(135deg,#EDE9FE 0%,#F5F3FF 100%)', border: '#C4B5FD', code: '#6D28D9', icon: '#7C3AED' },
  'Heart':             { bg: 'linear-gradient(135deg,#FEE2E2 0%,#FFF5F5 100%)', border: '#FECACA', code: '#B91C1C', icon: '#DC2626' },
  'Kidney':            { bg: 'linear-gradient(135deg,#DBEAFE 0%,#EFF6FF 100%)', border: '#BFDBFE', code: '#1D4ED8', icon: '#2563EB' },
  'Gallbladder':       { bg: 'linear-gradient(135deg,#D1FAE5 0%,#ECFDF5 100%)', border: '#A7F3D0', code: '#065F46', icon: '#059669' },
  'Triple Energizer':  { bg: 'linear-gradient(135deg,#CFFAFE 0%,#ECFEFF 100%)', border: '#A5F3FC', code: '#0E7490', icon: '#0891B2' },
  'Bladder':           { bg: 'linear-gradient(135deg,#E0F2FE 0%,#F0F9FF 100%)', border: '#7DD3FC', code: '#0C4A6E', icon: '#0369A1' },
  'Conception Vessel': { bg: 'linear-gradient(135deg,#F3E8FF 0%,#FAF5FF 100%)', border: '#E9D5FF', code: '#7E22CE', icon: '#9333EA' },
  'Lung':              { bg: 'linear-gradient(135deg,#FEF2F2 0%,#FFF5F5 100%)', border: '#FECACA', code: '#991B1B', icon: '#DC2626' },
  'Extra Point':       { bg: 'linear-gradient(135deg,#CCFBF1 0%,#F0FDFA 100%)', border: '#99F6E4', code: '#0F766E', icon: '#0D9488' },
};

const DEFAULT_COLOR = { bg: 'linear-gradient(135deg,#F3F4F6 0%,#FAFAFA 100%)', border: '#E5E7EB', code: '#374151', icon: '#6B7280' };

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
    div.innerHTML = `<span style="font-size:12px;color:#9CA3AF;padding:8px 0;display:block;">No specific points detected — add manually below.</span>`;
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
      const theme  = CHANNEL_COLORS[info.channel] || DEFAULT_COLOR;
      const icon   = CHANNEL_ICONS[info.channel]  || CHANNEL_ICONS['Extra Point'];
      const name   = info.name     || code;
      const ch     = info.channel  || '';
      const loc    = aiLocation    || info.location || '';
      const action = info.actions  || '';

      // Border: right border between cards except last in each row (handled by gap + auto-fill)
      const borderBottom = '1px solid #F3F4F6';

      return `<div style="padding:18px 16px;border-bottom:${borderBottom};border-right:1px solid #F3F4F6;display:flex;flex-direction:column;gap:8px;transition:background .12s;"
                   onmouseenter="this.style.background='#FAFAFA'" onmouseleave="this.style.background=''">

        <!-- Code badge + quick-add -->
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
          <div style="background:${theme.bg};border:1.5px solid ${theme.border};border-radius:10px;padding:5px 12px;display:inline-flex;align-items:center;gap:7px;">
            <span style="color:${theme.icon};opacity:.75;">${needleSVG}</span>
            <span style="font-size:18px;font-weight:800;color:${theme.code};letter-spacing:.3px;">${escHtml(code)}</span>
          </div>
          <button onclick="quickAddPoint('${escHtml(code)}')" title="Add ${escHtml(code)} to used points"
            style="width:28px;height:28px;flex-shrink:0;border-radius:8px;border:1.5px solid #E5E7EB;background:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;color:#9CA3AF;transition:all .14s;"
            onmouseenter="this.style.borderColor='#0D9488';this.style.color='#0D9488';this.style.background='#F0FDFA'"
            onmouseleave="this.style.borderColor='#E5E7EB';this.style.color='#9CA3AF';this.style.background='#fff'">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          </button>
        </div>

        <!-- Name + channel badge -->
        <div>
          <div style="font-size:13px;font-weight:700;color:#111827;">${escHtml(name)}</div>
          ${ch ? `<div style="display:inline-flex;align-items:center;gap:4px;margin-top:3px;background:${theme.bg};border:1px solid ${theme.border};border-radius:6px;padding:2px 8px;">
            <span style="color:${theme.icon};flex-shrink:0;">${icon}</span>
            <span style="font-size:10px;font-weight:600;color:${theme.code};">${escHtml(ch)}</span>
          </div>` : ''}
        </div>

        <!-- Location -->
        ${loc ? `<div style="display:flex;gap:5px;align-items:flex-start;">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2.5" stroke-linecap="round" style="flex-shrink:0;margin-top:2px;"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
          <span style="font-size:11px;color:#6B7280;line-height:1.5;">${escHtml(loc)}</span>
        </div>` : ''}

        <!-- Actions -->
        ${action ? `<div style="display:flex;gap:5px;align-items:flex-start;">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2.5" stroke-linecap="round" style="flex-shrink:0;margin-top:2px;"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
          <span style="font-size:11px;color:#374151;line-height:1.5;">${escHtml(action)}</span>
        </div>` : ''}

        ${needleTechnique ? `<div style="font-size:10px;color:#0D9488;font-weight:500;display:flex;align-items:center;gap:4px;">
          <span>🪡</span><span>${escHtml(needleTechnique)}</span>
        </div>` : ''}

        <!-- AI rationale for this patient -->
        ${rationale ? `<div style="background:linear-gradient(135deg,#F0FDFA,#ECFDF5);border-radius:8px;padding:9px 11px;border-left:2.5px solid #0D9488;margin-top:2px;">
          <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:#0D9488;letter-spacing:.7px;margin-bottom:3px;">${_ZF_LANG === 'he' ? 'עבור מטופל זה' : 'For this patient'}</div>
          <p style="font-size:11px;color:#065F46;line-height:1.55;margin:0;">${escHtml(rationale)}</p>
        </div>` : ''}
      </div>`;
    }).join('');

    section.style.display = '';
  }

  // ── 2. Compact chip row inside "Acupuncture Points Used" card ─────────────────
  div.innerHTML =
    `<span style="font-size:11px;color:#9CA3AF;width:100%;display:block;margin-bottom:6px;">${_ZF_LANG === 'he' ? 'פורמולת AI — לחץ <strong>+</strong> להוספה:' : 'AI formula — click <strong>+</strong> to add:'}</span>` +
    pointObjects.map(pt => {
      const code = typeof pt === 'object' ? (pt.code || '') : String(pt);
      const info  = getPointInfo(code);
      const theme = CHANNEL_COLORS[info.channel] || DEFAULT_COLOR;
      return `<button class="zf-point-chip" onclick="quickAddPoint('${escHtml(code)}')"
        style="border-color:${theme.border};color:${theme.code};background:${theme.bg};"
        title="Add ${escHtml(code)} to used points">${escHtml(code)} <span style="opacity:.6;font-size:14px;">+</span></button>`;
    }).join('');
}
