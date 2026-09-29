const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

function fixture(read, hidden = false) {
  const timers = new Map(), handlers = new Map(), window = {};
  let id = 0;
  const document = {hidden, addEventListener: (kind, fn) => handlers.set(kind, fn)};
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/polling.js'), 'utf8'), {
    document, window, AbortController, console,
    setTimeout: (callback, delay) => { timers.set(++id, {callback, delay}); return id; },
    clearTimeout: key => timers.delete(key),
  });
  const poller = new window.VisiblePoller(read, 5000);
  return {poller, timers, visibility(hidden) { document.hidden = hidden; handlers.get('visibilitychange')(); },
    fire() { const [key, timer] = timers.entries().next().value; timers.delete(key); timer.callback(); }};
}
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

test('hidden tabs issue no polls and becoming visible refreshes immediately', async () => {
  let calls = 0;
  const f = fixture(async () => { calls++; }, true);
  f.poller.start();
  assert.equal(calls, 0);
  assert.equal(f.timers.size, 0);
  f.visibility(false);
  await settle();
  assert.equal(calls, 1);
  assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].delay, 5000);
  f.visibility(true);
  assert.equal(f.timers.size, 0);
  f.visibility(false);
  await settle();
  assert.equal(calls, 2);
});

test('hiding aborts an in-flight poll and a late response cannot restart hidden polling', async () => {
  let finish, signal, calls = 0;
  const f = fixture(s => { calls++; signal = s; return new Promise(resolve => { finish = resolve; }); });
  f.poller.start();
  f.visibility(true);
  assert.equal(signal.aborted, true);
  finish(); await settle();
  assert.equal(f.timers.size, 0);
  f.visibility(false);
  assert.equal(calls, 2);
  f.poller.stop(); finish(); await settle();
  assert.equal(f.timers.size, 0);
});

test('rapid changes cancel obsolete reads without overlapping or duplicating timers', async () => {
  const signals = [], finishes = [];
  const f = fixture(signal => { signals.push(signal); return new Promise(resolve => finishes.push(resolve)); });
  f.poller.start();
  f.poller.refresh(); f.poller.refresh();
  assert.equal(signals.length, 1);
  assert.equal(signals[0].aborted, true);
  finishes[0](); await settle();
  assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].delay, 0);
  f.fire();
  assert.equal(signals.length, 2);
  finishes[1](); await settle();
  assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].delay, 5000);
  f.poller.stop();
  f.visibility(true); f.visibility(false);
  assert.equal(signals.length, 2);
  assert.equal(f.timers.size, 0);
});
