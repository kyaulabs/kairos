const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../kairos/static/preferences.js'), 'utf8');
const tick = () => new Promise(setImmediate);
function server() {
  const data = {values:{candle_interval:1, appearance:{theme:'dark',accent:'violet'}}, revisions:{candle_interval:0,appearance:0}, csrf:'fixture'};
  const writes = [];
  let fail = false;
  return {data, writes, fail() { fail = true; }, async fetch(path, options = {}) {
    assert.equal(path, '/api/preferences');
    assert.equal(options.credentials, 'same-origin');
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); writes.push(body);
      assert.equal(options.headers['X-CSRF-Token'], 'fixture');
      if (fail) { fail = false; throw new Error('Network failed; save could not be confirmed.'); }
      if (body.revision !== data.revisions[body.key]) return {ok:false,status:409,json:async()=>({error:'Changed elsewhere'})};
      data.values[body.key] = body.value; data.revisions[body.key]++;
    }
    const saved = structuredClone(data);
    return {ok:true,json:async()=>saved};
  }};
}
function browser(remote) {
  const window = {addEventListener() {}}, status = {}, polls = [];
  runInNewContext(source, {
    window, document:{visibilityState:'visible',getElementById:()=>status,addEventListener() {}},
    fetch: (...args) => remote.fetch(...args), structuredClone, setInterval: callback => polls.push(callback),
    get localStorage() { assert.fail('No browser persistence'); }, get sessionStorage() { assert.fail('No session persistence'); },
  });
  return {api:window.DashboardPreferences, status, polls};
}

test('refresh and a separate device load server choices; edits save only changed keys', async () => {
  const remote = server(), a = browser(remote);
  await a.api.ready;
  await a.api.set('candle_interval', 15);
  const b = browser(remote); await b.api.ready;
  assert.equal(b.api.get('candle_interval'), 15);
  await b.api.set('appearance', {theme:'light',accent:'blue'});
  await a.api.refresh();
  assert.equal(a.api.get('appearance').theme, 'light');
  assert.equal(a.api.get('candle_interval'), 15);
  const copy = a.api.get('appearance'); copy.theme = 'dark';
  assert.equal(a.api.get('appearance').theme, 'light');
  assert.deepEqual(remote.writes.map(item=>item.key), ['candle_interval','appearance']);
});

test('rapid changes serialize without losing the last selected interval', async () => {
  const remote = server(), {api} = browser(remote); await api.ready;
  await Promise.all([api.set('candle_interval',5),api.set('candle_interval',15),api.set('candle_interval',60)]);
  assert.equal(remote.data.values.candle_interval,60);
  assert.deepEqual(remote.writes.map(item=>item.revision),[0,1,2]);
});

test('a stale page does not overwrite server edits or replay queued drafts', async () => {
  const remote = server(), a = browser(remote), b = browser(remote);
  await Promise.all([a.api.ready,b.api.ready]);
  await a.api.set('candle_interval', 15);
  const results = await Promise.allSettled([b.api.set('candle_interval',5),b.api.set('candle_interval',60)]);
  await tick();
  assert.ok(results.every(item=>item.status==='rejected'));
  assert.equal(remote.data.values.candle_interval,15);
  assert.equal(b.api.get('candle_interval'),15);
  assert.equal(remote.writes.length,2);
  assert.match(b.status.textContent,/No browser copy/);
});

test('failed saves are visible, reload server truth, and never retry writes', async () => {
  const remote = server(), {api,status} = browser(remote); await api.ready;
  remote.fail();
  await assert.rejects(api.set('candle_interval',15),/Network failed/);
  await tick();
  assert.equal(api.get('candle_interval'),1);
  assert.equal(remote.writes.length,1);
  assert.match(status.textContent,/could not be confirmed/);
  await api.set('candle_interval',30);
  assert.equal(api.get('candle_interval'),30);
  assert.equal(status.hidden,true);
});

test('a delayed read cannot replace a more recent saved preference', async () => {
  const remote = server(), {api} = browser(remote); await api.ready;
  const fetch = remote.fetch.bind(remote);
  let release;
  remote.fetch = async (path,options) => {
    const response = await fetch(path,options);
    if (!options.method) await new Promise(resolve=>{release=resolve;});
    return response;
  };
  const refresh = api.refresh(); await tick();
  await api.set('candle_interval',15);
  release(); await refresh;
  assert.equal(api.get('candle_interval'),15);
});

test('an in-flight read cannot make an already queued draft appear current', async () => {
  const remote=server(), {api}=browser(remote); await api.ready;
  const fetch=remote.fetch.bind(remote); let release;
  remote.fetch=async (path,options)=>{
    const response=await fetch(path,options);
    if (options.method==='POST' && JSON.parse(options.body).key==='appearance') await new Promise(resolve=>{release=resolve;});
    return response;
  };
  const first=api.set('appearance',{theme:'light',accent:'blue'}); await tick();
  const draft=api.set('candle_interval',15);
  remote.data.values.candle_interval=30; remote.data.revisions.candle_interval++;
  await api.refresh(); release(); await first;
  await assert.rejects(draft,/Changed elsewhere/); await tick();
  assert.equal(remote.data.values.candle_interval,30);
  assert.equal(api.get('candle_interval'),30);
});

test('an early click cannot overwrite preferences before their first load', async () => {
  const remote=server(), fetch=remote.fetch.bind(remote); let release;
  remote.fetch=async (...args)=>{const response=await fetch(...args); await new Promise(resolve=>{release=resolve;});return response;};
  const {api}=browser(remote); await tick();
  await assert.rejects(api.set('candle_interval',15),/still loading/);
  release(); await api.ready; await tick();
  assert.equal(remote.writes.length,0);
});

test('visible-page polling updates controls without causing another save', async () => {
  const remote = server(), {api,polls} = browser(remote); await api.ready;
  const changes=[]; api.subscribe('candle_interval', value=>changes.push(value));
  remote.data.values.candle_interval=240; remote.data.revisions.candle_interval++;
  polls[0](); await tick();
  assert.deepEqual(changes,[1,240]);
  assert.equal(remote.writes.length,0);
});
