/* Public market browsing is separate from bot configuration and execution. */
class MarketPicker {
  static iconSymbols = new Set(window.CryptoIconSymbols);

  constructor(request, select) {
    this.request = request;
    this.select = select;
    this.markets = [];
    this.received = 0;
    this.error = '';
    this.selected = null;
    this.favoritesOnly = false;
    this.sortKey = 'symbol';
    this.sortDirection = 'ascending';
    this.dialog = document.getElementById('market-dialog');
    this.search = document.getElementById('market-search');
    this.rows = document.getElementById('market-rows');
    this.watchlist = document.getElementById('watchlist');
    this.favorites = this.loadFavorites();
    document.getElementById('market-open').addEventListener('click', () => this.open(false));
    document.getElementById('favorites-open').addEventListener('click', () => this.open(true));
    document.getElementById('market-close').addEventListener('click', () => this.dialog.close());
    this.dialog.addEventListener('close', () => this.opener?.focus({preventScroll: true}));
    this.dialog.addEventListener('click', event => {
      const r = this.dialog.getBoundingClientRect();
      if (event.target === this.dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) this.dialog.close();
    });
    this.search.addEventListener('input', () => this.renderRows());
    for (const button of document.querySelectorAll('.market-sort')) {
      button.addEventListener('click', () => this.sortBy(button.dataset.sort));
    }
    this.dialog.addEventListener('keydown', event => {
      if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      const buttons = [...this.rows.querySelectorAll('.market-select:not(:disabled)')];
      const index = buttons.indexOf(document.activeElement);
      if (document.activeElement !== this.search && index < 0) return;
      event.preventDefault();
      if (event.key === 'ArrowUp' && index <= 0) this.search.focus();
      else buttons[Math.min(buttons.length-1, index + (event.key === 'ArrowDown' ? 1 : -1))]?.focus();
    });
    for (const [id, favorites] of [['markets-all', false], ['markets-favorites', true]]) {
      document.getElementById(id).addEventListener('click', () => {
        this.favoritesOnly = favorites; this.renderRows();
      });
    }
    window.addEventListener('storage', event => {
      if (event.key !== 'kairos:favorites' && event.key !== null) return;
      this.favorites = this.loadFavorites(); this.renderFooter(); this.renderRows();
    });
    this.renderFooter();
  }
  sortBy(key) {
    if (!['symbol', 'last', 'volume'].includes(key)) return;
    this.sortDirection = key === this.sortKey
      ? (this.sortDirection === 'ascending' ? 'descending' : 'ascending')
      : (key === 'symbol' ? 'ascending' : 'descending');
    this.sortKey = key;
    this.renderRows();
  }
  sortedMarkets(markets) {
    const direction = this.sortDirection === 'ascending' ? 1 : -1;
    const numeric = value => value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
    return [...markets].sort((a, b) => {
      const byName = a.symbol.localeCompare(b.symbol) || a.id.localeCompare(b.id);
      if (this.sortKey === 'symbol') return direction * byName;
      const left = numeric(a[this.sortKey]), right = numeric(b[this.sortKey]);
      // Unavailable values stay last in both directions; zero is a valid volume.
      if (left === null || right === null) return left === right ? byName : left === null ? 1 : -1;
      return direction * (left-right) || byName;
    });
  }
  static iconPath(currency) {
    const symbol = currency.toLowerCase();
    return MarketPicker.iconSymbols.has(symbol) ? `/static/vendor/crypto-icons/${symbol}.svg` : null;
  }
  static pairIcon(symbol) {
    const pair = document.createElement('span'); pair.className = 'asset-pair'; pair.setAttribute('aria-hidden', 'true');
    for (const currency of symbol.split('/').slice(0, 2)) {
      const fallback = document.createElement('span'); fallback.className = 'asset-icon asset-monogram';
      fallback.textContent = currency.toUpperCase().slice(0, 3);
      const path = MarketPicker.iconPath(currency);
      if (path) {
        const image = document.createElement('img'); image.className = 'asset-icon'; image.alt = '';
        image.width = 22; image.height = 22; image.loading = 'lazy';
        image.addEventListener('error', () => image.replaceWith(fallback), {once: true});
        image.src = path; pair.append(image);
      } else pair.append(fallback);
    }
    return pair;
  }
  loadFavorites() {
    try {
      const saved = JSON.parse(localStorage.getItem('kairos:favorites') || '[]');
      return Array.isArray(saved) ? [...new Set(saved.filter(id => typeof id === 'string' && id.length > 0 && id.length <= 64))] : [];
    } catch { return []; }
  }
  toggleFavorite(id) {
    this.favorites = this.favorites.includes(id) ? this.favorites.filter(value => value !== id) : [...this.favorites, id];
    const warning = document.getElementById('market-storage-status');
    try {
      localStorage.setItem('kairos:favorites', JSON.stringify(this.favorites));
      warning.hidden = true;
    } catch {
      warning.textContent = 'Browser storage is unavailable. Favorites will last only for this page session.';
      warning.hidden = false;
    }
    this.renderFooter(); this.renderRows();
  }
  open(favorites) {
    this.favoritesOnly = favorites;
    this.opener = document.getElementById(favorites ? 'favorites-open' : 'market-open');
    this.search.value = '';
    if (!this.dialog.open) this.dialog.showModal();
    this.renderRows(); this.updateFreshness(); this.search.focus();
  }
  setSelected(pair) {
    if (this.selected === pair.id) return;
    this.selected = pair.id;
    document.getElementById('market-icons').replaceChildren(MarketPicker.pairIcon(pair.symbol));
    this.renderRows(); this.renderFooter();
  }
  choose(market) {
    this.select({id: market.id, symbol: market.symbol});
    this.dialog.close();
  }
  static number(value) {
    return value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 8});
  }
  ticker(symbol) {
    const market = this.markets.find(row => row.symbol === symbol);
    if (!market || !(Number(market.bid) > 0) || !(Number(market.ask) > 0)) return null;
    return {bid: Number(market.bid), ask: Number(market.ask), last: market.last, received: this.received};
  }
  renderRows() {
    if (!this.dialog.open) return;
    const focused = document.activeElement;
    const focusId = focused.dataset.favorite || focused.dataset.market;
    const favoriteFocus = focused.dataset.favorite !== undefined;
    const query = this.search.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
    const known = new Set(this.markets.map(market => market.id));
    const candidates = this.favoritesOnly ? [...this.markets, ...this.favorites.filter(id => !known.has(id))
      .map(id => ({id, symbol: id, unavailable: true}))] : this.markets;
    const matches = this.sortedMarkets(candidates.filter(market =>
      (!this.favoritesOnly || this.favorites.includes(market.id)) && market.symbol.toUpperCase().replace(/[^A-Z0-9]/g, '').includes(query)));
    for (const button of document.querySelectorAll('.market-sort')) {
      const active = button.dataset.sort === this.sortKey;
      button.closest('th').setAttribute('aria-sort', active ? this.sortDirection : 'none');
      button.querySelector('.sort-direction').textContent = active ? (this.sortDirection === 'ascending' ? '↑' : '↓') : '↕';
    }
    document.getElementById('markets-all').setAttribute('aria-pressed', String(!this.favoritesOnly));
    document.getElementById('markets-favorites').setAttribute('aria-pressed', String(this.favoritesOnly));
    const fragment = document.createDocumentFragment();
    for (const market of matches) {
      const row = document.createElement('tr');
      row.classList.toggle('selected-market', market.id === this.selected);
      const name = document.createElement('td'), price = document.createElement('td'), volume = document.createElement('td'), favorite = document.createElement('td');
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'market-select'; button.dataset.market = market.id;
      button.disabled = market.unavailable === true;
      const label = document.createElement('span'); label.textContent = market.symbol;
      button.append(MarketPicker.pairIcon(market.symbol), label);
      button.setAttribute('aria-pressed', String(market.id === this.selected));
      button.addEventListener('click', () => this.choose(market)); name.append(button);
      price.textContent = MarketPicker.number(market.last);
      volume.textContent = MarketPicker.number(market.volume);
      const star = document.createElement('button');
      const saved = this.favorites.includes(market.id);
      star.type = 'button'; star.className = 'favorite-star'; star.dataset.favorite = market.id;
      const icon = document.createElement('span'); icon.className = 'icon icon-star'; icon.setAttribute('aria-hidden', 'true');
      icon.textContent = saved ? '★' : '☆'; star.append(icon); star.setAttribute('aria-pressed', String(saved));
      star.setAttribute('aria-label', `${saved ? 'Remove' : 'Add'} ${market.symbol} ${saved ? 'from' : 'to'} favorites`);
      star.addEventListener('click', () => this.toggleFavorite(market.id)); favorite.append(star);
      row.append(name, price, volume, favorite); fragment.append(row);
    }
    this.rows.replaceChildren(fragment);
    document.getElementById('market-empty').hidden = matches.length > 0;
    if (focusId) {
      const target = [...this.rows.querySelectorAll(favoriteFocus ? '[data-favorite]' : '[data-market]')]
        .find(button => (favoriteFocus ? button.dataset.favorite : button.dataset.market) === focusId);
      (target || this.search).focus({preventScroll: true});
    }
  }
  renderFooter() {
    const focusedId = document.activeElement.dataset.watch;
    const fragment = document.createDocumentFragment();
    for (const id of this.favorites) {
      const market = this.markets.find(row => row.id === id);
      const button = document.createElement('button'), name = document.createElement('span'), price = document.createElement('strong');
      button.type = 'button'; button.className = 'watch-market'; button.dataset.watch = id; button.disabled = !market;
      button.setAttribute('aria-pressed', String(id === this.selected));
      name.textContent = market?.symbol || id; price.textContent = MarketPicker.number(market?.last);
      button.append(MarketPicker.pairIcon(market?.symbol || id), name, price); button.addEventListener('click', () => this.choose(market));
      fragment.append(button);
    }
    if (!this.favorites.length) {
      const hint = document.createElement('span'); hint.className = 'muted';
      hint.textContent = 'Star markets to watch their prices here.'; fragment.append(hint);
    }
    this.watchlist.replaceChildren(fragment);
    this.updateFreshness();
    if (focusedId) [...this.watchlist.children].find(button => button.dataset.watch === focusedId)?.focus({preventScroll: true});
  }
  updateFreshness() {
    const age = this.received ? Math.max(0, Date.now()/1000-this.received) : null;
    const stale = age === null || age > 30;
    this.watchlist.classList.toggle('stale', stale);
    this.watchlist.title = age === null ? 'Waiting for market prices' : `${stale ? 'STALE · ' : ''}Market snapshot ${age.toFixed(0)}s ago`;
    this.rows.classList.toggle('stale', stale);
    document.getElementById('market-feed-status').textContent = this.error || (age === null ? 'Loading prices…' : `${stale ? 'STALE · ' : ''}Updated ${age.toFixed(0)}s ago`);
    for (const button of this.watchlist.querySelectorAll('.watch-market')) {
      const market = this.markets.find(row => row.id === button.dataset.watch);
      button.title = `${market?.symbol || button.dataset.watch} · ${market?.last == null ? 'Price unavailable' : this.watchlist.title} · Chart only`;
    }
  }
  async refresh() {
    try {
      const data = await this.request('markets');
      if (!Array.isArray(data.markets) || !Number.isFinite(data.received)) throw new Error('Invalid market snapshot');
      this.markets = data.markets;
      this.received = data.received; this.error = '';
      this.renderRows(); this.renderFooter();
    } catch {
      this.error = 'Market prices unavailable — retrying'; this.updateFreshness();
    } finally {
      this.timer = setTimeout(() => this.refresh(), 10000);
    }
  }
}
window.MarketPicker = MarketPicker;
