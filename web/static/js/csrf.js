/* CSRF (Phase 9.3): echo the double-submit token on every state-changing same-origin request.
 *
 * The server sets a readable `zf_csrf` cookie; an unsafe request has to send the same value back.
 * Rather than touch three dozen `fetch(...)` call sites, this wraps `fetch` once (loaded on every
 * page) so the `X-CSRF-Token` header rides along automatically, and fills the hidden field of any
 * native <form> so a real form post carries the token too. A cross-site page can neither read our
 * cookie nor run this script, so it cannot produce a matching token. */
(function () {
  'use strict';

  var COOKIE = 'zf_csrf';
  var HEADER = 'X-CSRF-Token';
  var FIELD = 'csrf_token';
  var UNSAFE = { POST: 1, PUT: 1, PATCH: 1, DELETE: 1 };

  function token() {
    var match = document.cookie.match(/(?:^|;\s*)zf_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  // Only same-origin requests carry the ambient cookie, so only they need — or should leak — the
  // token. A relative URL, or an absolute one to our own origin, counts as same-origin.
  function sameOrigin(url) {
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (e) {
      return true; // a malformed/relative URL is same-origin by default
    }
  }

  var nativeFetch = window.fetch ? window.fetch.bind(window) : null;
  if (nativeFetch) {
    window.fetch = function (input, init) {
      init = init || {};
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var method = (init.method || (typeof input === 'object' && input.method) || 'GET').toUpperCase();
      if (UNSAFE[method] && sameOrigin(url)) {
        var tok = token();
        if (tok) {
          var headers = new Headers(init.headers || (typeof input === 'object' && input.headers) || {});
          if (!headers.has(HEADER)) headers.set(HEADER, tok);
          init = Object.assign({}, init, { headers: headers });
        }
      }
      return nativeFetch(input, init);
    };
  }

  // Native <form method=post>: put the token in a hidden field so the form post carries it. Done on
  // load and again just before submit, in case the form was added or the cookie set late.
  function fillForms(root) {
    var forms = (root || document).querySelectorAll('form[method]');
    for (var i = 0; i < forms.length; i++) {
      var form = forms[i];
      if ((form.getAttribute('method') || '').toUpperCase() !== 'POST') continue;
      if (form.action && !sameOrigin(form.action)) continue;
      var input = form.querySelector('input[name="' + FIELD + '"]');
      if (!input) {
        input = document.createElement('input');
        input.type = 'hidden';
        input.name = FIELD;
        form.appendChild(input);
      }
      input.value = token();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { fillForms(); });
  } else {
    fillForms();
  }
  document.addEventListener('submit', function (e) { fillForms(e.target); }, true);
})();
