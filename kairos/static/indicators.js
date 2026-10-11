/* Chart-only studies of supplied closes. No execution settings, signals or network IO. */
const ChartIndicators = (() => {
  'use strict';
  const defaults = Object.freeze({volume: true, macd: false, bollinger: false, rsi: false, sma: false});
  const storageKey = 'kairos:chart-indicators';
  const normalize = value => Object.fromEntries(Object.entries(defaults).map(([key, fallback]) =>
    [key, typeof value?.[key] === 'boolean' ? value[key] : fallback]));

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
  function calculate(points) {
    const closes = points.map(p => p.value);
    const fast = ema(closes, 12), slow = ema(closes, 26);
    const macd = fast.map((v, i) => v === null || slow[i] === null ? null : v - slow[i]);
    const signal = ema(macd, 9);
    let gain = 0, loss = 0;
    const mean = (i, period) => i + 1 < period ? null : closes.slice(i + 1 - period, i + 1).reduce((sum, v) => sum + v / period, 0);
    const finite = value => Number.isFinite(value) ? value : null;
    return points.map((point, i) => {
      const middle = mean(i, 20);
      const deviation = middle === null ? null : Math.sqrt(closes.slice(i - 19, i + 1).reduce((sum, v) => sum + (v - middle) ** 2 / 20, 0));
      if (i) {
        const delta = closes[i] - closes[i - 1];
        if (i <= 14) { gain += Math.max(0, delta) / 14; loss += Math.max(0, -delta) / 14; }
        else { gain += (Math.max(0, delta) - gain) / 14; loss += (Math.max(0, -delta) - loss) / 14; }
      }
      return {
        time: point.time, sma8: finite(mean(i, 8)), sma21: finite(mean(i, 21)),
        middle: finite(middle), upper: middle === null ? null : finite(middle + 2 * deviation), lower: middle === null ? null : finite(middle - 2 * deviation),
        macd: finite(macd[i]), signal: finite(signal[i]), histogram: signal[i] === null ? null : finite(macd[i] - signal[i]),
        rsi: i < 14 ? null : gain === 0 && loss === 0 ? 50 : loss === 0 ? 100 : finite(100 - 100 / (1 + gain / loss)),
      };
    });
  }

  function bind(chart) {
    const button = document.getElementById('indicators-toggle');
    const dialog = document.getElementById('chart-indicators');
    const status = document.getElementById('indicator-status');
    const legend = document.getElementById('indicator-legend');
    let preference;
    try { preference = normalize(JSON.parse(localStorage.getItem(storageKey))); }
    catch { preference = normalize(null); }
    function apply() {
      for (const input of dialog.querySelectorAll('input[data-indicator]')) input.checked = preference[input.dataset.indicator];
      chart.setIndicators(preference);
      const keys = [];
      if (preference.sma) keys.push(['indicator-primary', 'SMA 8'], ['indicator-signal', 'SMA 21']);
      if (preference.bollinger) keys.push(['indicator-band', 'Bollinger 20 · ±2σ']);
      legend.replaceChildren(...keys.map(([name, text]) => {
        const span = document.createElement('span'); span.className = name; span.textContent = text; return span;
      }));
      legend.hidden = keys.length === 0;
    }
    button.addEventListener('click', () => {
      dialog.showModal();
      button.setAttribute('aria-expanded', 'true');
      dialog.querySelector('input').focus({preventScroll: true});
    });
    document.getElementById('indicators-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
      button.setAttribute('aria-expanded', 'false');
      button.focus({preventScroll: true});
    });
    dialog.addEventListener('change', event => {
      const input = event.target;
      if (!Object.hasOwn(defaults, input.dataset.indicator)) return;
      preference = normalize({...preference, [input.dataset.indicator]: input.checked});
      apply();
      try { localStorage.setItem(storageKey, JSON.stringify(preference)); status.hidden = true; status.textContent = ''; }
      catch { status.hidden = false; status.textContent = 'Browser storage unavailable; chart selections last for this page session.'; }
    });
    window.addEventListener('storage', event => {
      if (event.key !== storageKey && event.key !== null) return;
      try { preference = normalize(JSON.parse(event.newValue)); }
      catch { preference = normalize(null); }
      apply();
    });
    apply();
  }
  return {normalize, calculate, bind};
})();
window.ChartIndicators = ChartIndicators;
