/* Settings navigation only. Collapsing the drawer never stops or starts an engine. */
class SettingsSidebar {
  constructor(selectTab) {
    this.root = document.getElementById('settings-sidebar');
    this.panel = document.getElementById('settings-panel');
    this.toggle = document.getElementById('sidebar-toggle');
    this.backdrop = document.getElementById('settings-backdrop');
    this.shortcuts = [...this.root.querySelectorAll('[data-settings-tab]')];
    this.mobile = matchMedia('(max-width: 75em)');
    this.expanded = false; this.selected = 'strategy-tab';
    document.getElementById('sidebar-body').append(this.panel);
    this.toggle.addEventListener('click', () => this.setOpen(!this.expanded));
    this.backdrop.addEventListener('click', () => this.setOpen(false, true));
    for (const button of this.shortcuts) button.addEventListener('click', () => selectTab(document.getElementById(button.dataset.settingsTab)));
    this.mobile.addEventListener('change', () => this.render());
    document.addEventListener('keydown', event => {
      if (!this.expanded || event.defaultPrevented || document.querySelector(':popover-open, dialog[open]')) return;
      if (event.key === 'Escape') { event.preventDefault(); this.setOpen(false, true); }
      if (event.key === 'Tab' && this.mobile.matches && this.root.contains(document.activeElement)) {
        const controls = [...this.root.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), [tabindex="0"]')].filter(e => e.getClientRects().length);
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    this.render();
  }
  reveal(tab) { this.selected = tab; this.setOpen(true); }
  setOpen(open, restoreFocus = false) {
    this.expanded = open; this.render();
    if (restoreFocus) this.toggle.focus();
  }
  render() {
    document.body.classList.toggle('sidebar-open', this.expanded);
    this.panel.hidden = !this.expanded;
    this.toggle.setAttribute('aria-expanded', String(this.expanded));
    const label = `${this.expanded ? 'Close' : 'Open'} Kairos settings`;
    this.toggle.setAttribute('aria-label', label); this.toggle.title = label;
    this.backdrop.hidden = !this.expanded || !this.mobile.matches;
    for (const button of this.shortcuts) button.setAttribute('aria-expanded', String(this.expanded && button.dataset.settingsTab === this.selected));
    for (const element of document.querySelectorAll('.desk-header, main.desk, .desk-footer')) element.inert = this.expanded && this.mobile.matches;
  }
}
window.SettingsSidebar = SettingsSidebar;
