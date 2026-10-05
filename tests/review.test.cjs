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

test('Review exposes scheduled recovery, session-only uptime and distinct trial candidates without actions', () => {
  const {view,state,text} = fixture();
  state.settings.htf_policy = 'multibar-v2';
  state.operations = {status:'retry-wait',since:1800000000,last_evaluation_at:null,session_seconds:{running:20,'waiting-data':5},notifications:{configured:false,status:'disabled'}};
  state.scheduled_recovery = {status:'waiting',attempts:0,max_attempts:5,next_at:1800000300,reason:'source inactive'};
  state.decision_summary = {distinct_windows:3,distinct_candidates:1,assessments:100};
  state.decision = {state:{structural_gates:{closed_recovery:false}}};
  state.paper_qualification = {status:'complete'};
  const before = JSON.stringify(state); view.render(state,true);
  assert.equal(JSON.stringify(state),before);
  assert.match(text('review-readiness'),/waiting · 0\/5/);
  assert.match(text('review-readiness'),/running · service-session seconds 20/);
  assert.match(text('review-readiness'),/Discord alerts disabled/);
  assert.match(text('review-funnel'),/windows \/ v2 candidates 3 \/ 1/);
  assert.match(text('review-funnel'),/Economics inconclusive below 30/);
  assert.match(text('review-blockers'),/structural gate failed: closed_recovery/);
});

test('Review separates unclassified settlement debits from actual fees and entry permission', () => {
  const {view,state,text} = fixture();
  state.fee_settlement = {status:'pending',overdue:true,checked_at:1800000000,deadline:1800172800,debits:{BTC:'4.33E-7',USD:'0.074000458896'},periods:{'2027-01-15':{status:'pending',caps:{BTC:'7.21E-7'},observed:{BTC:'4.33E-7'},posted:{}}}};
  state.automatic_account_checks = {status:'fees pending',checked_at:1800000000,next_at:1800000060};
  state.start_block_reason = 'Complete the separately authorized paper round trip before trial Start.';
  const before = JSON.stringify(state); view.render(state,true);
  assert.equal(JSON.stringify(state),before);
  assert.match(text('review-readiness'), /BTC unclassified debit 4.33E-7/);
  assert.match(text('review-readiness'), /conservative pending-fee reserves/);
  assert.match(text('review-readiness'), /No permission while paused/);
  assert.match(text('review-readiness'), /Risk equity\/P&L/);
  assert.match(text('review-readiness'), /Stop overrides exits/);
  assert.match(text('review-readiness'), /not confirmed fees/);
  assert.match(text('review-readiness'), /Fee period 2027-01-15 pending/);
  assert.match(text('review-readiness'), /4.33E-7 \/ unposted \/ 7.21E-7/);
  assert.match(text('review-readiness'), /Automatic account checks fees pending/);
  assert.match(text('review-readiness'), /Start \/ Restart unavailable Complete the separately authorized/);
  assert.match(text('review-readiness'), /Age alone does not halt execution/);
  assert.match(text('review-readiness'), /do not block qualification or explicit trial Start/);
  assert.match(text('review-readiness'), /Cannot submit\/cancel orders/);
  state.fee_settlement.status = 'settled'; view.render(state,true);
  assert.match(text('review-readiness'), /qualification still apply/);
});

test('Review labels native bar readiness without claiming fabricated minute coverage', () => {
  const {view,state,text} = fixture();
  state.settings.strategy = 'htf';
  state.review.data = {history_policy:'alpaca-us-native-bars-v1',bar_minutes:60,required_native_bars:30,consecutive_native_bars:30,native_bar_shortfall:0,history_revision:'recorded-hash'};
  view.render(state,true);
  assert.match(text('review-readiness'), /Completed native 60-minute bars 30 \/ 30/);
  assert.match(text('review-readiness'), /native bars needed 0/);
  assert.doesNotMatch(text('review-readiness'), /Confirmed minute suffix/);
  state.review.data.consecutive_native_bars = null;
  state.review.data.native_bar_shortfall = null;
  view.render(state,false);
  assert.match(text('review-readiness'), /bars Unavailable \/ 30/);
  assert.match(text('review-status'), /cached state only/);
});

test('Review distinguishes account-read recovery from market data and preserves completed episode diagnostics', () => {
  const {view,state,text} = fixture();
  state.account_reads = {last_success_at:null,last_failure:{method:'GET',endpoint:'/v2/orders',reason:'timeout',at:1800000000},recovery:{status:'waiting',since:1800000000,attempts:0,max_attempts:3,timeout_seconds:120,duration_seconds:12,next_retry_at:1800000015}};
  const before = JSON.stringify(state); view.render(state,true);
  assert.equal(JSON.stringify(state), before);
  assert.match(text('review-readiness'), /Last verified account read Unavailable/);
  assert.match(text('review-readiness'), /GET \/v2\/orders · timeout/);
  assert.match(text('review-readiness'), /0 \/ 3 recovery attempts/);
  assert.match(text('review-readiness'), /Orders and local exits blocked/);
  state.account_reads.recovery.status = 'restored';
  state.account_reads.recovery.attempts = 1;
  state.account_reads.recovery.ended_at = 1800000030;
  state.account_reads.recovery.duration_seconds = 30;
  view.render(state,false);
  assert.match(text('review-readiness'), /restored · 1 \/ 3/);
  assert.match(text('review-readiness'), /duration · seconds 30/);
  assert.match(text('review-readiness'), /No order write was retried/);
  assert.match(text('review-status'), /cached state only/);
});

test('Review separates market age, receipt latency, quota exhaustion and bounded waiting', () => {
  const {view,state,text} = fixture();
  state.market_data = {status:'reconnecting', books:[{pair:'alpaca:BTC/USD', source:'REST', fresh:false, market_age_seconds:48, market_at:1800000000, received_at:1800000048}], requests:{admissions_last_minute:92,budget_per_minute:150,queued:2,last_response:{method:'GET',endpoint:'/v1beta3/crypto/us/latest/orderbooks',http_status:200,latency_seconds:.2},quotas:{data:{remaining:0,limit:200,observed_at:1800000048,reset:1800000078,retry_in_seconds:30},trading:{retry_in_seconds:0}}}};
  state.market_wait = {since:1800000048,timeout_seconds:300};
  view.render(state,true);
  assert.match(text('review-readiness'), /REST \/ no/);
  assert.match(text('review-readiness'), /seconds 0.2/);
  assert.match(text('review-readiness'), /remaining \/ limit 0 \/ 200/);
  assert.match(text('review-readiness'), /300s maximum/);
  assert.match(text('review-readiness'), /local exits may be unavailable/);
});
