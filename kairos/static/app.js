(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const form = $('settings');
  const priceChart = new LiveChart('#chart', 'var(--success)', 'price', Number($('candle-interval').value));
  const equityChart = new LiveChart('#equity-chart', 'var(--accent)', 'equity');
  const number = value => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 8});
  const money = value => value == null ? '—' : Number(value).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const time = ts => new Date(ts * 1000).toLocaleTimeString();
  let state, csrf, chartPair, pairs = [], initialized = false, busy = false, events = [], lastMarket, lastPortfolio;
  let tickers = {}, connected = false;
  let candleTimer, candleController, candleGeneration = 0, candleReceived = 0, candleError = '';
  let settingsSchema, settingsForm;
  const scheduledStrategy = strategy => settingsSchema.strategies[strategy].scheduled;
  const assessmentView = new AssessmentView($('assessment-viz'));
  const marketPicker = new MarketPicker(request, pair => { chartPair = pair; if (state) render(state); }, markets => {
    strategyMarketPicker.setPairs(markets);
    if (state) updateStrategyMarket();
  });
  const strategyMarketPicker = new StrategyMarketPicker();
  const accountView = new AccountView(request);
  Promise.all([document.fonts.load('400 12px "Kairos Icons"'), document.fonts.load('900 12px "Kairos Icons"')])
    .then(faces => { if (faces.every(loaded => loaded.length)) document.documentElement.classList.add('icons-ready'); })
    .catch(() => { /* Licensed Pro files are optional; keep the text icon fallbacks. */ });

  function selectTab(tab) {
    for (const sibling of tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]')) {
      const selected = sibling === tab;
      sibling.setAttribute('aria-selected', String(selected));
      sibling.tabIndex = selected ? 0 : -1;
      $(sibling.getAttribute('aria-controls')).hidden = !selected;
    }
    const panel = $(tab.getAttribute('aria-controls'));
    const scroll = panel.closest('.settings-scroll');
    if (scroll) scroll.scrollTop = 0;
    if (tab.id === 'accounts-tab') accountView.open();
  }
  for (const list of document.querySelectorAll('[role="tablist"]')) {
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    for (const tab of tabs) {
      tab.addEventListener('click', () => selectTab(tab));
      tab.addEventListener('keydown', event => {
        const i = tabs.indexOf(tab);
        const next = {ArrowRight: (i+1)%tabs.length, ArrowLeft: (i+tabs.length-1)%tabs.length, Home: 0, End: tabs.length-1}[event.key];
        if (next === undefined) return;
        event.preventDefault();
        selectTab(tabs[next]); tabs[next].focus();
      });
    }
  }
  for (const button of document.querySelectorAll('.workspace-switcher button')) {
    button.addEventListener('click', () => {
      document.querySelector('.workspace').dataset.view = button.dataset.view;
      for (const sibling of document.querySelectorAll('.workspace-switcher button')) sibling.setAttribute('aria-pressed', String(sibling === button));
    });
  }
  // Reveal the first invalid field before native validation tries to focus it.
  form.addEventListener('invalid', event => {
    if (event.target !== form.querySelector('input:invalid, select:invalid')) return;
    const panel = event.target.closest('[role="tabpanel"]');
    if (panel) selectTab($(panel.getAttribute('aria-labelledby')));
  }, true);

  let messageTimer;
  function message(text) {
    clearTimeout(messageTimer);
    $('message').textContent = text;
    messageTimer = setTimeout(() => { $('message').textContent = ''; }, 15000);
  }
  async function request(path, body, signal) {
    const options = body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(body)};
    const response = await fetch(`/api/${path}`, {...options, credentials: 'same-origin', signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  function symbol() { return chartPair?.symbol; }
  function decisionLabel(decision) {
    const inventory = decision.state?.inventory;
    return decision.action === 'hold' && inventory != null && Number(inventory) === 0 ? 'wait' : decision.action;
  }
  function decisionContext(decision) {
    if (decision.deterministic) return decision.reason || '';
    if (decision.action !== 'hold') return '';
    const input = decision.state || {};
    const parts = ['No order requested; allocation unchanged.'];
    if (decisionLabel(decision) === 'wait') {
      parts.push(input.product === 'spot' ? `No ${input.symbol?.split('/')[0] || 'base-asset'} inventory to hold or sell.` : 'No open position in this market.');
    }
    if (input.strategy === 'htf' && input.entry_eligible === false) {
      const move = (Number(input.eight_candle_return_bps)/100).toFixed(3);
      const threshold = input.round_trip_cost_bps == null ? '' : `; required >${(Number(input.round_trip_cost_bps)/100).toFixed(3)}% plus a rising trend`;
      parts.push(`HTF buy filter not met: eight-candle move ${move}%${threshold}.`);
    }
    return parts.join(' ');
  }
  function restartCandles() {
    clearTimeout(candleTimer);
    candleController?.abort();
    const generation = ++candleGeneration;
    const pair = chartPair.id, interval = Number($('candle-interval').value);
    candleReceived = 0; candleError = '';
    $('candle-status').textContent = 'Loading candles…';
    async function refresh() {
      candleController = new AbortController();
      try {
        const data = await request(`candles?${new URLSearchParams({pair, interval})}`, undefined, candleController.signal);
        if (generation !== candleGeneration) return;
        if (data.pair !== pair || data.interval !== interval) throw new Error('Candle market or interval mismatch');
        priceChart.volumeUnit = data.volume_unit || 'base asset';
        priceChart.setCandles(data.candles);
        candleReceived = data.received; candleError = '';
      } catch (error) {
        if (generation !== candleGeneration || error.name === 'AbortError') return;
        candleError = 'Candle feed unavailable — retrying';
      } finally {
        // No overlapping polls; obsolete market/interval requests cannot redraw or reschedule.
        if (generation === candleGeneration) candleTimer = setTimeout(refresh, 5000);
      }
    }
    refresh();
  }
  function updateStrategyMarket() {
    strategyMarketPicker.update({
      quote: state.settings.quote,
      quoteLabel: pairs.find(pair => pair.id === state.settings.pair)?.symbol.split('/')[1] || state.settings.quote,
      product: form.elements.namedItem('product').value,
      leverage: Number(form.elements.namedItem('leverage').value), mode: state.mode,
    });
  }
  for (const event of ['input', 'change']) form.addEventListener(event, () => {
    if (state) { updateProgramFields(); updateStrategyMarket(); }
  });
  function updateProgramFields() {
    const strategy = form.elements.namedItem('strategy').value;
    const derivative = form.elements.namedItem('product').value === 'futures';
    settingsForm.update();
    $('futures-settings').hidden = !derivative;
    $('twap-quantity-label').textContent = derivative ? 'Total contract quantity' : 'Total base quantity';
    $('close-futures').disabled = busy || state?.running || state?.settings.product !== 'futures';
    $('program-settings').hidden = !scheduledStrategy(strategy);
    $('bot-market-label').textContent = strategy === 'rebalance' ? 'Anchor market · basket configured below' : 'Bot market';
    $('new-program').disabled = busy || state?.running || strategy !== state?.settings.strategy;
  }
  function loadForm() {
    settingsForm.load(state.settings);
    updateStrategyMarket(); strategyMarketPicker.sync(); updateProgramFields();
  }
  function textRow(container, left, right) {
    const row = document.createElement('div'); row.className = 'holding';
    const name = document.createElement('span'), value = document.createElement('span');
    name.textContent = left; value.textContent = right; row.append(name, value); container.append(row);
  }
  function updateFeeStatus() {
    const fees = state?.fees, rows = fees?.markets || [];
    const stale = !rows.length || rows.some(row => row.stale || !row.received || Date.now()/1000 < row.received || Date.now()/1000-row.received >= fees.max_age_seconds);
    const received = rows.filter(row => row.received).map(row => row.received);
    const observed = received.length ? `Oldest snapshot ${time(Math.min(...received))} · ` : '';
    const text = observed + (stale ? 'Fees unavailable or stale; new orders blocked. Check selected markets and Kraken fee-query access.' : `valid for ${fees.max_age_seconds}s. Spread, slippage, funding and borrowing are separate.`);
    if ($('fees-status').textContent !== text) $('fees-status').textContent = text;
    $('fees-status').classList.toggle('warning', stale);
  }
  function render(next) {
    state = next;
    if (next.csrf) csrf = next.csrf;
    if (next.tickers) tickers = next.tickers;
    if (!initialized) { loadForm(); initialized = true; }
    const botPair = pairs.find(pair => pair.id === state.settings.pair) || {id: state.settings.pair, symbol: state.settings.pair};
    if (!chartPair) chartPair = botPair;
    const selected = symbol();
    marketPicker.setSelected(chartPair);
    $('bot-market').textContent = botPair.symbol;
    $('bot-market-kind').textContent = state.settings.strategy === 'rebalance' ? 'ANCHOR MARKET' : 'BOT MARKET';
    $('bot-market').title = state.settings.strategy === 'rebalance' ? 'Show anchor chart; the configured basket controls which markets trade' : 'Show the bot’s configured market on the chart';
    $('chart-context').textContent = state.settings.strategy === 'rebalance' ? 'Basket strategy · chart only' : chartPair.id === botPair.id ? 'Bot market' : `${MarketPicker.kindLabel(chartPair)} · chart only · bot: ${botPair.symbol}`;
    $('chart-context').title = chartPair.execution_reason || MarketPicker.kindLabel(chartPair);
    $('chart-context').classList.toggle('browsing', chartPair.id !== botPair.id);
    if (lastMarket !== chartPair.id) {
      priceChart.clear(); lastMarket = chartPair.id;
      $('price').textContent = '—'; $('bid-ask').textContent = 'Waiting for prices'; $('feed-age').textContent = '—';
      restartCandles();
    }
    const portfolio = `${state.mode}:${state.settings.product}`;
    if (lastPortfolio !== portfolio) {
      equityChart.clear(); priceChart.fills = []; priceChart.draw(); lastPortfolio = portfolio;
    }
    $('market-title').textContent = selected || state.settings.pair;
    $('market-open').title = `${selected} · ${MarketPicker.kindLabel(chartPair)} · Chart only`;
    $('mode').value = state.mode;
    $('mode-badge').textContent = state.mode === 'trading' ? 'LIVE TRADING' : `DRY-RUN · ${state.settings.product.toUpperCase()}`;
    $('mode-badge').classList.toggle('live', state.mode === 'trading');
    $('equity').textContent = money(state.equity);
    $('pnl').textContent = `${Number(state.daily_pnl) > 0 ? '+' : ''}${money(state.daily_pnl)}`;
    $('pnl').className = Number(state.daily_pnl) < 0 ? 'sell' : 'buy';
    $('exposure').textContent = money(state.exposure);
    $('exposure-cap').textContent = `Effective limit ${money(state.effective_exposure_cap)} USD`;
    $('valuation-time').textContent = state.valuation_ts ? `Valued ${time(state.valuation_ts)}` : 'Not yet valued';
    if (state.valuation_ts && state.equity != null) equityChart.add(state.valuation_ts, Number(state.equity));
    $('engine-status').textContent = state.running ? 'Running' : 'Paused';
    $('engine-status').classList.toggle('running', state.running);
    $('engine-strategy').textContent = settingsSchema.strategies[state.settings.strategy].label;
    $('engine-error').hidden = !state.error;
    $('engine-error').textContent = state.error || '';
    $('start').disabled = busy || !connected || state.running || !state.ready;
    $('settings-fields').disabled = busy || state.running;
    updateStrategyMarket(); updateProgramFields();
    $('mode').disabled = busy || !connected;
    $('trading-fees').replaceChildren();
    for (const row of state.fees?.markets || []) {
      const percent = value => value == null ? '—' : `${number(Number(value)/100)}%`;
      textRow($('trading-fees'), row.symbol, `${percent(row.maker_bps)} maker · ${percent(row.taker_bps)} taker`);
    }
    updateFeeStatus();
    const decision = AssessmentView.matches(state.decision, state) ? state.decision : null;
    const definition = settingsSchema.strategies[state.settings.strategy];
    const scheduled = scheduledStrategy(state.settings.strategy);
    const rules = scheduled || definition.deterministic;
    $('mode').querySelector('option[value="trading"]').disabled = !!definition.paper_only;
    $('assessment-label').textContent = rules ? 'Strategy signals' : 'Jev assessment';
    $('assessment-kind').textContent = rules ? 'RULES' : 'MODEL';
    $('assessment-disclaimer').textContent = scheduled ? 'Scheduled orders and rebalancing do not guarantee profit. Missed or unfilled slices are not caught up automatically.' : rules ? 'Paper signals are not evidence of profitability. Price bounds, liquidity and data failures can prevent exits; Stop pauses protection checks.' : 'Model weights and directional bias are not calibrated probabilities of profit. Historical moves are not forecasts.';
    if (scheduled) {
      const program = state.program;
      $('decision').textContent = program?.configuration_changed ? 'Rearm required' : program?.status === 'complete' ? 'Complete' : state.running ? 'Running' : 'Paused';
      $('decision').className = '';
      $('confidence').textContent = 'Deterministic rules · no model calls';
      $('decision-context').textContent = program?.message || 'No run started. Save settings, then Start with a pre-funded allocation.';
      $('decision-market').textContent = state.settings.strategy === 'rebalance' ? `Basket: ${state.settings.rebalance_targets}` : `Program market: ${botPair.symbol}`;
      $('model-info').textContent = program ? `${program.orders} orders · ${number(program.spent_including_fees)} USD turnover incl. fees · ${program.skipped_slots} missed slots${program.next_at ? ` · Next ${new Date(program.next_at*1000).toLocaleString()}` : ''}` : 'Schedules persist across restarts. A completed run never rearms automatically.';
      $('decision-inputs').textContent = JSON.stringify(program || {}, null, 2);
    } else if (decision) {
      $('decision').textContent = decisionLabel(decision);
      $('decision').className = decision.action === 'buy' ? 'buy' : decision.action === 'sell' ? 'sell' : '';
      const confidence = AssessmentView.number(decision.confidence);
      $('confidence').textContent = decision.deterministic ? 'DETERMINISTIC · PAPER · 1 MINUTE' : confidence != null && confidence >= 0 && confidence <= 1 ? `${(confidence*100).toFixed(1)}% confidence in ${decision.action === 'hold' ? 'no trade' : decision.action}` : 'Confidence unavailable';
      $('decision-context').textContent = decisionContext(decision);
      $('decision-market').textContent = `Assessment market: ${decision.state?.symbol || decision.pair}`;
      $('model-info').textContent = `${decision.model} · ${decision.latency_ms} ms · ${time(decision.ts)} · ${decision.mode}`;
      $('decision-inputs').textContent = JSON.stringify(decision.state, null, 2);
    } else {
      $('decision').textContent = state.scalp?.position ? 'Saved plan' : 'Waiting'; $('decision').className = '';
      $('confidence').textContent = '—'; $('decision-context').textContent = '';
      $('decision-market').textContent = '';
      $('model-info').textContent = $('decision-inputs').textContent = state.decision ? 'Previous assessment belongs to another configuration.' : 'No assessment yet.';
    }
    assessmentView.render(decision, state, events, definition);
    $('holdings').replaceChildren();
    if (state.settings.product === 'futures') {
      const ledger = state.futures;
      $('portfolio-label').textContent = `${state.mode === 'trading' ? 'Live' : 'Paper'} Futures · separate USD collateral / linear perpetuals`;
      if (ledger) {
        textRow($('holdings'), 'Collateral cash · USD', money(ledger.cash));
        for (const [pair, position] of Object.entries(ledger.positions)) if (Number(position.quantity) !== 0) textRow($('holdings'), pair, `${number(position.quantity)} contracts @ ${number(position.entry)}`);
        $('margin-info').textContent = `Fees ${money(ledger.fees)} USD · realized funding ${money(ledger.funding)} USD · local leverage cap ${state.settings.futures_leverage}×. Stop does not close positions.`;
      } else $('margin-info').textContent = 'Allocate a dedicated USD-only Futures wallet and explicitly arm live Futures.';
    } else if (state.settings.product === 'margin') {
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
    $('orders-count').textContent = state.orders.length;
    $('orders-empty').hidden = state.orders.length > 0;
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
    if (event.kind !== 'fill' || !state || d.pair !== chartPair?.id || d.mode !== state.mode || (d.product || 'spot') !== state.settings.product || !(Number(d.volume) > 0)) return;
    priceChart.fill({id: event.id, time: event.ts*1000, value: Number(d.cost)/Number(d.volume), side: d.side});
  }
  function addEvent(event) {
    if (!event.id || events.some(e => e.id === event.id)) return;
    events.push(event); events = events.slice(-200);
    plotFill(event);
    const li = document.createElement('li'), when = document.createElement('time'), kind = document.createElement('b'), detail = document.createElement('p');
    when.textContent = time(event.ts); kind.textContent = event.kind;
    const d = event.data;
    detail.textContent = d.message || d.reason || (event.kind === 'decision' ? `${d.mode} · ${decisionLabel(d).toUpperCase()} · ${d.deterministic ? 'rules' : `${(d.confidence*100).toFixed(1)}% confidence${d.action === 'hold' ? ' in no trade' : ''}`}${decisionContext(d) ? ` · ${decisionContext(d)}` : ''}` : event.kind === 'fill' ? `${d.mode} · ${d.side} ${number(d.volume)} · ${d.pair} · fee ${number(d.fee)}` : event.kind === 'order' ? `${d.mode} · ${d.side} ${d.pair} · ${d.status}` : event.kind === 'mode' ? `${d.mode} · ${d.running ? 'running' : 'paused'}` : JSON.stringify(d));
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
    action('settings', settingsForm.values(state.settings), true);
  });
  $('new-program').addEventListener('click', () => {
    if (confirm('Create a new run using the SAVED strategy settings? This resets that run’s schedule/budget allowance, not balances or holdings. Existing history and today’s rebalance turnover remain. Review settings before pressing Start.')) action('reset-program', {confirmation: 'NEW STRATEGY RUN'});
  });
  $('close-futures').addEventListener('click', () => {
    if (confirm('Submit one bounded reduce-only order for the saved Futures market? The order cap and available liquidity may leave a residual position. Live mode sends a REAL order.')) action('close-futures', {confirmation: 'REDUCE FUTURES POSITION'});
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
    const derivative = state.settings.product === 'futures';
    const warning = derivative ? 'Enable REAL linear Futures trading? This uses a dedicated USD collateral wallet and can open shorts. Funding and liquidation can cause losses while paused. Confirm the saved notional caps and collateral allocation. Futures remain paused until you press Start.' : 'Enable REAL Kraken spot trading with the saved limits? If the engine is running, it will resume in live mode after reconciliation.';
    if (mode === 'trading' && !confirm(warning)) { $('mode').value = state.mode; return; }
    action('mode', {mode, confirmation: mode === 'trading' ? (derivative ? 'ENABLE LIVE FUTURES' : 'ENABLE LIVE TRADING') : ''});
  });
  $('candle-interval').addEventListener('change', () => {
    priceChart.setCandleInterval(Number($('candle-interval').value));
    if (state) restartCandles();
  });
  $('follow').addEventListener('click', () => priceChart.follow());
  $('bot-market').addEventListener('click', () => {
    if (!state) return;
    chartPair = pairs.find(pair => pair.id === state.settings.pair) || {id: state.settings.pair, symbol: state.settings.pair};
    render(state);
  });
  setInterval(() => {
    updateFeeStatus();
    const candleAge = Math.max(0, Date.now()/1000-candleReceived);
    $('candle-status').textContent = candleError || (candleReceived ? `${candleAge > 15 ? 'STALE · ' : ''}Candles refreshed ${candleAge.toFixed(0)}s ago · latest candle may be forming` : 'Loading candles…');
    marketPicker.updateFreshness();
    const spotTicker = ['xstocks', 'futures'].includes(chartPair?.kind) ? null : tickers[symbol()];
    const ticker = [spotTicker, marketPicker.ticker(chartPair?.id)].filter(Boolean).sort((a, b) => b.received-a.received)[0];
    if (!ticker) { $('price').textContent = '—'; $('bid-ask').textContent = 'Waiting for prices'; $('feed-age').textContent = 'No market feed'; return; }
    const age = Math.max(0, (Date.now()/1000-ticker.received));
    $('feed-age').textContent = `${age > 30 ? 'STALE · ' : ''}${age.toFixed(0)}s ago`;
    $('price').textContent = number((Number(ticker.bid)+Number(ticker.ask))/2);
    $('bid-ask').textContent = `BID ${number(ticker.bid)}  /  ASK ${number(ticker.ask)}`;
  }, 500);
  async function boot() {
    [settingsSchema, pairs] = await Promise.all([request('settings-schema'), request('pairs')]);
    settingsForm = new SettingsForm(form, settingsSchema);
    SettingsForm.choices($('candle-interval'), settingsSchema.fields.candle_minutes.choices, $('candle-interval').value);
    priceChart.intervals = Object.keys(settingsSchema.fields.candle_minutes.choices).map(Number);
    strategyMarketPicker.setPairs(pairs);
    render(await request('state'));
    marketPicker.refresh();
    for (const event of await request('history')) addEvent(event);
    const stream = new EventSource('/api/events');
    stream.onopen = async () => {
      connected = true; $('connection').textContent = 'DESK CONNECTED'; $('connection').classList.add('connected');
      try { render(await request('state')); for (const event of await request('history')) addEvent(event); }
      catch (error) { message(error.message); }
    };
    stream.onerror = () => { connected = false; $('connection').textContent = 'RECONNECTING'; $('connection').classList.remove('connected'); if (state) render(state); };
    stream.addEventListener('state', event => render(JSON.parse(event.data)));
    stream.addEventListener('ticker', event => { const ticker = JSON.parse(event.data); tickers[ticker.symbol] = ticker; });
    stream.addEventListener('feed', () => { $('feed-age').textContent = 'Market feed reconnecting'; });
    for (const kind of ['decision','order','fill','engine-error','skip','cycle','liquidation','recovery','mode','settings','system','program']) stream.addEventListener(kind, event => addEvent(JSON.parse(event.data)));
  }
  boot().catch(error => { message(`Unable to initialize: ${error.message}. Reload after checking the server.`); $('connection').textContent = 'DISCONNECTED'; });
})();
