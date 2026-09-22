/* Kairos UI starter. No network requests, credentials, exchange access or trading logic. */
'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let toastTimer;
const storage = { get(key) { try { return localStorage.getItem(key); } catch { return null; } }, set(key, value) { try { localStorage.setItem(key, value); } catch { /* File previews may disable storage. */ } } };
let accentPreference = storage.get('kairos.brand.accent') || 'violet';
function applyAccent(value) {
  accentPreference = ['violet', 'green', 'red'].includes(value) ? value : 'violet';
  document.documentElement.dataset.accent = accentPreference;
  $('#accent-select').value = accentPreference;
  storage.set('kairos.brand.accent', accentPreference);
}
applyAccent(accentPreference);
const systemTheme = window.matchMedia('(prefers-color-scheme: light)');
let themePreference = storage.get('kairos.brand.theme') || 'dark';
function applyTheme(preference) {
  themePreference = ['light', 'dark', 'system'].includes(preference) ? preference : 'dark';
  const theme = themePreference === 'system' ? (systemTheme.matches ? 'light' : 'dark') : themePreference;
  document.documentElement.dataset.theme = theme;
  $('#theme-toggle').setAttribute('aria-label', `Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`);
  $('meta[name="theme-color"]').content = theme === 'dark' ? '#181C20' : '#F6F7F8';
  $('#theme-select').value = themePreference;
  $('.nav-stack a[href$=".pdf"]').href = `../guide/kairos-brand-guide-${theme}.pdf`;
  storage.set('kairos.brand.theme', themePreference);
}
applyTheme(themePreference);
systemTheme.addEventListener('change', () => { if (themePreference === 'system') applyTheme('system'); });
$('#theme-toggle').addEventListener('click', () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
function toast(message) { clearTimeout(toastTimer); const box = $('#toast'); box.textContent = message; box.hidden = false; toastTimer = setTimeout(() => { box.hidden = true; }, 4500); }
$$('[data-toast]').forEach(button => button.addEventListener('click', () => toast(button.dataset.toast)));
const routeNames = { overview: 'Overview', activity: 'Activity', components: 'UI library' };
function route(focus = false) {
  const requested = location.hash.slice(1); const name = Object.hasOwn(routeNames, requested) ? requested : 'overview';
  $$('.page').forEach(page => { page.hidden = page.id !== name; });
  $$('[data-route]').forEach(link => { if (link.dataset.route === name) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
  $('#breadcrumb-current').textContent = routeNames[name]; document.title = `Kairos — ${routeNames[name]}`;
  if (focus) { $('#main').focus({ preventScroll: true }); window.scrollTo(0, 0); }
}
window.addEventListener('hashchange', () => route(true)); route();
const sampleLog = [
  { title: 'Assessment received', detail: 'BTC / USD · Hold · confidence 0.68', time: '09:42' },
  { title: 'Execution threshold checked', detail: 'Sample signal stayed below the configured threshold.', time: '09:42' },
  { title: 'Paper portfolio loaded', detail: 'Three illustrative spot positions are available.', time: '09:41' },
  { title: 'Workspace ready', detail: 'Demo mode. No exchange is connected.', time: '09:40' }
];
let log = [...sampleLog];
function renderLog() {
  const list = $('#activity-list'); list.replaceChildren();
  log.forEach(item => { const li = document.createElement('li'); const symbol = document.createElement('span'); symbol.className = 'activity-symbol'; symbol.setAttribute('aria-hidden', 'true'); symbol.textContent = '↗'; const content = document.createElement('div'); const title = document.createElement('strong'); title.textContent = item.title; const detail = document.createElement('p'); detail.textContent = item.detail; const time = document.createElement('time'); time.textContent = item.time; content.append(title, detail); li.append(symbol, content, time); list.append(li); });
  $('#activity-empty').hidden = log.length > 0; $('#activity-count').textContent = String(log.length);
}
function addLog(title, detail) { log.unshift({ title, detail, time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }); renderLog(); }
renderLog();
$('#clear-activity').addEventListener('click', () => { log = []; renderLog(); toast('The session activity list is clear.'); });
$('#notifications').addEventListener('click', () => toast(log.length ? `${log.length} demo activity entries. Open Activity to review them.` : 'No new session activity.'));
let running = false;
$('#run-toggle').addEventListener('click', () => { running = !running; $('#run-toggle span').textContent = running ? 'Pause demo' : 'Start demo'; $('#run-toggle use').setAttribute('href', running ? '#i-pause' : '#i-play'); $('#run-state').textContent = running ? 'Demo running' : 'Paused'; addLog(running ? 'Demo started' : 'Demo paused', 'Presentation state only; no orders or model calls were made.'); toast(running ? 'Demo running. Sample values remain fixed.' : 'Demo paused.'); });
let settings = { name: 'Personal workspace', threshold: 75, allocation: 12500 };
$$('[data-open-settings]').forEach(button => button.addEventListener('click', () => { $('#workspace-name').value = settings.name; $('#settings-threshold').value = settings.threshold; $('#settings-threshold-value').value = (settings.threshold / 100).toFixed(2); $('#paper-allocation').value = settings.allocation; $('#theme-select').value = themePreference; $('#accent-select').value = accentPreference; $('#settings-dialog').showModal(); }));
$('#settings-threshold').addEventListener('input', event => { $('#settings-threshold-value').value = (Number(event.target.value) / 100).toFixed(2); });
$('#settings-form').addEventListener('submit', event => { event.preventDefault(); if (!event.currentTarget.reportValidity()) return; const name = $('#workspace-name').value.trim(); if (!name) { $('#workspace-name').setCustomValidity('Enter a workspace name.'); $('#workspace-name').reportValidity(); return; } settings = { name, threshold: Number($('#settings-threshold').value), allocation: Number($('#paper-allocation').value) }; $('.profile small').textContent = name; $('.workspace-label').childNodes[1].textContent = ` ${name} `; $('#threshold-marker').style.left = `${settings.threshold}%`; $('#threshold-copy').textContent = (settings.threshold / 100).toFixed(2); $('.confidence-track').setAttribute('aria-label', `Sample confidence 0.68, threshold ${(settings.threshold / 100).toFixed(2)}`); $('.assessment-copy').textContent = settings.threshold > 68 ? 'The sample signal does not clear the execution threshold. Wait for a stronger setup.' : 'The sample confidence clears the threshold, but the suggested action is hold. No order is authorized.'; applyTheme($('#theme-select').value); applyAccent($('#accent-select').value); $('#settings-dialog').close(); addLog('Preferences saved', `Workspace: ${name}. Sample portfolio values are unchanged.`); toast('Preferences saved for this session.'); });
$('#workspace-name').addEventListener('input', event => event.target.setCustomValidity(''));
$$('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
$$('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target !== dialog) return; const b = dialog.getBoundingClientRect(); if (event.clientX < b.left || event.clientX > b.right || event.clientY < b.top || event.clientY > b.bottom) dialog.close(); }));
['#review-signal', '#component-dialog'].forEach(selector => $(selector).addEventListener('click', () => $('#review-dialog').showModal()));
$('#acknowledge-review').addEventListener('click', () => { $('#review-dialog').close(); addLog('Assessment reviewed', 'BTC / USD · Sample hold assessment marked as reviewed.'); toast('Assessment marked as reviewed.'); });
$('#demo-danger').addEventListener('click', () => $('#confirm-dialog').showModal());
$('#confirm-example').addEventListener('click', () => { $('#confirm-dialog').close(); toast('Example cleared. Workspace data is unchanged.'); });
const histories = {
 '1D': [194,191,200,180,186,169,178,175,160,162,174,153,155,146,160,145,136,145,130,138,133,147,122,123,133,118,103,111,101,108,102,79,88,77,88,64,81,79,95,90,103,87,95,85,93],
 '1H': [115,117,111,118,109,108,105,111,120,117,110,115,107,110,98,104,110,106,99,95,101,105,97,91,98,89,96,93],
 '4H': [170,160,168,153,149,158,145,149,152,131,140,128,134,121,111,122,118,106,115,99,101,111,95,93],
 '1W': [202,195,200,176,168,180,165,177,158,161,145,166,151,135,144,117,129,108,132,113,121,98,115,84,101,93]
};
function drawChart(period) { const series = histories[period]; const points = series.map((y,i) => [20 + i * 660 / (series.length - 1), y]); const d = points.map(([x,y],i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y}`).join(' '); $('#chart-line').setAttribute('d', d); $('#chart-area').setAttribute('d', `${d} L680 223 L20 223 Z`); $('#chart-point').setAttribute('cy', series.at(-1)); $('#chart-title').textContent = `Bitcoin illustrative price history, ${period}`; $('#chart-period-label').textContent = { '1H':'1-hour view','4H':'4-hour view','1D':'24-hour view','1W':'7-day view' }[period]; const labels = { '1H':['09:00','09:15','09:30','09:45','10:00'],'4H':['06:00','07:00','08:00','09:00','10:00'],'1D':['00:00','06:00','12:00','18:00','23:59'],'1W':['Mon','Tue','Thu','Sat','Sun'] }; $$('.chart-grid text').slice(4).forEach((node,i) => { node.textContent = labels[period][i]; }); $$('[data-period]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.period === period))); }
$$('[data-period]').forEach(button => button.addEventListener('click', () => drawChart(button.dataset.period))); drawChart('1D');
function filterPositions() { const query = $('#position-search').value.trim().toLowerCase(); const rows = $$('#positions-body tr'); let count = 0; rows.forEach(row => { row.hidden = !row.dataset.search.includes(query); if (!row.hidden) count++; }); $('#positions-empty').hidden = count > 0; $('#positions-count').textContent = `${count} position${count === 1 ? '' : 's'}`; }
$('#position-search').addEventListener('input', filterPositions); $('#clear-search').addEventListener('click', () => { $('#position-search').value = ''; filterPositions(); $('#position-search').focus(); });
$('#component-form').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return; const data = new FormData(form); $('#form-result').textContent = `Saved example: ${String(data.get('name')).trim()} · ${data.get('market')}`; toast('Example form validated.'); });
$('#component-range').addEventListener('input', event => { $('#range-output').value = `${event.target.value}%`; });
$('#component-switch').addEventListener('change', event => toast(`Demo notifications ${event.target.checked ? 'enabled' : 'disabled'}.`));
$$('[name=density]').forEach(input => input.addEventListener('change', () => { document.body.classList.toggle('compact', input.value === 'compact'); toast(`${input.value === 'compact' ? 'Compact' : 'Comfortable'} table density selected.`); }));
const tabs = $$('[role=tab]');
function activateTab(tab, focus = false) { tabs.forEach(button => { const selected = button === tab; button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1; document.getElementById(button.getAttribute('aria-controls')).hidden = !selected; }); if (focus) tab.focus(); }
tabs.forEach((tab,index) => { tab.addEventListener('click', () => activateTab(tab)); tab.addEventListener('keydown', event => { let next; if (event.key === 'ArrowRight') next = (index+1)%tabs.length; if (event.key === 'ArrowLeft') next = (index+tabs.length-1)%tabs.length; if (event.key === 'Home') next = 0; if (event.key === 'End') next = tabs.length-1; if (next !== undefined) { event.preventDefault(); activateTab(tabs[next], true); } }); });
$('#loading-demo').addEventListener('click', event => { const button = event.currentTarget; button.disabled = true; button.setAttribute('aria-busy','true'); button.textContent = 'Working…'; setTimeout(() => { button.disabled = false; button.removeAttribute('aria-busy'); button.textContent = 'Loading example'; toast('Loading example completed.'); },1000); });
$$('[data-color]').forEach(button => button.addEventListener('click', async () => { try { await navigator.clipboard.writeText(button.dataset.color); toast(`Copied ${button.dataset.color}`); } catch { toast(`Color value: ${button.dataset.color}`); } }));
