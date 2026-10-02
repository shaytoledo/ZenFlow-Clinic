// actions.js — delegated event handling for every dashboard page (SF-016).
//
// The Content-Security-Policy (web/csp.py) can only be enforced once no markup carries inline
// `on*="…"` handlers: those are inline script, which a nonce cannot cover. So the pages declare what
// an element does with data attributes, and each page registers the functions by name:
//
//   <button data-click="refresh-messages">          → click
//   <input  data-on-input="filter-patients">         → input
//   <select data-on-change="new-appt-date">          → change
//   <input  data-on-enter="send-reply">              → keydown Enter
//   <input  data-on-blur="hide-name-suggestions">    → focus leaving the element
//   <a      data-confirm="Disconnect Google?">       → click asks first; Cancel stops it
//
//   ZF.actions.register({ 'refresh-messages': (el, ev) => refreshMessages() });
//
// A handler receives the element that declared the action and the event; arguments travel in its
// other data-* attributes (read them from `el.dataset`, never build them into code). Only registered
// names can run, so injected markup cannot call an arbitrary global. The treatment page keeps its
// own `data-action` dispatcher (static/js/treatment/events.js); the two attribute sets never clash.
(function () {
  'use strict';
  const handlers = Object.create(null);
  const ZF = (window.ZF = window.ZF || {});

  ZF.actions = {
    register(map) {
      for (const [name, fn] of Object.entries(map)) handlers[name] = fn;
    },
  };

  // Escape a value for HTML text or a quoted attribute — anything from the API (patient names come
  // from Telegram profiles) goes through this before it reaches innerHTML.
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' };
  ZF.esc = (value) => String(value == null ? '' : value).replace(/[&<>"'`]/g, (c) => ESC[c]);

  function closest(ev, attr) {
    return ev.target instanceof Element ? ev.target.closest('[' + attr + ']') : null;
  }

  function run(attr, ev) {
    const el = closest(ev, attr);
    if (!el) return;
    const name = el.getAttribute(attr);
    const fn = handlers[name];
    if (fn) fn(el, ev);
    else console.warn('ZF: no handler registered for', attr + '="' + name + '"');
  }

  document.addEventListener('click', (ev) => {
    const asking = closest(ev, 'data-confirm');
    if (asking && !window.confirm(asking.getAttribute('data-confirm'))) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      return;
    }
    run('data-click', ev);
  });
  document.addEventListener('input', (ev) => run('data-on-input', ev));
  document.addEventListener('change', (ev) => run('data-on-change', ev));
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') run('data-on-enter', ev);
  });
  document.addEventListener('focusout', (ev) => run('data-on-blur', ev));
})();
