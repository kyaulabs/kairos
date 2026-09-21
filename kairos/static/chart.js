/* D3 charts use local assets only; prices and fills stay in the authenticated session. */
class LiveChart {
  constructor(selector, color, prefix, intervalMinutes = null) {
    this.node = document.querySelector(selector);
    this.svg = d3.select(this.node);
    this.color = color;
    this.prefix = prefix;
    this.intervalMs = intervalMinutes === null ? null : intervalMinutes * 60000;
    this.anchor = null;
    this.points = [];
    this.fills = [];
    this.transform = d3.zoomIdentity;
    this.margin = {top: this.intervalMs ? 72 : 28, right: 82, bottom: 26, left: 4};
    const defs = this.svg.append('defs');
    const gradient = defs.append('linearGradient').attr('id', `${prefix}-gradient`)
      .attr('x1', 0).attr('y1', 0).attr('x2', 0).attr('y2', 1);
    gradient.append('stop').attr('offset', '0%').attr('stop-color', color).attr('stop-opacity', .22);
    gradient.append('stop').attr('offset', '100%').attr('stop-color', color).attr('stop-opacity', 0);
    this.clip = defs.append('clipPath').attr('id', `${prefix}-clip`).append('rect');
    this.volumeClip = defs.append('clipPath').attr('id', `${prefix}-volume-clip`).append('rect');
    this.volumeGroup = this.svg.append('g').attr('class', 'volume-bars').attr('clip-path', `url(#${prefix}-volume-clip)`);
    this.volumeAxis = this.svg.append('g').attr('class', 'd3-axis');
    this.volumeDivider = this.svg.append('line').attr('stroke', '#243348');
    this.volumeLabel = this.svg.append('text').attr('class', 'chart-tip');
    this.grid = this.svg.append('g').attr('class', 'd3-grid');
    this.plot = this.svg.append('g').attr('clip-path', `url(#${prefix}-clip)`);
    this.area = this.plot.append('path').attr('fill', `url(#${prefix}-gradient)`);
    this.line = this.plot.append('path').attr('fill', 'none').attr('stroke', color).attr('stroke-width', 1.8);
    this.candleGroup = this.plot.append('g').attr('class', 'candles');
    this.fillGroup = this.plot.append('g').attr('class', 'fills');
    this.lastLine = this.plot.append('line').attr('stroke', color).attr('stroke-opacity', .45).attr('stroke-dasharray', '3 4');
    this.lastDot = this.plot.append('circle').attr('r', 4).attr('fill', color).attr('stroke', '#101723').attr('stroke-width', 2);
    this.xAxis = this.svg.append('g').attr('class', 'd3-axis');
    this.yAxis = this.svg.append('g').attr('class', 'd3-axis');
    this.lastBadge = this.svg.append('rect').attr('class', 'last-price-badge');
    this.lastLabel = this.svg.append('text').attr('class', 'chart-tip').attr('fill', color);
    this.empty = this.svg.append('text').attr('class', 'chart-empty').attr('text-anchor', 'middle').text('Waiting for live observations');
    this.cross = this.svg.append('g').attr('class', 'd3-crosshair').attr('display', 'none');
    this.crossX = this.cross.append('line');
    this.crossY = this.cross.append('line');
    this.crossDot = this.cross.append('circle').attr('r', 4).attr('fill', color);
    this.tip = this.svg.append('text').attr('class', 'chart-tip');
    this.zoom = d3.zoom().scaleExtent([this.intervalMs ? 1/12 : 1, 40]).on('zoom', event => {
      if (this.intervalMs && event.sourceEvent && this.anchor === null) this.anchor = this.points.at(-1)?.time ?? null;
      this.transform = event.transform;
      this.draw();
    });
    this.svg.call(this.zoom).on('dblclick.zoom', null)
      .on('pointermove.inspect', event => this.inspect(event))
      .on('pointerleave.inspect', () => { this.cross.attr('display', 'none'); this.tip.text(''); })
      .on('keydown', event => { if (event.key === 'Home' || event.key === 'Escape') this.follow(); });
    this.resizeObserver = new ResizeObserver(() => this.draw());
    this.resizeObserver.observe(this.node);
  }
  add(ts, value) {
    if (!Number.isFinite(value) || !Number.isFinite(ts)) return;
    const time = ts * 1000;
    const last = this.points.at(-1);
    if (last && time < last.time) return;
    // Keep one point per second without letting frequent ticks overwrite history forever.
    if (last && Math.floor(time / 1000) === Math.floor(last.time / 1000)) this.points[this.points.length - 1] = {time, value};
    else this.points.push({time, value});
    if (this.points.length > 1800) this.points.shift();
    this.draw();
  }
  setCandles(candles) {
    const points = candles.slice(-720).map(c => ({
      time: Number(c.time) * 1000, open: Number(c.open), high: Number(c.high), low: Number(c.low), value: Number(c.close),
      volume: c.volume == null || c.volume === '' ? NaN : Number(c.volume),
    }));
    if (points.some((p, i) => !Object.values(p).every(Number.isFinite) || p.time <= 0 || p.low <= 0 || p.volume < 0 ||
      p.high < Math.max(p.open, p.value) || p.low > Math.min(p.open, p.value) ||
      (i > 0 && p.time <= points[i-1].time))) throw new Error('Invalid candle data');
    // Replace the snapshot: updates to a forming candle must not append duplicates.
    this.points = points;
    this.draw();
  }
  fill(event) {
    if (!event.id || this.fills.some(fill => fill.id === event.id)) return;
    this.fills.push(event);
    this.fills = this.fills.slice(-200);
    this.draw();
  }
  clear() {
    this.points = [];
    this.fills = [];
    this.follow();
  }
  follow() {
    this.anchor = null;
    this.svg.call(this.zoom.transform, d3.zoomIdentity);
  }
  setCandleInterval(minutes) {
    if (![1, 5, 15, 30, 60, 240, 1440].includes(minutes)) throw new RangeError('Unsupported candle interval');
    this.intervalMs = minutes * 60000;
    this.points = [];
    this.follow();
  }
  timeDomain() {
    const first = this.points[0].time, last = this.points.at(-1).time;
    if (this.intervalMs) {
      const end = (this.anchor ?? last) + 5 * this.intervalMs;
      return [end - 60 * this.intervalMs, end];
    }
    const start = Math.min(first, last - 60000);
    return [start, last + Math.max((last-start)*.025, 1000)];
  }
  draw() {
    const width = this.node.clientWidth, height = this.node.clientHeight;
    if (!width || !height) return;
    const m = this.margin;
    this.cross.attr('display', 'none'); this.tip.text('');
    this.width = width; this.height = height;
    this.svg.attr('viewBox', `0 0 ${width} ${height}`);
    const bottom = height-m.bottom;
    const volumeTop = bottom - (bottom-m.top)*.23;
    this.priceBottom = this.intervalMs ? volumeTop-30 : bottom;
    this.clip.attr('x', m.left).attr('y', m.top).attr('width', width-m.left-m.right).attr('height', this.priceBottom-m.top);
    this.volumeClip.attr('x', m.left).attr('y', volumeTop).attr('width', width-m.left-m.right).attr('height', bottom-volumeTop);
    const showVolume = this.intervalMs && this.points.length;
    this.volumeGroup.attr('display', showVolume ? null : 'none');
    this.volumeAxis.attr('display', showVolume ? null : 'none');
    this.volumeLabel.attr('display', showVolume ? null : 'none');
    this.volumeDivider.attr('display', showVolume ? null : 'none')
      .attr('x1', m.left).attr('x2', width).attr('y1', volumeTop-22).attr('y2', volumeTop-22);
    this.zoom.extent([[m.left, m.top], [width-m.right, height-m.bottom]])
      .translateExtent([[m.left, m.top], [width-m.right, height-m.bottom]]);
    this.empty.attr('x', (width-m.right)/2).attr('y', height/2).attr('display', this.points.length ? 'none' : null);
    if (!this.points.length) {
      this.area.attr('d', null); this.line.attr('d', null); this.fillGroup.selectAll('*').remove();
      this.candleGroup.selectAll('*').remove(); this.volumeGroup.selectAll('*').remove();
      this.volumeAxis.selectAll('*').remove();
      this.lastBadge.attr('display', 'none');
      this.lastDot.attr('display', 'none'); this.lastLine.attr('display', 'none'); this.lastLabel.text('');
      this.xAxis.selectAll('*').remove(); this.yAxis.selectAll('*').remove(); this.grid.selectAll('*').remove();
      return;
    }
    const baseX = d3.scaleTime().domain(this.timeDomain()).range([m.left, width-m.right]);
    if (this.intervalMs) {
      this.zoom.translateExtent([[Math.min(m.left, baseX(this.points[0].time)), m.top],
        [Math.max(width-m.right, baseX(this.points.at(-1).time + 5*this.intervalMs)), height-m.bottom]]);
    }
    this.x = this.transform.rescaleX(baseX);
    const domain = this.x.domain().map(Number);
    const visible = this.points.filter(p => this.intervalMs
      ? p.time + this.intervalMs > domain[0] && p.time < domain[1]
      : p.time >= domain[0] && p.time <= domain[1]);
    const values = (visible.length ? visible : this.points).flatMap(p => this.intervalMs ? [p.low, p.high] : [p.value]);
    for (const fill of this.fills) if (fill.time >= domain[0] && fill.time <= domain[1]) values.push(fill.value);
    const [low, high] = d3.extent(values);
    const pad = Math.max((high-low)*.15, Math.abs(high)*.00005, .000001);
    this.y = d3.scaleLinear().domain([low-pad, high+pad]).range([this.priceBottom, m.top]);
    this.xAxis.attr('transform', `translate(0,${height-m.bottom})`)
      .call(d3.axisBottom(this.x).ticks(Math.max(2, Math.floor((width-m.left-m.right)/100))).tickFormat(this.intervalMs ? null : d3.timeFormat('%H:%M:%S')).tickSize(0).tickPadding(12));
    this.xAxis.selectAll('.tick text').attr('text-anchor', d => this.x(d) < m.left+30 ? 'start' : this.x(d) > width-m.right-30 ? 'end' : 'middle');
    this.yAxis.attr('transform', `translate(${width-m.right},0)`)
      .call(d3.axisRight(this.y).ticks(this.priceBottom-m.top < 200 ? 3 : 6).tickFormat(d3.format(',.5~f')).tickSize(0).tickPadding(12));
    this.grid.attr('transform', `translate(${width-m.right},0)`)
      .call(d3.axisRight(this.y).ticks(this.priceBottom-m.top < 200 ? 3 : 6).tickSize(-(width-m.right-m.left)).tickFormat(''));
    const center = p => p.time + (this.intervalMs || 0)/2;
    if (this.intervalMs) {
      this.line.attr('d', null); this.area.attr('d', null);
      const bodyWidth = Math.max(1, Math.min(24, (this.x(this.intervalMs)-this.x(0))*.7));
      const volumeY = d3.scaleLinear().domain([0, d3.max(visible, p => p.volume) || 1]).nice().range([bottom, volumeTop]);
      this.volumeGroup.selectAll('rect').data(visible, d => d.time).join('rect')
        .attr('x', d => this.x(center(d))-bodyWidth/2).attr('width', bodyWidth)
        .attr('y', d => volumeY(d.volume)).attr('height', d => bottom-volumeY(d.volume))
        .attr('fill', d => d.value >= d.open ? '#3de0b1' : '#ff788d').attr('fill-opacity', .5);
      this.volumeAxis.attr('transform', `translate(${width-m.right},0)`)
        .call(d3.axisRight(volumeY).ticks(2).tickFormat(d3.format('.3~s')).tickSize(0).tickPadding(12));
      this.volumeLabel.attr('x', m.left+2).attr('y', volumeTop-7).text('Volume · base asset');
      const bars = this.candleGroup.selectAll('g.candle').data(visible, d => d.time).join(enter => {
        const bar = enter.append('g').attr('class', 'candle');
        bar.append('line'); bar.append('rect');
        return bar;
      }).attr('stroke', d => d.value >= d.open ? '#3de0b1' : '#ff788d');
      bars.select('line').attr('x1', d => this.x(center(d))).attr('x2', d => this.x(center(d)))
        .attr('y1', d => this.y(d.high)).attr('y2', d => this.y(d.low));
      bars.select('rect').attr('x', d => this.x(center(d))-bodyWidth/2).attr('width', bodyWidth)
        .attr('y', d => Math.min(this.y(d.open), this.y(d.value)))
        .attr('height', d => Math.max(1, Math.abs(this.y(d.open)-this.y(d.value))))
        .attr('fill', d => d.value >= d.open ? '#3de0b1' : '#ff788d');
    } else {
      const line = d3.line().x(p => this.x(p.time)).y(p => this.y(p.value));
      const area = d3.area().x(p => this.x(p.time)).y0(height-m.bottom).y1(p => this.y(p.value));
      this.line.attr('d', line(this.points)); this.area.attr('d', area(this.points));
    }
    const latest = this.points.at(-1), latestY = this.y(latest.value);
    const inView = center(latest) <= domain[1] && center(latest) >= domain[0];
    const latestColor = this.intervalMs ? (latest.value >= latest.open ? '#3de0b1' : '#ff788d') : this.color;
    this.lastDot.attr('display', inView && !this.intervalMs ? null : 'none').attr('cx', this.x(center(latest))).attr('cy', latestY);
    this.lastLine.attr('display', inView ? null : 'none').attr('stroke', latestColor)
      .attr('x1', m.left).attr('x2', width-m.right).attr('y1', latestY).attr('y2', latestY);
    this.lastBadge.attr('display', inView && this.intervalMs ? null : 'none')
      .attr('x', width-m.right).attr('y', latestY-10).attr('width', m.right).attr('height', 20).attr('fill', latestColor);
    this.lastLabel.attr('x', width-m.right+6).attr('y', inView && this.intervalMs ? latestY : m.top-10)
      .attr('dy', inView && this.intervalMs ? '.35em' : 0)
      .style('fill', inView && this.intervalMs ? '#101723' : this.color)
      .text(inView ? d3.format(',.5~f')(latest.value) : 'HISTORY');
    this.fillGroup.selectAll('path').data(this.fills, d => d.id).join('path')
      .attr('d', d3.symbol().type(d3.symbolTriangle).size(65))
      .attr('transform', d => `translate(${this.x(d.time)},${this.y(d.value)}) rotate(${d.side === 'sell' ? 180 : 0})`)
      .attr('fill', d => d.side === 'buy' ? '#3de0b1' : '#ff788d').attr('stroke', '#101723').attr('stroke-width', 1.5);
  }
  inspect(event) {
    if (!this.points.length || !this.x) return;
    const [px, py] = d3.pointer(event, this.node);
    this.cross.attr('display', 'none'); this.tip.text('');
    if (px < this.margin.left || px > this.width-this.margin.right || py < this.margin.top || py > this.height-this.margin.bottom) return;
    const time = +this.x.invert(px);
    const center = p => p.time + (this.intervalMs || 0)/2;
    const i = d3.bisector(center).center(this.points, time);
    const point = this.points[i];
    const x = this.x(center(point)), y = this.y(point.value);
    if (x < this.margin.left || x > this.width-this.margin.right) return;
    this.cross.attr('display', null);
    this.crossX.attr('x1', x).attr('x2', x).attr('y1', this.margin.top).attr('y2', this.height-this.margin.bottom);
    this.crossY.attr('x1', 0).attr('x2', this.width-this.margin.right).attr('y1', y).attr('y2', y);
    this.crossDot.attr('display', this.intervalMs ? 'none' : null).attr('cx', x).attr('cy', y);
    const format = d3.format(',.8~g');
    const lines = this.intervalMs ? [
      d3.timeFormat('%Y-%m-%d %H:%M')(new Date(point.time)),
      `O ${format(point.open)}  H ${format(point.high)}`,
      `L ${format(point.low)}  C ${format(point.value)}`,
      `V ${format(point.volume)} · base asset`,
    ] : [`${d3.timeFormat('%H:%M:%S')(new Date(point.time))}  ·  ${d3.format(',.8~f')(point.value)}`];
    this.tip.selectAll('tspan').data(lines).join('tspan').attr('x', 6).attr('y', (_, i) => 14+i*14).text(d => d);
  }
}
window.LiveChart = LiveChart;
