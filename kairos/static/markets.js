/* Public market browsing is separate from bot configuration and execution. */
class MarketPicker {
  static iconSymbols = new Set(window.CryptoIconSymbols);

  constructor(request, select, catalogChanged = () => {}) {
    this.request = request;
    this.select = select;
    this.catalogChanged = catalogChanged;
    this.kind = 'all';
    this.markets = [];
    this.received = 0;
    this.changeReceived = 0;
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
    this.dialog.addEventListener('close', () => { this.cancelReorder(); this.opener?.focus({preventScroll: true}); });
    window.addEventListener('blur', () => this.cancelReorder());
    this.dialog.addEventListener('click', event => {
      const r = this.dialog.getBoundingClientRect();
      if (event.target === this.dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) this.dialog.close();
    });
    this.search.addEventListener('input', () => { this.cancelReorder(); this.renderRows(); });
    document.getElementById('market-kind').addEventListener('change', event => { this.cancelReorder(); this.kind = event.target.value; this.renderRows(); });
    for (const button of document.querySelectorAll('.market-sort')) {
      button.addEventListener('click', () => this.sortBy(button.dataset.sort));
    }
    this.dialog.addEventListener('keydown', event => {
      if (event.key === 'Escape' && (this.drag || this.pickedFavorite)) {
        event.preventDefault(); this.cancelReorder(); this.renderRows(); return;
      }
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
        this.cancelReorder(); this.favoritesOnly = favorites; this.renderRows();
      });
    }
    window.addEventListener('storage', event => {
      if (event.key !== 'kairos:favorites' && event.key !== null) return;
      this.cancelReorder(); this.favorites = this.loadFavorites(); this.renderFooter(); this.renderRows();
    });
    this.renderFooter();
  }
  sortBy(key) {
    if (this.favoritesOnly || !['symbol', 'last', 'volume', 'change_pct'].includes(key)) return;
    this.sortDirection = key === this.sortKey
      ? (this.sortDirection === 'ascending' ? 'descending' : 'ascending')
      : (key === 'symbol' ? 'ascending' : 'descending');
    this.sortKey = key;
    this.renderRows();
  }
  sortedMarkets(markets) {
    if (this.favoritesOnly) {
      const positions = new Map(this.favorites.map((id, index) => [id, index]));
      return [...markets].sort((a, b) => positions.get(a.id) - positions.get(b.id));
    }
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
  static kindLabel(market) {
    return {spot: 'Crypto spot', fx: 'FX spot', xstocks: 'xStocks · tokenized', futures: 'Futures'}[market.kind] || 'Spot';
  }
  static matchesKind(market, kind) {
    return !kind || kind === 'all' || (kind === 'margin' ? market.margin === true && market.kind === 'spot' : market.kind === kind);
  }
  static iconSymbol(market) { return market.kind === 'futures' && market.underlying ? market.underlying.replace(':', '/') : market.symbol; }
  static matches(market, query) {
    const normalize = value => value.toUpperCase().replace(/[^A-Z0-9]/g, '').replaceAll('XBT', 'BTC').replaceAll('XDG', 'DOGE');
    return [market.symbol, market.id, market.underlying || ''].some(value => normalize(value).includes(normalize(query)));
  }
  static percentage(value) {
    return value == null || value === '' || !Number.isFinite(Number(value)) ? '—'
      : `${Number(value).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: 'exceptZero'})}%`;
  }
  static iconPath(currency) {
    // Kraken's XDG symbol denotes Dogecoin, not a separate asset.
    const symbol = currency.toUpperCase() === 'XDG' ? 'doge' : currency.toLowerCase();
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
    this.cancelReorder();
    this.favorites = this.favorites.includes(id) ? this.favorites.filter(value => value !== id) : [...this.favorites, id];
    this.saveFavorites();
  }
  saveFavorites() {
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
  moveFavorite(id, target, after = false) {
    if (id === target || !this.favorites.includes(id) || !this.favorites.includes(target)) return;
    const order = this.favorites.filter(value => value !== id);
    order.splice(order.indexOf(target) + Number(after), 0, id);
    this.favorites = order;
    this.saveFavorites();
    const symbol = this.markets.find(market => market.id === id)?.symbol || id;
    document.getElementById('favorite-order-status').textContent = `${symbol} moved to position ${order.indexOf(id) + 1} of ${order.length}.`;
  }
  pickFavorite(id) {
    if (this.pickedFavorite && this.pickedFavorite !== id) {
      const source = this.pickedFavorite; this.pickedFavorite = null;
      this.moveFavorite(source, id);
    } else {
      this.pickedFavorite = this.pickedFavorite === id ? null : id;
      document.getElementById('favorite-order-status').textContent = this.pickedFavorite
        ? 'Choose another handle to move this favorite before it. Escape cancels.' : 'Reorder canceled.';
      this.renderRows();
    }
  }
  cancelReorder() {
    const picked = this.pickedFavorite; this.pickedFavorite = null;
    if (this.drag) this.finishDrag(false);
    else if (picked) this.renderRows();
  }
  startDrag(event, id, handle) {
    if (event.button !== 0 || this.drag) return;
    this.suppressHandleClick = false;
    this.drag = {id, handle, pointer: event.pointerId, x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, moved: false};
    handle.setPointerCapture(event.pointerId);
    handle.focus({preventScroll: true});
  }
  dragOver() {
    const drag = this.drag;
    if (!drag?.moved) return;
    const panel = this.rows.closest('.market-results'), bounds = panel.getBoundingClientRect();
    if (drag.x >= bounds.left && drag.x <= bounds.right && drag.y >= bounds.top && drag.y <= bounds.bottom) {
      const delta = drag.y < bounds.top + 32 ? -8 : drag.y > bounds.bottom - 32 ? 8 : 0;
      if (delta) panel.scrollTop += delta;
    }
    const target = document.elementFromPoint(drag.x, drag.y)?.closest('[data-market-row]');
    drag.target = target && this.rows.contains(target) ? target.dataset.marketRow : null;
    drag.after = !!target && drag.y > target.getBoundingClientRect().top + target.getBoundingClientRect().height / 2;
    for (const row of this.rows.children) {
      const active = row.dataset.marketRow === drag.target && drag.target !== drag.id;
      row.classList.toggle('favorite-dragging', row.dataset.marketRow === drag.id);
      row.classList.toggle('favorite-drop-before', active && !drag.after);
      row.classList.toggle('favorite-drop-after', active && drag.after);
    }
    this.dragFrame = requestAnimationFrame(() => this.dragOver());
  }
  finishDrag(commit) {
    const drag = this.drag;
    if (!drag) return;
    this.drag = null; cancelAnimationFrame(this.dragFrame);
    if (drag.handle.hasPointerCapture(drag.pointer)) drag.handle.releasePointerCapture(drag.pointer);
    if (drag.moved) this.suppressHandleClick = true;
    if (commit && drag.moved && drag.target && drag.target !== drag.id) this.moveFavorite(drag.id, drag.target, drag.after);
    else if (drag.moved || !commit) this.renderRows();
  }
  reorderHandle(market) {
    const handle = document.createElement('button');
    handle.type = 'button'; handle.className = 'favorite-handle'; handle.dataset.reorder = market.id;
    handle.setAttribute('aria-label', `Reorder ${market.symbol}`);
    handle.setAttribute('aria-describedby', 'favorite-order-help');
    handle.setAttribute('aria-pressed', String(this.pickedFavorite === market.id));
    handle.title = 'Drag to reorder · ↑ ↓ move · Click then choose a destination';
    const icon = document.createElement('span'); icon.className = 'icon icon-grip icon-solid'; icon.textContent = '⠿'; icon.setAttribute('aria-hidden', 'true'); handle.append(icon);
    handle.addEventListener('click', event => {
      if (this.suppressHandleClick && event.detail > 0) { this.suppressHandleClick = false; return; }
      this.pickFavorite(market.id);
    });
    handle.addEventListener('keydown', event => {
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation(); this.cancelReorder();
      const visible = [...this.rows.querySelectorAll('[data-reorder]')].map(button => button.dataset.reorder);
      const target = visible[visible.indexOf(market.id) + (event.key === 'ArrowUp' ? -1 : 1)];
      if (target) this.moveFavorite(market.id, target, event.key === 'ArrowDown');
    });
    handle.addEventListener('pointerdown', event => this.startDrag(event, market.id, handle));
    handle.addEventListener('pointermove', event => {
      const drag = this.drag;
      if (!drag || drag.pointer !== event.pointerId) return;
      drag.x = event.clientX; drag.y = event.clientY;
      if (!drag.moved && Math.hypot(drag.x - drag.startX, drag.y - drag.startY) > 4) { drag.moved = true; this.pickedFavorite = null; this.dragOver(); }
    });
    handle.addEventListener('pointerup', event => {
      if (this.drag?.pointer !== event.pointerId) return;
      if (this.drag.moved) {
        this.drag.x = event.clientX; this.drag.y = event.clientY;
        cancelAnimationFrame(this.dragFrame); this.dragOver(); event.preventDefault();
      }
      this.finishDrag(true);
    });
    for (const name of ['pointercancel', 'lostpointercapture']) handle.addEventListener(name, () => this.finishDrag(false));
    return handle;
  }
  open(favorites) {
    this.cancelReorder();
    this.favoritesOnly = favorites;
    this.opener = document.getElementById(favorites ? 'favorites-open' : 'market-open');
    this.search.value = '';
    if (!this.dialog.open) this.dialog.showModal();
    this.renderRows(); this.updateFreshness(); this.search.focus();
  }
  setSelected(pair) {
    if (this.selected === pair.id) return;
    this.selected = pair.id;
    document.getElementById('market-icons').replaceChildren(MarketPicker.pairIcon(MarketPicker.iconSymbol(pair)));
    this.renderRows(); this.renderFooter();
  }
  choose(market) {
    this.select(market);
    this.dialog.close();
  }
  static number(value) {
    return value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 8});
  }
  ticker(id) {
    const market = this.markets.find(row => row.id === id);
    if (!market || !(Number(market.bid) > 0) || !(Number(market.ask) > 0)) return null;
    return {bid: Number(market.bid), ask: Number(market.ask), last: market.last, received: market.received || 0};
  }
  renderRows() {
    if (!this.dialog.open || this.drag) return;
    const focused = document.activeElement;
    const focusKind = focused.dataset.reorder !== undefined ? 'reorder' : focused.dataset.favorite !== undefined ? 'favorite' : 'market';
    const focusId = focused.dataset[focusKind];
    const query = this.search.value;
    const known = new Set(this.markets.map(market => market.id));
    const candidates = this.favoritesOnly ? [...this.markets, ...this.favorites.filter(id => !known.has(id))
      .map(id => ({id, symbol: id, unavailable: true}))] : this.markets;
    const matches = this.sortedMarkets(candidates.filter(market =>
      (!this.favoritesOnly || this.favorites.includes(market.id)) && MarketPicker.matchesKind(market, this.kind) && MarketPicker.matches(market, query)));
    for (const button of document.querySelectorAll('.market-sort')) {
      button.disabled = this.favoritesOnly;
      button.title = this.favoritesOnly ? 'Favorites use saved order; drag the handles to rearrange them.' : '';
      const active = !this.favoritesOnly && button.dataset.sort === this.sortKey;
      button.closest('th').setAttribute('aria-sort', active ? this.sortDirection : 'none');
      button.querySelector('.sort-direction').textContent = this.favoritesOnly ? '' : active ? (this.sortDirection === 'ascending' ? '↑' : '↓') : '↕';
    }
    document.getElementById('favorite-order-help').hidden = !this.favoritesOnly;
    document.getElementById('markets-all').setAttribute('aria-pressed', String(!this.favoritesOnly));
    document.getElementById('markets-favorites').setAttribute('aria-pressed', String(this.favoritesOnly));
    const fragment = document.createDocumentFragment();
    for (const market of matches) {
      const row = document.createElement('tr');
      row.classList.toggle('selected-market', market.id === this.selected);
      row.dataset.marketRow = market.id;
      const name = document.createElement('td'), price = document.createElement('td'), volume = document.createElement('td'), favorite = document.createElement('td');
      const change = document.createElement('td');
      change.className = `market-change${Number(market.change_pct) > 0 ? ' buy' : Number(market.change_pct) < 0 ? ' sell' : ''}`;
      change.textContent = MarketPicker.percentage(market.change_pct);
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'market-select'; button.dataset.market = market.id;
      button.disabled = market.unavailable === true;
      const label = document.createElement('span'), product = document.createElement('small');
      label.textContent = market.symbol; product.textContent = MarketPicker.kindLabel(market);
      label.append(product);
      button.append(MarketPicker.pairIcon(MarketPicker.iconSymbol(market)), label);
      button.setAttribute('aria-pressed', String(market.id === this.selected));
      button.addEventListener('click', () => this.choose(market));
      const marketName = document.createElement('div'); marketName.className = 'favorite-name';
      if (this.favoritesOnly) marketName.append(this.reorderHandle(market));
      marketName.append(button); name.append(marketName);
      price.textContent = MarketPicker.number(market.last);
      volume.textContent = MarketPicker.number(market.volume);
      const unit = document.createElement('small'); unit.textContent = market.volume_unit || ''; volume.append(unit);
      const star = document.createElement('button');
      const saved = this.favorites.includes(market.id);
      star.type = 'button'; star.className = 'favorite-star'; star.dataset.favorite = market.id;
      const icon = document.createElement('span'); icon.className = 'icon icon-star'; icon.setAttribute('aria-hidden', 'true');
      icon.textContent = saved ? '★' : '☆'; star.append(icon); star.setAttribute('aria-pressed', String(saved));
      star.setAttribute('aria-label', `${saved ? 'Remove' : 'Add'} ${market.symbol} ${saved ? 'from' : 'to'} favorites`);
      star.addEventListener('click', () => this.toggleFavorite(market.id)); favorite.append(star);
      row.append(name, price, change, volume, favorite); fragment.append(row);
    }
    this.rows.replaceChildren(fragment);
    document.getElementById('market-empty').hidden = matches.length > 0;
    this.updateFreshness();
    if (focusId) {
      const target = [...this.rows.querySelectorAll(`[data-${focusKind}]`)]
        .find(button => button.dataset[focusKind] === focusId);
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
      button.append(MarketPicker.pairIcon(market ? MarketPicker.iconSymbol(market) : id), name, price); button.addEventListener('click', () => this.choose(market));
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
    const byId = new Map(this.markets.map(market => [market.id, market]));
    document.getElementById('market-change-status').textContent = '24h: venue snapshots · hover for age';
    for (const row of this.rows.querySelectorAll('[data-market-row]')) {
      const market = byId.get(row.dataset.marketRow);
      const quoteAge = market?.received ? Math.max(0, Date.now()/1000-market.received) : Infinity;
      row.classList.toggle('stale', quoteAge > 30);
      row.title = Number.isFinite(quoteAge) ? `${MarketPicker.kindLabel(market)} · quote ${quoteAge.toFixed(0)}s ago` : 'Quote unavailable';
      const changeAge = market?.change_received ? Math.max(0, Date.now()/1000-market.change_received) : Infinity;
      const cell = row.querySelector('.market-change');
      cell.classList.toggle('stale', changeAge > 90);
      cell.title = market?.change_pct == null ? 'Rolling 24h change unavailable' : `${changeAge > 90 ? 'STALE · ' : ''}24h change snapshot ${changeAge.toFixed(0)}s ago`;
    }
    document.getElementById('market-feed-status').textContent = this.error || (age === null ? 'Loading prices…' : `${stale ? 'STALE · ' : ''}Updated ${age.toFixed(0)}s ago`);
    for (const button of this.watchlist.querySelectorAll('.watch-market')) {
      const market = this.markets.find(row => row.id === button.dataset.watch);
      const quoteAge = market?.received ? Math.max(0, Date.now()/1000-market.received) : Infinity;
      button.classList.toggle('stale', quoteAge > 30);
      button.title = `${market?.symbol || button.dataset.watch} · ${market ? MarketPicker.kindLabel(market) : 'Unavailable'} · ${market?.last == null ? 'Price unavailable' : `${quoteAge > 30 ? 'STALE · ' : ''}Quote ${quoteAge.toFixed(0)}s ago`} · Chart only`;
    }
  }
  async refresh() {
    try {
      const data = await this.request('markets');
      if (!Array.isArray(data.markets) || !Number.isFinite(data.received)) throw new Error('Invalid market snapshot');
      this.markets = data.markets;
      this.received = data.received;
      this.changeReceived = Number.isFinite(data.change_received) ? data.change_received : 0;
      this.error = data.errors?.length ? 'Some feeds unavailable' : '';
      const status = document.getElementById('market-source-status');
      status.textContent = (data.errors || []).join(' · '); status.hidden = !this.error;
      this.catalogChanged(this.markets);
      this.renderRows(); this.renderFooter();
    } catch {
      this.error = 'Market prices unavailable — retrying'; this.updateFreshness();
    } finally {
      this.timer = setTimeout(() => this.refresh(), 10000);
    }
  }
}
window.MarketPicker = MarketPicker;
