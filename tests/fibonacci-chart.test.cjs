const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');
const d3 = require('../kairos/static/vendor/d3.v7.9.0.min.js');
const browser = {};
runInNewContext(['indicators.js','chart.js'].map(file => readFileSync(path.join(__dirname,'../kairos/static',file),'utf8')).join('\n'), {window:browser,d3});

function fixture() {
  const settings={strategy:'fib-meta',product:'spot',pair:'okx-demo:BTC-USDC:USDC',fib_context_minutes:240,meta_structure_minutes:60,fib_confirmation_minutes:15};
  const scope={exchange:'okx-demo',mode:'paper',pair:settings.pair,strategy:'fib-meta',context_minutes:240,structure_minutes:60,confirmation_minutes:15};
  const data={status:'available',scope,kind:'retracement',family:'fib-ema',phase:'armed',running:true,known_at:1700020000,observed_at:1700020900,
    low:{at:1700000000,price:'100'},high:{at:1700007200,price:'120'},
    levels:[{ratio:'0',price:'120'},{ratio:'0.382',price:'112.36'},{ratio:'0.618',price:'107.64'},{ratio:'1',price:'100'},{ratio:'1.618',price:'87.64'}]};
  const state={exchange:scope.exchange,mode:scope.mode,settings,meta:{fibonacci:data}};
  const view=Object.assign(Object.create(browser.LiveChart.prototype),{intervalMs:900000,intervals:[5,15,60],node:{style:{setProperty(){}}},points:[{time:1700020900000,value:110}],indicatorPoints:[],indicators:{},fills:[{id:'existing'}],anchor:1700020000000,transform:{k:2,x:100},draw(){},follow(){this.anchor=null;}});
  const market={id:settings.pair,exchange:scope.exchange};
  return {view,state,data,market};
}

test('strategy Fibonacci automatically accepts matching data without changing prices, fills, indicators or viewport',()=>{
  const {view,state,market}=fixture();
  const {points,fills,indicators,transform,anchor}=view, before=JSON.stringify(state);
  view.setFibonacci(state,market);
  assert.equal(view.fibonacci.kind,'retracement');
  assert.equal(view.fibonacci.levels.find(r=>r.ratio==='0.618').value,107.64);
  assert.equal(view.points,points);assert.equal(view.fills,fills);assert.equal(view.indicators,indicators);assert.equal(view.transform,transform);assert.equal(view.anchor,anchor);
  assert.equal(JSON.stringify(state),before);
  assert.equal(view.fibonacciValues([0,view.fibonacci.knownTime-1]).length,0,'no levels before they became known');
  assert.equal(view.fibonacciValues([0,view.fibonacci.knownTime]).length,5);
});

test('other markets, exchanges, modes, products, strategies and clock profiles clear the overlay',()=>{
  const {view,state,market}=fixture();
  for(const [next,chart] of [
    [state,{...market,id:'another-market'}],[state,{...market,exchange:'kraken'}],
    [{...state,exchange:'okx'},market],[{...state,mode:'trading'},market],
    ...Object.entries({strategy:'htf',product:'margin',fib_context_minutes:720,meta_structure_minutes:240,fib_confirmation_minutes:5}).map(([key,value])=>[{...state,settings:{...state.settings,[key]:value}},market]),
  ]) {
    view.setFibonacci(state,market);assert.ok(view.fibonacci);
    view.setFibonacci(next,chart);assert.equal(view.fibonacci,null);
  }
  view.setFibonacci(null,null);assert.equal(view.fibonacci,null);
});

test('extension orientation is explicit and cannot silently reuse a retracement or three-point projection',()=>{
  const {view,state,data,market}=fixture();
  data.kind='extension';data.family='fib-extension';data.phase='owned';
  data.levels=[{ratio:'0',price:'100'},{ratio:'1',price:'120'},{ratio:'1.618',price:'132.36'},{ratio:'4.236',price:'184.72'}];
  view.setFibonacci(state,market);
  assert.equal(view.fibonacci.kind,'extension');
  assert.equal(view.fibonacci.levels.at(-1).value,184.72);
  data.levels[2].price='140';view.setFibonacci(state,market);
  assert.equal(view.fibonacci,null,'the displayed formula must match the actual two-point parent');
});

test('invalid geometry, duplicate levels and future knowledge fail closed rather than leave a stale overlay',()=>{
  for(const change of [
    {known_at:1700021000},{low:{at:1700000000,price:'NaN'}},{levels:[null]},
    {levels:[{ratio:'0',price:'120'},{ratio:'1',price:'-1'}]},
    {levels:[{ratio:'0',price:'120'},{ratio:'0',price:'120'},{ratio:'1',price:'100'}]},
    {levels:[{ratio:'0',price:'120'},{ratio:'1',price:'100'},{ratio:'0.7',price:'106'}]},
    {kind:'extension'}, {family:'unrelated'}, {phase:'predicted'},
  ]) {
    const {view,state,data,market}=fixture();view.setFibonacci(state,market);assert.ok(view.fibonacci);
    state.meta.fibonacci={...data,...change};view.setFibonacci(state,market);assert.equal(view.fibonacci,null);
  }
});

test('waiting and paused captions distinguish candidates, references and saved geometry without claiming predictions',()=>{
  const {view,state,data,market}=fixture(), status={hidden:true,textContent:''};
  view.bindFibonacciStatus(status);assert.equal(status.hidden,true);
  view.setFibonacci(state,market);assert.equal(status.hidden,false);
  assert.ok(status.textContent.includes('armed candidate, not an entry'));
  assert.ok(status.textContent.includes('not a forecast or TP orders'));
  data.running=false;data.phase='owned';view.setFibonacci(state,market);
  assert.ok(status.textContent.includes('paused snapshot: saved trade geometry'));
  data.running=true;data.phase='entry-plan';view.setFibonacci(state,market);
  assert.ok(status.textContent.includes('entry plan, fill confirmation pending'));
  state.meta.fibonacci={scope:data.scope,status:'waiting'};view.setFibonacci(state,market);
  assert.ok(status.textContent.includes('No guessed anchors'));
  assert.equal(view.fibonacciValues([0,Date.now()]).length,0);
});

test('chart intervals preserve strategy geometry while market resets and equity views remove it',()=>{
  const {view,state,market}=fixture(), status={hidden:true,textContent:''};
  view.bindFibonacciStatus(status);view.setFibonacci(state,market);const geometry=view.fibonacci;
  view.setCandleInterval(5);assert.equal(view.fibonacci,geometry,'chart interval is not the strategy structure frame');
  view.clear();assert.equal(view.fibonacci,null);assert.equal(status.hidden,true);assert.equal(status.textContent,'');
  view.intervalMs=null;view.setFibonacci(state,market);assert.equal(view.fibonacci,null,'never draw on the equity chart');
});
