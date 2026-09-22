const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

function fixture(saved = null) {
  const values = new Map(saved === null ? [] : [['kairos:favorites', saved]]);
  const warning = {hidden: true};
  const timers = [];
  const browser = {};
  const storage = {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value)};
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/markets.js'), 'utf8'), {
    window: browser, localStorage: storage,
    document: {getElementById: () => warning},
    setTimeout: (callback, delay) => timers.push({callback, delay}),
  });
  const picker = Object.assign(Object.create(browser.MarketPicker.prototype), {
    markets: [], received: 0, error: '', renderRows() {}, renderFooter() {}, updateFreshness() {},
  });
  picker.favorites = picker.loadFavorites();
  return {picker, values, storage, warning, timers};
}

test('favorites persist across page instances; removing one retains the rest', () => {
  const {picker, values} = fixture();
  picker.toggleFavorite('XXBTZUSD');
  picker.toggleFavorite('XETHZUSD');
  const reloaded = fixture(values.get('kairos:favorites'));
  assert.deepEqual(Array.from(reloaded.picker.favorites), ['XXBTZUSD', 'XETHZUSD']);
  reloaded.picker.toggleFavorite('XXBTZUSD');
  assert.deepEqual(JSON.parse(reloaded.values.get('kairos:favorites')), ['XETHZUSD']);
});

test('corrupt or unavailable storage cannot prevent market browsing', () => {
  for (const saved of ['not json', '{}', 'null']) {
    assert.deepEqual(Array.from(fixture(saved).picker.favorites), []);
  }
  const {picker, storage} = fixture('["XXBTZUSD","XXBTZUSD",null,12,""]');
  assert.deepEqual(Array.from(picker.favorites), ['XXBTZUSD']);
  storage.getItem = () => { throw new Error('Storage denied'); };
  assert.deepEqual(Array.from(picker.loadFavorites()), []);
});

test('failed persistence warns instead of pretending favorites were saved', () => {
  const {picker, storage, warning} = fixture();
  storage.setItem = () => { throw new Error('Quota exceeded'); };
  picker.toggleFavorite('XXBTZUSD');
  assert.deepEqual(Array.from(picker.favorites), ['XXBTZUSD']);
  assert.equal(warning.hidden, false);
  assert.match(warning.textContent, /only for this page session/);
});

test('market refresh failures retain the old timestamp and schedule a retry', async () => {
  const {picker, timers} = fixture();
  picker.received = 123;
  picker.markets = [{id: 'XXBTZUSD', symbol: 'BTC/USD', last: '100'}];
  picker.request = async () => { throw new Error('Offline'); };
  await picker.refresh();
  assert.equal(picker.received, 123);
  assert.equal(picker.markets[0].last, '100');
  assert.match(picker.error, /unavailable/);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 10000);
  picker.request = async () => ({received: 456, markets: [{id: 'XXBTZUSD', symbol: 'BTC/USD', last: '101'}]});
  await picker.refresh();
  assert.equal(picker.received, 456);
  assert.equal(picker.markets[0].last, '101');
  assert.equal(picker.error, '');
});

test('browsing quotes are selected by symbol and never synthesize missing prices', () => {
  const {picker} = fixture();
  picker.received = 123;
  picker.markets = [{symbol: 'BTC/USD', bid: '99', ask: '101', last: '100'}, {symbol: 'ETH/USD'}];
  assert.equal(picker.ticker('BTC/USD').bid, 99);
  assert.equal(picker.ticker('BTC/USD').received, 123);
  assert.equal(picker.ticker('ETH/USD'), null);
  assert.equal(picker.ticker('BTC/EUR'), null);
});
