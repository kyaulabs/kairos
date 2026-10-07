const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

function fixture() {
  const nodes = new Map(), browser = {}, calls = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'', textContent:'', disabled:false, hidden:false, events:{}, children:[],
      addEventListener(event, callback) { this.events[event] = callback; },
      querySelectorAll() { return []; },
      replaceChildren(...children) { this.children = children; },
      reportValidity() { return true; }, closest() { return {hidden:false}; },
      showModal() { this.open = true; }, close() { this.open = false; }, focus() {},
    });
    return nodes.get(id);
  };
  const document = {getElementById:node, createElement:() => ({})};
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/okx.js'), 'utf8'), {window:browser, document, Date});
  const state = {exchange:'okx-demo', environment:'demo', settings:{pair:'okx-demo:BTC-USD:USDC'}, credentials_configured:true, write_gate:true, running:false, armed:false};
  const preview = {id:'one-use-id', kind:'execution_cycle', environment:'demo', instrument:'BTC-USD', spending_currency:'USDC', write_gate:true, expires_at:Date.now()/1000 + 60, confirmation:'AUTHORIZE OKX DEMO EXECUTION_CYCLE', budget:'25'};
  const request = async (route, body) => { calls.push({route, body}); return route === 'okx-preview' ? {state, preview} : state; };
  node('okx-inputs').querySelectorAll = () => ['kind','pair','allocation','budget','buy','sell','exits','duration','allowance'].map(id => node('okx-' + id));
  const controller = new browser.OKXOperations(request, () => {}, () => {});
  controller.render(state, true);
  controller.open('execution_cycle');
  return {controller, node:id => node('okx-' + id), state, preview, calls};
}

test('OKX preflight is not authorization; wrong phrases and reused previews never send writes', async () => {
  const {controller, node, preview, calls} = fixture();
  await controller.preview();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].route, 'okx-preview');
  assert.equal(node('authorize').disabled, true);
  node('confirmation').value = 'START ALPACA PAPER'; controller.buttons();
  await controller.authorize();
  assert.equal(calls.length, 1);
  node('confirmation').value = preview.confirmation; controller.buttons();
  assert.equal(node('authorize').disabled, false);
  await controller.authorize();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].route, 'okx-authorize');
  assert.deepEqual({...calls[1].body}, {preview_id:'one-use-id', confirmation:preview.confirmation});
  await controller.authorize();
  assert.equal(calls.length, 2);
});

test('OKX expired previews and changed terms require new preflight; Stop stays usable while busy', async () => {
  const {controller, node, preview, calls} = fixture();
  await controller.preview();
  node('confirmation').value = preview.confirmation;
  preview.expires_at = 1; controller.buttons();
  assert.equal(node('authorize').disabled, true);
  controller.changed();
  assert.equal(controller.proposal, null);
  controller.pending = true; controller.buttons();
  assert.equal(node('preview').disabled, true);
  assert.equal(node('dialog-stop').disabled, false);
  await node('dialog-stop').events.click();
  assert.equal(calls.at(-1).route, 'stop');
  assert.equal(controller.proposal, null);
});

test('OKX pending preview locks terms and reopening until its response, without disabling Stop', async () => {
  const {controller, node, state, preview} = fixture();
  let resolve;
  controller.request = () => new Promise(done => { resolve = done; });
  node('budget').value = '17.123456789';
  const pending = controller.preview();
  for (const id of ['kind','pair','allocation','budget','buy','sell','exits','duration','allowance','confirmation','open']) assert.equal(node(id).disabled, true, id);
  assert.equal(node('dialog-stop').disabled, false);
  controller.dialog.close();
  controller.open('twap');
  assert.equal(node('kind').value, 'execution_cycle');
  assert.equal(controller.dialog.open, false);
  resolve({state, preview});
  await pending;
  assert.equal(node('budget').disabled, false);
  assert.equal(node('open').disabled, false);
  assert.equal(node('budget').value, '17.123456789');
  assert.equal(node('authorize').disabled, true);
});

test('OKX displays exact residual evidence without erasing input drafts or claiming mocks are hosted', () => {
  const {controller, node, state} = fixture();
  node('budget').value = '17.12345678912345';
  assert.match(node('result').textContent, /Not run.*authorization required/);
  controller.render({...state, execution_cycle:{status:'PARTIAL', residual:'0.00000000012345'}}, true);
  assert.match(node('result').textContent, /0\.00000000012345/);
  assert.equal(node('budget').value, '17.12345678912345');
  assert.equal(node('title').textContent, 'OKX Demo — virtual funds');
  controller.render({...state, exchange:'okx', environment:'live'}, true);
  assert.equal(node('title').textContent, 'OKX Live — real funds');
  assert.equal(node('allowance-label').hidden, true);
});
