const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

function fixture(saved, blocked = false) {
  const values = new Map([['kairos:appearance', saved], ['kairos:favorites', '["XXBTZUSD"]']]);
  const nodes = new Map(), listeners = {};
  const root = {dataset: {}};
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {listeners: {}, addEventListener(name, callback) { this.listeners[name] = callback; }});
    return nodes.get(id);
  };
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/appearance.js'), 'utf8'), {
    document: {documentElement: root, getElementById: node, querySelector: () => node('meta'), addEventListener: (event, callback) => { listeners[event] = callback; }},
    window: {addEventListener: (event, callback) => { listeners[event] = callback; }},
    localStorage: {getItem: key => { if (blocked) throw new Error('Unavailable'); return values.get(key); }, setItem: (key, value) => { if (blocked) throw new Error('Unavailable'); values.set(key, value); }},
  });
  listeners.DOMContentLoaded();
  return {root, node, values, listeners};
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

test('corrupt and blocked preference storage cannot prevent branded initialization', () => {
  for (const saved of ['malformed', '{"theme":"external-url","accent":"invalid"}']) {
    const {root} = fixture(saved);
    assert.equal(root.dataset.theme, 'dark');
    assert.equal(root.dataset.accent, 'violet');
  }
  const {root, node} = fixture(null, true);
  node('appearance-theme').listeners.change({target: {value: 'light'}});
  assert.equal(root.dataset.theme, 'light');
  assert.match(node('appearance-status').textContent, /page session/);
});

test('cross-tab preference changes update artwork and controls, not unrelated storage', () => {
  const {root, node, listeners} = fixture(null);
  listeners.storage({key: 'kairos:favorites', newValue: '["ETH"]'});
  assert.equal(root.dataset.theme, 'dark');
  listeners.storage({key: 'kairos:appearance', newValue: '{"theme":"light","accent":"green"}'});
  assert.equal(node('appearance-theme').value, 'light');
  assert.equal(root.dataset.accent, 'green');
  assert.match(node('brand-icon').src, /on-light.svg$/);
  listeners.storage({key: null, newValue: null});
  assert.equal(root.dataset.theme, 'dark');
});
