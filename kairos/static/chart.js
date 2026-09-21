/* D3 charts use local assets only; prices and fills stay in the authenticated session. */
class LiveChart {
  constructor(selector, color, prefix, timeframeMs = null) {
    this.node = document.querySelector(selector);
    this.svg = d3.select(this.node);
    this.color = color;
    this.prefix = prefix;
    this.timeframeMs = timeframeMs;
    this.points = [];
    this.fills = [];
    this.transform = d3.zoomIdentity;
    this.margin = {top: 28, right: 82, bottom: 26, left: 4};
    const defs = this.svg.append('defs');
    const gradient = defs.append('linearGradient').attr('id', `${prefix}-gradient`)
      .attr('x1', 0).attr('y1', 0).attr('x2', 0).attr('y2', 1);
    gradient.append('stop').attr('offset', '0%').attr('stop-color', color).attr('stop-opacity', .22);
    gradient.append('stop').attr('offset', '100%').attr('stop-color', color).attr('stop-opacity', 0);
    this.clip = defs.append('clipPath').attr('id', `${prefix}-clip`).append('rect');
    this.grid = this.svg.append('g').attr('class', 'd3-grid');
    this.plot = this.svg.append('g').attr('clip-path', `url(#${prefix}-clip)`);
    this.area = this.plot.append('path').attr('fill', `url(#${prefix}-gradient)`);
    this.line = this.plot.append('path').attr('fill', 'none').attr('stroke', color).attr('stroke-width', 1.8);
    this.fillGroup = this.plot.append('g');
    this.lastLine = this.plot.append('line').attr('stroke', color).attr('stroke-opacity', .45).attr('stroke-dasharray', '3 4');
    this.lastDot = this.plot.append('circle').attr('r', 4).attr('fill', color).attr('stroke', '#101723').attr('stroke-width', 2);
    this.xAxis = this.svg.append('g').attr('class', 'd3-axis');
    this.yAxis = this.svg.append('g').attr('class', 'd3-axis');
    this.lastLabel = this.svg.append('text').attr('class', 'chart-tip').attr('fill', color);
    this.empty = this.svg.append('text').attr('class', 'chart-empty').attr('text-anchor', 'middle').text('Waiting for live observations');
    this.cross = this.svg.append('g').attr('class', 'd3-crosshair').attr('display', 'none');
    this.crossX = this.cross.append('line');
    this.crossY = this.cross.append('line');
    this.crossDot = this.cross.append('circle').attr('r', 4).attr('fill', color);
    this.tip = this.svg.append('text').attr('class', 'chart-tip');
    this.zoom = d3.zoom().scaleExtent([1, 40]).on('zoom', event => {
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
    if (this.timeframeMs !== null) {
      // Retain the largest selectable window, plus one point for the left-edge segment.
      const cutoff = time - 60 * 60 * 1000;
      while (this.points.length > 2 && this.points[1].time < cutoff) this.points.shift();
    } else if (this.points.length > 1800) this.points.shift();
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
    this.cross.attr('display', 'none'); this.tip.text('');
    this.svg.call(this.zoom.transform, d3.zoomIdentity);
  }
  setTimeframe(milliseconds) {
    if (!Number.isFinite(milliseconds) || milliseconds <= 0 || milliseconds > 60 * 60 * 1000) {
      throw new RangeError('Chart timeframe must be between zero and one hour');
    }
    this.timeframeMs = milliseconds;
    this.follow();
  }
  timeDomain() {
    const first = this.points[0].time, last = this.points.at(-1).time;
    if (this.timeframeMs !== null) return [last - this.timeframeMs, last];
    const start = Math.min(first, last - 60000);
    return [start, last + Math.max((last-start)*.025, 1000)];
  }
  draw() {
    const width = this.node.clientWidth, height = this.node.clientHeight;
    if (!width || !height) return;
    const m = this.margin;
    this.width = width; this.height = height;
    this.svg.attr('viewBox', `0 0 ${width} ${height}`);
    this.clip.attr('x', m.left).attr('y', m.top).attr('width', width-m.left-m.right).attr('height', height-m.top-m.bottom);
    this.zoom.extent([[m.left, m.top], [width-m.right, height-m.bottom]])
      .translateExtent([[m.left, m.top], [width-m.right, height-m.bottom]]);
    this.empty.attr('x', (width-m.right)/2).attr('y', height/2).attr('display', this.points.length ? 'none' : null);
    if (!this.points.length) {
      this.area.attr('d', null); this.line.attr('d', null); this.fillGroup.selectAll('*').remove();
      this.lastDot.attr('display', 'none'); this.lastLine.attr('display', 'none'); this.lastLabel.text('');
      this.xAxis.selectAll('*').remove(); this.yAxis.selectAll('*').remove(); this.grid.selectAll('*').remove();
      return;
    }
    const baseX = d3.scaleTime().domain(this.timeDomain()).range([m.left, width-m.right]);
    this.x = this.transform.rescaleX(baseX);
    const domain = this.x.domain().map(Number);
    const visible = this.points.filter(p => p.time >= domain[0] && p.time <= domain[1]);
    const values = (visible.length ? visible : this.points).map(p => p.value);
    for (const fill of this.fills) if (fill.time >= domain[0] && fill.time <= domain[1]) values.push(fill.value);
    const [low, high] = d3.extent(values);
    const pad = Math.max((high-low)*.15, Math.abs(high)*.00005, .000001);
    this.y = d3.scaleLinear().domain([low-pad, high+pad]).range([height-m.bottom, m.top]);
    this.xAxis.attr('transform', `translate(0,${height-m.bottom})`)
      .call(d3.axisBottom(this.x).ticks(Math.max(2, Math.floor((width-m.left-m.right)/100))).tickFormat(d3.timeFormat('%H:%M:%S')).tickSize(0).tickPadding(12));
    this.yAxis.attr('transform', `translate(${width-m.right},0)`)
      .call(d3.axisRight(this.y).ticks(height < 200 ? 3 : 6).tickFormat(d3.format(',.5~f')).tickSize(0).tickPadding(12));
    this.grid.attr('transform', `translate(${width-m.right},0)`)
      .call(d3.axisRight(this.y).ticks(height < 200 ? 3 : 6).tickSize(-(width-m.right-m.left)).tickFormat(''));
    const line = d3.line().x(p => this.x(p.time)).y(p => this.y(p.value));
    const area = d3.area().x(p => this.x(p.time)).y0(height-m.bottom).y1(p => this.y(p.value));
    this.line.attr('d', line(this.points)); this.area.attr('d', area(this.points));
    const latest = this.points.at(-1), latestY = this.y(latest.value);
    const inView = latest.time <= domain[1] && latest.time >= domain[0];
    this.lastDot.attr('display', inView ? null : 'none').attr('cx', this.x(latest.time)).attr('cy', latestY);
    this.lastLine.attr('display', inView ? null : 'none').attr('x1', m.left).attr('x2', width-m.right).attr('y1', latestY).attr('y2', latestY);
    this.lastLabel.attr('x', width-m.right+10).attr('y', m.top-10).text(inView ? d3.format(',.5~f')(latest.value) : 'HISTORY');
    this.fillGroup.selectAll('path').data(this.fills, d => d.id).join('path')
      .attr('d', d3.symbol().type(d3.symbolTriangle).size(65))
      .attr('transform', d => `translate(${this.x(d.time)},${this.y(d.value)}) rotate(${d.side === 'sell' ? 180 : 0})`)
      .attr('fill', d => d.side === 'buy' ? '#3de0b1' : '#ff788d').attr('stroke', '#101723').attr('stroke-width', 1.5);
  }
  inspect(event) {
    if (!this.points.length || !this.x) return;
    const [px, py] = d3.pointer(event, this.node);
    if (px > this.width-this.margin.right || py < this.margin.top || py > this.height-this.margin.bottom) return;
    const time = +this.x.invert(px);
    const i = d3.bisector(p => p.time).center(this.points, time);
    const point = this.points[i];
    const x = this.x(point.time), y = this.y(point.value);
    this.cross.attr('display', null);
    this.crossX.attr('x1', x).attr('x2', x).attr('y1', this.margin.top).attr('y2', this.height-this.margin.bottom);
    this.crossY.attr('x1', 0).attr('x2', this.width-this.margin.right).attr('y1', y).attr('y2', y);
    this.crossDot.attr('cx', x).attr('cy', y);
    this.tip.attr('x', 6).attr('y', 14).text(`${d3.timeFormat('%H:%M:%S')(new Date(point.time))}  ·  ${d3.format(',.8~f')(point.value)}`);
  }
}
window.LiveChart = LiveChart;
