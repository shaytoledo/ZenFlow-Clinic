// App shell (Phase 4.2c): below 900px the sidebar is a drawer opened from the topbar menu button.
// Loaded by base.html on every page.
(() => {
  const app = document.querySelector('.zf-app');
  const button = document.getElementById('zf-menu-btn');
  const sidebar = document.getElementById('zf-sidebar');
  const scrim = document.getElementById('zf-scrim');
  if (!app || !button || !sidebar || !scrim) return;

  const isOpen = () => app.classList.contains('nav-open');
  const setOpen = (open) => {
    app.classList.toggle('nav-open', open);
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      const first = sidebar.querySelector('a, button');
      if (first) first.focus();
    }
  };

  button.addEventListener('click', () => setOpen(!isOpen()));
  scrim.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen()) {
      setOpen(false);
      button.focus();
    }
  });
  // Following a link closes the drawer (the page may be served from the back/forward cache).
  sidebar.addEventListener('click', (e) => {
    if (e.target.closest('a')) setOpen(false);
  });
  // A wide window never keeps the drawer state.
  window.matchMedia('(min-width: 901px)').addEventListener('change', (e) => {
    if (e.matches) setOpen(false);
  });
})();
