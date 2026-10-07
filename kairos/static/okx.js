/* Explicit finite-run consent only. No credentials or client-side monetary arithmetic. */
class OKXOperations {
  static enabled(state) { return ['okx', 'okx-demo'].includes(state?.exchange); }
  constructor(request, render, message) {
    this.request = request; this.renderState = render; this.message = message;
    this.$ = id => document.getElementById(`okx-${id}`);
    this.dialog = this.$('dialog'); this.pending = false; this.proposal = null;
    this.$('open').addEventListener('click', () => this.open('execution_cycle'));
    this.$('close').addEventListener('click', () => this.dialog.close());
    this.$('kind').addEventListener('change', () => this.changed());
    for (const input of this.$('inputs').querySelectorAll('input, select')) input.addEventListener('input', () => this.changed());
    this.$('confirmation').addEventListener('input', () => this.buttons());
    this.$('preview').addEventListener('click', () => this.preview());
    this.$('authorize').addEventListener('click', () => this.authorize());
    // Independent of an in-flight preview/authorization: Stop must remain reachable.
    this.$('dialog-stop').addEventListener('click', async () => {
      this.proposal = null; this.buttons();
      try { this.renderState(await this.request('stop', {})); }
      catch (error) { this.$('status').textContent = error.message; }
    });
  }
  setPairs(pairs) {
    const chosen = this.$('pair').value;
    const option = (value, label) => { const row = document.createElement('option'); row.value = value; row.textContent = label; return row; };
    const markets = [...pairs].sort((a, b) => Number(b.base === 'BTC') - Number(a.base === 'BTC') || a.symbol.localeCompare(b.symbol));
    this.$('pair').replaceChildren(option('', 'Choose an account-enabled instrument / currency…'), ...markets.map(p => option(p.id, p.name || p.symbol)));
    this.$('pair').value = markets.some(p => p.id === chosen) ? chosen : '';
  }
  changed() {
    this.proposal = null; this.$('confirmation').value = '';
    this.$('preview-data').textContent = '';
    this.$('status').textContent = 'Not authorized. Run a new read-only preflight after changing terms.';
    this.buttons();
  }
  buttons() {
    const cycle = this.$('kind').value === 'execution_cycle';
    for (const label of this.dialog.querySelectorAll('[data-okx-cycle]')) label.hidden = !cycle;
    this.$('allowance-label').hidden = !cycle || this.state?.environment !== 'demo';
    this.$('preview').disabled = this.pending || !this.connected || this.state?.running;
    this.$('authorize').disabled = this.pending || !this.connected || this.state?.running || !this.proposal?.write_gate || this.$('confirmation').value !== this.proposal?.confirmation || Date.now()/1000 >= (this.proposal?.expires_at || 0);
    this.$('dialog-stop').disabled = !this.connected;
  }
  render(state, connected) {
    this.state = state; this.connected = connected;
    const enabled = OKXOperations.enabled(state);
    this.$('open').hidden = this.$('evidence').hidden = !enabled;
    if (!enabled) return;
    this.$('open').disabled = !connected || state.running;
    this.$('title').textContent = state.environment === 'demo' ? 'OKX Demo — virtual funds' : 'OKX Live — real funds';
    this.$('notice').textContent = `${state.credentials_configured ? 'Credentials configured privately' : 'Credentials missing; see OKX.md for private setup'}. Write gate ${state.write_gate ? 'enabled' : 'disabled'}; ${state.armed ? 'finite permission active' : 'unarmed'}. Account ${state.account_identity || 'unbound — preflight required'}. ${state.market_data?.status || 'Data unavailable'}. Monetary amounts use the selected asset, never interchangeable USD/stablecoin balances.`;
    this.$('result').textContent = state.execution_cycle ? JSON.stringify(state.execution_cycle, null, 2) : 'Not run / authorization required. Mocks and quotes do not establish hosted execution.';
    this.buttons();
  }
  open(kind) {
    if (!OKXOperations.enabled(this.state)) return;
    this.$('kind').value = kind;
    if (!this.$('pair').value) this.$('pair').value = this.state.settings.pair;
    if (this.state.ledger) this.$('allocation').value = this.state.ledger.initial;
    this.$('allocation').readOnly = Boolean(this.state.ledger);
    this.changed();
    this.dialog.showModal();
    this.$('pair').focus();
  }
  async preview() {
    if (this.pending) return;
    if (!this.$('pair').value) { this.$('status').textContent = 'Choose the exact instrument and spending currency; no substitute is selected.'; return; }
    for (const input of this.$('inputs').querySelectorAll('input')) {
      if (!input.closest('label').hidden && !input.reportValidity()) return;
    }
    const cycle = this.$('kind').value === 'execution_cycle';
    const body = {kind: cycle ? 'execution_cycle' : 'twap', pair: this.$('pair').value, allocation: this.$('allocation').value};
    if (cycle) Object.assign(body, {budget: this.$('budget').value, buy_ceiling: this.$('buy').value, sell_floor: this.$('sell').value, max_exit_attempts: Number(this.$('exits').value), duration_seconds: Number(this.$('duration').value), demo_fee_allowance_bps: this.state.environment === 'demo' ? this.$('allowance').value : null});
    this.pending = true; this.proposal = null; this.buttons();
    this.$('status').textContent = 'Read-only native account, instrument, fee and data preflight. No orders are authorized.';
    try {
      const response = await this.request('okx-preview', body);
      this.renderState(response.state);
      this.proposal = response.preview;
      const keys = ['kind','environment','account','instrument','spending_currency','allocation','budget','quantity','fee_bps','fee_source','buy_ceiling','sell_floor','slippage_bps','attempts','duration_seconds','dust_policy','write_gate','confirmation'];
      this.$('preview-data').textContent = JSON.stringify(Object.fromEntries(keys.map(k => [k, this.proposal[k]])), null, 2);
      this.$('confirmation').value = '';
      this.$('confirmation').placeholder = this.proposal.confirmation;
      this.$('status').textContent = `Preflight completed; no orders submitted. Expires ${new Date(this.proposal.expires_at * 1000).toLocaleTimeString()}. Type the exact confirmation to authorize only these terms.${this.proposal.write_gate ? '' : ' Server write gate is disabled.'}`;
    } catch (error) { this.$('status').textContent = error.message; }
    finally { this.pending = false; this.buttons(); }
  }
  async authorize() {
    if (this.$('authorize').disabled || !this.proposal) return;
    const proposal = this.proposal;
    this.pending = true; this.buttons();
    this.$('status').textContent = 'Rechecking this one-use authorization. Stop remains available.';
    try {
      this.renderState(await this.request('okx-authorize', {preview_id: proposal.id, confirmation: this.$('confirmation').value}));
      this.proposal = null;
      this.$('confirmation').value = '';
      this.dialog.close();
      this.$('evidence').open = true;
      this.message('Finite permission issued. Inspect execution evidence; order acceptance is not a successful cycle.');
    } catch (error) { this.proposal = null; this.$('status').textContent = error.message; }
    finally { this.pending = false; this.buttons(); }
  }
}
window.OKXOperations = OKXOperations;
