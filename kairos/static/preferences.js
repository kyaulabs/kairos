/* Server-authoritative dashboard preferences. Memory only; no browser persistence. */
window.DashboardPreferences = (() => {
  'use strict';
  let snapshot, refreshing, loadFailed = false, pending = 0, generation = 0, queue = Promise.resolve();
  const listeners = new Map(), reserved = new Map();
  function status(text = '') {
    const node = document.getElementById('preferences-status');
    if (node) { node.textContent = text; node.hidden = !text; }
  }
  function accept(next) {
    if (!next?.values || !next.revisions || !next.csrf) throw new Error('Invalid server preferences response.');
    const previous = snapshot;
    if (!snapshot) snapshot = {values: {}, revisions: {}, csrf: next.csrf};
    snapshot.csrf = next.csrf;
    for (const [key, value] of Object.entries(next.values)) {
      if (previous && next.revisions[key] < (snapshot.revisions[key] ?? 0)) continue;
      const changed = JSON.stringify(snapshot.values[key]) !== JSON.stringify(value);
      snapshot.values[key] = value; snapshot.revisions[key] = next.revisions[key];
      if (changed) for (const callback of listeners.get(key) || []) callback(structuredClone(value));
    }
  }
  async function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      const response = await fetch('/api/preferences', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('Server preferences unavailable; changes cannot be saved.');
      accept(await response.json());
    })();
    try { await refreshing; if (loadFailed) status(); loadFailed = false; }
    catch (error) { loadFailed = true; throw error; }
    finally { refreshing = null; }
  }
  const ready = refresh();
  ready.catch(error => { document.addEventListener('DOMContentLoaded', () => { if (loadFailed) status(error.message); }, {once:true}); status(error.message); });
  function get(key, fallback) { return structuredClone(snapshot?.values[key] ?? fallback); }
  function subscribe(key, callback) {
    if (!listeners.has(key)) listeners.set(key, new Set());
    listeners.get(key).add(callback);
    if (snapshot && Object.hasOwn(snapshot.values, key)) callback(get(key));
  }
  function set(key, value) {
    const draft = structuredClone(value), issued = generation, loaded = !!snapshot;
    const revision = Math.max(snapshot?.revisions[key] ?? 0, reserved.get(key) ?? 0);
    reserved.set(key, revision + 1);
    pending++;
    const write = queue.then(async () => {
      if (!loaded) throw new Error('Server preferences are still loading. Wait before editing.');
      if (issued !== generation) throw new Error('An earlier save failed. Review server settings before saving again.');
      const response = await fetch('/api/preferences', {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json', 'X-CSRF-Token':snapshot.csrf},
        body:JSON.stringify({key, value:draft, revision})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Dashboard preference was not saved.');
      accept(data); status();
    });
    queue = write.catch(async error => {
      generation++; reserved.clear();
      try { await refresh(); } catch { /* Keep the failure visible; never claim a local save. */ }
      status(`${error.message} No browser copy was saved.`);
    }).finally(() => { pending--; });
    return write;
  }
  function synchronize() {
    if (!pending && document.visibilityState !== 'hidden') refresh().catch(error => status(error.message));
  }
  setInterval(synchronize, 5000);
  document.addEventListener('visibilitychange', synchronize);
  window.addEventListener('focus', synchronize);
  return {ready, get, set, subscribe, refresh};
})();
