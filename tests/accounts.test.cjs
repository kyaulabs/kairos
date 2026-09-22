const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

function fixture(request) {
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.textContent = ''; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    append(...nodes) { for (const node of nodes) this.children.push(...(node.tag === 'fragment' ? node.children : [node])); }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    set innerHTML(_) { throw new Error('Account values must never be parsed as HTML'); }
  }
  const elements = new Map();
  const element = id => { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); };
  const browser = {};
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/accounts.js'), 'utf8'), {
    window: browser,
    document: {getElementById: element, createElement: tag => new Element(tag), createDocumentFragment: () => new Element('fragment')},
  });
  element('account-source').value = 'spot-balances';
  return {view: new browser.AccountView(request), element};
}

const snapshot = {source: 'spot-balances', columns: ['Asset', 'Amount'], rows: [['USD', '250.00']], received: 1000, title: 'Spot wallet', truncated: false};

test('account data loads explicitly as text using a read-only route', async () => {
  const calls = [];
  const data = {...snapshot, rows: [['<img src=x onerror=alert(1)>', '0']]};
  const {view} = fixture(async (...args) => { calls.push(args); return data; });
  assert.equal(calls.length, 0);
  await view.load();
  assert.deepEqual(calls, [['accounts/spot-balances']]);
  assert.equal(view.body.children[0].children[0].textContent, data.rows[0][0]);
  assert.equal(view.body.children[0].children[1].textContent, '0');
  assert.match(view.status.textContent, /Manual refresh; server cache up to 30s/);
  assert.equal(view.source.disabled, false);
  view.open();
  assert.equal(calls.length, 1);
});

test('failed refresh retains the old account timestamp and rows', async () => {
  const {view} = fixture(async () => snapshot);
  await view.load();
  const body = view.body.children[0];
  view.request = async () => { throw new Error('Permission denied'); };
  await view.load();
  assert.equal(view.data.received, 1000);
  assert.equal(view.body.children[0], body);
  assert.match(view.status.textContent, /Permission denied.*Retaining older snapshot/);
  assert.equal(view.refresh.disabled, false);
});

test('account reads cannot overlap or display another source as the selected wallet', async () => {
  let release, requests = 0;
  const {view} = fixture(() => { requests++; return new Promise(resolve => { release = resolve; }); });
  const pending = view.load();
  assert.equal(view.source.disabled, true);
  await view.load();
  assert.equal(requests, 1);
  release({...snapshot, source: 'futures-balances'});
  await pending;
  assert.equal(view.data, null);
  assert.equal(view.body.children.length, 0);
  assert.match(view.status.textContent, /Invalid account snapshot.*No account data loaded/);
});

test('partial history and empty snapshots are not represented as a complete balance', async () => {
  const {view} = fixture(async () => ({...snapshot, truncated: true}));
  await view.load();
  assert.match(view.status.textContent, /PARTIAL: additional records not shown/);
  view.request = async () => ({...snapshot, rows: []});
  await view.load();
  assert.match(view.status.textContent, /No records returned/);
  assert.equal(view.body.children.length, 0);
});
