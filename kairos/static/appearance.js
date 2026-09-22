/* Brand preferences are browser-local; they never enter bot settings. */
(() => {
  'use strict';
  const key = 'kairos:appearance';
  const normalize = value => ({
    theme: ['dark', 'light'].includes(value?.theme) ? value.theme : 'dark',
    accent: ['violet', 'green', 'red'].includes(value?.accent) ? value.accent : 'violet',
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
      toggle.setAttribute('aria-label', label); toggle.title = label;
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
    document.getElementById('appearance-theme').addEventListener('click', () => save({theme: preference.theme === 'dark' ? 'light' : 'dark'}));
    document.getElementById('appearance-accent').addEventListener('change', event => save({accent: event.target.value}));
  });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    try { preference = normalize(event.newValue ? JSON.parse(event.newValue) : null); }
    catch { preference = normalize(null); }
    apply();
  });
})();
