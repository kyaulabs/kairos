/* Configuration drafts only: selecting here never changes the chart or saves settings. */
class StrategyMarketPicker {
  constructor() {
    this.root = document.getElementById('strategy-market');
    this.input = document.getElementById('pair-search');
    this.value = document.getElementById('pair');
    this.list = document.getElementById('pair-options');
    this.status = document.getElementById('pair-status');
    this.pairs = []; this.context = {}; this.active = -1;
    this.input.addEventListener('focus', () => this.open());
    this.input.addEventListener('click', () => { if (this.list.hidden) this.open(); });
    this.input.addEventListener('input', () => { this.expand(); this.render(); });
    this.input.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !this.list.hidden) {
        event.preventDefault(); event.stopPropagation(); this.close();
      } else if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
        event.preventDefault();
        if (this.list.hidden) this.open();
        const count = this.list.children.length;
        if (!count) return;
        const step = event.key === 'ArrowDown' ? 1 : -1;
        this.activate(this.active < 0 ? (step === 1 ? 0 : count-1) : (this.active + step + count) % count);
      } else if (event.key === 'Enter') {
        event.preventDefault(); // Never implicitly submit settings from this picker.
        if (this.list.hidden) this.open();
        else if (this.active >= 0) this.choose(this.matches[this.active]);
      } else if (event.key === 'Tab') this.close();
    });
    this.list.addEventListener('pointerdown', event => event.preventDefault());
    this.list.addEventListener('click', event => {
      const option = event.target.closest('[role="option"]');
      if (option) this.choose(this.matches[Number(option.dataset.index)]);
    });
    document.addEventListener('pointerdown', event => {
      if (!this.root.contains(event.target)) this.close();
    });
    this.input.addEventListener('blur', () => this.close());
  }
  static limitation(pair, context) {
    if (pair.execution_reason) return pair.execution_reason;
    if (pair.quote !== context.quote) return `Quoted in ${pair.symbol.split('/')[1]}; this portfolio uses ${context.quoteLabel || context.quote}.`;
    if (context.product === 'margin') {
      if (context.mode === 'trading') return 'Margin is paper-only in Kairos.';
      if (!pair.leverage_buy.includes(context.leverage) || !pair.leverage_sell.includes(context.leverage)) return `${context.leverage}× margin is not advertised for this pair.`;
    }
    return '';
  }
  setPairs(pairs) { this.pairs = [...pairs].sort((a, b) => a.symbol.localeCompare(b.symbol)); }
  sync() {
    const pair = this.pairs.find(pair => pair.id === this.value.value);
    this.input.value = pair?.symbol || this.value.value;
    this.input.setCustomValidity(pair ? StrategyMarketPicker.limitation(pair, this.context) : 'Choose a supported bot market.');
  }
  update(context) {
    const changed = Object.keys(context).some(key => context[key] !== this.context[key]);
    this.context = context;
    if (this.input.matches(':disabled')) this.close();
    else if (!this.list.hidden && changed) this.render();
    const pair = this.pairs.find(pair => pair.id === this.value.value);
    this.input.setCustomValidity(pair ? StrategyMarketPicker.limitation(pair, context) : 'Choose a supported bot market.');
  }
  expand() { this.list.hidden = false; this.input.setAttribute('aria-expanded', 'true'); }
  open() {
    if (this.input.matches(':disabled')) return;
    this.input.value = ''; this.expand(); this.render();
  }
  close() {
    this.list.hidden = true; this.input.setAttribute('aria-expanded', 'false');
    this.input.removeAttribute('aria-activedescendant'); this.active = -1;
    this.status.textContent = ''; this.sync();
  }
  render() {
    this.matches = this.pairs.filter(pair => MarketPicker.matches(pair, this.input.value));
    const fragment = document.createDocumentFragment();
    this.matches.forEach((pair, index) => {
      const option = document.createElement('div'), label = document.createElement('span');
      const reason = StrategyMarketPicker.limitation(pair, this.context);
      option.id = `pair-option-${index}`; option.dataset.index = index; option.dataset.pair = pair.id;
      option.setAttribute('role', 'option'); option.setAttribute('aria-selected', String(pair.id === this.value.value));
      option.setAttribute('aria-disabled', String(Boolean(reason)));
      const name = document.createElement('strong'); name.textContent = pair.symbol; label.append(name);
      const product = document.createElement('small'); product.textContent = MarketPicker.kindLabel(pair); label.append(product);
      if (reason) { const note = document.createElement('small'); note.textContent = reason; label.append(note); }
      option.append(MarketPicker.pairIcon(MarketPicker.iconSymbol(pair)), label); fragment.append(option);
    });
    this.list.replaceChildren(fragment); this.active = -1; this.input.removeAttribute('aria-activedescendant');
    this.status.textContent = this.matches.length ? `${this.matches.length} markets. Unavailable choices include a reason.` : 'No matching markets.';
  }
  activate(index) {
    this.active = index;
    for (const [i, option] of [...this.list.children].entries()) option.classList.toggle('active-option', i === index);
    const option = this.list.children[index];
    this.input.setAttribute('aria-activedescendant', option.id);
    option.scrollIntoView({block: 'nearest'});
  }
  choose(pair) {
    if (!pair || this.input.matches(':disabled')) return;
    const reason = StrategyMarketPicker.limitation(pair, this.context);
    if (reason) { this.status.textContent = reason; return; }
    this.value.value = pair.id; this.close();
    this.value.dispatchEvent(new Event('change', {bubbles: true}));
    this.status.textContent = `${pair.symbol} selected. Save settings to apply.`;
  }
}
window.StrategyMarketPicker = StrategyMarketPicker;
