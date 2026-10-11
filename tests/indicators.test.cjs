const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');
const browser = {};
runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/indicators.js'), 'utf8'), {window: browser});
const {calculate, normalize} = browser.ChartIndicators;
const plain = value => JSON.parse(JSON.stringify(value));
const points = closes => closes.map((value, i) => ({time: 1700000000000 + i * 60000, value}));
const close = (actual, expected, epsilon = 1e-9) => assert.ok(Math.abs(actual - expected) < epsilon, `${actual} != ${expected}`);

test('preferences accept only known booleans; volume is the sole default', () => {
  const defaults = {volume: true, macd: false, bollinger: false, rsi: false, sma: false};
  assert.deepEqual(plain(normalize(null)), defaults);
  assert.deepEqual(plain(normalize({volume: false, macd: true, rsi: 'true', sma: 1, strategy: 'maker'})), {...defaults, volume: false, macd: true});
  assert.deepEqual(plain(normalize(JSON.parse('{"__proto__":{"rsi":true}}'))), defaults);
});

test('studies remain unavailable until their own warm-up completes', () => {
  const rows = calculate(points(Array.from({length: 40}, (_, i) => i + 1)));
  for (const [key, first] of [['sma8', 7], ['sma21', 20], ['middle', 19], ['upper', 19], ['lower', 19], ['rsi', 14], ['macd', 25], ['signal', 33], ['histogram', 33]]) {
    assert.ok(rows.slice(0, first).every(row => row[key] === null), key);
    assert.ok(Number.isFinite(rows[first][key]), key);
  }
  assert.equal(calculate([]).length, 0);
});

test('SMA and Bollinger use close means and population standard deviation', () => {
  const rows = calculate(points(Array.from({length: 40}, (_, i) => i + 1)));
  close(rows[7].sma8, 4.5);
  close(rows[20].sma21, 11);
  close(rows[19].middle, 10.5);
  close(rows[19].upper, 10.5 + 2 * Math.sqrt(33.25));
  close(rows[19].lower, 10.5 - 2 * Math.sqrt(33.25));
  close(rows[39].middle, 30.5);
});

test('SMA-seeded MACD of a unit ramp is seven; its seeded signal matches', () => {
  const rows = calculate(points(Array.from({length: 100}, (_, i) => i + 1)));
  for (const row of rows.slice(25)) close(row.macd, 7);
  for (const row of rows.slice(33)) { close(row.signal, 7); close(row.histogram, 0); }
});

test('Wilder RSI matches the standard worked close sequence', () => {
  const rows = calculate(points([44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00]));
  close(rows[14].rsi, 70.46413502109705);
  close(rows[15].rsi, 66.24961855355505);
});

test('flat, rising and falling series have finite RSI boundary values', () => {
  for (const [closes, expected] of [
    [Array(50).fill(100), 50],
    [Array.from({length: 50}, (_, i) => 100 + i), 100],
    [Array.from({length: 50}, (_, i) => 100 - i), 0],
  ]) {
    const rows = calculate(points(closes));
    for (const row of rows.slice(14)) close(row.rsi, expected);
    for (const row of rows) for (const value of Object.values(row)) assert.ok(value === null || Number.isFinite(value));
  }
});

test('periods count supplied bars across gaps without inventing source rows', () => {
  const source = points(Array.from({length: 40}, (_, i) => 100 + Math.sin(i)));
  const regular = calculate(source);
  const gapped = source.map((p, i) => ({...p, time: p.time + (i >= 20 ? 3600000 : 0)}));
  const before = JSON.stringify(gapped);
  const rows = calculate(gapped);
  assert.equal(rows.length, source.length);
  assert.equal(JSON.stringify(gapped), before);
  rows.forEach((row, i) => {
    assert.equal(row.time, gapped[i].time);
    for (const key of ['macd', 'signal', 'rsi', 'upper', 'sma8']) assert.equal(row[key], regular[i][key]);
  });
});

test('indicator calculation is causal: future candles cannot change earlier results', () => {
  const source = points(Array.from({length: 80}, (_, i) => 100 + i / 5 + Math.sin(i / 3) * 7));
  assert.deepEqual(plain(calculate(source).slice(0, 50)), plain(calculate(source.slice(0, 50))));
});
