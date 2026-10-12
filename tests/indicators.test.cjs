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
  for (const [key, first] of [['smaFast', 7], ['smaSlow', 20], ['middle', 19], ['upper', 19], ['lower', 19], ['rsi', 14], ['macd', 25], ['signal', 33], ['histogram', 33]]) {
    assert.ok(rows.slice(0, first).every(row => row[key] === null), key);
    assert.ok(Number.isFinite(rows[first][key]), key);
  }
  assert.equal(calculate([]).length, 0);
});

test('SMA and Bollinger use close means and population standard deviation', () => {
  const rows = calculate(points(Array.from({length: 40}, (_, i) => i + 1)));
  close(rows[7].smaFast, 4.5);
  close(rows[20].smaSlow, 11);
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
    for (const key of ['macd', 'signal', 'rsi', 'upper', 'smaFast']) assert.equal(row[key], regular[i][key]);
  });
});

test('configuration validates persisted inputs and appearance without accepting arbitrary CSS', () => {
  const {configuration, preferences, visible} = browser.ChartIndicators;
  const base = configuration(null);
  const bad = configuration({macd:{fast:30,slow:10,signal:Infinity,line:'url(https://example.org)'}, rsi:{period:'7',width:99}, bollinger:{period:0,deviation:NaN,style:'evil'}, sma:{fast:1.5}});
  assert.deepEqual(plain(bad), plain(base));
  const old = preferences({volume:false,bollinger:true});
  assert.equal(visible(old).bollinger, true);
  const hidden = preferences({...old,hidden:{bollinger:true},settings:{bollinger:{period:5,deviation:3,line:'warning',style:'dotted',width:2}},collapsed:true});
  assert.equal(hidden.bollinger, true);
  assert.equal(visible(hidden).bollinger, false);
  assert.equal(hidden.settings.bollinger.period, 5);
  assert.equal(hidden.settings.bollinger.line, 'warning');
  assert.equal(hidden.collapsed, true);
  assert.equal(preferences({...hidden,bollinger:false}).hidden.bollinger, false);
  assert.deepEqual(plain(preferences(JSON.parse('{"__proto__":{"collapsed":true}}'))), plain(preferences(null)));
});

test('custom periods change calculations and warm-up while appearance leaves values intact', () => {
  const {configuration, warmup} = browser.ChartIndicators;
  const c = configuration({sma:{fast:2,slow:4},bollinger:{period:3,deviation:1.5},macd:{fast:2,slow:4,signal:2},rsi:{period:2}});
  const source = points([1,2,3,4,5,6,7,8]);
  const before = JSON.stringify(source), rows = calculate(source,c);
  close(rows[1].smaFast,1.5); close(rows[3].smaSlow,2.5);
  close(rows[2].middle,2); close(rows[2].upper,2+1.5*Math.sqrt(2/3));
  assert.equal(rows[1].rsi,null); close(rows[2].rsi,100);
  assert.equal(rows[2].macd,null); close(rows[3].macd,1);
  assert.equal(rows[3].signal,null); close(rows[4].signal,1); close(rows[4].histogram,0);
  assert.deepEqual(plain(warmup(c)),{sma:'2 / 4',bollinger:3,rsi:3,macd:5});
  assert.deepEqual(plain(rows),plain(calculate(source,{...c,bollinger:{...c.bollinger,line:'danger',style:'solid',width:3}})));
  assert.deepEqual(plain(rows.slice(0,5)),plain(calculate(source.slice(0,5),c)));
  assert.equal(JSON.stringify(source),before);
});

test('indicator calculation is causal: future candles cannot change earlier results', () => {
  const source = points(Array.from({length: 80}, (_, i) => 100 + i / 5 + Math.sin(i / 3) * 7));
  assert.deepEqual(plain(calculate(source).slice(0, 50)), plain(calculate(source.slice(0, 50))));
});
