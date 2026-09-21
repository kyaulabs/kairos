const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

const browser = {};
runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/chart.js'), 'utf8'), {window: browser});
const start = 1700000040;

function chart(intervalMs = 60000) {
  // Exercise the real data/viewport methods without an SVG renderer.
  return Object.assign(Object.create(browser.LiveChart.prototype), {
    intervalMs, anchor: null, points: [], fills: [], draw() {}, follow() { this.anchor = null; },
  });
}
function candle(time = start, close = '102') {
  return {time, open: '100', high: '105', low: '95', close};
}
function domain(view) { return Array.from(view.timeDomain()); }

test('forming candle updates replace the bar; the next candle advances the live view', () => {
  const view = chart();
  view.setCandles([candle()]);
  const firstDomain = domain(view);
  view.setCandles([candle(start, '103')]);
  assert.equal(view.points.length, 1);
  assert.equal(view.points[0].value, 103);
  assert.deepEqual(domain(view), firstDomain);
  view.setCandles([candle(start, '103'), candle(start + 60)]);
  assert.equal(view.points.length, 2);
  assert.equal(domain(view)[0] - firstDomain[0], 60000);
  assert.equal(domain(view)[1] - domain(view)[0], 60 * 60000);
});

test('snapshots retain up to 720 historical candles for pan/zoom', () => {
  const view = chart();
  view.setCandles(Array.from({length: 800}, (_, i) => candle(start + i * 60)));
  assert.equal(view.points.length, 720);
  assert.equal(view.points[0].time, (start + 80 * 60) * 1000);
  assert.equal(view.points.at(-1).time, (start + 799 * 60) * 1000);
});

test('invalid OHLC or duplicate/out-of-order timestamps cannot replace a good snapshot', () => {
  const view = chart();
  view.setCandles([candle()]);
  const previous = view.points;
  for (const rows of [
    [candle(start, 'NaN')], [{...candle(), high: '99'}], [{...candle(), low: '103'}],
    [{...candle(), time: Infinity}], [{...candle(), open: '-1'}],
    [candle(), candle()], [candle(start + 60), candle()],
  ]) {
    assert.throws(() => view.setCandles(rows), /Invalid candle data/);
    assert.equal(view.points, previous);
  }
});

test('missing trading periods stay gaps rather than invented candles', () => {
  const view = chart();
  view.setCandles([candle(), candle(start + 180)]);
  assert.equal(view.points.length, 2);
  assert.equal(view.points[1].time - view.points[0].time, 180000);
});

test('a manually inspected history viewport stays anchored as new candles arrive', () => {
  const view = chart();
  view.setCandles([candle()]);
  view.anchor = start * 1000;
  const before = domain(view);
  view.setCandles([candle(), candle(start + 60)]);
  assert.deepEqual(domain(view), before);
  view.follow();
  assert.equal(domain(view)[1] - before[1], 60000);
});

test('native interval changes discard incompatible bars but preserve fill events', () => {
  const view = chart();
  view.fills.push({id: 1, time: start * 1000, value: 101, side: 'buy'});
  for (const minutes of [1, 5, 15, 30, 60, 240, 1440]) {
    view.setCandleInterval(minutes);
    assert.equal(view.points.length, 0);
    view.setCandles([candle()]);
    assert.equal(domain(view)[1] - domain(view)[0], 60 * minutes * 60000);
    assert.equal(view.fills.length, 1);
  }
  for (const invalid of [0, 10, NaN, '5', true]) {
    assert.throws(() => view.setCandleInterval(invalid), /Unsupported candle interval/);
  }
});

test('market changes clear candles and markers without changing the selected interval', () => {
  const view = chart();
  view.setCandleInterval(5);
  view.setCandles([candle()]);
  view.fills.push({id: 1});
  view.clear();
  assert.equal(view.points.length, 0);
  assert.equal(view.fills.length, 0);
  assert.equal(view.intervalMs, 300000);
});

test('session equity remains a bounded line series and retains frequent observations', () => {
  const view = chart(null);
  for (let i = 0; i <= 240; i++) view.add(start + i / 2, 100 + i);
  assert.equal(view.points.length, 121);
  assert.equal(domain(view)[0], (start + .5) * 1000);
  for (let i = 121; i < 3000; i++) view.add(start + i, 1100);
  assert.equal(view.points.length, 1800);
  view.add(start, 9999);
  assert.equal(view.points.at(-1).value, 1100);
});
