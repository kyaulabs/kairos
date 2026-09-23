/* Brand preferences are browser-local; they never enter bot settings. */
(() => {
  'use strict';
  const key = 'kairos:appearance';
  const normalize = value => ({
    theme: ['dark', 'light'].includes(value?.theme) ? value.theme : 'dark',
    accent: ['violet', 'green', 'red', 'blue'].includes(value?.accent) ? value.accent : 'violet',
  });
  let preference;
  try { preference = normalize(JSON.parse(localStorage.getItem(key))); }
  catch { preference = normalize(null); }
  const root = document.documentElement;
  function apply() {
    root.dataset.theme = preference.theme; root.dataset.accent = preference.accent;
    const logo = document.getElementById('brand-wordmark');
    const icon = document.getElementById('brand-icon');
    if (logo) logo.src = `/static/brand/kairos-wordmark-on-${preference.theme}.svg`;
    if (icon) icon.src = `/static/brand/kai-icon-on-${preference.theme}.svg`;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = preference.theme === 'light' ? '#F6F7F8' : '#181C20';
    const toggle = document.getElementById('appearance-theme');
    if (toggle) {
      const label = preference.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
      toggle.setAttribute('aria-label', label); toggle.title = `${label} · Right-click for color schemes`;
    }
    const sun = document.getElementById('appearance-sun'), moon = document.getElementById('appearance-moon');
    if (sun) sun.hidden = preference.theme !== 'dark';
    if (moon) moon.hidden = preference.theme !== 'light';
    const accent = document.getElementById('appearance-accent');
    if (accent) accent.value = preference.accent;
  }
  apply();
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    function save(change) {
      preference = normalize({...preference, ...change}); apply();
      const status = document.getElementById('appearance-status');
      try { localStorage.setItem(key, JSON.stringify(preference)); status.textContent = 'Appearance saved in this browser; trading settings unchanged.'; }
      catch { status.textContent = 'Browser storage unavailable; appearance lasts for this page session.'; }
    }
    const toggle = document.getElementById('appearance-theme');
    const menu = document.getElementById('appearance-menu');
    const accent = document.getElementById('appearance-accent');
    let holdTimer, touchStart, suppressClick = false;
    const clearHold = () => { clearTimeout(holdTimer); holdTimer = null; touchStart = null; };
    function positionMenu() {
      const rect = toggle.getBoundingClientRect(), box = menu.getBoundingClientRect();
      menu.style.left = `${Math.max(8, Math.min(innerWidth - box.width - 8, rect.right - box.width))}px`;
      menu.style.top = `${Math.max(8, Math.min(innerHeight - box.height - 8, rect.bottom + 6))}px`;
    }
    function openMenu() {
      clearHold(); toggle.focus({preventScroll: true});
      if (!menu.matches(':popover-open')) menu.showPopover();
      positionMenu(); toggle.setAttribute('aria-expanded', 'true'); accent.focus();
    }
    toggle.addEventListener('click', event => {
      event?.preventDefault(); // Left click toggles brightness, not the native popover target.
      if (suppressClick && event?.detail > 0) { suppressClick = false; return; }
      save({theme: preference.theme === 'dark' ? 'light' : 'dark'});
    });
    toggle.addEventListener('contextmenu', event => { event.preventDefault(); suppressClick = suppressClick || !!touchStart; openMenu(); });
    toggle.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) { event.preventDefault(); openMenu(); }
    });
    toggle.addEventListener('pointerdown', event => {
      clearHold(); suppressClick = false;
      if (event.pointerType !== 'touch') return;
      touchStart = {x: event.clientX, y: event.clientY};
      holdTimer = setTimeout(() => { suppressClick = true; openMenu(); }, 550);
    });
    toggle.addEventListener('pointermove', event => {
      if (touchStart && Math.hypot(event.clientX - touchStart.x, event.clientY - touchStart.y) > 8) clearHold();
    });
    for (const name of ['pointerup', 'pointercancel', 'pointerleave']) toggle.addEventListener(name, clearHold);
    window.addEventListener('blur', clearHold);
    window.addEventListener('resize', () => { if (menu.matches(':popover-open')) positionMenu(); });
    menu.addEventListener('toggle', event => { toggle.setAttribute('aria-expanded', String(event.newState === 'open')); });
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); menu.hidePopover(); toggle.focus({preventScroll: true}); }
    });
    accent.addEventListener('change', event => {
      save({accent: event.target.value}); menu.hidePopover(); toggle.focus({preventScroll: true});
    });
  });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    try { preference = normalize(event.newValue ? JSON.parse(event.newValue) : null); }
    catch { preference = normalize(null); }
    apply();
  });
})();
