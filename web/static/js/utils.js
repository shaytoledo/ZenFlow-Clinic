/* ZenFlow — Shared utilities */

const $ = id => document.getElementById(id);

let _toastTimer;

function showToast(msg, icon = '✓') {
  clearTimeout(_toastTimer);
  $('toast-icon').textContent = icon;
  $('toast-msg').textContent  = msg;
  $('toast').classList.add('show');
  _toastTimer = setTimeout(() => $('toast').classList.remove('show'), 3000);
}

function fmt(d) {
  return new Date(d).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}
