const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

function fixture() {
  const browser = {}, classes = new Set(), calls = [];
  runInNewContext(readFileSync(path.join(__dirname, '../kairos/static/records.js'), 'utf8'), {window:browser});
  const workspace = {classList:{toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); }}};
  const names = ['orders', 'activity', 'portfolio', 'accounts', 'equity'];
  const tabs = names.map((name, index) => ({
    id:name+'-tab', tabIndex:index ? -1 : 0,
    attributes:{'aria-selected':String(index === 0), 'aria-controls':name+'-view'},
    getAttribute(key) { return this.attributes[key]; },
    setAttribute(key, value) { this.attributes[key] = value; },
  }));
  const views = Object.fromEntries(names.map((name, index) => [name+'-view', {hidden:index !== 0, scrollTop:37, retainedContent:{draft:name}}]));
  const button = {attributes:{}, events:{}, setAttribute(key,value) { this.attributes[key]=value; }, addEventListener(key,fn) { this.events[key]=fn; }};
  const panel = {
    closest:() => workspace, querySelectorAll:() => tabs,
    querySelector:selector => selector === '#records-toggle' ? button : views[selector.slice(1)],
  };
  let controller;
  const select = tab => {
    calls.push(tab.id);
    for (const sibling of tabs) {
      const active = sibling === tab;
      sibling.setAttribute('aria-selected',String(active));
      sibling.tabIndex = active ? 0 : -1;
    }
    controller.setOpen(true);
  };
  controller = new browser.RecordsPanel(panel,select);
  return {controller,tabs,views,button,classes,calls,select};
}

test('records initialize without selecting another tab or requesting account data', () => {
  const {controller,tabs,views,button,classes,calls} = fixture();
  assert.equal(controller.expanded,true);
  assert.deepEqual(calls,[]);
  assert.equal(views['orders-view'].hidden,false);
  assert.equal(Object.values(views).filter(view => !view.hidden).length,1);
  assert.equal(button.attributes['aria-expanded'],'true');
  assert.equal(button.attributes['aria-label'],'Collapse lower panels');
  assert.equal(tabs[0].attributes['aria-expanded'],'true');
  assert.equal(classes.has('records-collapsed'),false);
});

test('each active records tab collapses all content without activating or clearing anything', () => {
  for (let index=0; index<5; index++) {
    const {controller,tabs,views,classes,calls,select} = fixture();
    select(tabs[index]);
    const count=calls.length, retained=views[tabs[index].getAttribute('aria-controls')].retainedContent;
    controller.click(tabs[index]);
    assert.equal(controller.expanded,false);
    assert.equal(classes.has('records-collapsed'),true);
    assert.equal(Object.values(views).every(view => view.hidden),true);
    assert.equal(tabs[index].getAttribute('aria-selected'),'true');
    assert.equal(tabs.every(tab => tab.getAttribute('aria-expanded') === 'false'),true);
    assert.equal(calls.length,count);
    assert.equal(views[tabs[index].getAttribute('aria-controls')].retainedContent,retained);
  }
});

test('clicking a collapsed active tab restores only that tab and retains its scroll and draft', () => {
  const {controller,tabs,views,calls} = fixture();
  const content=views['orders-view'].retainedContent;
  controller.click(tabs[0]);
  controller.click(tabs[0]);
  assert.equal(controller.expanded,true);
  assert.equal(views['orders-view'].hidden,false);
  assert.equal(Object.values(views).filter(view => !view.hidden).length,1);
  assert.equal(views['orders-view'].retainedContent,content);
  assert.equal(views['orders-view'].scrollTop,37);
  assert.deepEqual(calls,['orders-tab']);
});

test('choosing another tab while collapsed opens that tab rather than toggling the old one', () => {
  const {controller,tabs,views,calls} = fixture();
  controller.click(tabs[0]);
  controller.click(tabs[3]);
  assert.equal(controller.expanded,true);
  assert.equal(views['accounts-view'].hidden,false);
  assert.equal(views['orders-view'].hidden,true);
  assert.equal(tabs[3].tabIndex,0);
  assert.equal(tabs[0].tabIndex,-1);
  assert.deepEqual(calls,['accounts-tab']);
});

test('right-hand toggle restores the last selected panel and exposes its expanded state', () => {
  const {controller,tabs,views,button,calls,select} = fixture();
  select(tabs[4]);
  button.events.click();
  assert.equal(controller.expanded,false);
  assert.equal(button.attributes['aria-expanded'],'false');
  assert.equal(button.attributes['aria-label'],'Expand lower panels');
  assert.equal(button.title,'Expand lower panels');
  assert.deepEqual(calls,['equity-tab']);
  button.events.click();
  assert.equal(views['equity-view'].hidden,false);
  assert.equal(Object.values(views).filter(view => !view.hidden).length,1);
  assert.equal(button.attributes['aria-expanded'],'true');
  assert.equal(button.attributes['aria-label'],'Collapse lower panels');
  assert.deepEqual(calls,['equity-tab','equity-tab']);
});
