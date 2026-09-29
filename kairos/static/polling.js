/* Read-only dashboard polling. Engine cadence and the safety event stream are separate. */
class VisiblePoller {
  constructor(read, interval) {
    this.read = read; this.interval = interval; this.enabled = false;
    this.timer = null; this.controller = null; this.pending = false;
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        clearTimeout(this.timer); this.controller?.abort();
      } else if (this.enabled) this.refresh();
    });
  }
  start() { this.enabled = true; this.refresh(); }
  stop() {
    this.enabled = false; this.pending = false;
    clearTimeout(this.timer); this.controller?.abort();
  }
  refresh() {
    clearTimeout(this.timer);
    if (!this.enabled || document.hidden) return;
    this.pending = true;
    if (this.controller) this.controller.abort();
    else this.poll();
  }
  async poll() {
    if (!this.enabled || document.hidden || this.controller) return;
    this.pending = false;
    const controller = this.controller = new AbortController();
    try { await this.read(controller.signal); }
    catch (error) {
      if (error.name !== 'AbortError') console.error('Dashboard poll failed:', error.name);
    } finally {
      this.controller = null;
      if (this.enabled && !document.hidden) {
        this.timer = setTimeout(() => this.poll(), this.pending ? 0 : this.interval);
      }
    }
  }
}
window.VisiblePoller = VisiblePoller;
