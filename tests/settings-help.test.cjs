const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');
const root = path.join(__dirname, '..');
const schema = JSON.parse(execFileSync('uv', ['run', '--no-sync', 'python', '-c', 'import json; from kairos.settings import schema; print(json.dumps(schema()))'], {cwd: root, encoding: 'utf8'}));
const browser = {};
const context = {window: browser, innerWidth: 844, innerHeight: 260};
runInNewContext(readFileSync(path.join(root, 'kairos/static/settings-help.js'), 'utf8'), context);
const Help = browser.SettingsHelp;

test('every editable server field and supported strategy has configuration help', () => {
  const editable = Object.keys(schema.fields).filter(key => schema.fields[key].editable !== false).sort();
  assert.deepEqual(Object.keys(Help.fields).sort(), editable);
  assert.deepEqual(Object.keys(Help.strategies).sort(), Object.keys(schema.strategies).sort());
  for (const key of editable) {
    const paragraphs = Help.paragraphs(key, {strategy: 'htf', product: 'spot'}, schema);
    assert.ok(paragraphs[0].length > 50, `Missing usable explanation for ${key}`);
    assert.match(paragraphs.at(-1), /Default:/);
  }
});

test('bounds, choice labels and unconfigured defaults follow the supplied schema', () => {
  const updated = structuredClone(schema);
  updated.fields.order_size.min = '12.5'; updated.fields.order_size.max = '987.5'; updated.fields.order_size.default = '123.25';
  const text = Help.paragraphs('order_size', {}, updated).at(-1);
  assert.match(text, /12\.5–987\.5/);
  assert.match(text, /123\.25/);
  assert.match(Help.paragraphs('product', {}, schema).at(-1), /Spot · crypto \/ FX/);
  assert.match(Help.paragraphs('twap_limit', {}, schema).at(-1), /unconfigured; enter an explicit value/);
  assert.doesNotMatch(Help.paragraphs('twap_limit', {}, schema).at(-1), /Default: 0/);
  assert.match(Help.paragraphs('reinvest_profits', {}, schema).at(-1), /Default: On/);
});

test('strategy and anchor guidance follows drafts without mutating them or the contract', () => {
  const contract = JSON.stringify(schema);
  const scalp = Object.freeze({strategy: 'scalp', product: 'futures'});
  const dca = Object.freeze({strategy: 'dca', product: 'spot'});
  assert.equal(Help.paragraphs('strategy', scalp, schema)[1], Help.strategies.scalp);
  assert.equal(Help.paragraphs('strategy', dca, schema)[1], Help.strategies.dca);
  assert.ok(Help.paragraphs('pair', {strategy: 'rebalance'}, schema).some(text => /anchor market only/.test(text)));
  assert.ok(!Help.paragraphs('pair', scalp, schema).some(text => /anchor market only/.test(text)));
  assert.equal(JSON.stringify(schema), contract);
  assert.equal(scalp.strategy, 'scalp');
});

test('long help stays inside the viewport without covering its clickable trigger', () => {
  for (const [width, height, top] of [[844, 260, 30], [844, 260, 165], [360, 640, 310], [1440, 900, 700]]) {
    context.innerWidth = width; context.innerHeight = height;
    const rect = {top, bottom: top + 24, right: width - 16};
    const view = Object.create(Help.prototype);
    view.panel = {getBoundingClientRect: () => ({top: 0, bottom: height})};
    view.active = {trigger: {getBoundingClientRect: () => rect, getClientRects: () => [rect], closest: () => null}};
    view.popup = {style: {}, getBoundingClientRect: () => ({width: Math.min(360, width - 16), height: Math.min(400, parseFloat(view.popup.style.maxHeight) || 420)})};
    view.position();
    const box = view.popup.getBoundingClientRect(), left = parseFloat(view.popup.style.left), y = parseFloat(view.popup.style.top);
    assert.ok(left >= 8 && left + box.width <= width - 8);
    assert.ok(y >= 8 && y + box.height <= height - 8);
    assert.ok(y + box.height <= rect.top || y >= rect.bottom, 'Popover must not intercept its own trigger click');
  }
});

test('help closes when its source is clipped by the outer landscape settings panel', () => {
  const view = Object.create(Help.prototype);
  view.panel = {getBoundingClientRect: () => ({top: 100, bottom: 200})};
  view.active = {trigger: {
    getBoundingClientRect: () => ({top: 220, bottom: 244}), getClientRects: () => [{}],
    closest: () => ({getBoundingClientRect: () => ({top: 0, bottom: 1000})}),
  }};
  let closed = false; view.hide = () => { closed = true; };
  view.position(); assert.equal(closed, true);
});
