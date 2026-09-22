const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const path = require('node:path');
const browser = {};
runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/assessment.js'), 'utf8'), {window:browser});
const View = browser.AssessmentView;

test('model weights preserve genuine zeros and never fabricate missing probabilities', () => {
  const values = View.weights({probabilities:{buy:0,hold:.8,sell:.2}});
  assert.equal(values[0].value,0);
  assert.equal(values[1].value,.8);
  for (const probabilities of [{buy:null,hold:.8,sell:.2},{buy:NaN,hold:1,sell:0},{buy:1,hold:1,sell:1},{buy:-.1,hold:.9,sell:.2},{}]) assert.equal(View.weights({probabilities}),null);
  assert.equal(View.weights({deterministic:true,probabilities:{buy:1,hold:0,sell:0}}),null);
});

test('assessments cannot masquerade as another market, mode, product or strategy', () => {
  const state={mode:'dry-run',settings:{pair:'XXBTZUSD',product:'spot',strategy:'htf'}};
  const decision={pair:'XXBTZUSD',mode:'dry-run',strategy:'htf',state:{product:'spot'}};
  assert.ok(View.matches(decision,state));
  for(const update of [{pair:'XETHZUSD'},{mode:'trading'},{strategy:'scalp'},{state:{product:'futures'}}]) assert.equal(View.matches({...decision,...update},state),false);
  assert.equal(View.matches(null,state),false);
});

test('missing metrics remain unavailable rather than becoming zero', () => {
  for(const value of [null,undefined,'',false,true,NaN,Infinity,'not a number']) assert.equal(View.number(value),null);
  assert.equal(View.number('0'),0);
  assert.equal(View.number('-2.5'),-2.5);
});

test('Bollinger position uses the assessed bands, not model scores or a configurable sigma', () => {
  const input={lower:'98',middle:'100',upper:'102',series:[{time:60,close:'100'}]};
  for (const [close,expected] of [[98,-1],[100,0],[102,1],[96,-2],[104,2]]) {
    assert.equal(View.bandPosition({...input,series:[{time:60,close}]}),expected);
  }
  for (const update of [{series:[]},{series:[{close:null}]},{series:[{close:'NaN'}]},{lower:'100',upper:'100'},{upper:'99'},{middle:null}]) {
    assert.equal(View.bandPosition({...input,...update}),null);
  }
});

// Minimal DOM surface for actual vendored D3 selections; browser checks cover geometry/fonts.
function documentFixture() {
  const doc = {documentElement:{namespaceURI:'http://www.w3.org/1999/xhtml'}};
  class Element {
    constructor(name, namespace) {
      this.name=name;this.namespaceURI=namespace;this.ownerDocument=doc;this.children=[];
      this.attributes=new Map();this.clientWidth=220;this.textContent='';
      this.style={setProperty(){},removeProperty(){}};
    }
    appendChild(child) { return this.insertBefore(child,null); }
    insertBefore(child,next) {
      if(child.parentNode)child.parentNode.removeChild(child);
      const index=next?this.children.indexOf(next):this.children.length;
      this.children.splice(index,0,child);child.parentNode=this;return child;
    }
    removeChild(child) { this.children.splice(this.children.indexOf(child),1);child.parentNode=null;return child; }
    setAttribute(key,value) { this.attributes.set(key,String(value)); }
    getAttribute(key) { return this.attributes.get(key) || null; }
    removeAttribute(key) { this.attributes.delete(key); }
    compareDocumentPosition() { return 4; }
    querySelectorAll(selector) {
      const nodes=this.children.flatMap(child=>[child,...child.querySelectorAll('*')]);
      return nodes.filter(node=>selector==='*' || (selector[0]==='.' ? (node.getAttribute('class')||'').split(' ').includes(selector.slice(1)) : node.name===selector));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  doc.createElementNS=(namespace,name)=>new Element(name,namespace);
  doc.createElement=name=>doc.createElementNS(doc.documentElement.namespaceURI,name);
  return doc.createElement('div');
}

function instrumentFixture() {
  const window={};
  runInNewContext(readFileSync(path.join(__dirname,'../kairos/static/assessment.js'),'utf8'),{
    window,d3:require('../kairos/static/vendor/d3.v7.9.0.min.js'),ResizeObserver:class {observe(){}},
  });
  const root=documentFixture(), view=new window.AssessmentView(root);
  return {root,view};
}

test('real D3 renders model, missing data, program and band instruments without invalid geometry or input mutations', () => {
  const {root,view}=instrumentFixture();
  const model={action:'buy',probabilities:{buy:.72,hold:.2,sell:.08},state:{eight_candle_return_bps:'195.4',round_trip_cost_bps:'100',trend:'rising',spread_bps:'10',inventory:'0'}};
  const state={mode:'dry-run',settings:{strategy:'htf',product:'spot',pair:'XXBTZUSD'}};
  const before=JSON.stringify({model,state});
  view.render(model,state);
  assert.equal(root.querySelectorAll('path').length,30);
  assert.match(root.querySelector('svg').getAttribute('aria-label'),/64.0 percentage points/);
  assert.equal(JSON.stringify({model,state}),before);
  view.render(null,state);
  assert.match(root.querySelector('svg').getAttribute('aria-label'),/No current model/);
  view.render(null,{...state,settings:{...state.settings,strategy:'twap',twap_slices:12} },[],{scheduled:true});
  assert.match(root.querySelector('svg').getAttribute('aria-label'),/Claimed slots/);
  const rule={deterministic:true,state:{window:30,candle_close_time:1800,lower:'99',middle:'100',upper:'101',series:[{time:0,close:'100'},{time:60,close:'98'},{time:120,close:'99.5'}]}};
  view.render(rule,{...state,settings:{...state.settings,strategy:'scalp'}});
  assert.match(root.querySelector('.range-gauge').getAttribute('aria-label'),/Not confidence or permission to trade/);
  assert.match(root.querySelector('.bollinger-instrument').getAttribute('aria-label'),/one-minute closing prices/);
  assert.equal(root.querySelector('.bollinger-instrument').getAttribute('viewBox'),'0 0 360 214');
  assert.equal(root.querySelectorAll('path').length,31);
  for(const node of root.querySelectorAll('*')) for(const value of node.attributes.values()) assert.doesNotMatch(value,/NaN|Infinity/);
  view.render({...rule,state:{...rule.state,lower:'100',middle:'100',upper:'100'}},{...state,settings:{...state.settings,strategy:'scalp'}});
  assert.ok(root.querySelectorAll('text').some(node=>node.textContent==='FLAT'));
  assert.equal(root.querySelector('.instrument-needle'),null);
});

test('mini gauge moves with assessed band position, caps the needle, and labels paused and saved data', () => {
  const {root,view}=instrumentFixture();
  const state={running:true,mode:'dry-run',settings:{strategy:'scalp',product:'spot',pair:'XXBTZUSD'}};
  const input={window:30,candle_close_time:120,lower:'98',middle:'100',upper:'102',z_score:'-2',series:[{time:0,close:'100'},{time:60,close:'98'}]};
  const decision={deterministic:true,action:'hold',state:input};
  const before=JSON.stringify({state,decision});
  view.render(decision,state);
  const lower=Number(root.querySelector('.instrument-needle').getAttribute('x2'));
  assert.ok(lower>180);
  assert.equal(root.querySelector('svg'),root.querySelector('.range-gauge'));
  assert.match(root.querySelector('.range-gauge').getAttribute('aria-label'),/Latest assessed window/);
  const plot=root.querySelector('.bollinger-instrument').querySelector('path').getAttribute('d');
  view.render({...decision,state:{...input,z_score:'2',series:[{time:0,close:'100'},{time:60,close:'102'}]}},state);
  const upper=Number(root.querySelector('.instrument-needle').getAttribute('x2'));
  assert.ok(upper<180);
  assert.notEqual(root.querySelector('.bollinger-instrument').querySelector('path').getAttribute('d'),plot);
  view.render({...decision,state:{...input,z_score:'4',series:[{time:0,close:'100'},{time:60,close:'104'}]}},state);
  assert.equal(Number(root.querySelector('.instrument-needle').getAttribute('x2')),upper);
  assert.equal(root.querySelector('.instrument-score').textContent,'+4.00σ');
  view.render(decision,{...state,running:false});
  assert.match(root.querySelector('.range-gauge').getAttribute('aria-label'),/Paused, last assessed/);
  view.render(null,{...state,running:false,scalp:{position:{signal:input,stop:'97',target:'100',deadline:300}}});
  assert.match(root.querySelector('.range-gauge').getAttribute('aria-label'),/Saved entry window/);
  assert.equal(Number(root.querySelector('.instrument-needle').getAttribute('x2')),lower);
  view.render(null,state);
  assert.equal(root.querySelector('.instrument-needle'),null);
  assert.equal(root.querySelector('.bollinger-instrument'),null);
  assert.equal(root.querySelector('.instrument-score').textContent,'—');
  assert.equal(JSON.stringify({state,decision}),before);
});
