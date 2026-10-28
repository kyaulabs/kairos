/* Presentation only: collapsing records never changes trading or saved settings. */
class RecordsPanel {
  constructor(panel, selectTab) {
    this.panel = panel;
    this.workspace = panel.closest('.workspace');
    this.tabs = [...panel.querySelectorAll('[role="tab"]')];
    this.button = panel.querySelector('#records-toggle');
    this.selectTab = selectTab;
    this.button.addEventListener('click', () => this.toggle());
    this.setOpen(true);
  }
  setOpen(open) {
    this.expanded = Boolean(open);
    this.workspace.classList.toggle('records-collapsed', !this.expanded);
    const label = this.expanded ? 'Collapse lower panels' : 'Expand lower panels';
    this.button.setAttribute('aria-expanded', String(this.expanded));
    this.button.setAttribute('aria-label', label);
    this.button.title = label;
    for (const tab of this.tabs) {
      const visible = this.expanded && tab.getAttribute('aria-selected') === 'true';
      tab.setAttribute('aria-expanded', String(visible));
      this.panel.querySelector('#' + tab.getAttribute('aria-controls')).hidden = !visible;
    }
  }
  click(tab) {
    if (this.expanded && tab.getAttribute('aria-selected') === 'true') this.setOpen(false);
    else this.selectTab(tab);
  }
  toggle() {
    if (this.expanded) this.setOpen(false);
    else this.selectTab(this.tabs.find(tab => tab.getAttribute('aria-selected') === 'true') || this.tabs[0]);
  }
}
window.RecordsPanel = RecordsPanel;
