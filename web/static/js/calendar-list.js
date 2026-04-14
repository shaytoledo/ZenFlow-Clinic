/* ZenFlow — Calendar list, visibility toggles, and rename */

const _hiddenCals = new Set();

const _calRenames = JSON.parse(localStorage.getItem('zf_cal_renames') || '{}');

function _saveCalRenames() {
  localStorage.setItem('zf_cal_renames', JSON.stringify(_calRenames));
}

function _showCalCtxMenu(e, calId, originalName) {
  e.preventDefault();
  e.stopPropagation();
  const old = document.getElementById('zf-cal-ctx');
  if (old) old.remove();

  const menu = document.createElement('div');
  menu.id = 'zf-cal-ctx';
  menu.style.cssText = `
    position:fixed;left:${e.clientX}px;top:${e.clientY}px;
    background:white;border:1px solid #E5E7EB;border-radius:8px;
    box-shadow:0 4px 16px rgba(0,0,0,0.12);z-index:9999;
    min-width:148px;padding:4px 0;font-family:inherit;`;

  const rename = document.createElement('button');
  rename.textContent = '✏️  Rename';
  rename.style.cssText = 'display:block;width:100%;text-align:left;padding:8px 14px;border:none;background:none;cursor:pointer;font-size:13px;color:#374151;';
  rename.onmouseenter = () => { rename.style.background = '#F3F4F6'; };
  rename.onmouseleave = () => { rename.style.background = ''; };
  rename.onclick = () => {
    menu.remove();
    const current = _calRenames[calId] || originalName;
    const newName = prompt(`Rename "${originalName}":`, current);
    if (newName && newName.trim() && newName.trim() !== originalName) {
      _calRenames[calId] = newName.trim();
    } else if (newName !== null && newName.trim() === originalName) {
      delete _calRenames[calId];
    }
    _saveCalRenames();
    loadCalendarList();
  };
  menu.appendChild(rename);

  const reset = document.createElement('button');
  reset.textContent = '↩  Reset name';
  reset.style.cssText = rename.style.cssText + 'color:#9CA3AF;';
  reset.onmouseenter = () => { reset.style.background = '#F3F4F6'; };
  reset.onmouseleave = () => { reset.style.background = ''; };
  reset.onclick = () => {
    menu.remove();
    delete _calRenames[calId];
    _saveCalRenames();
    loadCalendarList();
  };
  if (_calRenames[calId]) menu.appendChild(reset);

  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }), 0);
}

function _showLocalCalendarBadge() {
  $('cal-items').innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;padding:4px 0 6px;">
      <span style="width:10px;height:10px;background:#27ae60;border-radius:50%;flex-shrink:0;"></span>
      <span style="font-size:12px;color:#374151;font-weight:500;">Local Calendar</span>
    </div>
    <p style="font-size:11px;color:#9CA3AF;line-height:1.5;margin-top:2px;">
      Slots saved here are visible to patients.<br>
      <a href="/settings" style="color:#0D9488;">Connect Google</a> to sync.
    </p>`;
}

async function loadCalendarList() {
  try {
    const r = await fetch('/api/calendars');
    if (!r.ok) { _showLocalCalendarBadge(); return; }
    const data = await r.json();
    if (!data.length) { _showLocalCalendarBadge(); return; }
    const el = $('cal-items');
    el.innerHTML = '';
    data.forEach(c => {
      const row = document.createElement('label');
      row.className = 'cal-item';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      cb.className = 'cal-checkbox';
      cb.style.accentColor = c.color;
      cb.addEventListener('change', () => {
        if (cb.checked) {
          _hiddenCals.delete(c.id);
          row.classList.remove('cal-hidden');
          mainCal.refetchEvents();
        } else {
          _hiddenCals.add(c.id);
          row.classList.add('cal-hidden');
          mainCal.getEvents().forEach(ev => {
            if (ev.extendedProps?.calendarId === c.id) ev.remove();
          });
        }
      });

      const name = document.createElement('span');
      name.className = 'cal-name';
      name.textContent = _calRenames[c.id] || c.name;

      row.appendChild(cb);
      row.appendChild(name);
      row.addEventListener('contextmenu', e => _showCalCtxMenu(e, c.id, c.name));
      el.appendChild(row);
    });
  } catch (_) { _showLocalCalendarBadge(); }
}
