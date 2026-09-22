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
    for (const name of ['theme', 'accent']) {
      const input = document.getElementById(`appearance-${name}`);
      if (input) input.value = preference[name];
    }
  }
  apply();
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    for (const name of ['theme', 'accent']) {
      document.getElementById(`appearance-${name}`).addEventListener('change', event => {
        preference = normalize({...preference, [name]: event.target.value}); apply();
        const status = document.getElementById('appearance-status');
        try { localStorage.setItem(key, JSON.stringify(preference)); status.textContent = 'Appearance saved in this browser; trading settings unchanged.'; }
        catch { status.textContent = 'Browser storage unavailable; appearance lasts for this page session.'; }
      });
    }
  });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    try { preference = normalize(event.newValue ? JSON.parse(event.newValue) : null); }
    catch { preference = normalize(null); }
    apply();
  });
})();
