const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

class Element {
  constructor() { this.children = []; this.textContent = ''; this.classList = {toggle() {}}; }
  set textContent(value) { this.content = value; this.children = []; }
  get textContent() { return this.content; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  get text() { return this.textContent + this.children.map(child => child.text).join(' '); }
}
const window = {};
runInNewContext(readFileSync('kairos/static/review.js', 'utf8'), {window, document: {createElement: () => new Element()}});
function fixture() {
  const nodes = new Map(), root = {querySelector(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); }};
  const view = new window.StrategyReview(root);
  const state = {exchange: 'alpaca', running: false, settings: {strategy:'dca', pair:'alpaca:BTC/USD', product:'spot', daily_loss:'12.50'},
    review: {generated_at:1800000000, run_id:'test-run', data:{}, entry:{}, costs:{},
      orders:{records:2,filled:1,partial:1,working:0,terminal_unfilled:1}, holdings:{BTC:'0.0001'}, working_orders:0,
      program:{id:'test-program', status:'complete', claimed_slots:7, missed_slots:2, message:'Completed with unfilled quantity', orders:{records:2,filled:1,partial:1,working:0,terminal_unfilled:1}, turnover_with_fee_reserve:'9.99', budget:'70', unspent_allowance:'60.01', markets:{}}}};
  return {view,state,text:id=>root.querySelector(`#${id}`).text};
}

test('Review exposes completion with partial fills and retained inventory, without treating cash allowance as spendable', () => {
  const {view,state,text} = fixture(); view.render(state,true);
  assert.match(text('review-program'), /70 \/ 60.01/);
  assert.match(text('review-program'), /2 \/ 1 \/ 1/);
  assert.match(text('review-exposure-note'), /Paused or complete does not mean flat/);
  assert.match(text('review-exposure-note'), /do not create automatic sell plans/);
  assert.match(text('review-costs'), /posted fees are account-wide/);
  assert.match(text('review-readiness'), /Not required; fresh quotes/);
});

test('Review preserves real zero, missing diagnostics, disconnected state and renders unsafe strings as text', () => {
  const {view,state,text} = fixture();
  state.review.costs.spread_bps = '0';
  state.review.entry.entry_blockers = ['<img src=x onerror=alert(1)>'];
  const before = JSON.stringify(state); view.render(state,false);
  assert.equal(JSON.stringify(state), before);
  assert.match(text('review-status'), /Disconnected · cached state only/);
  assert.match(text('review-costs'), /Observed spread · bps 0/);
  assert.match(text('review-costs'), /Unavailable/);
  assert.match(text('review-blockers'), /<img src=x onerror=alert\(1\)>/);
  delete state.review; view.render(state,false);
  assert.match(text('review-holdings'), /Unavailable/);
  assert.equal(text('review-blockers'), '');
});
