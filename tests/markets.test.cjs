const assert = require('node:assert/strict');
const {readFileSync, readdirSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

function fixture(saved = null) {
  const values = new Map(saved === null ? [] : [['kairos:favorites', saved]]);
  const warning = {hidden: true}, status = {};
  const timers = [];
  const browser = {};
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/vendor/crypto-icons/symbols.js'), 'utf8'), {window: browser});
  const storage = {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value)};
  const document = {hidden: false, getElementById: id => id === 'favorite-order-status' ? status : warning};
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/markets.js'), 'utf8'), {
    window: browser, localStorage: storage, URLSearchParams,
    document,
    setTimeout: (callback, delay) => timers.push({callback, delay}),
  });
  const picker = Object.assign(Object.create(browser.MarketPicker.prototype), {
    markets: [], received: 0, error: '', sortKey: 'symbol', sortDirection: 'ascending',
    renderRows() {}, renderFooter() {}, updateFreshness() {}, catalogChanged() {},
    dialog: {open: false}, poller: {refresh() {}, start() {}}, selected: null,
  });
  picker.favorites = picker.loadFavorites();
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/strategy-market.js'), 'utf8'), {window: browser});
  return {picker, values, storage, warning, timers, browser, status, document};
}

test('exchange favorites stay isolated without replacing the existing Kraken watchlist', () => {
  const {picker, values} = fixture('["XBTUSD"]');
  picker.setExchange('alpaca');
  assert.deepEqual(Array.from(picker.favorites), []);
  picker.toggleFavorite('alpaca:AAPL');
  assert.equal(values.get('kairos:favorites'), '["XBTUSD"]');
  assert.equal(values.get('kairos:alpaca:favorites'), '["alpaca:AAPL"]');
  picker.setExchange('kraken');
  assert.deepEqual(Array.from(picker.favorites), ['XBTUSD']);
});

test('Alpaca stocks are scheduled-only rather than tokenized xStocks or HTF markets', () => {
  const {browser} = fixture();
  const pair = {id: 'alpaca:AAPL', symbol: 'AAPL/USD', quote: 'USD', kind: 'equity', supported_strategies: ['dca', 'twap', 'rebalance']};
  assert.match(browser.MarketPicker.kindLabel(pair), /US stock.*IEX/);
  assert.match(browser.StrategyMarketPicker.limitation(pair, {product: 'spot', quote: 'USD', strategy: 'htf'}), /DCA, TWAP/);
  assert.equal(browser.StrategyMarketPicker.limitation(pair, {product: 'spot', quote: 'USD', strategy: 'twap'}), '');
});

test('product filters distinguish margin eligibility from new product execution', () => {
  const {browser} = fixture();
  const {matchesKind, matches, iconSymbol} = browser.MarketPicker;
  const rows = [
    {id: 'BTC', symbol: 'BTC/USD', kind: 'spot', margin: true},
    {id: 'EUR', symbol: 'EUR/USD', kind: 'fx', margin: true},
    {id: 'xstocks:AAPLxUSD', symbol: 'AAPLx/USD', kind: 'xstocks', margin: true},
    {id: 'futures:PF_XBTUSD', symbol: 'PF_XBTUSD', kind: 'futures', underlying: 'BTC:USD'},
  ];
  assert.deepEqual(rows.filter(row => matchesKind(row, 'margin')).map(row => row.id), ['BTC']);
  assert.equal(rows.filter(row => matchesKind(row, 'all')).length, 4);
  assert.equal(rows.filter(row => matchesKind(row, 'xstocks')).length, 1);
  assert.equal(matches(rows[3], 'BTC/USD'), true);
  assert.equal(iconSymbol(rows[3]), 'BTC/USD');
  const reason = 'Futures are browse-only; derivatives execution is not integrated.';
  assert.equal(browser.StrategyMarketPicker.limitation({...rows[3], execution_reason: reason}, {quote: 'ZUSD', product: 'spot'}), reason);
});

test('Strategy requires a matching Futures product and a qualified linear perpetual', () => {
  const {limitation} = fixture().browser.StrategyMarketPicker;
  const pair = {id: 'futures:PF_XBTUSD', symbol: 'PF_XBTUSD', kind: 'futures', quote: 'USD', linear_perpetual: true};
  assert.equal(limitation(pair, {product: 'futures', quote: 'ZUSD'}), '');
  assert.match(limitation(pair, {product: 'spot', quote: 'ZUSD'}), /Choose the Futures product/);
  assert.match(limitation({...pair, linear_perpetual: false}, {product: 'futures'}), /browse-only/);
  assert.match(limitation({id: 'BTC', symbol: 'BTC/USD', quote: 'ZUSD'}, {product: 'futures'}), /qualified USD/);
  assert.equal(limitation({...pair, execution_reason: 'Unsupported contract'}, {product: 'futures'}), 'Unsupported contract');
});

test('chart selection keeps product identity and restrictions, not just the display symbol', () => {
  const {picker} = fixture();
  const market = {id: 'futures:PF_XBTUSD', symbol: 'PF_XBTUSD', kind: 'futures', execution_reason: 'Browse only'};
  let selected, closed = false;
  picker.select = value => { selected = value; };
  picker.dialog = {close() { closed = true; }};
  picker.choose(market);
  assert.equal(selected, market);
  assert.equal(closed, true);
});

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

test('24h change uses signed percentage points and sorts losses below zero', () => {
  const {picker, browser} = fixture();
  const format = browser.MarketPicker.percentage;
  assert.equal(format('2.5'), '+2.50%');
  assert.equal(format('-1.25'), '-1.25%');
  assert.equal(format('0'), '0.00%');
  for (const missing of [null, undefined, '', 'NaN', Infinity]) assert.equal(format(missing), '—');
  const rows = [
    {id: 'loss', symbol: 'A/USD', change_pct: '-5.2'}, {id: 'zero', symbol: 'B/USD', change_pct: '0'},
    {id: 'gain', symbol: 'C/USD', change_pct: '12'}, {id: 'missing', symbol: 'D/USD', change_pct: null},
  ];
  picker.sortBy('change_pct');
  assert.deepEqual(Array.from(picker.sortedMarkets(rows), row => row.id), ['gain', 'zero', 'loss', 'missing']);
  picker.sortBy('change_pct');
  assert.deepEqual(Array.from(picker.sortedMarkets(rows), row => row.id), ['loss', 'zero', 'gain', 'missing']);
});

test('both market pickers search names, IDs, punctuation, and Kraken aliases', () => {
  const {browser} = fixture();
  const matches = browser.MarketPicker.matches;
  for (const query of ['btc', 'BTC/USD', 'xbt usd', 'XXBTZUSD', '']) assert.equal(matches({id: 'XXBTZUSD', symbol: 'BTC/USD'}, query), true);
  assert.equal(matches({id: 'XDGUSD', symbol: 'XDG/USD'}, 'doge'), true);
  assert.equal(matches({id: 'XXBTZUSD', symbol: 'BTC/USD'}, 'ETH'), false);
});

test('Strategy shows limitations for quote mismatch and unsupported margin without changing drafts', () => {
  const {browser} = fixture();
  const limitation = browser.StrategyMarketPicker.limitation;
  const pair = {id: 'ETHUSD', symbol: 'ETH/USD', quote: 'ZUSD', leverage_buy: [2, 3], leverage_sell: [2]};
  const context = {quote: 'ZUSD', quoteLabel: 'USD', product: 'spot', leverage: 2, mode: 'dry-run'};
  assert.equal(limitation(pair, context), '');
  assert.match(limitation({...pair, quote: 'XXBT', symbol: 'ETH/BTC'}, context), /Quoted in BTC.*USD/);
  assert.equal(limitation(pair, {...context, product: 'margin'}), '');
  assert.match(limitation(pair, {...context, product: 'margin', leverage: 3}), /3× margin/);
  assert.match(limitation(pair, {...context, product: 'margin', mode: 'trading'}), /paper-only/);
  assert.equal(context.product, 'spot');
  assert.equal(pair.id, 'ETHUSD');
});

test('Enter in the Strategy picker never implicitly submits settings', () => {
  const handlers = {}, browser = {};
  const input = {addEventListener(name, handler) { handlers[name] = handler; }};
  const list = {hidden: true, addEventListener() {}};
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/strategy-market.js'), 'utf8'), {
    window: browser,
    document: {getElementById: id => id === 'pair-search' ? input : id === 'pair-options' ? list : {}, addEventListener() {}},
  });
  const picker = new browser.StrategyMarketPicker();
  let opens = 0, choices = 0, prevented = 0;
  picker.open = () => { opens++; list.hidden = false; };
  picker.choose = () => { choices++; list.hidden = true; };
  const enter = () => handlers.keydown({key: 'Enter', preventDefault() { prevented++; }});
  enter(); // Closed: open the picker, not the form's implicit submit.
  assert.equal(opens, 1);
  picker.active = 0; picker.matches = [{id: 'ETHUSD'}];
  enter(); // Open: select a draft only.
  assert.equal(choices, 1);
  enter(); // A second/repeated Enter must not save that draft.
  assert.equal(opens, 2);
  assert.equal(prevented, 3);
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

test('a healthy venue cannot hide stale prices in another venue’s favorites', () => {
  const {picker, browser} = fixture();
  const now = Date.now()/1000;
  const stale = new Map();
  const buttons = ['BTC', 'futures:BTC'].map(id => ({dataset: {watch: id}, classList: {toggle: (name, value) => stale.set(id, value)}}));
  picker.received = now;
  picker.markets = [
    {id: 'BTC', symbol: 'BTC/USD', kind: 'spot', last: '100', received: now},
    {id: 'futures:BTC', symbol: 'PF_XBTUSD', kind: 'futures', last: '200', received: now-120},
  ];
  picker.rows = {querySelectorAll: () => []};
  picker.watchlist = {querySelectorAll: () => buttons};
  browser.MarketPicker.prototype.updateFreshness.call(picker);
  assert.equal(stale.get('BTC'), false);
  assert.equal(stale.get('futures:BTC'), true);
  assert.match(buttons[1].title, /STALE/);
  assert.doesNotMatch(buttons[0].title, /STALE/);
});

test('market refresh failures retain old data until a successful refresh', async () => {
  const {picker} = fixture();
  picker.received = 123;
  picker.markets = [{id: 'XXBTZUSD', symbol: 'BTC/USD', last: '100'}];
  picker.request = async () => { throw new Error('Offline'); };
  await picker.refresh();
  assert.equal(picker.received, 123);
  assert.equal(picker.markets[0].last, '100');
  assert.match(picker.error, /unavailable/);
  picker.request = async () => ({received: 456, markets: [{id: 'XXBTZUSD', symbol: 'BTC/USD', last: '101'}]});
  await picker.refresh();
  assert.equal(picker.received, 456);
  assert.equal(picker.markets[0].last, '101');
  assert.equal(picker.error, '');
});

test('closed picker requests only selected/favorite quotes and preserves catalog and old ages', async () => {
  const {picker} = fixture();
  picker.markets = ['BTC', 'ETH', 'SOL'].map(id => ({id, symbol: `${id}/USD`, received: 100}));
  picker.selected = 'BTC'; picker.favorites = ['ETH', 'no-longer-listed'];
  let query;
  picker.request = async path => { query = path; return {received: 200, partial: true, markets: [
    {id: 'BTC', symbol: 'BTC/USD', received: 200}, {id: 'ETH', symbol: 'ETH/USD', received: 200},
  ]}; };
  await picker.refresh();
  assert.deepEqual(new URLSearchParams(query.split('?')[1]).get('ids').split(','), ['BTC', 'ETH']);
  assert.equal(picker.markets.length, 3);
  assert.equal(picker.markets.find(row => row.id === 'SOL').received, 100);
  assert.equal(picker.markets.find(row => row.id === 'BTC').received, 200);
  picker.dialog.open = true;
  picker.request = async path => { query = path; return {received: 300, markets: [...picker.markets, {id: 'NEW'}]}; };
  await picker.refresh();
  assert.equal(query, 'markets');
  assert.equal(picker.markets.length, 4);
});

test('hidden or aborted market polls cannot fetch or replace visible state', async () => {
  const {picker, document} = fixture();
  let calls = 0, finish;
  picker.request = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  document.hidden = true;
  await picker.refresh();
  assert.equal(calls, 0);
  document.hidden = false;
  const controller = new AbortController();
  const pending = picker.refresh(controller.signal);
  controller.abort();
  finish({received: 123, markets: [{id: 'obsolete'}]});
  await pending;
  assert.equal(picker.received, 0);
  assert.equal(picker.markets.length, 0);
  assert.equal(picker.error, '');
});

test('quotes use instrument identity and preserve per-venue age, not display symbols', () => {
  const {picker} = fixture();
  picker.received = 999;
  picker.markets = [
    {id: 'BTC', symbol: 'BTC/USD', bid: '99', ask: '101', last: '100', received: 123},
    {id: 'futures:BTC', symbol: 'BTC/USD', bid: '199', ask: '201', received: 456},
    {id: 'ETH', symbol: 'ETH/USD'},
  ];
  assert.equal(picker.ticker('BTC').bid, 99);
  assert.equal(picker.ticker('BTC').received, 123);
  assert.equal(picker.ticker('futures:BTC').bid, 199);
  assert.equal(picker.ticker('futures:BTC').received, 456);
  assert.equal(picker.ticker('ETH'), null);
  assert.equal(picker.ticker('BTC/USD'), null);
});

test('manual favorite order persists, keeps hidden/unavailable IDs, and never selects a market', () => {
  const {picker, values, status} = fixture('["BTC","unavailable","ETH","DOGE"]');
  picker.request = picker.select = () => assert.fail('Reordering must be browser-local');
  picker.moveFavorite('DOGE', 'BTC');
  assert.deepEqual(JSON.parse(values.get('kairos:favorites')), ['DOGE', 'BTC', 'unavailable', 'ETH']);
  assert.match(status.textContent, /position 1 of 4/);
  picker.moveFavorite('BTC', 'ETH', true);
  assert.deepEqual(Array.from(fixture(values.get('kairos:favorites')).picker.favorites), ['DOGE', 'unavailable', 'ETH', 'BTC']);
  const saved = values.get('kairos:favorites');
  picker.moveFavorite('BTC', 'BTC'); picker.moveFavorite('not-saved', 'BTC'); picker.moveFavorite('BTC', 'not-saved');
  assert.equal(values.get('kairos:favorites'), saved);
});

test('favorite view follows saved order while All markets keeps its independent sort', () => {
  const {picker} = fixture('["ETH","BTC"]');
  const rows = [{id: 'BTC', symbol: 'BTC/USD', last: '2'}, {id: 'ETH', symbol: 'ETH/USD', last: '1'}];
  picker.sortBy('last');
  picker.favoritesOnly = true;
  picker.sortBy('symbol');
  assert.equal(picker.sortKey, 'last');
  assert.deepEqual(Array.from(picker.sortedMarkets(rows), row => row.id), ['ETH', 'BTC']);
  picker.favoritesOnly = false;
  assert.deepEqual(Array.from(picker.sortedMarkets(rows), row => row.id), ['BTC', 'ETH']);
  assert.equal(rows[0].id, 'BTC');
});

test('click-to-move and cancel preserve storage until a destination is chosen', () => {
  const {picker, values} = fixture('["BTC","ETH","DOGE"]');
  picker.pickFavorite('DOGE');
  assert.equal(values.get('kairos:favorites'), '["BTC","ETH","DOGE"]');
  picker.cancelReorder();
  assert.equal(picker.pickedFavorite, null);
  picker.pickFavorite('DOGE'); picker.pickFavorite('BTC');
  assert.deepEqual(JSON.parse(values.get('kairos:favorites')), ['DOGE', 'BTC', 'ETH']);
  assert.equal(picker.pickedFavorite, null);
});

test('reorder remains usable with a visible warning when browser storage fails', () => {
  const {picker, storage, warning} = fixture('["BTC","ETH"]');
  storage.setItem = () => { throw new Error('Unavailable'); };
  picker.moveFavorite('ETH', 'BTC');
  assert.deepEqual(Array.from(picker.favorites), ['ETH', 'BTC']);
  assert.equal(warning.hidden, false);
  assert.match(warning.textContent, /page session/);
});

test('Alpaca chooser volume subscriptions contain only watched chart and favorites', async () => {
  const {picker} = fixture();
  picker.exchange = 'alpaca';
  picker.markets = ['watched', 'favorite', 'browse-only', 'bot-not-watched'].map(id => ({id, symbol: id}));
  picker.selected = 'watched'; picker.favorites = ['favorite', 'delisted'];
  picker.dialog.open = true;
  const queries = [];
  picker.request = async path => {
    queries.push(new URLSearchParams(path.split('?')[1]));
    return {received: 123, markets: picker.markets};
  };
  await picker.refresh();
  assert.equal(queries[0].has('ids'), false); // Full chooser catalog remains available.
  assert.equal(queries[0].has('volume_ids'), false);
  assert.equal(queries[1].get('ids'), 'watched,favorite');
  assert.equal(queries[1].get('volume_ids'), 'watched,favorite');
  picker.sortBy('volume');
  await picker.refresh();
  assert.equal(queries[2].has('volume_ids'), false);
  assert.equal(queries[3].get('volume_ids'), 'watched,favorite');
  picker.favorites = []; picker.selected = 'browse-only';
  await picker.refresh();
  assert.equal(queries[4].has('volume_ids'), false);
  assert.equal(queries[5].get('volume_ids'), 'browse-only');
});

test('all Alpaca favorites are fetched in bounded batches, never the remaining catalog', async () => {
  const {picker} = fixture();
  picker.exchange = 'alpaca';
  picker.markets = Array.from({length: 260}, (_, i) => ({id: `m${i}`, symbol: `m${i}`}));
  picker.favorites = picker.markets.slice(0, 205).map(row => row.id);
  picker.selected = 'm250'; picker.dialog.open = true;
  const fetched = [];
  picker.request = async path => {
    const params = new URLSearchParams(path.split('?')[1]);
    if (!params.has('ids')) {
      assert.equal(params.has('volume_ids'), false);
      return {received: 123, markets: picker.markets};
    }
    const ids = params.get('volume_ids').split(',');
    assert.ok(ids.length <= 100);
    fetched.push(...ids);
    return {received: 123, partial: params.has('ids'), markets: params.has('ids') ? picker.markets.filter(row => ids.includes(row.id)) : picker.markets};
  };
  await picker.refresh();
  assert.deepEqual(fetched, [...picker.favorites, 'm250']);
  assert.equal(new Set(fetched).size, 206);
  assert.equal(picker.markets.length, 260);
});

test('initial discovery does not warm the entire Alpaca catalog and Kraken never subscribes', async () => {
  const {picker} = fixture();
  picker.exchange = 'alpaca'; picker.selected = 'watched'; picker.favorites = ['favorite', 'removed'];
  const queries = [];
  picker.request = async path => {
    queries.push(path);
    return {received: 123, markets: ['watched', 'favorite', 'other'].map(id => ({id, symbol: id}))};
  };
  await picker.refresh();
  assert.equal(queries[0], 'markets');
  assert.equal(new URLSearchParams(queries[1].split('?')[1]).get('volume_ids'), 'watched,favorite');
  picker.exchange = 'kraken';
  await picker.refresh();
  assert.equal(new URLSearchParams(queries[2].split('?')[1]).has('volume_ids'), false);
});

test('volume labels distinguish IEX daily, crypto 24h, zero and stale cached values', () => {
  const {browser} = fixture(), Picker = browser.MarketPicker;
  const stock = {kind: 'equity', volume: '0', volume_unit: 'shares', volume_received: Date.now()/1000, volume_session: '2026-09-29'};
  assert.equal(Picker.volumeText(stock), 'IEX daily vol 0 shares');
  assert.match(Picker.volumeTitle(stock), /IEX-only shares · session 2026-09-29 · not consolidated/);
  const crypto = {kind: 'spot', volume: '10', volume_unit: 'BTC', volume_received: Date.now()/1000 - 601, volume_asof: Date.now()/1000 - 700};
  assert.equal(Picker.volumeText(crypto), '24h vol 10 BTC · cached');
  assert.match(Picker.volumeTitle(crypto), /24h of completed five-minute bars/);
  assert.match(Picker.volumeTitle(crypto), /STALE CACHE/);
  assert.match(Picker.volumeText({kind: 'equity'}), /IEX daily vol —/);
  assert.match(Picker.volumeTitle({}), /Volume unavailable/);
});
