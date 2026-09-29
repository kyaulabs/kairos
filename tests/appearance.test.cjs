const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

function fixture(saved, blocked = false) {
  const values = new Map([['kairos:appearance', saved], ['kairos:favorites', '["XXBTZUSD"]']]);
  const nodes = new Map(), listeners = {}, timers = new Map();
  const root = {dataset: {}};
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {listeners: {}, attributes: {}, style: {}, open: false,
      setAttribute(name, value) { this.attributes[name] = value; }, addEventListener(name, callback) { this.listeners[name] = callback; },
      focus() { this.focused = true; }, matches() { return this.open; },
      showPopover() { this.open = true; this.listeners.toggle?.({newState: 'open'}); },
      hidePopover() { this.open = false; this.listeners.toggle?.({newState: 'closed'}); },
      getBoundingClientRect() { return {right: 350, bottom: 40, width: 180, height: 80}; },
    });
    return nodes.get(id);
  };
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/appearance.js'), 'utf8'), {
    document: {documentElement: root, getElementById: node, querySelector: () => node('meta'), addEventListener: (event, callback) => { listeners[event] = callback; }},
    window: {addEventListener: (event, callback) => { listeners[event] = callback; }},
    innerWidth: 360, innerHeight: 640,
    setTimeout: (callback, delay) => { timers.set(1, {callback, delay}); return 1; }, clearTimeout: id => timers.delete(id),
    localStorage: {getItem: key => { if (blocked) throw new Error('Unavailable'); return values.get(key); }, setItem: (key, value) => { if (blocked) throw new Error('Unavailable'); values.set(key, value); }},
  });
  listeners.DOMContentLoaded();
  return {root, node, values, listeners, timers};
}

test('brand mode and accent persist independently without touching favorites or bot settings', () => {
  const {root, node, values} = fixture('{"theme":"light","accent":"red"}');
  assert.equal(root.dataset.theme, 'light');
  assert.equal(root.dataset.accent, 'red');
  assert.match(node('brand-wordmark').src, /on-light.svg$/);
  node('appearance-accent').listeners.change({target: {value: 'green'}});
  assert.deepEqual(JSON.parse(values.get('kairos:appearance')), {theme: 'light', accent: 'green'});
  assert.equal(values.get('kairos:favorites'), '["XXBTZUSD"]');
  assert.equal(values.size, 2);
});

test('sun/moon button toggles the theme with matching accessible labels and keeps the accent', () => {
  const {root,node,values}=fixture('{"theme":"dark","accent":"red"}');
  assert.equal(node('appearance-sun').hidden,false);
  assert.equal(node('appearance-moon').hidden,true);
  assert.equal(node('appearance-theme').attributes['aria-label'],'Switch to light theme');
  node('appearance-theme').listeners.click();
  assert.equal(root.dataset.theme,'light');
  assert.match(node('appearance-theme').title,/Switch to dark theme.*Right-click/);
  assert.equal(node('appearance-sun').hidden,true);
  assert.equal(node('appearance-moon').hidden,false);
  assert.deepEqual(JSON.parse(values.get('kairos:appearance')),{theme:'light',accent:'red'});
  node('appearance-theme').listeners.click();
  assert.equal(root.dataset.theme,'dark');
  assert.equal(root.dataset.accent,'red');
  assert.equal(node('appearance-theme').attributes['aria-label'],'Switch to light theme');
});

test('corrupt and blocked preference storage cannot prevent branded initialization', () => {
  for (const saved of ['malformed', '{"theme":"external-url","accent":"invalid"}']) {
    const {root} = fixture(saved);
    assert.equal(root.dataset.theme, 'dark');
    assert.equal(root.dataset.accent, 'violet');
  }
  const {root, node} = fixture(null, true);
  node('appearance-theme').listeners.click();
  assert.equal(root.dataset.theme, 'light');
  assert.match(node('appearance-status').textContent, /page session/);
});

test('cross-tab preference changes update artwork and controls, not unrelated storage', () => {
  const {root, node, listeners} = fixture(null);
  listeners.storage({key: 'kairos:favorites', newValue: '["ETH"]'});
  assert.equal(root.dataset.theme, 'dark');
  listeners.storage({key: 'kairos:appearance', newValue: '{"theme":"light","accent":"green"}'});
  assert.equal(node('appearance-theme').attributes['aria-label'], 'Switch to dark theme');
  assert.equal(node('appearance-sun').hidden, true);
  assert.equal(node('appearance-moon').hidden, false);
  assert.equal(root.dataset.accent, 'green');
  assert.match(node('brand-icon').src, /on-light.svg$/);
  listeners.storage({key: null, newValue: null});
  assert.equal(root.dataset.theme, 'dark');
});

test('right-click and keyboard expose the hidden accent chooser without changing theme', () => {
  const {root, node, values} = fixture('{"theme":"dark","accent":"violet"}');
  const toggle = node('appearance-theme'), menu = node('appearance-menu');
  let prevented = 0;
  toggle.listeners.contextmenu({preventDefault() { prevented++; }});
  assert.equal(prevented, 1);
  assert.equal(menu.open, true);
  assert.equal(toggle.attributes['aria-expanded'], 'true');
  assert.equal(root.dataset.theme, 'dark');
  assert.equal(JSON.parse(values.get('kairos:appearance')).accent, 'violet');
  assert.equal(menu.style.left, '170px');
  node('appearance-accent').listeners.change({target: {value: 'blue'}});
  assert.equal(menu.open, false);
  assert.equal(root.dataset.accent, 'blue');
  assert.equal(root.dataset.theme, 'dark');
  assert.equal(toggle.focused, true);
  for (const event of [{key: 'ArrowDown'}, {key: 'F10', shiftKey: true}, {key: 'ContextMenu'}]) {
    toggle.listeners.keydown({...event, preventDefault() {}});
    assert.equal(menu.open, true);
    menu.listeners.keydown({key: 'Escape', preventDefault() {}});
    assert.equal(menu.open, false);
  }
  assert.equal(fixture(values.get('kairos:appearance')).root.dataset.accent, 'blue');
});

test('touch hold opens accents and suppresses the release click, while movement cancels the hold', () => {
  const {root, node, timers} = fixture(null);
  const toggle = node('appearance-theme'), menu = node('appearance-menu');
  const down = () => toggle.listeners.pointerdown({pointerType: 'touch', clientX: 10, clientY: 10});
  down();
  assert.equal(timers.get(1).delay, 550);
  timers.get(1).callback();
  toggle.listeners.contextmenu({preventDefault() {}}); // A native long-press event can follow our timer.
  toggle.listeners.pointerup();
  toggle.listeners.click({detail: 1, preventDefault() {}});
  assert.equal(menu.open, true);
  assert.equal(root.dataset.theme, 'dark');
  menu.hidePopover();
  down();
  toggle.listeners.pointermove({clientX: 30, clientY: 10});
  assert.equal(timers.size, 0);
  down(); toggle.listeners.pointerup(); toggle.listeners.click({detail: 1, preventDefault() {}});
  assert.equal(root.dataset.theme, 'light');
});
