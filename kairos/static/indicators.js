/* Chart-only studies of supplied closes. No execution settings, signals or network IO. */
const ChartIndicators = (() => {
  'use strict';
  const defaults = Object.freeze({volume: true, macd: false, bollinger: false, rsi: false, sma: false});
  const storageKey = 'kairos:chart-indicators';
  const colors = {primary: 'Violet / lilac', info: 'Blue', warning: 'Yellow', success: 'Green', danger: 'Red'};
  const definitions = {
    volume: {name: 'Volume', short: 'VOL', inputs: {}, colors: {up: ['Up bars', 'success'], down: ['Down bars', 'danger']}},
    macd: {name: 'MACD', short: 'MACD', inputs: {fast: ['Fast EMA', 12], slow: ['Slow EMA', 26], signal: ['Signal EMA', 9]}, colors: {line: ['MACD', 'primary'], signalColor: ['Signal', 'warning'], up: ['Positive histogram', 'success'], down: ['Negative histogram', 'danger']}},
    bollinger: {name: 'Bollinger Bands', short: 'BB', inputs: {period: ['SMA period', 20], deviation: ['Standard deviations', 2]}, colors: {line: ['Bands and midpoint', 'info']}},
    rsi: {name: 'RSI', short: 'RSI', inputs: {period: ['Wilder period', 14]}, colors: {line: ['RSI', 'info']}},
    sma: {name: 'Moving averages', short: 'SMA', inputs: {fast: ['Fast SMA', 8], slow: ['Slow SMA', 21]}, colors: {line: ['Fast average', 'primary'], signalColor: ['Slow average', 'warning']}},
  };
  const normalize = value => Object.fromEntries(Object.entries(defaults).map(([key, fallback]) =>
    [key, typeof value?.[key] === 'boolean' ? value[key] : fallback]));
  function configuration(value) {
    return Object.fromEntries(Object.entries(definitions).map(([key, def]) => {
      const input = value?.[key], result = {};
      for (const [name, [, fallback]] of Object.entries(def.inputs)) {
        const v = input?.[name], deviation = name === 'deviation';
        result[name] = typeof v === 'number' && Number.isFinite(v) && v >= (deviation ? .1 : 1) && v <= (deviation ? 10 : 200) && (deviation || Number.isInteger(v)) ? v : fallback;
      }
      if (result.fast >= result.slow) { result.fast = def.inputs.fast[1]; result.slow = def.inputs.slow[1]; }
      for (const [name, [, fallback]] of Object.entries(def.colors)) result[name] = Object.hasOwn(colors, input?.[name]) ? input[name] : fallback;
      if (key !== 'volume') {
        result.style = ['default', 'solid', 'dashed', 'dotted'].includes(input?.style) ? input.style : 'default';
        result.width = [1, 1.5, 2, 3].includes(input?.width) ? input.width : 'default';
      }
      return [key, result];
    }));
  }
  function preferences(value) {
    const added = normalize(value);
    return {...added, hidden: Object.fromEntries(Object.keys(defaults).map(key => [key, added[key] && value?.hidden?.[key] === true])),
      settings: configuration(value?.settings), collapsed: value?.collapsed === true};
  }
  const visible = value => Object.fromEntries(Object.keys(defaults).map(key => [key, value[key] && !value.hidden[key]]));
  const color = name => name === 'primary' ? 'var(--indicator-primary)' : `var(--${name})`;
  const dash = (style, fallback) => ({default: fallback, solid: null, dashed: '6 3', dotted: '1 3'})[style];
  function summary(key, c) {
    return key === 'volume' ? 'Native units' : key === 'macd' ? `${c.fast} / ${c.slow} / ${c.signal}` : key === 'bollinger' ? `${c.period} SMA close · ${c.deviation}σ` : key === 'rsi' ? `${c.period} close` : `${c.fast} / ${c.slow} close`;
  }
  const warmup = c => ({sma: `${c.sma.fast} / ${c.sma.slow}`, bollinger: c.bollinger.period, rsi: c.rsi.period + 1, macd: c.macd.slow + c.macd.signal - 1});
  function ema(values, period) {
    let seed = [], average = null;
    return values.map(value => {
      if (!Number.isFinite(value)) { seed = []; average = null; return null; }
      if (average === null) {
        seed.push(value);
        if (seed.length < period) return null;
        average = seed.reduce((sum, n) => sum + n / period, 0);
      } else average += (value - average) * 2 / (period + 1);
      return average;
    });
  }
  function calculate(points, settings) {
    const c = configuration(settings), period = c.rsi.period;
    const closes = points.map(p => p.value);
    const fast = ema(closes, c.macd.fast), slow = ema(closes, c.macd.slow);
    const macd = fast.map((v, i) => v === null || slow[i] === null ? null : v - slow[i]);
    const signal = ema(macd, c.macd.signal);
    let gain = 0, loss = 0;
    const mean = (i, period) => i + 1 < period ? null : closes.slice(i + 1 - period, i + 1).reduce((sum, v) => sum + v / period, 0);
    const finite = value => Number.isFinite(value) ? value : null;
    return points.map((point, i) => {
      const middle = mean(i, c.bollinger.period);
      const deviation = middle === null ? null : Math.sqrt(closes.slice(i + 1 - c.bollinger.period, i + 1).reduce((sum, v) => sum + (v - middle) ** 2 / c.bollinger.period, 0));
      if (i) {
        const delta = closes[i] - closes[i - 1];
        if (i <= period) { gain += Math.max(0, delta) / period; loss += Math.max(0, -delta) / period; }
        else { gain += (Math.max(0, delta) - gain) / period; loss += (Math.max(0, -delta) - loss) / period; }
      }
      return {
        time: point.time, smaFast: finite(mean(i, c.sma.fast)), smaSlow: finite(mean(i, c.sma.slow)),
        middle: finite(middle), upper: middle === null ? null : finite(middle + c.bollinger.deviation * deviation), lower: middle === null ? null : finite(middle - c.bollinger.deviation * deviation),
        macd: finite(macd[i]), signal: finite(signal[i]), histogram: signal[i] === null ? null : finite(macd[i] - signal[i]),
        rsi: i < period ? null : gain === 0 && loss === 0 ? 50 : loss === 0 ? 100 : finite(100 - 100 / (1 + gain / loss)),
      };
    });
  }

  function bind(chart) {
    const $ = id => document.getElementById(id);
    const button = $('indicators-toggle'), dialog = $('chart-indicators'), status = $('indicator-status');
    const legend = $('indicator-legend'), list = $('indicator-rows'), roll = $('indicator-roll');
    const editor = $('indicator-config'), form = $('indicator-config-form'), fields = $('indicator-fields'), tip = $('indicator-tooltip');
    let preference, editing, returnFocus, tipTarget, tipTimer;
    try { preference = preferences(JSON.parse(localStorage.getItem(storageKey))); }
    catch { preference = preferences(null); }
    function hideTip() {
      clearTimeout(tipTimer);
      tipTarget?.removeAttribute('aria-describedby'); tipTarget = null;
      if (tip.matches(':popover-open')) tip.hidePopover();
    }
    function showTip(target) {
      hideTip(); tipTarget = target; tip.textContent = target.dataset.tip;
      target.setAttribute('aria-describedby', tip.id); tip.showPopover();
      const r = target.getBoundingClientRect(), b = tip.getBoundingClientRect();
      tip.style.left = `${Math.max(8, Math.min(innerWidth-b.width-8, r.left))}px`;
      tip.style.top = `${Math.max(8, Math.min(innerHeight-b.height-8, r.top > b.height+12 ? r.top-b.height-6 : r.bottom+6))}px`;
    }
    function deferTip() {
      tipTimer = setTimeout(() => { if (!tip.matches(':hover') && !tipTarget?.matches(':hover, :focus-visible')) hideTip(); }, 100);
    }
    legend.addEventListener('pointerover', event => { const target = event.target.closest('[data-tip]'); if (target && target !== tipTarget) showTip(target); });
    legend.addEventListener('pointerout', deferTip);
    legend.addEventListener('focusin', event => { const target = event.target.closest('[data-tip]'); if (target?.matches(':focus-visible')) showTip(target); });
    legend.addEventListener('focusout', deferTip);
    tip.addEventListener('pointerenter', () => clearTimeout(tipTimer)); tip.addEventListener('pointerleave', deferTip);
    document.addEventListener('keydown', event => { if (event.key === 'Escape') hideTip(); });
    document.addEventListener('scroll', () => {
      const target = tipTarget;
      if (target?.matches(':focus-visible')) showTip(target); else hideTip();
    }, true);
    window.addEventListener('resize', hideTip);
    const rows = {};
    function control(label, glyph, handler) {
      const node = document.createElement('button'); node.type = 'button'; node.dataset.tip = label; node.setAttribute('aria-label', label);
      const icon = document.createElement('span'); icon.className = `icon ${glyph}`; icon.setAttribute('aria-hidden', 'true');
      icon.textContent = {'icon-eye':'◉', 'icon-config':'⚙', 'icon-trash':'×'}[glyph]; node.append(icon);
      node.addEventListener('click', () => { hideTip(); handler(node); }); return node;
    }
    for (const [key, def] of Object.entries(definitions)) {
      const row = document.createElement('div'); row.className = 'indicator-row'; row.dataset.study = key;
      const name = document.createElement('button'); name.type = 'button'; name.className = 'indicator-name'; name.textContent = def.short;
      name.dataset.tip = def.name; name.setAttribute('aria-label', `Configure ${def.name}`); name.addEventListener('click', () => openEditor(key, name));
      const params = document.createElement('span'); params.className = 'indicator-parameters'; params.tabIndex = 0;
      const actions = document.createElement('span'); actions.className = 'indicator-actions';
      const eye = control(`Hide ${def.name}`, 'icon-eye', () => { preference.hidden[key] = !preference.hidden[key]; commit(); });
      const configure = control(`Configure ${def.name}`, 'icon-config', node => openEditor(key, node));
      const remove = control(`Remove ${def.name}`, 'icon-trash', () => {
        preference[key] = false; preference.hidden[key] = false; preference.settings[key] = configuration(null)[key];
        commit(); (Object.values(rows).find(r => !r.row.hidden)?.name || button).focus({preventScroll:true});
      });
      actions.append(eye, configure, remove); row.append(name, params, actions); list.append(row); rows[key] = {row, name, params, eye};
    }
    function apply(updateChart = true) {
      for (const input of dialog.querySelectorAll('input[data-indicator]')) {
        const key = input.dataset.indicator; input.checked = preference[key];
        input.nextElementSibling.textContent = `${definitions[key].name} · ${summary(key, preference.settings[key])}`;
      }
      if (updateChart) chart.setIndicators(visible(preference), preference.settings);
      for (const [key, {row, params, eye}] of Object.entries(rows)) {
        row.hidden = !preference[key]; row.classList.toggle('indicator-hidden', preference.hidden[key]);
        params.textContent = summary(key, preference.settings[key]); params.dataset.tip = `${definitions[key].name}: ${params.textContent}. Chart only; periods count supplied candles.`;
        const label = `${preference.hidden[key] ? 'Show' : 'Hide'} ${definitions[key].name}`;
        eye.dataset.tip = label; eye.setAttribute('aria-label', label); eye.setAttribute('aria-pressed', String(!preference.hidden[key]));
        eye.firstChild.className = `icon ${preference.hidden[key] ? 'icon-eye-slash' : 'icon-eye'}`;
      }
      for (const [key, value] of Object.entries(warmup(preference.settings))) dialog.querySelector(`[data-warmup="${key}"]`).textContent = value;
      legend.hidden = !Object.keys(defaults).some(key => preference[key]);
      legend.classList.toggle('rolled-up', preference.collapsed); list.inert = preference.collapsed;
      roll.setAttribute('aria-expanded', String(!preference.collapsed));
      roll.dataset.tip = preference.collapsed ? 'Show indicator list' : 'Hide indicator list'; roll.setAttribute('aria-label', roll.dataset.tip);
    }
    function commit(updateChart = true) {
      apply(updateChart);
      try { localStorage.setItem(storageKey, JSON.stringify(preference)); status.hidden = true; status.textContent = ''; }
      catch { status.hidden = false; status.textContent = 'Browser storage unavailable; chart selections last for this page session.'; }
    }
    roll.addEventListener('click', () => { hideTip(); preference.collapsed = !preference.collapsed; commit(false); });
    function field(label, name, options, value) {
      const wrapper = document.createElement('div'); wrapper.className = 'indicator-field';
      const caption = document.createElement('label'); caption.textContent = label; caption.htmlFor = `indicator-setting-${name}`;
      const input = document.createElement(options ? 'select' : 'input'); input.name = name; input.id = caption.htmlFor;
      if (options) for (const [v, text] of Object.entries(options)) { const option = document.createElement('option'); option.value = v; option.textContent = text; input.append(option); }
      else { input.type = 'number'; input.required = true; input.min = name === 'deviation' ? '.1' : '1'; input.max = name === 'deviation' ? '10' : '200'; input.step = name === 'deviation' ? '.1' : '1'; }
      input.value = value; wrapper.append(caption, input); fields.append(wrapper);
    }
    function populate(config) {
      fields.replaceChildren(); const def = definitions[editing];
      for (const [name, [label]] of Object.entries(def.inputs)) field(label, name, null, config[name]);
      for (const [name, [label]] of Object.entries(def.colors)) field(label, name, colors, config[name]);
      if (editing !== 'volume') {
        field('Line style', 'style', {default:'Theme default', solid:'Solid', dashed:'Dashed', dotted:'Dotted'}, config.style);
        field('Line width', 'width', {default:'Theme default', 1:'1 px', 1.5:'1.5 px', 2:'2 px', 3:'3 px'}, config.width);
      }
    }
    function openEditor(key, origin) {
      hideTip(); editing = key; returnFocus = origin; $('indicator-config-title').textContent = definitions[key].name;
      populate(preference.settings[key]); editor.showModal(); fields.querySelector('input,select').focus({preventScroll:true});
    }
    $('indicator-config-reset').addEventListener('click', () => populate(configuration(null)[editing]));
    for (const node of editor.querySelectorAll('[data-config-cancel]')) node.addEventListener('click', () => editor.close());
    editor.addEventListener('close', () => { (returnFocus?.isConnected && !returnFocus.closest('[hidden], [inert]') ? returnFocus : button).focus({preventScroll:true}); });
    form.addEventListener('input', () => form.elements.namedItem('fast')?.setCustomValidity(''));
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!preference[editing]) { editor.close(); return; }
      const draft = Object.fromEntries(new FormData(form));
      for (const name of Object.keys(definitions[editing].inputs)) draft[name] = Number(draft[name]);
      if (draft.width !== 'default' && draft.width !== undefined) draft.width = Number(draft.width);
      if (draft.fast >= draft.slow) { form.elements.namedItem('fast').setCustomValidity('Fast period must be less than slow period.'); form.reportValidity(); return; }
      preference.settings[editing] = configuration({[editing]:draft})[editing]; commit(); editor.close();
    });
    button.addEventListener('click', () => {
      hideTip(); dialog.showModal(); button.setAttribute('aria-expanded', 'true'); dialog.querySelector('input').focus({preventScroll:true});
    });
    $('indicators-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { button.setAttribute('aria-expanded', 'false'); button.focus({preventScroll:true}); });
    dialog.addEventListener('change', event => {
      const input = event.target, key = input.dataset.indicator;
      if (!Object.hasOwn(defaults, key)) return;
      preference[key] = input.checked; preference.hidden[key] = false;
      if (!input.checked) preference.settings[key] = configuration(null)[key];
      commit();
    });
    window.addEventListener('storage', event => {
      if (event.storageArea !== localStorage || (event.key !== storageKey && event.key !== null)) return;
      hideTip();
      try { preference = preferences(JSON.parse(event.newValue)); }
      catch { preference = preferences(null); }
      if (editor.open) editor.close(); apply();
    });
    apply();
  }
  return {normalize, configuration, preferences, visible, color, dash, warmup, calculate, bind};
})();
window.ChartIndicators = ChartIndicators;
