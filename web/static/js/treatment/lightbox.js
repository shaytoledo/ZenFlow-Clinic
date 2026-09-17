// Treatment page — the point lightbox (Phase 4.3d): a point's image(s) with attribution, zoom,
// and the clinical detail. Classic script: shares globals with the other treatment/*.js files.
//
// Opened from a card's code badge or a used-point tag. It is a native <dialog> (showModal): the
// browser traps focus, closes it on Escape and returns focus to the opener. Images come from
// /api/acupoints only when ZF_POINT_IMAGES is on; otherwise — or when an image fails to load —
// a placeholder is shown, never a broken icon.

const LIGHTBOX_TEXT = {
  en: {
    close: 'Close',
    noImage: 'No image for {code} yet',
    image: 'Image {n} of {total}',
    zoomIn: 'Zoom in',
    zoomOut: 'Zoom out',
    licence: 'Licence',
    source: 'Source',
    location: 'Location',
    actions: 'Actions',
    forPatient: 'AI rationale for this session',
    noReference: 'No reference data for {code}.',
  },
  he: {
    close: 'סגור',
    noImage: 'אין עדיין תמונה עבור {code}',
    image: 'תמונה {n} מתוך {total}',
    zoomIn: 'הגדל',
    zoomOut: 'הקטן',
    licence: 'רישיון',
    source: 'מקור',
    location: 'מיקום',
    actions: 'פעולות',
    forPatient: 'נימוק AI לטיפול זה',
    noReference: 'אין נתוני עזר עבור {code}.',
  },
};

// Plain text (callers escape it).
function lightboxText(key, vars = {}) {
  const table = LIGHTBOX_TEXT[_ZF_LANG === 'he' ? 'he' : 'en'];
  return table[key].replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ''));
}

// Only links a stored row may carry: absolute http(s) URLs, or our own /media/ paths.
function safeUrl(value) {
  const url = String(value ?? '').trim();
  return /^https?:\/\//i.test(url) || url.startsWith('/media/') ? url : '';
}

const ICON_CLOSE = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
const ICON_IMAGE = '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>';

function rationaleFor(code) {
  const key = Object.keys(aiPointRationale).find((k) => normPointCode(k) === code);
  return key ? aiPointRationale[key] : '';
}

function lightboxPlaceholderHtml(code) {
  const label = lightboxText('noImage', { code });
  return `<div class="pl-placeholder" role="img" aria-label="${escHtml(label)}">${ICON_IMAGE}<span>${escHtml(label)}</span></div>`;
}

// The dialog's content for one point. `index` picks which image is shown.
function lightboxHtml({ code, info = {}, rationale = '', images = [], index = 0 }) {
  const usable = images.filter((img) => safeUrl(img.url));
  const current = usable[Math.min(Math.max(index, 0), Math.max(usable.length - 1, 0))];
  const themeClass = channelThemeClass(info.channel_en || '');
  const title = info.name || code;
  const subtitle = [info.name_en, info.channel].filter(Boolean).join(' · ');
  const hanziHtml = info.name_cn ? `<span class="pl-hanzi" lang="zh">${escHtml(info.name_cn)}</span>` : '';

  let figureHtml = lightboxPlaceholderHtml(code);
  let creditHtml = '';
  if (current) {
    const alt = [code, title, current.kind].filter(Boolean).join(' ');
    figureHtml = `<img class="pl-image" src="${escHtml(safeUrl(current.url))}" alt="${escHtml(alt)}"
        width="${escHtml(Number(current.width) || '')}" height="${escHtml(Number(current.height) || '')}">`;
    const licenceUrl = safeUrl(current.licence_url);
    const sourceUrl = safeUrl(current.source_url);
    const licenceHtml = licenceUrl
      ? `<a href="${escHtml(licenceUrl)}" target="_blank" rel="license noopener noreferrer">${escHtml(current.licence)}</a>`
      : escHtml(current.licence);
    const sourceHtml = sourceUrl
      ? ` · <a href="${escHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${escHtml(lightboxText('source'))}</a>`
      : '';
    creditHtml = `<figcaption class="pl-credit">${escHtml(current.credit)} · ${escHtml(lightboxText('licence'))}: ${licenceHtml}${sourceHtml}</figcaption>`;
  }

  const thumbsHtml = usable.length > 1
    ? `<div class="pl-thumbs">${usable.map((img, i) => `<button type="button" class="pl-thumb" data-action="lightbox-show-image" data-index="${escHtml(i)}" aria-pressed="${img === current ? 'true' : 'false'}" aria-label="${escHtml(lightboxText('image', { n: i + 1, total: usable.length }))}"><img src="${escHtml(safeUrl(img.thumb_url) || safeUrl(img.url))}" alt=""></button>`).join('')}</div>`
    : '';
  const zoomHtml = current
    ? `<button type="button" class="pl-zoom" data-action="lightbox-zoom" aria-pressed="false">${escHtml(lightboxText('zoomIn'))}</button>`
    : '';

  const detail = [];
  if (info.name) {
    if (hasPregnancyCaution(code)) {
      detail.push(`<p class="pc-caution">${ICON_CAUTION}<span>${escHtml(pointText('pregnancy'))}</span></p>`);
    }
    detail.push(`<h3 class="pl-label">${escHtml(lightboxText('location'))}</h3><p class="pl-text">${escHtml(info.location)}</p>`);
    detail.push(`<h3 class="pl-label">${escHtml(lightboxText('actions'))}</h3><p class="pl-text">${escHtml(info.actions)}</p>`);
  } else {
    detail.push(`<p class="pl-text">${escHtml(lightboxText('noReference', { code }))}</p>`);
  }
  if (rationale) {
    detail.push(`<section class="pc-why"><h4 class="pc-why-label">${escHtml(lightboxText('forPatient'))}</h4><p>${escHtml(rationale)}</p></section>`);
  }
  const detailHtml = detail.join('');

  return `<div class="pl-card ${themeClass}">
    <header class="pl-head">
      <span class="pc-code">${escHtml(code)}</span>
      <div class="pl-names">
        <h2 class="pl-title" id="pl-title">${escHtml(title)} ${hanziHtml}</h2>
        <p class="pl-sub">${escHtml(subtitle)}</p>
      </div>
      <button type="button" class="pl-close" data-action="close-point-lightbox" aria-label="${escHtml(lightboxText('close'))}">${ICON_CLOSE}</button>
    </header>
    <div class="pl-body">
      <figure class="pl-figure">
        <div class="pl-frame">${figureHtml}</div>
        ${creditHtml}
      </figure>
      <div class="pl-tools">${thumbsHtml}${zoomHtml}</div>
      <div class="pl-detail">${detailHtml}</div>
    </div>
  </div>`;
}

let _lightboxCode = '';

function renderLightbox(code, index) {
  const dialog = document.getElementById('point-lightbox');
  const info = getPointInfo(code);
  dialog.innerHTML = lightboxHtml({
    code, info, rationale: rationaleFor(code), images: info.images || [], index,
  });
  // A link that no longer loads becomes the placeholder, not a broken image.
  const img = dialog.querySelector('.pl-image');
  if (img) {
    img.addEventListener('error', () => {
      img.closest('.pl-frame').innerHTML = lightboxPlaceholderHtml(code);
      dialog.querySelector('.pl-zoom')?.remove();
      dialog.querySelector('.pl-credit')?.remove();
    }, { once: true });
  }
}

function openPointLightbox(code) {
  const dialog = document.getElementById('point-lightbox');
  if (!dialog) return;
  _lightboxCode = normPointCode(code);
  renderLightbox(_lightboxCode, 0);
  if (!dialog.open) dialog.showModal();
  dialog.querySelector('.pl-close').focus();
}

function closePointLightbox() {
  const dialog = document.getElementById('point-lightbox');
  if (dialog && dialog.open) dialog.close();
}

function showLightboxImage(index) {
  if (!_lightboxCode) return;
  const i = Number(index) || 0;
  renderLightbox(_lightboxCode, i);
  document.querySelectorAll('#point-lightbox .pl-thumb')[i]?.focus();
}

function toggleLightboxZoom(button) {
  const frame = document.querySelector('#point-lightbox .pl-frame');
  if (!frame) return;
  const zoomed = frame.classList.toggle('is-zoomed');
  button.setAttribute('aria-pressed', zoomed ? 'true' : 'false');
  button.textContent = lightboxText(zoomed ? 'zoomOut' : 'zoomIn');
}

document.addEventListener('DOMContentLoaded', () => {
  const dialog = document.getElementById('point-lightbox');
  if (!dialog) return;
  // A click on the backdrop (the dialog element itself, outside its card) closes it.
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) dialog.close();
  });
  dialog.addEventListener('close', () => {
    _lightboxCode = '';
    dialog.innerHTML = '';
  });
});
