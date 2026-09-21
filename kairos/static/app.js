(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const form = $('settings');
  const priceChart = new LiveChart('#chart', '#3de0b1', 'price', Number($('timeframe').value) * 60000);
  const equityChart = new LiveChart('#equity-chart', '#74a8ff', 'equity');
  const number = value => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 8});
  const money = value => value == null ? '—' : Number(value).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const time = ts => new Date(ts * 1000).toLocaleTimeString();
  let state, csrf, pairs = [], initialized = false, busy = false, events = [], lastSymbol, lastPortfolio;
  let tickers = {}, lastTickRendered = 0, connected = false;
  const integerFields = new Set(['interval_seconds', 'candle_minutes', 'stale_seconds', 'leverage', 'recovery_check_seconds']);

  function message(text) { $('message').textContent = text; }
  async function request(path, body) {
    const options = body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(body)};
    const response = await fetch(`/api/${path}`, {...options, credentials: 'same-origin'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  function symbol() { return pairs.find(pair => pair.id === state?.settings.pair)?.symbol; }
  function loadForm() {
    for (const [key, value] of Object.entries(state.settings)) {
      const input = form.elements.namedItem(key);
      if (input?.type === 'checkbox') input.checked = value;
      else if (input) input.value = value;
    }
  }
  function textRow(container, left, right) {
    const row = document.createElement('div'); row.className = 'holding';
    const name = document.createElement('span'), value = document.createElement('span');
    name.textContent = left; value.textContent = right; row.append(name, value); container.append(row);
  }
  function render(next) {
    state = next;
    if (next.csrf) csrf = next.csrf;
    if (next.tickers) tickers = next.tickers;
    if (!initialized) { loadForm(); initialized = true; }
    const selected = symbol();
    if (lastSymbol !== selected) { priceChart.clear(); lastTickRendered = 0; lastSymbol = selected; }
    const portfolio = `${state.mode}:${state.settings.product}`;
    if (lastPortfolio !== portfolio) { equityChart.clear(); lastPortfolio = portfolio; }
    $('market-title').textContent = selected || state.settings.pair;
    $('mode').value = state.mode;
    $('mode-badge').textContent = state.mode === 'trading' ? 'LIVE TRADING' : `DRY-RUN · ${state.settings.product.toUpperCase()}`;
    $('mode-badge').classList.toggle('live', state.mode === 'trading');
    $('equity').textContent = money(state.equity);
    $('pnl').textContent = money(state.daily_pnl);
    $('pnl').className = Number(state.daily_pnl) < 0 ? 'sell' : 'buy';
    $('exposure').textContent = money(state.exposure);
    $('exposure-cap').textContent = `Effective limit ${money(state.effective_exposure_cap)} USD`;
    $('valuation-time').textContent = state.valuation_ts ? `Valued ${time(state.valuation_ts)}` : 'Not yet valued';
    if (state.valuation_ts && state.equity != null) equityChart.add(state.valuation_ts, Number(state.equity));
    $('engine-status').textContent = state.running ? 'Running' : 'Paused';
    $('engine-strategy').textContent = {htf: 'Higher-timeframe trend', maker: 'Rate-limited market making', arbitrage: 'Triangular arbitrage'}[state.settings.strategy];
    $('engine-error').hidden = !state.error;
    $('engine-error').textContent = state.error || '';
    $('start').disabled = busy || !connected || state.running || !state.ready;
    $('settings-fields').disabled = busy || state.running;
    $('mode').disabled = busy || !connected;
    const decision = state.decision;
    if (decision) {
      $('decision').textContent = decision.action;
      $('confidence').textContent = `${(decision.confidence*100).toFixed(1)}% confidence`;
      $('model-info').textContent = `${decision.model} · ${decision.latency_ms} ms · ${time(decision.ts)} · ${decision.mode}`;
      $('decision-inputs').textContent = JSON.stringify(decision.state, null, 2);
      $('probabilities').replaceChildren();
      for (const [action, probability] of Object.entries(decision.probabilities)) {
        const row = document.createElement('div'); row.className = `probability ${action}`;
        const header = document.createElement('header'), name = document.createElement('span'), value = document.createElement('span');
        name.textContent = action.toUpperCase(); value.textContent = `${(probability*100).toFixed(1)}%`;
        header.append(name, value);
        const bar = document.createElement('progress'); bar.max = 1; bar.value = probability; bar.setAttribute('aria-label', `${action} assessment`);
        row.append(header, bar); $('probabilities').append(row);
      }
    }
    $('holdings').replaceChildren();
    if (state.settings.product === 'margin') {
      const ledger = state.margin;
      $('portfolio-label').textContent = 'Paper margin · simplified cross-collateral simulation';
      if (ledger) {
        textRow($('holdings'), 'Collateral cash · USD', money(ledger.cash));
        for (const [pair, position] of Object.entries(ledger.positions)) {
          if (Number(position.quantity) !== 0) textRow($('holdings'), pair, `${number(position.quantity)} @ ${number(position.entry)} · ${position.leverage}×`);
        }
        $('margin-info').textContent = `Trading/opening fees ${money(ledger.fees)} USD · funding ${money(ledger.funding)} USD`;
      }
    } else {
      $('portfolio-label').textContent = `${state.mode === 'trading' ? 'Live' : 'Paper'} bot allocation only; not your full Kraken account.`;
      for (const [asset, amount] of Object.entries(state.ledger?.balances || {})) if (Number(amount) !== 0) textRow($('holdings'), asset, number(amount));
      const recovery = state.ledger?.recovery;
      if (recovery) textRow($('holdings'), `Protected reserve · ${state.settings.quote}`, money(recovery.reserved));
      $('margin-info').textContent = `Fees: ${Object.entries(state.ledger?.fees || {}).map(([a,v]) => `${number(v)} ${a}`).join(', ') || 'none'}. Recovery: ${recovery?.recovered ? 'original recovered; reserve excluded from orders' : recovery?.pending ? 'raising cash' : state.settings.recover_initial ? 'waiting for >2× original equity' : 'disabled'}.`;
    }
    $('orders').replaceChildren();
    for (const order of [...state.orders].reverse()) {
      const row = document.createElement('tr');
      const values = [time(order.created), `${order.mode} / ${order.product || 'spot'}`, order.pair, order.side,
        number(order.price), number(order.volume), number(order.filled), `${number(order.fee)} ${order.quote}`, order.status];
      for (const value of values) { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); }
      row.title = `Client ID: ${order.id}${order.txid ? ` · Kraken: ${order.txid}` : ''}`;
      $('orders').append(row);
    }
    for (const event of events) plotFill(event);
  }
  function plotFill(event) {
    const d = event.data;
    if (event.kind !== 'fill' || !state || d.pair !== state.settings.pair || d.mode !== state.mode || (d.product || 'spot') !== state.settings.product || !(Number(d.volume) > 0)) return;
    priceChart.fill({id: event.id, time: event.ts*1000, value: Number(d.cost)/Number(d.volume), side: d.side});
  }
  function addEvent(event) {
    if (!event.id || events.some(e => e.id === event.id)) return;
    events.push(event); events = events.slice(-200);
    plotFill(event);
    const li = document.createElement('li'), when = document.createElement('time'), kind = document.createElement('b'), detail = document.createElement('p');
    when.textContent = time(event.ts); kind.textContent = event.kind;
    const d = event.data;
    detail.textContent = d.message || d.reason || (event.kind === 'decision' ? `${d.mode} · ${d.action.toUpperCase()} · ${(d.confidence*100).toFixed(1)}% confidence` : event.kind === 'fill' ? `${d.mode} · ${d.side} ${number(d.volume)} · ${d.pair} · fee ${number(d.fee)}` : event.kind === 'order' ? `${d.mode} · ${d.side} ${d.pair} · ${d.status}` : event.kind === 'mode' ? `${d.mode} · ${d.running ? 'running' : 'paused'}` : JSON.stringify(d));
    li.append(when, kind, detail); $('activity').prepend(li);
    while ($('activity').children.length > 120) $('activity').lastChild.remove();
  }
  async function action(path, body = {}, refreshForm = false) {
    if (busy) return;
    busy = true; message('Working…');
    if (state) render(state);
    try {
      const next = await request(path, body);
      if (path === 'reset-paper') equityChart.clear();
      render(next);
      if (refreshForm) loadForm();
      message(path === 'mode' ? `Execution mode: ${state.mode}. ${state.running ? 'Engine running.' : 'Press Start to run.'}` : 'Done.');
    } catch (error) {
      message(error.message);
      try { render(await request('state')); } catch { /* Keep existing state visibly disconnected. */ }
    } finally { busy = false; if (state) render(state); }
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    const values = {...state.settings};
    for (const [key, value] of new FormData(form)) values[key] = integerFields.has(key) ? Number(value) : value;
    for (const key of ['reinvest_profits', 'recover_initial']) values[key] = form.elements.namedItem(key).checked;
    action('settings', values, true);
  });
  $('full-allocation').addEventListener('click', () => {
    const amount = form.elements.namedItem(state.mode === 'trading' ? 'live_budget' : 'paper_balance').value;
    if (!(Number(amount) > 0)) { message('Set a positive starting allocation first.'); return; }
    form.elements.namedItem('order_size').value = amount;
    form.elements.namedItem('max_exposure').value = amount;
    form.elements.namedItem('reinvest_profits').checked = true;
    message('Full-allocation sizing selected. Save settings. For a new paper starting balance, reset the paper portfolio after saving.');
  });
  $('start').addEventListener('click', () => action('start'));
  $('stop').addEventListener('click', () => action('stop'));
  $('reconcile').addEventListener('click', () => {
    const acknowledge = state?.cycle ? confirm('An interrupted arbitrage cycle may have left intermediate holdings. Have you reviewed the order records and balances? Acknowledging retains those holdings; it does not liquidate them.') : false;
    action('reconcile', {acknowledge});
  });
  $('reset').addEventListener('click', () => {
    if (confirm('Reset the selected PAPER portfolio and discard its simulated holdings? Historical events remain. This never resets live holdings.')) action('reset-paper');
  });
  $('mode').addEventListener('change', () => {
    const mode = $('mode').value;
    if (mode === 'trading' && !confirm('Enable REAL Kraken spot trading with the saved limits? If the engine is running, it will resume in live mode after reconciliation.')) { $('mode').value = state.mode; return; }
    action('mode', {mode, confirmation: mode === 'trading' ? 'ENABLE LIVE TRADING' : ''});
  });
  $('timeframe').addEventListener('change', () => priceChart.setTimeframe(Number($('timeframe').value) * 60000));
  $('follow').addEventListener('click', () => priceChart.follow());
  setInterval(() => {
    const ticker = tickers[symbol()];
    if (!ticker) { $('price').textContent = '—'; $('feed-age').textContent = 'No market feed'; return; }
    const age = Math.max(0, (Date.now()/1000-ticker.received));
    $('feed-age').textContent = `${age > 15 ? 'STALE · ' : ''}${age.toFixed(0)}s ago`;
    $('price').textContent = number((ticker.bid+ticker.ask)/2);
    $('bid-ask').textContent = `BID ${number(ticker.bid)}  /  ASK ${number(ticker.ask)}`;
    if (ticker.received !== lastTickRendered) {
      priceChart.add(ticker.received, (ticker.bid+ticker.ask)/2); lastTickRendered = ticker.received;
    }
  }, 500);
  async function boot() {
    pairs = await request('pairs');
    pairs.sort((a,b) => a.symbol.localeCompare(b.symbol));
    for (const pair of pairs) { const option = document.createElement('option'); option.value = pair.id; option.textContent = pair.symbol; $('pair').append(option); }
    render(await request('state'));
    for (const event of await request('history')) addEvent(event);
    const stream = new EventSource('/api/events');
    stream.onopen = async () => {
      connected = true; $('connection').textContent = 'DESK CONNECTED';
      try { render(await request('state')); for (const event of await request('history')) addEvent(event); }
      catch (error) { message(error.message); }
    };
    stream.onerror = () => { connected = false; $('connection').textContent = 'RECONNECTING'; if (state) render(state); };
    stream.addEventListener('state', event => render(JSON.parse(event.data)));
    stream.addEventListener('ticker', event => { const ticker = JSON.parse(event.data); tickers[ticker.symbol] = ticker; });
    stream.addEventListener('feed', () => { $('feed-age').textContent = 'Market feed reconnecting'; });
    for (const kind of ['decision','order','fill','engine-error','skip','cycle','liquidation','recovery','mode','settings','system']) stream.addEventListener(kind, event => addEvent(JSON.parse(event.data)));
  }
  boot().catch(error => { message(`Unable to initialize: ${error.message}. Reload after checking the server.`); $('connection').textContent = 'DISCONNECTED'; });
})();
