/* Read-only D3 instruments. Model weights are not probabilities of profit or trade fills. */
class AssessmentView {
  constructor(element) {
    this.element = element; this.width = 0;
    this.resize = new ResizeObserver(entries => {
      const width = Math.round(entries[0].contentRect.width);
      if (!width || width === this.width) return;
      this.width = width;
      if (this.latest) this.render(...this.latest);
    });
    this.resize.observe(element);
  }
  static number(value) {
    if (value == null || value === '' || typeof value === 'boolean') return null;
    const n = Number(value); return Number.isFinite(n) ? n : null;
  }
  static weights(decision) {
    if (!decision || decision.deterministic) return null;
    const rows = ['buy', 'hold', 'sell'].map(action => ({action, value: AssessmentView.number(decision.probabilities?.[action])}));
    if (rows.some(r => r.value == null || r.value < 0 || r.value > 1) || Math.abs(rows.reduce((s, r) => s+r.value, 0)-1) > .02) return null;
    return rows;
  }
  static matches(decision, state) {
    return !!decision && decision.strategy === state.settings.strategy && decision.pair === state.settings.pair && decision.mode === state.mode && (decision.state?.product || 'spot') === state.settings.product;
  }
  static bandPosition(input) {
    const close = AssessmentView.number(Array.isArray(input.series) ? input.series.at(-1)?.close : null);
    const [lower, middle, upper] = ['lower', 'middle', 'upper'].map(key => AssessmentView.number(input[key]));
    if (!(close > 0 && lower > 0 && lower < middle && middle < upper)) return null;
    const position = (close-middle) / (close < middle ? middle-lower : upper-middle);
    return Number.isFinite(position) ? position : null;
  }
  static dial(svg, value, score, caption, left, right) {
    const text = (x,y,label,cls) => svg.append('text').attr('x',x).attr('y',y).attr('class',cls).attr('text-anchor','middle').text(label);
    text(180,18,caption,'instrument-caption');
    const arc=d3.arc().innerRadius(73).outerRadius(84);
    svg.append('g').attr('transform','translate(180,120)').selectAll('path').data(d3.range(30)).join('path').attr('d',i=>arc({startAngle:-Math.PI/2+i*Math.PI/30+.008,endAngle:-Math.PI/2+(i+1)*Math.PI/30-.008})).attr('fill',i=>i<14?'var(--danger)':i>15?'var(--success)':'var(--muted)').attr('opacity',value == null ? .12 : .35);
    if (value != null) {
      const angle=Math.max(-1,Math.min(1,value))*Math.PI/2;
      svg.append('line').attr('class','instrument-needle').attr('x1',180+Math.sin(angle)*65).attr('y1',120-Math.cos(angle)*65).attr('x2',180+Math.sin(angle)*89).attr('y2',120-Math.cos(angle)*89).attr('stroke','var(--text)').attr('stroke-width',3).attr('stroke-linecap','round');
    }
    text(180,110,score,'instrument-score');
    text(85,143,left,'instrument-caption');text(275,143,right,'instrument-caption');
  }
  render(decision, state, events = [], definition = {}) {
    this.latest = [decision, state, events, definition];
    const root = d3.select(this.element); root.selectAll('*').remove();
    const color = action => ({buy: 'var(--success)', sell: 'var(--danger)', hold: 'var(--muted)'}[action] || 'var(--accent)');
    const svg = root.append('svg').attr('viewBox', '0 0 360 214').attr('role', 'img').attr('class', 'assessment-instrument');
    const text = (x, y, value, cls = '', anchor = 'start') => svg.append('text').attr('x', x).attr('y', y).attr('class', cls).attr('text-anchor', anchor).text(value);
    const rules = state.settings.strategy === 'scalp';
    const input = decision?.state || (rules && state.scalp?.position ? {...state.scalp.position.signal, position: state.scalp.position} : {});
    root.attr('data-kind', rules ? 'scalp' : 'model');
    if (definition.scheduled) {
      svg.attr('viewBox', '0 0 360 100').attr('aria-label', 'Deterministic program status. Claimed slots are not confirmed fills.');
      const total = state.settings.strategy === 'dca' ? state.settings.dca_count : state.settings.strategy === 'twap' ? state.settings.twap_slices : null;
      const claimed = state.program?.next_slot || 0;
      text(14, 20, 'DETERMINISTIC EXECUTION', 'instrument-caption');
      text(14, 50, total ? `${claimed} / ${total} SLOTS CLAIMED` : 'CONTINUOUS REBALANCING', 'instrument-value');
      svg.append('rect').attr('x',14).attr('y',66).attr('width',332).attr('height',5).attr('fill','var(--border)');
      if (total) svg.append('rect').attr('x',14).attr('y',66).attr('width',332*Math.min(1,claimed/total)).attr('height',5).attr('fill','var(--accent)');
      text(14, 91, 'CLAIMED ≠ FILLED', 'instrument-caption');
    } else if (rules) {
      const position=AssessmentView.bandPosition(input), z=AssessmentView.number(input.z_score);
      const source=input.position ? 'ENTRY' : state.running ? 'ASSESSED' : 'PAUSED';
      svg.attr('viewBox','0 0 360 96').attr('class','assessment-instrument range-gauge').attr('aria-label',position == null ? 'Bollinger band position unavailable; awaiting a non-flat window.' : `${source === 'ENTRY' ? 'Saved entry window' : source === 'PAUSED' ? 'Paused, last assessed window' : 'Latest assessed window'}: ${z == null ? 'standard deviation unavailable' : `${z.toFixed(2)} standard deviations from the midpoint`}. Upper band left, lower band right; needle capped at bands. Not confidence or permission to trade.`);
      text(14,24,position == null ? 'RANGE · AWAITING DATA' : `${source} · RANGE`,'instrument-caption');
      text(346,24,position != null && z != null ? `${z>0?'+':''}${z.toFixed(2)}σ` : '—','instrument-score','end');
      const level=position == null ? null : (1-Math.max(-1,Math.min(1,position)))/2;
      const step=(332+2)/30; // Equal segments with gaps only between them.
      svg.append('g').attr('class','range-segments').selectAll('rect').data(d3.range(30)).join('rect').attr('x',i=>14+i*step).attr('y',44).attr('width',step-2).attr('height',12).attr('rx',1).attr('fill',i=>i<14?'var(--danger)':i>15?'var(--success)':'var(--muted)').attr('opacity',i=>level == null ? .12 : i/29<=level ? .75 : .2);
      if (level != null) {
        const x=14+level*332;
        svg.append('line').attr('class','instrument-needle').attr('x1',x).attr('x2',x).attr('y1',38).attr('y2',62).attr('stroke','var(--text)').attr('stroke-width',2).attr('stroke-linecap','round');
      }
      text(14,82,'UPPER','instrument-caption');text(180,82,'MID','instrument-caption','middle');text(346,82,'LOWER','instrument-caption','end');
      if (Array.isArray(input.series) && input.series.length > 1) {
        // Labels sit inside the plot; reserve no separate right-hand label gutter.
        const svg=root.append('svg').attr('viewBox','0 0 360 214').attr('role','img').attr('class','assessment-instrument bollinger-instrument');
        const text=(x,y,value,cls='',anchor='start')=>svg.append('text').attr('x',x).attr('y',y).attr('class',cls).attr('text-anchor',anchor).text(value);
        const series = input.series.map(row => ({time: AssessmentView.number(row.time), close: AssessmentView.number(row.close)})).filter(row => row.time != null && row.close > 0);
        const levels = ['lower', 'middle', 'upper'].map(name => ({name, value: AssessmentView.number(input[name])}));
        if (series.length > 1 && levels.every(row => row.value > 0) && levels[0].value <= levels[1].value && levels[1].value <= levels[2].value) {
          svg.attr('aria-label', 'Rolling one-minute closing prices with the latest window bands; fixed entry window while holding.');
          text(14, 18, input.position ? 'ENTRY WINDOW · FIXED TARGET' : `${input.window} × 1 MINUTE · LOCAL RANGE`, 'instrument-caption');
          const extent = d3.extent([...series.map(row => row.close), ...levels.map(row => row.value)]);
          const pad = Math.max((extent[1]-extent[0])*.15, extent[1]*.0001);
          const y = d3.scaleLinear().domain([extent[0]-pad, extent[1]+pad]).range([166, 32]);
          const x = d3.scaleLinear().domain(d3.extent(series, row => row.time)).range([14, 346]);
          svg.append('rect').attr('x',14).attr('y',y(levels[2].value)).attr('width',332).attr('height',y(levels[0].value)-y(levels[2].value)).attr('fill','var(--accent)').attr('opacity',.09);
          const gap=12*360/Math.max(180,this.element.clientWidth || 360);
          const middleY=Math.max(32+gap,Math.min(166-gap,y(levels[1].value)));
          const labels=levels[2].value===levels[0].value ? [{name:'flat',value:levels[1].value,labelY:y(levels[1].value)}] : levels.map((level,i)=>({...level,labelY:i===0?Math.max(y(level.value),middleY+gap):i===2?Math.min(y(level.value),middleY-gap):middleY}));
          for (const level of labels) {
            svg.append('line').attr('x1',14).attr('x2',346).attr('y1',y(level.value)).attr('y2',y(level.value)).attr('stroke','var(--accent)').attr('stroke-dasharray',level.name === 'middle' ? '4 3' : '2 4').attr('opacity',.7);
            text(340,level.labelY+3,level.name.toUpperCase(),'instrument-caption band-label','end');
          }
          svg.append('path').datum(series).attr('d',d3.line().x(row=>x(row.time)).y(row=>y(row.close))).attr('fill','none').attr('stroke','var(--text)').attr('stroke-width',1.8);
          const last=series.at(-1);
          svg.append('circle').attr('cx',x(last.time)).attr('cy',y(last.close)).attr('r',3.5).attr('fill','var(--accent)');
          svg.selectAll('.band-label').raise();
          text(14,192,`LOW ${d3.format('.5~g')(levels[0].value)}`,'instrument-caption');
          text(346,192,`HIGH ${d3.format('.5~g')(levels[2].value)}`,'instrument-caption','end');
          text(14,208,`BANDS · ${new Date(input.candle_close_time*1000).toLocaleTimeString()}`,'instrument-caption');
        } else {
          svg.attr('aria-label','No usable price range');
          text(180,110,'NO USABLE PRICE RANGE','instrument-caption','middle');
        }
      }
    } else {
      svg.attr('viewBox', '0 0 360 228');
      const rows = AssessmentView.weights(decision);
      svg.attr('aria-label', rows ? `Model directional bias ${(100*(rows[0].value-rows[2].value)).toFixed(1)} percentage points; ${rows.map(r=>`${r.action} ${(r.value*100).toFixed(1)} percent`).join(', ')}. Not profitability.` : 'No current model assessment.');
      const bias=rows ? rows[0].value-rows[2].value : null;
      AssessmentView.dial(svg,bias,rows ? `${bias>0?'+':''}${(bias*100).toFixed(1)}` : '—',rows ? 'BUY − SELL · MODEL BIAS' : 'MODEL · AWAITING DATA','SELL','BUY');
      if(rows) {
        text(180,130,'NET BIAS · pp','instrument-caption','middle');
        let offset=14;
        for(const row of rows) {
          const width=row.value*332;
          svg.append('rect').attr('x',offset).attr('y',161).attr('width',width).attr('height',6).attr('fill',color(row.action));offset+=width;
        }
        rows.forEach((row,i)=>{
          text(14+i*115,190,row.action==='hold'?'NO TRADE':row.action.toUpperCase(),'instrument-caption');
          text(14+i*115,220,`${(row.value*100).toFixed(1)}%`,'instrument-value');
        });
      }
    }
    if (!rules && decision) {
      const move=AssessmentView.number(input.eight_candle_return_bps), cost=AssessmentView.number(input.round_trip_cost_bps);
      if(move!=null && cost!=null && cost>=0) {
        const gauge=root.append('svg').attr('viewBox','0 0 360 58').attr('class','cost-instrument').attr('role','img').attr('aria-label',`Historical move magnitude ${Math.abs(move).toFixed(1)} bps; round-trip fee/slippage threshold ${cost.toFixed(1)} bps. Not a forecast.`);
        const x=d3.scaleLinear().domain([0,Math.max(Math.abs(move),cost,1)*1.15]).range([14,346]);
        gauge.append('text').attr('x',14).attr('y',12).attr('class','instrument-caption').text('MOVE MAGNITUDE / COSTS');
        gauge.append('rect').attr('x',14).attr('y',23).attr('width',332).attr('height',5).attr('fill','var(--border)');
        gauge.append('rect').attr('x',14).attr('y',23).attr('width',x(Math.abs(move))-14).attr('height',5).attr('fill','var(--accent)');
        gauge.append('line').attr('x1',x(cost)).attr('x2',x(cost)).attr('y1',18).attr('y2',33).attr('stroke','var(--warning)').attr('stroke-width',2);
        gauge.append('text').attr('x',14).attr('y',49).attr('class','instrument-caption').text(`${Math.abs(move).toFixed(1)} bps move`);
        gauge.append('text').attr('x',346).attr('y',49).attr('text-anchor','end').attr('class','instrument-caption').text(`${cost.toFixed(1)} bps costs`);
      }
      const tape=events.filter(e=>e.kind==='decision' && AssessmentView.matches(e.data,state)).slice(-12);
      if(tape.length) {
        const track=root.append('div').attr('class','assessment-tape').attr('aria-label','Recent assessments, not executed trades');
        track.append('span').text('SIGNAL TAPE');
        track.selectAll('i').data(tape).join('i').style('background',e=>color(e.data.action)).attr('role','img').attr('aria-label',e=>`${e.data.action} assessment at ${new Date(e.ts*1000).toLocaleTimeString()}, not a fill`).attr('title',e=>`${e.data.action.toUpperCase()} · ${new Date(e.ts*1000).toLocaleTimeString()} · assessment, not fill`).text(e=>e.data.action[0].toUpperCase());
      }
    }
    const scale = 360 / Math.max(180, this.element.clientWidth || 360);
    root.selectAll('.instrument-caption').style('font-size', `${9*scale}px`);
    root.selectAll('.instrument-score').style('font-size', `${20*scale}px`);
    root.selectAll('.instrument-value').style('font-size', `${12*scale}px`);
    if (rules) svg.selectAll('.instrument-score').style('font-size', `${12*scale}px`);
    const metrics=definition.scheduled ? [['ORDERS',state.program?.orders || 0],['MISSED SLOTS',state.program?.skipped_slots || 0],['TURNOVER · USD',state.program?.spent_including_fees || 0]] : rules ? [['WINDOW Z', input.z_score],['TREND ER',input.efficiency],['NET · BPS',input.net_room_bps]] : [['TREND',input.trend],['SPREAD · BPS',input.spread_bps],['INVENTORY',input.inventory]];
    const cells=root.append('div').attr('class','assessment-metrics').selectAll('div').data(metrics).join('div');
    cells.append('span').text(row=>row[0]);
    cells.append('strong').text(row=>row[1]==null?'—':AssessmentView.number(row[1])!=null?Number(row[1]).toLocaleString(undefined,{maximumFractionDigits:3}):String(row[1]));
    const position = state.scalp?.position;
    if(rules) root.append('p').attr('class','assessment-protection muted').text(position ? `Stop ${Number(position.stop).toLocaleString(undefined,{maximumFractionDigits:8})} · target ${Number(position.target).toLocaleString(undefined,{maximumFractionDigits:8})} · until ${new Date(position.deadline*1000).toLocaleTimeString()}. Protection only while running.` : 'Flat · 1 position max · fixed target / stop / deadline');
  }
}
window.AssessmentView = AssessmentView;
