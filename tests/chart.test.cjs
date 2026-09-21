const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

const browser = {};
runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/chart.js'), 'utf8'), {window: browser});
const start = 1700000000;

function chart(timeframeMs = 300000) {
  // Exercise the chart's real data/window methods without an SVG renderer.
  return Object.assign(Object.create(browser.LiveChart.prototype), {
    timeframeMs, points: [], fills: [], draw() {}, follow() {},
  });
}

function domain(view) { return Array.from(view.timeDomain()); }

test('five-minute window rolls forward instead of expanding from the first tick', () => {
  const view = chart();
  view.add(start, 100);
  assert.deepEqual(domain(view), [start * 1000 - 300000, start * 1000]);
  view.add(start + 360, 101);
  assert.deepEqual(domain(view), [(start + 60) * 1000, (start + 360) * 1000]);
  view.add(start + 361, 102);
  assert.deepEqual(domain(view), [(start + 61) * 1000, (start + 361) * 1000]);
});

test('frequent ticks retain a history rather than continually replacing one point', () => {
  const view = chart();
  for (let i = 0; i <= 240; i++) view.add(start + i / 2, 100 + i);
  assert.equal(view.points.length, 121);
  assert.equal(view.points[0].time, (start + .5) * 1000);
  assert.equal(view.points.at(-1).time, (start + 120) * 1000);
});

test('an hour stays available when switching from a shorter view, with bounded storage', () => {
  const view = chart();
  for (let i = 0; i <= 7200; i++) view.add(start + i, 100 + i % 10);
  assert.ok(view.points.length <= 3602);
  view.setTimeframe(3600000);
  const [left, right] = domain(view);
  assert.equal(right - left, 3600000);
  assert.equal(left, (start + 3600) * 1000);
  assert.ok(view.points[0].time <= left);
  assert.ok(view.points[1].time >= left);
});

test('every offered timeframe keeps its duration as new ticks arrive', () => {
  const view = chart();
  view.add(start, 100);
  let time = start + 4000;
  for (const minutes of [1, 5, 10, 15, 30, 60]) {
    view.setTimeframe(minutes * 60000);
    view.add(time++, 101);
    const previous = domain(view);
    view.add(time++, 102);
    const current = domain(view);
    assert.equal(current[1] - current[0], minutes * 60000);
    assert.equal(current[0] - previous[0], 1000);
    assert.equal(current[1] - previous[1], 1000);
  }
});

test('out-of-order ticks cannot rewind the window or overwrite the latest value', () => {
  const view = chart();
  view.add(start + 10, 100);
  view.add(start, 999);
  assert.equal(view.points.length, 1);
  assert.equal(view.points[0].value, 100);
  assert.equal(domain(view)[1], (start + 10) * 1000);
});

test('changing markets clears observations but retains the selected timeframe', () => {
  const view = chart();
  view.setTimeframe(600000);
  view.add(start, 100);
  view.fills.push({id: 1});
  view.clear();
  assert.equal(view.points.length, 0);
  assert.equal(view.fills.length, 0);
  view.add(start + 20, 200);
  assert.equal(domain(view)[1] - domain(view)[0], 600000);
});

test('the separate session-equity view keeps its existing non-windowed behavior', () => {
  const view = chart(null);
  view.add(start, 1000);
  view.add(start + 600, 1100);
  assert.equal(domain(view)[0], start * 1000);
  assert.ok(domain(view)[1] > (start + 600) * 1000);
  for (let i = 601; i < 3000; i++) view.add(start + i, 1100);
  assert.equal(view.points.length, 1800);
});
