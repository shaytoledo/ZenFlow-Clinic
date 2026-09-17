// Treatment page — acupoint reference data for the point cards, the point panel and the handout.
// Classic script: shares globals with the other treatment/*.js files, loaded in order.
//
// The data comes from GET /api/acupoints in the page language (Phase 4.3a; the table is seeded
// from zenflow/seed_data/acupoints.json). Each point: name (translated), name_pinyin, name_cn,
// name_en, channel (translated), channel_en (picks the colour theme), location, actions,
// contraindications. Until it loads — or if it fails — points render with their code only.

let POINT_INFO = {};
let POINT_ALIASES = {};  // other spellings → table code, e.g. KD3 → KI3
let pointInfoReady = Promise.resolve();

async function loadPointInfo() {
  try {
    const r = await fetch(`/api/acupoints?lang=${_ZF_LANG === 'he' ? 'he' : 'en'}`);
    if (!r.ok) throw new Error(`reference data request failed (${r.status})`);
    const data = await r.json();
    POINT_INFO = data.points || {};
    POINT_ALIASES = data.aliases || {};
  } catch (e) {
    console.warn('loadPointInfo:', e.message);
  }
}

// Called once at start-up (main.js); loadTreatment() waits for it.
function startPointInfo() {
  pointInfoReady = loadPointInfo();
}

// The form every list on the page stores a code in: upper case, no spaces or dashes ("st-36" → "ST36").
function normPointCode(code) {
  return String(code ?? '').toUpperCase().replace(/[\s-]/g, '');
}

// The key a code has in POINT_INFO: the code itself, or the code an alias stands for.
function pointInfoKey(code) {
  const c = normPointCode(code);
  if (POINT_INFO[c]) return c;
  return POINT_ALIASES[c] || c;
}

function getPointInfo(code) {
  return POINT_INFO[pointInfoKey(code)] || {};
}

// The English channel name, whatever the page language: it picks the colour theme.
function pointChannel(code) {
  return getPointInfo(code).channel_en || '';
}

// Traditionally avoided in pregnancy (docs/POINT_CARD_DESIGN.md §1) — from the reference data.
function hasPregnancyCaution(code) {
  return (getPointInfo(code).contraindications || []).includes('pregnancy');
}
