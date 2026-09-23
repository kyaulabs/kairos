/* Read-only guidance. Help never changes setting values, saves or calls an API. */
class SettingsHelp {
  static fields = {
    product: 'Choose the portfolio and execution rules: Spot for currency crypto/FX, Margin for paper simulation, or Futures for qualified USD linear crypto perpetuals. Portfolios, allocations and leverage are separate; switching products never transfers funds. Live margin is blocked. xStocks are tokens, not brokerage shares; xStocks, inverse, dated and non-crypto Futures are browse-only. DEX and brokerage trading are not integrated.',
    pair: 'Choose the saved bot execution market, not the independently browsable chart. The catalog includes unsupported instruments with limitation reasons. Spot uses the portfolio quote currency; paper margin also requires advertised leverage. Futures requires a qualified USD linear crypto perpetual. These are Kairos safeguards, not a statement about your Kraken account entitlements. Select a supported market, then Save. Changing spot markets does not discard existing holdings.',
    strategy: 'Choose how new trades are planned. Only strategies supported by the selected product can be saved. A selection is a draft until Save; it does not switch the running engine. Continuous strategies have no scheduled completion or guaranteed profit target. DCA and TWAP have finite runs; rebalancing continues until paused.',
    interval_seconds: 'Seconds to wait between engine cycles; API calls and processing add time. A shorter cycle gives more frequent checks, not more completed candles or cheaper fees. Streaming does not execute on every price update. Bollinger requires a 10–30 second cycle. No strategy protection checks run while paused or offline.',
    candle_minutes: 'Duration of each completed candle used by the HTF strategy. It evaluates the latest 30 completed candles, including 8- and 21-candle trend averages and the last eight-candle move. This setting does not change the chart interval. Larger intervals measure longer trends, not higher certainty. Bollinger always uses one-minute candles instead.',
    min_confidence: 'Minimum Jev confidence, expressed from 0 to 1: 0.7 means 70%, not 70. It gates model-assisted HTF, market-making and arbitrage decisions. A higher threshold rejects more decisions but does not guarantee better trades. Confidence is not a calibrated probability of profit. Deterministic Bollinger, DCA, TWAP and rebalancing do not use Jev.',
    arb_min_profit_bps: 'Minimum estimated net edge for a complete spot triangular cycle after account-specific fees and modeled execution costs. One basis point (bps) is 0.01%; 10 bps is 0.10%. Raising it rejects smaller opportunities. The legs are not atomic: partial fills and price changes can leave inventory and require recovery. It is not a guaranteed return.',
    scalp_window: 'Number of completed one-minute candles in the rolling Bollinger window. A preceding window is also needed to detect re-entry. This is not the chart viewport or a full-history average. Larger windows smooth more history; smaller windows react faster. Missing, malformed or stale required candles block entries; Kairos does not invent missing bars.',
    scalp_sigma: 'Band width in population standard deviations above and below the rolling mean. A larger value makes wider bands; a smaller value makes narrower bands. An entry requires re-entry from outside a band, acceptable trend efficiency and enough room to the fixed midpoint after costs. Narrower bands do not bypass the cost filter.',
    scalp_max_efficiency: 'Maximum net price movement divided by total price travel in the recent window. Values nearer 1 indicate a more directional trend. Lower limits reject more trending windows and favor choppier ranges; flat/zero-volatility windows are also rejected. Passing this filter is not proof that price will return to the mean.',
    scalp_margin_bps: 'Extra net-cost headroom required before entry, in basis points (100 bps = 1%). Account-specific entry/exit fees, adverse spread/slippage and price rounding must be covered first. Raising this margin rejects more entries. Kairos never moves the fixed midpoint target just to make a trade pass. Protective exits do not wait for profitability.',
    scalp_stop_bps: 'Adverse distance from the planned entry limit that triggers a local protective exit. 50 bps = 0.50%. A smaller distance triggers sooner. The stop is saved with the position; later setting changes affect new entries only. This is not an exchange-held stop or a guaranteed maximum loss: liquidity, fees, data, price limits and minimum sizes can prevent or limit an exit. Stop/offline pauses these checks.',
    scalp_max_hold_seconds: 'Maximum intended holding time, in seconds, saved when entering. Reaching the deadline requests a bounded exit even at a loss; it does not guarantee an immediate or complete fill. The deadline survives pause/restart but is checked only while running. Changes affect later entries, not the deadline of an existing position.',
    scalp_cooldown_seconds: 'Wait after the owned scalp position becomes flat before considering another entry. A longer cooldown reduces rapid re-entry; zero removes this extra wait. Fresh completed-candle signals, costs and risk checks still apply. Pausing does not flatten the position.',
    dca_amount: 'USD budget per scheduled slice, including trading fees. Spot buys base assets from explicitly allocated cash; the whole run must be pre-funded. Futures uses notional USD, not a transfer of this amount into collateral, and checks whole-run collateral reserves. Order/exposure caps and exchange minimums can reduce or block a slice. No automatic conversion or transfer funds the run.',
    dca_count: 'Number of scheduled opportunities in this finite DCA run. Together with the slice amount it sets the whole-run budget. Missed or unfilled slots are not accumulated into catch-up orders. The run pauses on completion. Save changes and explicitly create a new strategy run when rearming is required; that does not replenish funds.',
    dca_period_seconds: 'Seconds between DCA slots; at least one engine cycle is required per slot. The first check is immediate on Start, subject to validation and funding. Actual checks follow engine cadence. Missed slots are skipped, not replayed in a burst after downtime.',
    futures_dca_side: 'Buy builds a long position; sell builds a short position in qualified linear Futures. With reduce-only enabled, the chosen side must instead reduce an existing opposite position. A program cannot implicitly reverse through zero. This does not change the selected chart.',
    twap_side: 'Direction for the parent order. Spot buys spend allocated quote cash; spot sells require already bot-owned base inventory and cannot create a short. Futures buys/sells can build longs/shorts or reduce an opposite position when reduce-only is enabled. No inventory is borrowed or imported automatically.',
    twap_quantity: 'Total quantity for the entire TWAP parent, not each slice. Spot uses base-asset units; qualified Futures uses contract units, with one base unit per contract. Divide the parent over the configured slices, subject to lot rounding, minimums, caps and available funding. Unfilled quantities are not automatically caught up.',
    twap_limit: 'Required positive price per base unit in USD, not a total budget. It is the maximum buy price or minimum sell price for the whole parent. There is no automatic price default: blank/zero is unconfigured. Slices also obey current execution bounds; a price outside the limit can skip a slice rather than chase the market.',
    twap_slices: 'Number of opportunities used to divide the parent quantity. More slices make smaller orders, which may fall below venue minimums after rounding. Total duration must allow at least one engine cycle for each slice. Missed/unfilled slices do not become a catch-up burst; completion pauses the run.',
    twap_duration_seconds: 'Total scheduling duration in seconds. It must be at least slices × engine-cycle seconds. The first check is immediate on Start; subsequent slots are checked on engine cycles, so execution timing is not exact. The parent quantity and limit remain bounded throughout the run.',
    futures_parent_notional: 'Maximum USD notional for the entire Futures TWAP parent, separate from each-order and total-exposure caps. The parent quantity and limit must fit this cap and whole-parent collateral reserves. It is not collateral funding, a wallet transfer or permission to exceed leverage/margin tiers.',
    futures_reduce_only: 'Restrict the Futures DCA/TWAP run to reducing an existing bot-owned position. Choose sell to reduce a long or buy to reduce a short. It cannot open exposure or reverse through zero. The available position, price, fees, minimum size and order limits still apply.',
    rebalance_targets: 'Comma-separated target weights such as BTC/USD=50,ETH/USD=30,CASH=20. Use 1–10 supported markets sharing the portfolio quote currency, with positive market weights and an explicit CASH weight (which may be zero), totaling exactly 100%. Only listed bot-owned assets and allocated cash participate. Other holdings stay untouched. The anchor market does not choose the basket. No wallet balances are imported and no transfers fund trades.',
    rebalance_band_pct: 'Permitted absolute deviation from each target, in percentage points, not a relative percentage. A 50% target with a 5-point band tolerates 45–55%. A tighter band can trade more often and incur more fees. Rebalancing is sell-first and can send at most one eligible order per cooldown.',
    rebalance_min_trade: 'Minimum proposed rebalance trade in USD. Smaller adjustments are skipped to avoid tiny trades; Kraken quantity and cost minimums still apply. This value cannot exceed the daily turnover allowance. It does not override order or exposure caps.',
    rebalance_daily_turnover: 'Maximum cumulative rebalance turnover per UTC day in USD, including trading fees. Buys and sells consume the allowance. Creating a new run or editing a basket does not erase today’s turnover. This limits activity, not maximum losses or portfolio value.',
    rebalance_cooldown_seconds: 'Minimum interval between rebalance checks/orders, in seconds. A longer cooldown trades less frequently; price drift can persist while waiting. At most one order is sent per cooldown, with sell-first planning and all budget, cost and minimum-size checks still enforced.',
    paper_balance: 'Starting USD allocation for the selected paper product. Save this value, then use Reset selected paper portfolio to apply it; saving alone does not refill an existing ledger. Reset replaces that paper portfolio’s balances/positions and resets its run state. It does not deposit money, modify live funds or delete historical events.',
    live_budget: 'Explicit USD cash allocation for the spot live bot. Zero leaves it unconfigured. Funds must already be available on Kraken, and the starting order cap cannot exceed the allocation. Kairos tracks only its allocation, not the whole wallet, and does not transfer or convert funds. Saving this value does not arm live trading or overwrite an existing live ledger.',
    futures_live_budget: 'Explicit USD collateral allocation for a separate live Futures portfolio. Zero leaves it unconfigured. First arming requires a dedicated USD-only cross-margin wallet with no open orders/positions and exactly the configured USD allocation. Wallet identity, external activity, margin and independent live gates/confirmation are checked. No automatic funding, transfer or conversion is performed. Saving is not live arming.',
    order_size: 'Base cap on the USD notional of each order, not the portfolio allocation. It cannot exceed the base exposure cap. Available funds, fee reserves, quantity precision and strategy rules may make an order smaller or block it. Reinvestment scales this cap with equity. Bounded protective reductions can leave a residual position.',
    max_exposure: 'Base cap on total marked bot exposure in USD. Spot measures held assets; leveraged products use notional exposure, not just collateral cash. Reinvestment scales the effective cap with equity. Setting a cap does not automatically flatten positions, guarantee a loss ceiling or authorize more funding.',
    daily_loss: 'Marked-to-market loss limit in USD against the portfolio’s UTC-day baseline, including accounted costs. Reaching it blocks ordinary trading; it does not guarantee a maximum loss or flatten holdings. Price gaps, fees and paused exposure can worsen losses. Owned Bollinger reductions and the explicit Futures reduction path have separate guarded exit handling.',
    leverage: 'Leverage for the simplified paper-margin model, only where Kraken advertises the chosen leverage for both sides of the pair. More leverage increases notional exposure and liquidation risk; it is not extra allocated cash. Real margin trading is blocked. The simulation is not Kraken’s exact borrowing or liquidation model.',
    futures_leverage: 'Local maximum notional-to-collateral leverage for qualified linear Futures. This is a Kairos cap, not a change to Kraken’s wallet/margin settings. Published tiers may require more margin and lower effective leverage. Reserve checks include 20% of starting collateral plus fees. Funding and liquidation risk continue while paused.',
    recovery_check_seconds: 'How often the enabled spot capital-recovery process may check/sell, in seconds. It uses bounded orders and may need several checks to realize enough cash. A shorter interval does not bypass fees, liquidity or risk checks. It has no effect when recovery is off and is unavailable for Bollinger, margin and Futures.',
    reinvest_profits: 'Scale the effective order and exposure caps by current equity ÷ starting allocation. Caps can grow with profits and shrink with losses; the daily loss limit is not scaled by this switch. No extra wallet funds are imported. Turning it off uses the configured base caps, not an automatic sale of excess holdings.',
    recover_initial: 'Spot only, excluding Bollinger. Once equity is above twice the original allocation, Kairos can sell enough bot-owned inventory to reserve that original amount once. Reserved cash stays on Kraken but is excluded from this bot’s trading allocation; no withdrawal occurs. It requires sufficient realized cash and equity, can take multiple checks, and is not guaranteed profit recovery. Turn it off before selecting an incompatible product/strategy.',
    slippage_bps: 'Basis-point price allowance for bounded execution: buys use the best ask plus the allowance; sells use the best bid minus it, with tick rounding and any tighter parent limit. For maker quotes, this is an offset combined with account maker fees. 10 bps = 0.10%. A larger value permits worse prices; it does not reduce Kraken fees or guarantee fills.',
    max_spread_bps: 'Maximum permitted bid/ask spread in basis points (100 bps = 1%). A tighter limit rejects less liquid or wider markets more often. This is separate from trading fees and slippage. Failing liquidity/data checks can also prevent protective execution; it is not an exchange-held stop.',
    stale_seconds: 'Maximum acceptable age of an execution depth snapshot, in seconds. Smaller values reject older books sooner. WebSocket books also obey their stricter 10-second cap and connection-generation checks. Raising this does not extend account-fee validity or permit incomplete candles. A stale/revoked plan must be rejected or replanned, not submitted unchanged.',
    margin_open_fee_bps: 'Additional modeled borrowing/opening charge for paper margin, on newly opened notional. It is separate from authenticated Kraken maker/taker trading fees. 100 bps = 1%. This is a simulation assumption, not a personal Kraken fee quote or an editable substitute for account trading fees.',
    margin_rollover_bps: 'Modeled paper-margin borrowing charge per four hours of open notional, accrued proportionally to elapsed time. It is separate from trading fees. Holding exposure through pauses still incurs modeled carrying cost when valued again. It does not configure real borrowing terms or Futures funding.',
    maintenance_ratio: 'Paper-margin liquidation threshold: marked collateral equity divided by used margin. A higher threshold triggers simulated maintenance handling earlier. This simplified cross-collateral model is not Kraken’s exact liquidation rule and does not guarantee full liquidation or a maximum loss. Live margin remains blocked.',
  };
  static strategies = {
    htf: 'HTF combines completed-candle trend/cost filters with a Jev decision. Historical momentum is not a forecast. Spot trades owned inventory; margin is paper-only and qualified Futures has separate accounting and live gates.',
    maker: 'Market making uses Jev decisions and bounded post-only quotes with fee-aware offsets. It is rate-limited, not high-frequency trading. Paper fills require later crossing market evidence and limited participation; a touch is not a guaranteed fill and the simulation does not model exchange queue priority.',
    scalp: 'Bollinger is paper-only: one owned position, spot longs or qualified Futures longs/shorts, no Jev. It uses rolling completed one-minute candles, band re-entry and range/cost checks. The entry midpoint target, stop and deadline are saved with the position; no scaling or implicit reversal. WAIT can be correct when costs exceed target room. Stops/deadlines are local, checked only while running; Stop/offline suspends protection and does not close positions.',
    arbitrage: 'Spot triangular arbitrage evaluates a bounded BTC/ETH bridge cycle with per-leg account fees and execution costs, then requires Jev approval. Sequential legs can partially fill or fail. Residual inventory is retained for reconciliation; an estimated edge is not guaranteed profit.',
    dca: 'DCA (dollar-cost averaging) is deterministic scheduled accumulation, with no Jev. It requires whole-run pre-funding, starts checking immediately on Start, skips missed/unfilled slots and pauses on completion. Futures adds direction and optional reduce-only behavior. A new run never automatically replenishes funds.',
    twap: 'TWAP (time-weighted average price) splits a finite parent into deterministic bounded slots, with no Jev. It does not guarantee an average execution price. Supply total quantity, an explicit positive limit, slice count and duration. Spot sells require owned inventory; Futures uses native contracts and a parent notional cap. Missed/unfilled slices are not caught up. Completion pauses the engine.',
    rebalance: 'Spot-only deterministic, sell-first rebalancing of explicit basket weights, including an explicit CASH weight. The anchor market is not the basket. Drift, cooldown, minimum trade, daily fee-inclusive turnover and portfolio caps constrain orders. At most one order per cooldown; no Jev, transfers or automatic funding.',
  };
  static topics = {
    overview: 'Choose Product, bot market and Strategy; then set explicit allocation and risk caps in Capital and review costs/data limits in Execution. Stop first to edit, Save explicitly, and Start separately. Unsaved drafts are not execution inputs. Help stays available while running. Spot holdings remain tracked when changing markets; open owned scalp positions restrict strategy/market changes. Stop cancels orders but retains positions and pauses local protection.',
    mode: 'Dry-run simulates orders and balances but still requires fresh public data and authenticated account trading fees. Trading uses real allocated funds and requires server gates plus explicit confirmation. Bollinger and margin remain paper-only. Mode changes reconcile/cancel tracked orders; they do not flatten positions. A running spot engine can resume after an accepted mode change; pause first if you want it to remain stopped. Futures live arming leaves the engine paused, with Start separate. Restart begins paused in Dry-run, retaining recorded live risk.',
    start: 'Start the saved configuration, not unsaved form edits. Requires a ready market catalog, fresh account fees, sufficient allocated funds and resolved outstanding orders/recovery. Model-assisted strategies require Jev access; deterministic strategies do not. Scheduled runs must be pre-funded. Start resumes local protection for a retained owned scalp position, including an elapsed deadline. It does not guarantee a trade or reset completed runs.',
    stop: 'Pause the engine and cancel/reconcile tracked orders. Stop does not sell spot holdings or close margin/Futures positions. Bollinger’s local stop/deadline checks pause too; its saved plan remains. Market, funding and liquidation risks can continue while paused. Do not treat Stop as an emergency flatten-all order.',
    reconcile: 'Pause, cancel tracked orders and resolve their latest status/fills against the recorded portfolio. Use after uncertain execution or recovery warnings, not as a portfolio reset. Interrupted cycles can require explicit acknowledgment after you inspect balances. It does not discard residual holdings, automatically restart, or guarantee that every exchange uncertainty is resolved.',
    'new-program': 'Explicitly arm a fresh run for the saved DCA, TWAP or rebalance configuration after stopping and reconciling. Requires NEW STRATEGY RUN confirmation. Save edits first. Start remains separate. Existing holdings/history stay; this does not refill the allocation, erase today’s rebalance turnover or catch up missed orders.',
    reset: 'While paused with paper orders settled, replace only the selected product’s paper portfolio using the saved paper starting balance. Simulated positions and relevant paper run/protection state are reset. Historical orders/events and live portfolios are retained. This is different from archiving/deleting history and cannot flatten a real exchange position.',
    'close-futures': 'While paused on Futures, explicitly confirm REDUCE FUTURES POSITION to request one bounded reduce-only exit for the saved bot market in the selected mode. It can reduce risk after a loss halt, but still needs fresh data/fees, settled recovery, valid size and price. The order cap, partial fills or minimum size may leave exposure. Repeat only after inspecting/reconciling the result; it is not flatten-all.',
    save: 'Validate and save the applicable draft settings while paused. Inactive settings keep their saved values; hidden drafts are not silently applied to other strategies/products. Save neither starts the engine nor resets balances. Save a paper starting balance, then explicitly Reset to apply it. Existing scalp protection keeps its entry-time parameters. Scheduled configuration changes can require New strategy run before Start.',
    fees: 'Read-only, authenticated Kraken account maker/taker trading rates per side for saved execution markets, not the chart. Maker orders add resting liquidity; taker orders execute against existing liquidity. Entry and exit can have different notionals and charges. These rates are mandatory in paper and live modes. Missing, failed or expired rates block orders; there is no public-tier fallback or manual override. Check the visible timestamp/error and fee-query access. Check both entry and exit rates. Spread, slippage, margin borrowing and Futures funding are separate costs. Paper fills are estimates, not a backtest or proof of profitability.',
    data: 'Read-only execution feed status at the last engine check. Spot-family execution prefers fresh checksum-validated public WebSocket books and confirmed completed candles, with REST bootstrap/recovery. Invalid/stale data fails closed. Futures uses its separate REST adapter. This is not private order/fill streaming, does not change engine cadence, and does not run protection while paused. Chart navigation never changes execution subscriptions.',
    status: 'Running means engine cycles are enabled, not that an entry is eligible or an order will fill. Paused means local strategy/protection checks are stopped, not that holdings are flat. Live errors and recovery warnings remain visible separately. After a failure or restart, inspect the state before starting again.',
    'saved-strategy': 'The strategy saved on the engine. A different selection in the form is only a draft until Save. Check Execution mode as well: paper simulation and live trading use separate records and safeguards.',
  };
  static paragraphs(key, values, schema) {
    const text = SettingsHelp.fields[key] || SettingsHelp.topics[key];
    if (!text) throw new Error(`Missing settings help: ${key}`);
    const paragraphs = [text];
    if (key === 'strategy' && SettingsHelp.strategies[values.strategy]) paragraphs.push(SettingsHelp.strategies[values.strategy]);
    if (key === 'pair' && values.strategy === 'rebalance') paragraphs.push('For rebalancing this is an anchor market only. Basket weights below determine which assets can trade.');
    const field = schema.fields[key];
    if (field) {
      const initial = field.positive && Number(field.default) === 0 ? 'unconfigured; enter an explicit value' : field.type === 'boolean' ? (field.default ? 'On' : 'Off') : field.choices?.[field.default] ?? String(field.default);
      const bounds = field.min !== undefined ? `Allowed range: ${field.min}–${field.max}. ` : '';
      paragraphs.push(`${bounds}Default: ${initial}. Defaults are starting values, not a profitability recommendation; strategy and portfolio checks can impose tighter limits.`);
    }
    return paragraphs;
  }
  constructor(panel, form, schema) {
    this.panel = panel; this.form = form; this.schema = schema; this.entries = new Map();
    this.popup = document.createElement('div'); this.popup.id = 'settings-help-popup'; this.popup.className = 'settings-help-popup';
    this.popup.setAttribute('popover', 'manual'); this.popup.setAttribute('role', 'dialog'); this.popup.tabIndex = -1;
    this.popup.setAttribute('aria-labelledby', 'settings-help-title');
    this.title = document.createElement('strong'); this.title.id = 'settings-help-title';
    const popupHeading = document.createElement('div'); popupHeading.className = 'settings-help-heading';
    const close = document.createElement('button'); close.type = 'button'; close.className = 'settings-help-close';
    close.setAttribute('aria-label', 'Close help'); close.textContent = '×'; close.addEventListener('click', () => this.hide(true));
    popupHeading.append(this.title, close);
    this.body = document.createElement('div'); this.body.className = 'settings-help-body'; this.body.tabIndex = 0;
    this.body.setAttribute('aria-labelledby', this.title.id);
    this.popup.append(popupHeading, this.body); document.body.append(this.popup);
    for (const [key, field] of Object.entries(schema.fields)) {
      if (field.editable === false) continue;
      const control = key === 'pair' ? document.getElementById('pair-search') : form.elements.namedItem(key);
      const label = key === 'pair' ? document.getElementById('bot-market-label') : control.closest('label');
      const heading = document.createElement('div'); heading.className = 'setting-heading';
      if (key === 'pair') { label.before(heading); heading.append(label); }
      else {
        const wrapper = document.createElement('div'); wrapper.className = `setting-field ${label.className}`;
        label.before(wrapper); control.remove(); label.className = '';
        control.id ||= `setting-${key}`; label.htmlFor = control.id;
        heading.append(label); wrapper.append(heading);
        if (field.type === 'boolean') wrapper.prepend(control); else wrapper.append(control);
      }
      this.attach(key, heading, label, control);
    }
    for (const id of ['start', 'stop', 'reconcile', 'new-program', 'reset', 'close-futures', 'save']) {
      const control = document.getElementById(id === 'save' ? 'save-settings' : id);
      const wrapper = document.createElement('div'); wrapper.className = 'help-action'; wrapper.dataset.helpAction = id;
      control.before(wrapper); wrapper.append(control); this.attach(id, wrapper, control, control);
    }
    for (const [key, id] of [['overview', 'settings-title'], ['status', 'engine-status'], ['saved-strategy', 'engine-strategy'], ['fees', 'fees-heading'], ['data', 'data-heading']]) {
      const label = document.getElementById(id), heading = document.createElement('div'); heading.className = 'setting-heading';
      label.before(heading); heading.append(label); this.attach(key, heading, label);
    }
    const mode = document.getElementById('mode'), label = mode.closest('label');
    const heading = document.createElement('div'); heading.className = 'setting-heading';
    label.before(heading); heading.append(label); this.attach('mode', heading, label.firstChild, mode);
    this.popup.addEventListener('pointerenter', () => clearTimeout(this.timer));
    this.popup.addEventListener('pointerleave', () => this.scheduleHide());
    this.popup.addEventListener('focusout', () => this.scheduleHide());
    document.addEventListener('pointerdown', event => {
      if (this.active && !this.popup.contains(event.target) && !this.active.trigger.contains(event.target)) this.hide();
    });
    document.addEventListener('focusin', event => {
      if (this.active && !this.popup.contains(event.target) && event.target !== this.active.trigger) this.hide();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && this.active) { event.preventDefault(); event.stopPropagation(); this.hide(true); }
    }, true);
    panel.addEventListener('click', event => { if (event.target.closest('[role="tab"]')) this.hide(); });
    panel.addEventListener('scroll', () => { if (this.active) this.position(); }, true);
    window.addEventListener('resize', () => this.hide());
    window.addEventListener('blur', () => this.hide());
    this.refresh();
  }
  attach(key, parent, label, control) {
    // Anchors with button semantics remain usable inside a disabled fieldset.
    // Keep the fieldset's native input/action lock intact; never unlock it for help.
    const trigger = document.createElement('a'); trigger.href = '#settings-help-popup'; trigger.className = 'settings-help-trigger';
    trigger.setAttribute('role', 'button'); trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-controls', this.popup.id); trigger.dataset.help = key;
    const icon = document.createElement('span'); icon.className = 'icon icon-help'; icon.setAttribute('aria-hidden', 'true'); icon.textContent = '?'; trigger.append(icon);
    const description = document.createElement('span'); description.id = `settings-description-${key}`; description.hidden = true; document.body.append(description);
    trigger.setAttribute('aria-describedby', description.id);
    if (control) control.setAttribute('aria-describedby', [control.getAttribute('aria-describedby'), description.id].filter(Boolean).join(' '));
    const entry = {key, label, trigger, description}; this.entries.set(key, entry); parent.append(trigger);
    trigger.addEventListener('pointerenter', event => { if (event.pointerType !== 'touch' && !this.pinned) this.show(entry); });
    trigger.addEventListener('pointerleave', () => this.scheduleHide());
    trigger.addEventListener('focus', () => { if (!this.restoringFocus) this.show(entry); });
    trigger.addEventListener('blur', () => this.scheduleHide());
    trigger.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation();
      if (this.active === entry && this.pinned) this.hide();
      else {
        this.show(entry);
        if (this.active === entry) { this.pinned = true; this.body.focus({preventScroll: true}); }
      }
    });
    trigger.addEventListener('keydown', event => {
      if (event.key === ' ') { event.preventDefault(); trigger.click(); }
    });
  }
  refresh() {
    const values = {product: this.form.elements.namedItem('product').value, strategy: this.form.elements.namedItem('strategy').value};
    for (const entry of this.entries.values()) {
      const label = entry.label.cloneNode(true);
      label.querySelectorAll?.('[aria-hidden="true"]').forEach(node => node.remove());
      entry.heading = label.textContent.trim();
      entry.paragraphs = SettingsHelp.paragraphs(entry.key, values, this.schema);
      entry.trigger.setAttribute('aria-label', `Help: ${entry.heading}`);
      entry.description.textContent = entry.paragraphs.join('\n\n');
    }
    if (this.active) {
      if (!this.active.trigger.getClientRects().length) this.hide();
      else { this.draw(); this.position(); }
    }
  }
  show(entry) {
    clearTimeout(this.timer);
    if (this.active !== entry) { this.hide(); this.active = entry; }
    entry.trigger.setAttribute('aria-expanded', 'true');
    this.draw();
    if (!this.popup.matches(':popover-open')) this.popup.showPopover();
    this.position();
  }
  draw() {
    const entry = this.active, text = entry.paragraphs.join('\n\n');
    if (this.body.textContent !== text) {
      this.body.textContent = text; this.body.scrollTop = 0;
    }
    this.title.textContent = entry.heading;
  }
  position() {
    const rect = this.active.trigger.getBoundingClientRect(), panel = this.panel.getBoundingClientRect();
    const bounds = (this.active.trigger.closest('.settings-scroll') || this.panel).getBoundingClientRect();
    if (rect.bottom <= Math.max(0, bounds.top, panel.top) || rect.top >= Math.min(innerHeight, bounds.bottom, panel.bottom) || !this.active.trigger.getClientRects().length) { this.hide(); return; }
    // Never cover the trigger: opening on hover/focus must not intercept its click.
    const above = rect.top - 14, below = innerHeight - rect.bottom - 14;
    this.popup.style.maxHeight = `${Math.min(420, Math.max(above, below))}px`;
    const box = this.popup.getBoundingClientRect();
    this.popup.style.left = `${Math.max(8, Math.min(innerWidth - box.width - 8, rect.right - box.width))}px`;
    const top = box.height <= below ? rect.bottom + 6 : rect.top - box.height - 6;
    this.popup.style.top = `${Math.max(8, Math.min(innerHeight - box.height - 8, top))}px`;
  }
  scheduleHide() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      if (this.active && !this.pinned && !this.active.trigger.matches(':hover, :focus') && !this.popup.matches(':hover, :focus-within')) this.hide();
    }, 180);
  }
  hide(restore = false) {
    clearTimeout(this.timer);
    const trigger = this.active?.trigger;
    trigger?.setAttribute('aria-expanded', 'false'); this.active = null; this.pinned = false;
    if (this.popup.matches(':popover-open')) this.popup.hidePopover();
    if (restore && trigger?.getClientRects().length) {
      this.restoringFocus = true; trigger.focus({preventScroll: true}); this.restoringFocus = false;
    }
  }
}
window.SettingsHelp = SettingsHelp;
