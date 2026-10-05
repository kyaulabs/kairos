/* Read-only projection. No requests, subscriptions, settings changes or trading actions. */
class StrategyReview {
  constructor(root) { this.root = root; }
  static number(value) {
    if (value == null || value === '' || typeof value === 'boolean' || !Number.isFinite(Number(value))) return 'Unavailable';
    return Number(value).toLocaleString(undefined, {maximumFractionDigits: 8});
  }
  static time(value) { return value == null ? 'Unavailable' : new Date(value * 1000).toLocaleString(); }
  rows(id, rows) {
    const container = this.root.querySelector(`#${id}`);
    container.replaceChildren();
    for (const [label, value] of rows) {
      const row = document.createElement('div'), name = document.createElement('span'), text = document.createElement('span');
      row.className = 'review-row'; name.textContent = label; text.textContent = value;
      row.append(name, text); container.append(row);
    }
  }
  text(id, value) { this.root.querySelector(`#${id}`).textContent = value; }
  render(state, connected) {
    const report = state.review, n = StrategyReview.number, t = StrategyReview.time;
    this.text('review-status', `${connected ? 'Read-only' : 'Disconnected · cached state only'} · ${state.exchange} · ${state.settings.strategy} · ${state.settings.pair} · saved settings, not drafts. Snapshot ${t(report?.generated_at)}.`);
    if (!report) {
      for (const id of ['review-readiness', 'review-funnel', 'review-costs', 'review-program', 'review-holdings']) this.rows(id, [['Report', 'Unavailable; updated backend required']]);
      this.text('review-blockers', ''); this.text('review-exposure-note', 'Holdings not assessed.'); return;
    }
    const data = report.data, costs = report.costs, entry = report.entry, checks = state.decision_summary?.entry_checks, program = report.program;
    const readiness = [
      ['Engine', state.operations ? state.operations.status.replaceAll('-', ' ') : state.running ? 'Running' : 'Paused · not evaluating new entries'],
      ['Last assessment', t(data.assessment_at)],
      ['Assessment at snapshot', connected && data.assessment_current ? 'Within review cadence; not an order authorization' : 'Unavailable, stale or paused'],
      ['Halt / recovery', state.error || (state.recovery_required ? 'Reconciliation required' : 'None reported')],
    ];
    if (state.operations) {
      readiness.push(['Operating state / since', `${state.operations.status} / ${t(state.operations.since)}`], ['Last strategy evaluation · service session', t(state.operations.last_evaluation_at)]);
      for (const [status, seconds] of Object.entries(state.operations.session_seconds || {})) readiness.push([`${status} · service-session seconds`, n(seconds)]);
      const alerts = state.operations.notifications;
      readiness.push(['Discord alerts', alerts?.configured ? `${alerts.status} · ${t(alerts.last_at)}` : alerts?.status || 'Not configured']);
    }
    if (state.diagnostic_observation) {
      const observed = state.diagnostic_observation;
      readiness.push(['Last read-only native window', `${observed.pair} · ${observed.bar_minutes}m · ${t(observed.window_end)}`], ['Diagnostic bars / required', `${n(observed.consecutive)} / ${n(observed.required)}`], ['Diagnostic record time', t(observed.observed_at)], ['Diagnostic permission', 'History only; does not clear a halt or authorize orders']);
    }
    if (state.scheduled_recovery) {
      const retry = state.scheduled_recovery;
      readiness.push(['Scheduled recovery', `${retry.status} · ${retry.attempts}/${retry.max_attempts}`], ['Next recovery attempt', t(retry.next_at)], ['Recovery reason', retry.reason]);
    }
    if (state.settings.strategy === 'htf') {
      readiness.push(['HTF history policy', data.history_policy || 'Unavailable']);
      if (data.required_native_bars != null) readiness.push(
        [`Completed native ${n(data.bar_minutes)}-minute bars`, `${n(data.consecutive_native_bars)} / ${n(data.required_native_bars)}`],
        ['Additional consecutive native bars needed', n(data.native_bar_shortfall)],
        ['Native window end', t(data.window_end)],
        ['History fetched', t(data.history_fetched_at)],
        ['History revision', data.history_revision || 'Unavailable'],
      );
      else readiness.push(
        ['Confirmed minute suffix', `${n(data.consecutive_minutes)} / ${n(data.required_minutes)}`],
        ['Additional consecutive minutes needed', n(data.consecutive_shortfall)],
        ['Rolling window end', t(data.window_end)],
      );
      readiness.push(['Review', data.status || 'Unavailable']);
    }
    else readiness.push(['Candle readiness', ['dca','twap','rebalance'].includes(state.settings.strategy) ? 'Not required; fresh quotes/risk checks still apply' : 'No separate rolling coverage count recorded']);
    const feed = state.market_data, requests = feed?.requests;
    if (state.exchange === 'alpaca') {
      readiness.push(['Execution feed', feed?.status || 'Unavailable'], ['Stream diagnostic', feed?.error || 'None reported']);
      for (const book of feed?.books || []) readiness.push(
        [`${book.pair} source / fresh at snapshot`, `${book.source || 'Unavailable'} / ${book.fresh ? 'yes' : 'no'}`],
        ['Market timestamp / age · seconds', `${t(book.market_at)} / ${n(book.market_age_seconds)}`],
        ['Book received locally', t(book.received_at)],
      );
      if (requests) {
        readiness.push(['Request admissions / minute budget', `${n(requests.admissions_last_minute)} / ${n(requests.budget_per_minute)} (includes rejected preflights)`], ['Queued requests', n(requests.queued)]);
        const last = requests.last_response;
        readiness.push(['Last HTTP endpoint / status', last ? `${last.method} ${last.endpoint} / ${last.http_status}` : 'Unavailable'], ['Last HTTP latency · seconds', n(last?.latency_seconds)]);
        for (const [bucket, quota] of Object.entries(requests.quotas)) readiness.push(
          [`${bucket} quota remaining / limit`, `${n(quota.remaining)} / ${n(quota.limit)}`],
          [`${bucket} quota observed / reset`, `${t(quota.observed_at)} / ${t(quota.reset)}`],
          [`${bucket} backoff · seconds`, n(quota.retry_in_seconds)],
        );
      }
      const account = state.account_reads;
      if (account) {
        const failure = account.last_failure, recovery = account.recovery;
        readiness.push(['Last full account reconciliation', t(account.last_success_at)]);
        readiness.push(['Last execution account-read failure', failure ? `${failure.method} ${failure.endpoint} · ${failure.reason} · ${t(failure.at)}` : 'None recorded this service session']);
        if (recovery) readiness.push(
          ['Account-read recovery', `${recovery.status} · ${n(recovery.attempts)} / ${n(recovery.max_attempts)} recovery attempts · ${n(recovery.timeout_seconds)}s retry deadline`],
          ['Account recovery since / ended', `${t(recovery.since)} / ${t(recovery.ended_at)}`],
          ['Account recovery duration · seconds', n(recovery.duration_seconds)],
          ['Account recovery execution', recovery.status === 'waiting' ? `Orders and local exits blocked; next retry no earlier than ${t(recovery.next_retry_at)}. Stop and loss halts take precedence.` : 'Episode ended; normal safety gates still apply. No order write was retried.'],
        );
      }
      if (state.market_wait) readiness.push(['Data wait', `Since ${t(state.market_wait.since)}; ${state.market_wait.timeout_seconds}s maximum. Orders blocked; local exits may be unavailable.`]);
    }
    this.rows('review-readiness', readiness);
    this.rows('review-funnel', [
      ['Execution run', report.run_id ? report.run_id.slice(0, 8) : 'Not started for this configuration/revision'],
      ['Assessments', n(state.decision_summary?.assessments)],
      ['Distinct recorded windows / v2 candidates', `${n(state.decision_summary?.distinct_windows)} / ${n(state.decision_summary?.distinct_candidates)}`],
      ['Trial end', t(state.execution_run?.trial_ends_at)],
      ['Trial protocol', state.execution_run?.protocol_hash || 'Not a registered multi-bar trial'],
      ['Paper execution qualification', state.paper_qualification?.status || 'Not performed'],
      ['Trial interpretation', state.settings.htf_policy === 'multibar-v2' ? '14 calendar days; ≥10 candidates, ≥5 accepted entries, ≥95% running availability. Economics inconclusive below 30 completed owned lineages; no automatic extension.' : 'Legacy experiment; no profitability claim'],
      [state.settings.htf_policy === 'multibar-v2' ? 'New closed-bar pattern positive / checks' : 'Raw entry checks positive / observed', `${n(checks?.signals)} / ${n(checks?.observed)}`],
      [state.settings.htf_policy === 'multibar-v2' ? 'Candidate + cost gate passed / checks' : 'Setup + cost checks passed / observed', `${n(checks?.cost_qualified)} / ${n(checks?.cost_observed)}`],
      ['Latest entry readiness', entry.entry_ready == null ? 'Unavailable' : entry.entry_ready ? 'Eligible at assessment; execution must recheck' : 'Not ready at assessment'],
      ['Order records / with fills', `${report.orders.records} / ${report.orders.filled}`],
      ['Partial / working / terminal unfilled', `${report.orders.partial} / ${report.orders.working} / ${report.orders.terminal_unfilled}`],
    ]);
    const blockers = this.root.querySelector('#review-blockers'); blockers.replaceChildren();
    const structural = state.decision?.state?.structural_gates || {};
    const reasons = [...Object.entries(structural).filter(([,pass]) => !pass).map(([gate]) => `Latest recorded structural gate failed: ${gate}`), ...(entry.entry_blockers || []).map(reason => `Latest assessment: ${reason}`), ...(state.decision_summary?.hold_reasons || []).map(row => `${row.count} HOLD assessments: ${row.reason}`)];
    for (const reason of reasons) { const item = document.createElement('li'); item.textContent = reason; blockers.append(item); }
    const risk = [
      ['Costs recorded at', t(data.assessment_at)],
      ['Entry passive / exit taker fee · bps', `${n(costs.maker_fee_bps)} / ${n(costs.taker_fee_bps)}`],
      ['Observed spread · bps', n(costs.spread_bps)],
      ['Configured slippage bound · bps', n(state.settings.slippage_bps)],
      ['Round-trip cost screen · bps', n(costs.round_trip_cost_bps)],
      ['Long / short net target room · bps', costs.target_net_room_bps ? `${n(costs.target_net_room_bps.buy)} / ${state.settings.product === 'spot' ? 'Not permitted' : n(costs.target_net_room_bps.sell)}` : n(costs.net_room_bps)],
      ['Required net target buffer · bps', n(costs.required_net_room_bps)],
      ['Effective order / exposure cap · USD', `${n(state.effective_order_cap)} / ${n(state.effective_exposure_cap)}`],
      ['Daily loss halt · USD', n(state.settings.daily_loss)],
      ['Saved stop / target price', report.position ? `${n(report.position.stop)} / ${n(report.position.target)}` : 'No selected-strategy position plan'],
      ['Saved stop distance from entry limit · bps', n(report.stop_distance_bps)],
      ['Saved holding deadline', t(report.position?.deadline)],
    ];
    for (const fee of state.fees?.markets || []) risk.push([`${fee.symbol} planning fee · passive / taker bps`, `${n(fee.maker_bps)} / ${n(fee.taker_bps)} (fee snapshot; separate from assessment)`]);
    if (state.exchange === 'alpaca') risk.push(['Fee attribution', 'Planning estimates only; posted fees are account-wide in Portfolio, not allocated to orders/runs']);
    this.rows('review-costs', risk);
    const execution = program ? [
      ['Program', `${program.id.slice(0, 8)} · ${program.status}${program.configuration_changed ? ' · saved settings differ; rearm required' : ''}`],
      ['Last outcome', program.message], ['Next slot', t(program.next_at)],
      ['Claimed / elapsed missed slots', `${program.claimed_slots} / ${program.missed_slots}`],
      ['Order records / with fills / partial', `${program.orders.records} / ${program.orders.filled} / ${program.orders.partial}`],
      ['Working / terminal unfilled', `${program.orders.working} / ${program.orders.terminal_unfilled}`],
      ['Turnover incl. fee reserves · USD', n(program.turnover_with_fee_reserve)],
    ] : [['Program', 'No saved schedule for this strategy']];
    if (program?.budget != null) execution.push(['DCA budget / unspent allowance · USD', `${n(program.budget)} / ${n(program.unspent_allowance)}`]);
    if (program?.parent_quantity != null) execution.push(['TWAP parent / unfilled native quantity', `${n(program.parent_quantity)} / ${n(program.unfilled_quantity)}`]);
    for (const [market, sides] of Object.entries(program?.markets || {})) for (const [side, totals] of Object.entries(sides)) execution.push([`${market} ${side} requested / filled native quantity`, `${n(totals.volume)} / ${n(totals.filled)}`]);
    this.rows('review-program', execution);
    const holdings = Object.entries(report.holdings), warning = holdings.length > 0 && !state.running;
    this.root.querySelector('#review-exposure-note').classList.toggle('warning', warning);
    this.text('review-exposure-note', `${warning ? 'Paused or complete does not mean flat. ' : ''}${holdings.length ? 'Assets remain exposed to price changes. ' : 'No non-cash holdings recorded in this product. '}${report.working_orders} working order records in this product. Stop and daily-loss halts do not guarantee liquidation or a maximum loss. Local stops/deadlines run only while started and connected; DCA/TWAP do not create automatic sell plans.`);
    this.rows('review-holdings', holdings.map(([asset, quantity]) => [asset, n(quantity)]));
  }
}
window.StrategyReview = StrategyReview;
