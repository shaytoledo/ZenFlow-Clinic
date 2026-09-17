// Treatment page — the printable patient handout (Phase 4.2c).
// Classic script: shares globals with the other treatment/*.js files, loaded in order.
//
// Printing the page prints only #print-handout (static/css/treatment.css, @media print): the
// points used today with their names and locations, and the recommendations the therapist left
// switched on. Nothing therapist-only (AI rationale, diagnosis certainty, notes, cautions meant
// for the practitioner) goes on it. It is rebuilt right before every print.

const HANDOUT_TEXT = {
  en: {
    title: 'Your treatment today',
    points: 'Acupuncture points used',
    point: 'Point',
    name: 'Name',
    location: 'Location',
    noPoints: 'No points were recorded for this session.',
    advice: 'Recommendations',
    noAdvice: 'No recommendations for this session.',
    therapist: 'Your therapist: {name}',
    footer: 'Keep this summary for your records. Contact your therapist with any questions.',
  },
  he: {
    title: 'הטיפול שלך היום',
    points: 'נקודות הדיקור שטופלו',
    point: 'נקודה',
    name: 'שם',
    location: 'מיקום',
    noPoints: 'לא נרשמו נקודות בטיפול זה.',
    advice: 'המלצות',
    noAdvice: 'אין המלצות לטיפול זה.',
    therapist: 'המטפל/ת שלך: {name}',
    footer: 'שמרו את הסיכום הזה. לכל שאלה פנו למטפל/ת.',
  },
};

// Plain text (callers escape it).
function handoutText(key, vars = {}) {
  const table = HANDOUT_TEXT[_ZF_LANG === 'he' ? 'he' : 'en'];
  return table[key].replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ''));
}

// The handout for one session. `points`: [{code, name, location}]; `advice`: [{category, text}].
function handoutHtml({ patient = '', when = '', therapist = '', points = [], advice = [] }) {
  const rows = points
    .map((p) => `<tr><td class="tp-handout-code">${escHtml(p.code)}</td><td>${escHtml(p.name)}</td><td>${escHtml(p.location)}</td></tr>`)
    .join('');
  const pointsHtml = points.length
    ? `<table class="tp-handout-table">
        <thead><tr><th>${escHtml(handoutText('point'))}</th><th>${escHtml(handoutText('name'))}</th><th>${escHtml(handoutText('location'))}</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`
    : `<p>${escHtml(handoutText('noPoints'))}</p>`;
  const items = advice.map((a) => `<li><strong>${escHtml(a.category)}</strong> — ${escHtml(a.text)}</li>`).join('');
  const adviceHtml = advice.length
    ? `<ul class="tp-handout-advice">${items}</ul>`
    : `<p>${escHtml(handoutText('noAdvice'))}</p>`;
  const signature = therapist ? `<p>${escHtml(handoutText('therapist', { name: therapist }))}</p>` : '';
  return `<header class="tp-handout-head">
      <p class="tp-handout-brand">ZenFlow</p>
      <h2 class="tp-handout-title">${escHtml(handoutText('title'))}</h2>
      <p class="tp-handout-meta">${escHtml([patient, when].filter(Boolean).join(' · '))}</p>
    </header>
    <section class="tp-handout-part">
      <h3>${escHtml(handoutText('points'))}</h3>
      ${pointsHtml}
    </section>
    <section class="tp-handout-part">
      <h3>${escHtml(handoutText('advice'))}</h3>
      ${adviceHtml}
    </section>
    <footer class="tp-handout-foot">
      ${signature}
      <p>${escHtml(handoutText('footer'))}</p>
    </footer>`;
}

// The points used today, with reference names and locations in the page language.
function handoutPoints(codes) {
  return codes.map((code) => {
    const info = getPointInfo(code);
    return { code, name: info.name || '', location: info.location || '' };
  });
}

function renderHandout() {
  const target = document.getElementById('print-handout');
  if (!target) return;
  const text = (id) => (document.getElementById(id)?.textContent || '').trim();
  target.innerHTML = handoutHtml({
    patient: text('pt-name'),
    when: text('pt-meta'),
    therapist: (document.querySelector('.zf-user-name')?.textContent || '').trim(),
    points: handoutPoints(usedPoints),
    advice: advice.filter((a) => a.enabled),
  });
}

function printHandout() {
  renderHandout();
  window.print();
}

// Ctrl+P prints the handout too.
window.addEventListener('beforeprint', renderHandout);
