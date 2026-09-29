const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

// Exercise the actual Python contract, not a second handwritten list of fields/bounds.
const schema = JSON.parse(execFileSync('uv', ['run', '--no-sync', 'python', '-c', 'import json; from kairos.settings import schema; print(json.dumps(schema()))'], {cwd: path.join(__dirname, '..'), encoding: 'utf8'}));
const defaults = Object.fromEntries(Object.entries(schema.fields).map(([key, field]) => [key, field.default]));
const browser = {};
runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/settings.js'), 'utf8'), {
  window: browser, document: {createElement: () => ({})},
});
function fixture(saved = defaults) {
  const inputs = {};
  for (const [name, field] of Object.entries(schema.fields)) {
    if (field.editable === false) continue;
    let value = '';
    const label = {hidden: false};
    inputs[name] = {
      tagName: field.choices ? 'SELECT' : 'INPUT', options: [], checked: false,
      get value() { return value; }, set value(next) { value = String(next); },
      closest() { return label; }, setCustomValidity(error) { this.error = error; },
      replaceChildren(...options) { this.options = options; },
    };
  }
  const twap = {hidden: true, querySelectorAll: () => [inputs.twap_side, inputs.twap_quantity, inputs.futures_parent_notional]};
  const form = {elements: {namedItem: name => inputs[name]}, querySelectorAll: () => [twap]};
  const view = new browser.SettingsForm(form, schema);
  view.load(saved);
  const values = () => JSON.parse(JSON.stringify(view.values(saved)));
  return {inputs, view, values, twap};
}

test('scalping exposes paper rules without editing the saved HTF interval or requesting model confidence', () => {
  const {inputs, view, values} = fixture();
  inputs.strategy.value = 'scalp'; view.update();
  assert.equal(schema.strategies.scalp.paper_only, true);
  assert.equal(schema.strategies.scalp.deterministic, true);
  assert.equal(inputs.scalp_window.disabled, false);
  assert.equal(inputs.min_confidence.disabled, true);
  assert.equal(inputs.candle_minutes.disabled, true);
  assert.equal(inputs.recovery_check_seconds.disabled, true);
  assert.equal(values().candle_minutes, defaults.candle_minutes);
  inputs.recover_initial.checked = true; view.update();
  assert.match(inputs.recover_initial.error, /capital recovery/);
});

test('server bounds, choices and types drive the form and saved payload', () => {
  const {inputs, values} = fixture();
  assert.deepEqual(values(), defaults);
  assert.equal(inputs.order_size.max, '1000000000');
  assert.equal(inputs.interval_seconds.step, '1');
  assert.equal(inputs.order_size.step, 'any');
  assert.equal(inputs.reinvest_profits.required, false);
  assert.deepEqual(Array.from(inputs.candle_minutes.options, option => Number(option.value)), [1, 5, 15, 30, 60, 240, 1440]);
  inputs.interval_seconds.value = '30';
  inputs.order_size.value = '123.456789123456789';
  inputs.reinvest_profits.checked = false;
  const result = values();
  assert.equal(result.interval_seconds, 30);
  assert.equal(result.order_size, '123.456789123456789'); // Never round monetary strings through JS Number.
  assert.equal(result.reinvest_profits, false);
  assert.equal(result.quote, 'ZUSD');
});

test('product changes expose one leverage and allocation without copying or saving inactive drafts', () => {
  const saved = {...defaults, live_budget: '123', futures_live_budget: '456', leverage: 3, futures_leverage: 2};
  const {inputs, view, values} = fixture(saved);
  inputs.live_budget.value = '999'; // Unsaved spot draft must not resize spot while configuring Futures.
  inputs.product.value = 'futures'; view.update();
  assert.equal(inputs.leverage.disabled, true);
  assert.equal(inputs.futures_leverage.disabled, false);
  assert.equal(inputs.live_budget.closest().hidden, true);
  assert.equal(inputs.futures_live_budget.closest().hidden, false);
  assert.equal(inputs.futures_live_budget.value, '456');
  assert.equal(values().live_budget, '123');
  assert.equal(values().leverage, 3);
  assert.equal(values().futures_leverage, 2);
  assert.equal(values().reinvest_profits, true);
  inputs.product.value = 'margin'; view.update();
  assert.equal(inputs.leverage.disabled, false);
  assert.equal(inputs.futures_leverage.disabled, true);
  assert.equal(inputs.live_budget.disabled, true);
  assert.equal(inputs.futures_live_budget.disabled, true);
  assert.equal(inputs.margin_rollover_bps.disabled, false);
  inputs.product.value = 'spot'; view.update();
  assert.equal(inputs.live_budget.value, '999'); // Draft survives returning before Save.
});

test('strategy applicability hides irrelevant controls and keeps TWAP price explicit', () => {
  const {inputs, view, values, twap} = fixture();
  assert.equal(inputs.twap_limit.value, '');
  assert.equal(inputs.twap_limit.disabled, true);
  assert.equal(values().twap_limit, '0');
  inputs.strategy.value = 'twap'; view.update();
  assert.equal(inputs.min_confidence.disabled, true);
  assert.equal(inputs.candle_minutes.disabled, true);
  assert.equal(inputs.twap_limit.disabled, false);
  assert.match(inputs.twap_limit.error, /positive/);
  assert.equal(twap.hidden, false);
  assert.equal(inputs.futures_parent_notional.disabled, true);
  inputs.twap_limit.value = '0'; view.update();
  assert.match(inputs.twap_limit.error, /positive/);
  inputs.twap_limit.value = '123.45'; view.update();
  assert.equal(inputs.twap_limit.error, '');
  inputs.product.value = 'futures'; view.update();
  assert.equal(inputs.futures_parent_notional.disabled, false);
  assert.equal(inputs.futures_dca_side.disabled, true);
  assert.equal(inputs.futures_reduce_only.disabled, false);
  inputs.strategy.value = 'dca'; view.update();
  assert.equal(inputs.futures_dca_side.disabled, false);
  assert.equal(inputs.futures_parent_notional.disabled, true);
  assert.equal(twap.hidden, true);
  assert.equal(values().twap_limit, '0'); // Hidden unsaved TWAP draft is not submitted by DCA.
});

test('an incompatible recovery switch stays visible until explicitly turned off', () => {
  const {inputs, view, values} = fixture({...defaults, recover_initial: true});
  inputs.product.value = 'futures'; view.update();
  assert.equal(inputs.recover_initial.checked, true);
  assert.equal(inputs.recover_initial.disabled, false);
  assert.equal(inputs.recover_initial.closest().hidden, false);
  assert.match(inputs.recover_initial.error, /spot-only/);
  inputs.recover_initial.checked = false; view.update();
  assert.equal(inputs.recover_initial.disabled, true);
  assert.equal(inputs.recover_initial.error, '');
  assert.equal(values().recover_initial, false);
});

test('unsupported strategies remain labeled and require an explicit supported choice', () => {
  const {inputs, view} = fixture({...defaults, strategy: 'rebalance'});
  inputs.product.value = 'futures'; view.update();
  assert.equal(inputs.strategy.value, 'rebalance');
  assert.match(inputs.strategy.error, /supported/);
  const option = inputs.strategy.options.find(option => option.value === 'rebalance');
  assert.equal(option.disabled, true);
  assert.match(option.textContent, /unavailable/);
  inputs.strategy.value = 'maker'; view.update();
  assert.equal(inputs.strategy.error, '');
});

test('snapshot rendering does not replace drafts, but an explicit load restores saved values', () => {
  const {inputs, view} = fixture();
  inputs.order_size.value = '42';
  inputs.reinvest_profits.checked = false;
  view.update(); view.update();
  assert.equal(inputs.order_size.value, '42');
  assert.equal(inputs.reinvest_profits.checked, false);
  view.load({...defaults, twap_limit: '501.25'});
  assert.equal(inputs.order_size.value, '1000');
  assert.equal(inputs.twap_limit.value, '501.25');
});

test('explicit labels and help wrappers preserve field applicability and inactive draft isolation', () => {
  const {inputs, view, values} = fixture();
  const wrappers = Object.fromEntries(Object.keys(inputs).map(name => [name, {hidden: false}]));
  for (const [name, input] of Object.entries(inputs)) input.closest = selector => selector === '.setting-field' ? wrappers[name] : null;
  inputs.live_budget.value = '987';
  inputs.product.value = 'futures'; view.update();
  assert.equal(wrappers.live_budget.hidden, true);
  assert.equal(inputs.live_budget.disabled, true);
  assert.equal(wrappers.futures_live_budget.hidden, false);
  assert.equal(values().live_budget, defaults.live_budget);
  inputs.product.value = 'spot'; view.update();
  assert.equal(wrappers.live_budget.hidden, false);
  assert.equal(inputs.live_budget.value, '987');
});
