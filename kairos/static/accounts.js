/* Explicit read-only exchange views. Never pooled with bot allocation or paper equity. */
class AccountView {
  constructor(request) {
    this.request = request;
    this.source = document.getElementById('account-source');
    this.refresh = document.getElementById('account-refresh');
    this.status = document.getElementById('account-status');
    this.head = document.getElementById('account-head');
    this.body = document.getElementById('account-rows');
    this.data = null; this.busy = false;
    this.refresh.addEventListener('click', () => this.load());
    this.source.addEventListener('change', () => { this.data = null; this.head.replaceChildren(); this.body.replaceChildren(); this.load(); });
  }
  open() { if (!this.data && !this.busy) this.load(); }
  async load() {
    if (this.busy) return;
    this.busy = true; this.source.disabled = this.refresh.disabled = true;
    this.status.textContent = 'Loading read-only account snapshot…';
    try {
      const data = await this.request(`accounts/${encodeURIComponent(this.source.value)}`);
      if (data.source !== this.source.value || !Array.isArray(data.columns) || !Array.isArray(data.rows) || !Number.isFinite(data.received)) throw new Error('Invalid account snapshot');
      const head = document.createElement('tr');
      for (const name of data.columns) { const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = name; head.append(cell); }
      const fragment = document.createDocumentFragment();
      for (const values of data.rows) {
        const row = document.createElement('tr');
        for (const value of values) { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); }
        fragment.append(row);
      }
      this.head.replaceChildren(head); this.body.replaceChildren(fragment); this.data = data;
      this.status.textContent = `${data.title} · Snapshot ${new Date(data.received*1000).toLocaleString()} · ${data.rows.length ? `${data.rows.length} rows` : 'No records returned'}${data.truncated ? ' · PARTIAL: additional records not shown' : ''}. Manual refresh; server cache up to 30s.`;
    } catch (error) {
      this.status.textContent = `${error.message}${this.data ? ` · Retaining older snapshot from ${new Date(this.data.received*1000).toLocaleString()}.` : ' · No account data loaded.'}`;
    } finally { this.busy = false; this.source.disabled = this.refresh.disabled = false; }
  }
}
window.AccountView = AccountView;
