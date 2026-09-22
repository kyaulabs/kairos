const assert = require('node:assert/strict');
const {readFileSync, readdirSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

function fixture(saved = null) {
  const values = new Map(saved === null ? [] : [['kairos:favorites', saved]]);
  const warning = {hidden: true};
  const timers = [];
  const browser = {};
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/vendor/crypto-icons/symbols.js'), 'utf8'), {window: browser});
  const storage = {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value)};
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/markets.js'), 'utf8'), {
    window: browser, localStorage: storage,
    document: {getElementById: () => warning},
    setTimeout: (callback, delay) => timers.push({callback, delay}),
  });
  const picker = Object.assign(Object.create(browser.MarketPicker.prototype), {
    markets: [], received: 0, error: '', sortKey: 'symbol', sortDirection: 'ascending',
    renderRows() {}, renderFooter() {}, updateFreshness() {},
  });
  picker.favorites = picker.loadFavorites();
  return {picker, values, storage, warning, timers, browser};
}

test('market sorting starts A–Z and repeated header clicks reverse the order', () => {
  const {picker} = fixture();
  const rows = [{id: 'eth', symbol: 'ETH/USD'}, {id: 'btc', symbol: 'BTC/USD'}, {id: 'ada', symbol: 'ADA/USD'}];
  const order = () => Array.from(picker.sortedMarkets(rows), row => row.id);
  assert.deepEqual(order(), ['ada', 'btc', 'eth']);
  picker.sortBy('symbol');
  assert.deepEqual(order(), ['eth', 'btc', 'ada']);
  picker.sortBy('symbol');
  assert.deepEqual(order(), ['ada', 'btc', 'eth']);
  assert.equal(rows[0].id, 'eth'); // Sorting cannot mutate the snapshot.
});

test('price and volume sort numerically, descending first, with missing values always last', () => {
  for (const key of ['last', 'volume']) {
    const {picker} = fixture();
    const rows = [
      {id: 'small', symbol: 'A/USD', [key]: '2.5'},
      {id: 'large', symbol: 'B/USD', [key]: '100'},
      {id: 'zero', symbol: 'C/USD', [key]: '0'},
      ...[undefined, null, '', 'NaN', Infinity].map((value, i) => ({id: `missing-${i}`, symbol: `Z${i}/USD`, [key]: value})),
    ];
    const order = () => Array.from(picker.sortedMarkets(rows), row => row.id);
    picker.sortBy(key);
    assert.deepEqual(order().slice(0, 3), ['large', 'small', 'zero']);
    assert.ok(order().slice(3).every(id => id.startsWith('missing-')));
    picker.sortBy(key);
    assert.deepEqual(order().slice(0, 3), ['zero', 'small', 'large']);
    assert.ok(order().slice(3).every(id => id.startsWith('missing-')));
    picker.sortBy('symbol');
    assert.equal(picker.sortDirection, 'ascending');
  }
});

test('equal numeric values use deterministic alphabetical ties across refreshes', async () => {
  const {picker} = fixture();
  picker.sortBy('volume');
  picker.request = async () => ({received: 123, markets: [
    {id: 'eth', symbol: 'ETH/USD', volume: '10'}, {id: 'btc', symbol: 'BTC/USD', volume: '10'},
  ]});
  await picker.refresh();
  assert.equal(picker.sortKey, 'volume');
  assert.equal(picker.sortDirection, 'descending');
  assert.deepEqual(Array.from(picker.sortedMarkets(picker.markets), row => row.id), ['btc', 'eth']);
});

test('currency icons use only bundled local files; unknown symbols have no image request', () => {
  const {browser} = fixture();
  assert.equal(browser.MarketPicker.iconPath('BTC'), '/static/vendor/crypto-icons/btc.svg');
  assert.equal(browser.MarketPicker.iconPath('USD'), '/static/vendor/crypto-icons/usd.svg');
  assert.equal(browser.MarketPicker.iconPath('XDG'), '/static/vendor/crypto-icons/doge.svg');
  assert.equal(browser.MarketPicker.iconPath('DOGE'), '/static/vendor/crypto-icons/doge.svg');
  for (const ambiguous of ['ACT', 'BEAM', 'BLZ', 'BOS', 'CC', 'CTR', 'POLIS', 'SAFE', 'SKY', 'WINGS']) {
    assert.equal(browser.MarketPicker.iconPath(ambiguous), null);
  }
  assert.equal(browser.MarketPicker.iconPath('UNLISTED'), null);
  assert.equal(browser.MarketPicker.iconPath('../btc'), null);
  const files = readdirSync(path.join(__dirname, '../kairos/static/vendor/crypto-icons')).filter(name => name.endsWith('.svg')).map(name => name.slice(0, -4));
  assert.deepEqual(Array.from(browser.CryptoIconSymbols).sort(), files.sort());
});

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
