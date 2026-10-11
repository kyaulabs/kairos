const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

const d3 = require('../kairos/static/vendor/d3.v7.9.0.min.js');
const browser = {};
runInNewContext(['indicators.js', 'chart.js'].map(file => readFileSync(path.join(__dirname, '../kairos/static', file), 'utf8')).join('\n'), {window: browser, d3});
const start = 1700000040;

function chart(intervalMs = 60000) {
  // Exercise the real data/viewport methods without an SVG renderer.
  return Object.assign(Object.create(browser.LiveChart.prototype), {
    intervalMs, intervals: [1, 5, 15, 30, 60, 240, 1440], anchor: null, points: [], fills: [],
    indicatorPoints: [], indicators: browser.ChartIndicators.normalize(null), node: {style: {setProperty() {}}},
    draw() {}, follow() { this.anchor = null; },
  });
}
function candle(time = start, close = '102') {
  return {time, open: '100', high: '105', low: '95', close, volume: '12.5'};
}
function domain(view) { return Array.from(view.timeDomain()); }

test('forming candle updates replace the bar; the next candle advances the live view', () => {
  const view = chart();
  view.setCandles([candle()]);
  const firstDomain = domain(view);
  view.setCandles([{...candle(start, '103'), volume: '15.75'}]);
  assert.equal(view.points.length, 1);
  assert.equal(view.points[0].value, 103);
  assert.equal(view.points[0].volume, 15.75);
  assert.deepEqual(domain(view), firstDomain);
  view.setCandles([candle(start, '103'), candle(start + 60)]);
  assert.equal(view.points.length, 2);
  assert.equal(domain(view)[0] - firstDomain[0], 60000);
  assert.equal(domain(view)[1] - domain(view)[0], 90 * 60000);
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
    ...['NaN', Infinity, '-1', undefined, null, ''].map(volume => [{...candle(), volume}]),
    [candle(), candle()], [candle(start + 60), candle()],
  ]) {
    assert.throws(() => view.setCandles(rows), /Invalid candle data/);
    assert.equal(view.points, previous);
  }
});

test('zero-volume candles remain aligned, and live view leaves room for the price badge', () => {
  const view = chart();
  view.setCandles([{...candle(), volume: '0'}]);
  assert.equal(view.points[0].volume, 0);
  assert.equal(domain(view)[1] - view.points[0].time, 5 * view.intervalMs);
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
    assert.equal(domain(view)[1] - domain(view)[0], 90 * minutes * 60000);
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

test('indicator toggles preserve data, fills, interval and the manually anchored viewport', () => {
  const view = chart();
  view.setCandles(Array.from({length: 60}, (_, i) => candle(start + i * 60)));
  view.anchor = start * 1000;
  view.transform = {k: 3, x: -120};
  view.fills.push({id: 'owned', time: start * 1000, value: 100});
  const before = {points: view.points, studies: view.indicatorPoints, fills: view.fills, transform: view.transform, domain: domain(view)};
  view.setIndicators({volume: false, macd: true, bollinger: true, rsi: true, sma: true});
  assert.equal(view.points, before.points);
  assert.equal(view.indicatorPoints, before.studies);
  assert.equal(view.fills, before.fills);
  assert.equal(view.transform, before.transform);
  assert.deepEqual(domain(view), before.domain);
  assert.equal(view.intervalMs, 60000);
});

test('forming updates recompute studies without altering earlier values or double-counting', () => {
  const view = chart();
  const rows = Array.from({length: 60}, (_, i) => candle(start + i * 60, String(100 + i % 4)));
  view.setCandles(rows);
  const before = JSON.stringify(view.indicatorPoints.slice(0, -1));
  const last = view.indicatorPoints.at(-1).macd;
  view.setCandles([...rows.slice(0, -1), candle(start + 59 * 60, '105')]);
  assert.equal(view.indicatorPoints.length, 60);
  assert.equal(JSON.stringify(view.indicatorPoints.slice(0, -1)), before);
  assert.notEqual(view.indicatorPoints.at(-1).macd, last);
  view.clear();
  assert.equal(view.indicatorPoints.length, 0);
  view.setCandles(rows);
  view.setCandleInterval(15);
  assert.equal(view.indicatorPoints.length, 0);
});

function navigable() {
  const view = chart();
  delete view.follow;
  view.node.__zoom = view.transform = d3.zoomIdentity;
  view.svg = d3.select(view.node);
  view.buttons = ['zoom-out', 'zoom-in', 'pan-left', 'pan-right', 'follow'].map(chartAction => ({dataset: {chartAction}}));
  view.navigation = {querySelectorAll: () => view.buttons};
  view.zoom = d3.zoom().extent([[0, 0], [900, 300]])
    .translateExtent([[-6300, 0], [900, 300]]).scaleExtent([1/12, 40])
    .on('zoom', event => { view.transform = event.transform; view.draw(); });
  view.draw = () => {
    view.updateNavigation();
    if (view.points.length) view.x = view.transform.rescaleX(d3.scaleTime().domain(view.timeDomain()).range([0, 900]));
  };
  view.setCandles(Array.from({length: 720}, (_, i) => candle(start + i * 60)));
  return view;
}
function visibleDomain(view) { return Array.from(view.x.domain(), Number); }

test('toolbar pan directions move a quarter viewport and freeze history until reset', () => {
  const view = navigable(), initial = visibleDomain(view);
  assert.equal(view.buttons.at(-1).hidden, true);
  view.navigate('pan-left');
  const older = visibleDomain(view);
  assert.equal(initial[0] - older[0], (initial[1] - initial[0]) / 4);
  assert.equal(view.buttons.at(-1).hidden, false);
  view.setCandles([...view.points.slice(1).map(p => candle(p.time / 1000)), candle(start + 720 * 60)]);
  assert.deepEqual(visibleDomain(view), older);
  view.navigate('pan-right');
  assert.deepEqual(visibleDomain(view), initial);
  assert.equal(view.buttons.at(-1).hidden, false, 'at the latest edge is still a manually anchored view');
  view.navigate('follow');
  assert.equal(view.anchor, null);
  assert.equal(view.transform.k, 1);
  assert.equal(view.transform.x, 0);
  assert.equal(view.buttons.at(-1).hidden, true);
  assert.equal(visibleDomain(view)[0] - initial[0], 60000);
});

test('toolbar zoom and pan use D3 bounds without altering candles, studies or fills', () => {
  const view = navigable(), initial = visibleDomain(view);
  view.fills.push({id: 'original'});
  const {points, indicatorPoints, fills} = view;
  view.navigate('zoom-in');
  assert.equal(view.transform.k, 1.25);
  const zoomed = visibleDomain(view);
  assert.equal(zoomed[1] - zoomed[0], (initial[1] - initial[0]) / 1.25);
  view.navigate('pan-left');
  assert.equal(zoomed[0] - visibleDomain(view)[0], (zoomed[1] - zoomed[0]) / 4);
  for (let i = 0; i < 50; i++) view.navigate('zoom-in');
  assert.equal(view.transform.k, 40);
  for (let i = 0; i < 70; i++) view.navigate('zoom-out');
  assert.equal(view.transform.k, 1/12);
  view.navigate('follow');
  for (let i = 0; i < 60; i++) view.navigate('pan-left');
  const oldest = visibleDomain(view);
  view.navigate('pan-left');
  assert.deepEqual(visibleDomain(view), oldest);
  for (let i = 0; i < 60; i++) view.navigate('pan-right');
  assert.deepEqual(visibleDomain(view), initial);
  assert.equal(view.points, points);
  assert.equal(view.indicatorPoints, indicatorPoints);
  assert.equal(view.fills, fills);
});

test('empty charts disable navigation and market/interval resets restore live state', () => {
  const view = navigable();
  view.navigate('pan-left');
  view.setCandleInterval(15);
  assert.equal(view.buttons.at(-1).hidden, true);
  assert.ok(view.buttons.slice(0, -1).every(b => b.disabled));
  view.navigate('zoom-in');
  assert.equal(view.anchor, null);
  assert.equal(view.transform.k, 1);
  view.setCandles([candle()]);
  assert.ok(view.buttons.slice(0, -1).every(b => !b.disabled));
  view.navigate('pan-left');
  view.clear();
  assert.equal(view.buttons.at(-1).hidden, true);
  assert.equal(view.anchor, null);
  assert.equal(view.intervalMs, 15 * 60000);
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
