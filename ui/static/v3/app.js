/* Eye of the System, v3 "Investigation canvas". No build step: one classic script.
   The metric graph is the page: pan and zoom it, pick an incident in the dock and the view flies to its path,
   replay the investigation step by step on the graph. Data logic follows ui/static/index.html. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var $ = function (id) { return document.getElementById(id); };
  var graph = null, meta = {}, domains = [], domOf = {}, incidents = [], example = false, skipped = 0, graphKey = '';
  var dom = 'all', selInc = null, selNode = null, mode = 'incident', drawn = null;
  var LIVE = ['open', 'ongoing'], COUNTED = ['open', 'ongoing', 'resolved'], KNOWN = ['open', 'ongoing', 'resolved', 'explained', 'pending'];
  var still = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: true };

  // anything the monitor writes may have the wrong type: show it as text, never let it stop the page
  function str(v) { return v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v)); }
  function list(v) { return Array.isArray(v) ? v : []; }
  function obj(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; }
  function text(s) { return document.createTextNode(str(s)); }
  function el(tag, cls, kids) { var e = document.createElement(tag); if (cls) e.className = cls; (kids || []).forEach(function (k) { e.appendChild(k && k.nodeType ? k : text(k)); }); return e; }
  function svg(tag, attrs, parent) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
  function get(url) { return fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || r.status); return j; }); });
  }
  function num(n) { return typeof n === 'number' && isFinite(n) ? n : null; }
  function signed(s) { return s.charAt(0) === '-' ? '\u2212' + s.slice(1) : s; }
  function fmt(n, d) { n = num(Number(n)); return n == null ? '-' : signed(n.toLocaleString('en', { maximumFractionDigits: d == null ? 1 : d })); }
  function short(n) {
    n = num(n); if (n == null) return '\u2013';
    var a = Math.abs(n);
    return signed(a >= 100 ? n.toLocaleString('en', { maximumFractionDigits: 0 }) : a >= 1 ? n.toLocaleString('en', { maximumFractionDigits: 2 })
      : n.toLocaleString('en', { maximumSignificantDigits: 3 }));
  }
  function pct(n) { n = num(n); return n == null ? '\u2013' : (n > 0 ? '+' : '') + signed(fmt(n)) + '%'; }
  function tone(metric, delta) {
    var d = obj(meta[metric]).direction; delta = num(delta);
    if (!delta || (d !== 'up' && d !== 'down')) return '';
    return (d === 'up') === (delta > 0) ? 'good' : 'bad';
  }
  function toneIn(i, metric, delta) { var t = tone(metric, delta); return t === 'good' && status(i) !== 'explained' ? '' : t; }
  function euroTone(i, v) { v = num(v); return !v ? '' : v < 0 ? 'bad' : status(i) === 'explained' ? 'good' : ''; }
  function euroOf(i) {
    var imp = obj(i.impact);
    var k = num(imp.contribution_eur) != null ? 'contribution_eur' : Object.keys(imp).filter(function (x) { return /_eur$/.test(x) && num(imp[x]) != null; })[0];
    return k ? num(imp[k]) : null;
  }
  function eurText(v) { return (v > 0 ? '+' : '') + short(v) + ' EUR'; }
  function segText(s) { var o = obj(s), k = Object.keys(o); return k.length ? k.map(function (x) { return str(o[x]); }).join(' x ') : 'all'; }
  function when(t) { return str(t).replace('T', ' ').replace(':00Z', '').replace(/-/g, '\u2011'); }
  function hourOf(t) { var m = /T(\d\d:\d\d)/.exec(str(t)); return m ? m[1] : str(t); }
  function dayOf(t) { var m = /^\d{4}-(\d\d)-(\d\d)/.exec(str(t)); return m ? m[2] + '.' + m[1] : ''; }
  function note(msg) { $('notice').textContent = msg || ''; }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

  // ----------------------------------------------------------- domains
  function domainOf(metric) { return domOf[metric] || 'other'; }
  function domainLabel(id) { if (id === 'all') return 'All domains'; var d = domains.filter(function (x) { return x.id === id; })[0]; return d ? d.label : 'Other'; }
  function incidentNodes(i) {
    var s = {}; s[str(i.metric)] = true;
    list(i.contributions).forEach(function (c) { if (c && c.node) s[str(c.node)] = true; });
    list(i.trail).forEach(function (t) { t = obj(t); if (t.action === 'watch' && t.target && meta[t.target]) s[str(t.target)] = true; });
    return Object.keys(s);
  }
  function status(i) { return str(i.status) || 'open'; }
  function stOf(i) { var s = status(i); return 'st-' + (KNOWN.indexOf(s) >= 0 ? s : 'other'); }
  function touches(i, d) { return d === 'all' || incidentNodes(i).some(function (m) { return domainOf(m) === d; }); }
  function inScope() { return incidents.filter(function (i) { return touches(i, dom); }); }
  function liveCount(d) { return incidents.filter(function (i) { return LIVE.indexOf(status(i)) >= 0 && touches(i, d); }).length; }
  function selected() { return incidents.filter(function (i) { return i.id === selInc; })[0] || null; }

  function renderChips() {
    var nav = $('domains'); nav.textContent = '';
    var l = [{ id: 'all', label: 'All' }].concat(domains);
    if (graph && list(graph.nodes).some(function (n) { return n.kind === 'metric' && !domOf[n.id]; })) l.push({ id: 'other', label: 'Other' });
    l.forEach(function (d) {
      var n = liveCount(d.id);
      var b = el('button', 'chip' + (d.id === dom ? ' on' : ''), [d.label].concat(n ? [el('span', 'n', [String(n)])] : []));
      b.setAttribute('aria-pressed', d.id === dom ? 'true' : 'false');
      if (n) b.title = n + ' live incident' + (n > 1 ? 's' : '') + ' touch' + (n > 1 ? '' : 'es') + ' ' + d.label;
      b.onclick = function () {
        if (dom === d.id) return;
        dom = d.id; selNode = null; mode = 'incident'; stopReplay();
        var s = inScope(); if (!s.some(function (i) { return i.id === selInc; })) selInc = s.length ? s[0].id : null;
        renderAll(); fit(false); if (selInc) flyToIncident(true);
      };
      nav.appendChild(b);
    });
    renderCounts();
  }
  function renderCounts() {
    var c = $('counts'); c.textContent = '';
    KNOWN.forEach(function (s) {
      var k = incidents.filter(function (i) { return status(i) === s; }).length;
      if (!k && COUNTED.indexOf(s) < 0) return;
      c.appendChild(el('span', 'count st-' + s + (k ? '' : ' zero'), [el('b', null, [String(k)]), s]));
    });
  }

  // ------------------------------------------------------------- graph layout
  // Boxes hold metric names in a 13px mono face (0.6 em per character); each column is as wide as its longest name.
  var CHAR = 7.8, NODE_H = 32, DY = 46, GAP = 46, PAD = 28;
  function filterTree(n) {
    if (dom === 'all') return n;
    var kids = list(n.children).map(filterTree).filter(Boolean);
    if (domainOf(n.metric) === dom || kids.length) return { metric: n.metric, role: n.role, repeated: n.repeated, children: kids };
    return null;
  }
  function columns(roots) {
    var chars = [], x = [], hw = [], at = PAD;
    var walk = function (n, d) { chars[d] = Math.max(chars[d] || 0, str(n.metric).length); list(n.children).forEach(function (c) { walk(c, d + 1); }); };
    roots.forEach(function (r) { walk(r, 0); });
    chars.forEach(function (c, d) { var w = Math.max(100, Math.ceil(c * CHAR) + 40); hw[d] = w / 2; x[d] = at + w / 2; at += w + GAP; });
    return { x: x, hw: hw, w: at - GAP + PAD };
  }
  function layout(root, y0, cols) {
    var nodes = [], edges = [], leaf = 0;
    (function walk(n, depth, parent) {
      var item = { n: n, d: depth, x: cols.x[depth], hw: cols.hw[depth], y: 0 }, ys = [];
      list(n.children).forEach(function (c) { ys.push(walk(c, depth + 1, item).y); });
      item.y = ys.length ? (Math.min.apply(null, ys) + Math.max.apply(null, ys)) / 2 : y0 + (leaf++) * DY + NODE_H / 2;
      nodes.push(item); if (parent) edges.push([parent, item]);
      return item;
    })(root, 0, null);
    return { nodes: nodes, edges: edges, h: leaf * DY };
  }
  function health() {
    var h = {}, rank = { open: 3, ongoing: 2, resolved: 1 };
    incidents.forEach(function (i) {
      var st = status(i);
      if (!rank[st]) return;
      incidentNodes(i).forEach(function (m) { if (!h[m] || rank[st] > rank[h[m]]) h[m] = st; });
    });
    return h;
  }
  function offTree(shown) {
    if (!graph) return [];
    var rest = (domains.filter(function (d) { return d.id === dom; })[0] || { metrics: [] }).metrics;
    if (dom === 'all') rest = list(graph.nodes).filter(function (n) { return n.kind === 'metric'; }).map(function (n) { return n.id; });
    if (dom === 'other') rest = list(graph.nodes).filter(function (n) { return n.kind === 'metric' && !domOf[n.id]; }).map(function (n) { return n.id; });
    return rest.filter(function (m) { return !shown[m] && meta[m]; });
  }
  function drawNode(parent, metric, x, y, hw, extra) {
    var outcome = !!obj(meta[metric]).outcome;
    var gr = svg('g', { 'class': 'node' + (outcome ? ' root' : '') + (extra || ''), tabindex: 0, role: 'button', 'aria-label': 'Metric ' + str(metric), transform: 'translate(' + (x - hw) + ',' + (y - NODE_H / 2) + ')' }, parent);
    svg('rect', { 'class': 'nb', width: 2 * hw, height: NODE_H, rx: 9 }, gr);
    svg('circle', { 'class': 'hdot', cx: 15, cy: NODE_H / 2, r: 4 }, gr);
    var t = svg('text', { x: 27, y: NODE_H / 2, dy: '0.35em' }, gr); t.textContent = str(metric);
    var pick = function () { pickNode(metric); };
    gr.addEventListener('click', function (e) { if (!dragMoved) pick(); e.stopPropagation(); });
    gr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
    return gr;
  }
  function drawGraph() {
    $('graph-title').textContent = dom === 'all' ? 'Metric graph' : 'Metric graph: ' + domainLabel(dom);
    var E = $('l-edges'), N = $('l-nodes');
    E.textContent = ''; N.textContent = ''; $('l-trace').textContent = ''; $('l-tags').textContent = ''; $('l-pins').textContent = '';
    drawn = { edges: [], nodes: [], at: {} }; traceKey = '';
    var roots = list(graph && graph.trees).map(filterTree).filter(Boolean);
    var cols = columns(roots.length ? roots : [{ metric: 'x' }]), all = { nodes: [], edges: [] }, y = PAD, shown = {}, pos = {};
    roots.forEach(function (r) {
      var L = layout(r, y, cols);
      all.nodes = all.nodes.concat(L.nodes); all.edges = all.edges.concat(L.edges); y += L.h + 24;
    });
    all.edges.forEach(function (pair) {
      var a = pair[0], b = pair[1], x1 = a.x + a.hw, x2 = b.x - b.hw, c = (x2 - x1) / 2;
      var p = svg('path', { d: 'M' + x1 + ',' + a.y + ' C' + (x1 + c) + ',' + a.y + ' ' + (x2 - c) + ',' + b.y + ' ' + x2 + ',' + b.y, 'class': 'edge' }, E);
      drawn.edges.push({ el: p, from: a.n.metric, to: b.n.metric, depth: b.d });
    });
    all.nodes.forEach(function (i) {
      shown[i.n.metric] = true; if (!pos[i.n.metric] || (pos[i.n.metric].n.repeated && !i.n.repeated)) pos[i.n.metric] = i;
      var gr = drawNode(N, i.n.metric, i.x, i.y, i.hw, i.n.repeated ? ' repeated' : '');
      if (i.n.repeated) gr.setAttribute('aria-label', 'Metric ' + str(i.n.metric) + ', shown again');
      drawn.nodes.push({ el: gr, metric: i.n.metric, x: i.x, y: i.y, hw: i.hw, repeated: !!i.n.repeated });
    });
    var w = roots.length ? cols.w : PAD * 2;
    // metrics with no place on a formula tree: a cluster under the trees, part of the same canvas
    var rest = offTree(shown);
    if (rest.length) {
      if (roots.length) y += 24;
      var lab = svg('text', { 'class': 'cluster-label', x: PAD, y: y }, N);
      lab.textContent = roots.length ? (dom === 'all' ? 'Not on a formula tree' : 'Also in this domain, not on a formula tree') : 'Metrics in this domain (no formula tree)';
      y += 18;
      var cw = Math.max(150, Math.ceil(Math.max.apply(null, rest.map(function (m) { return m.length; })) * CHAR) + 40);
      var per = Math.max(1, Math.floor((Math.max(w, 900) - 2 * PAD + 16) / (cw + 16)));
      rest.forEach(function (m, k) {
        var cx = PAD + (k % per) * (cw + 16) + cw / 2, cy = y + Math.floor(k / per) * (NODE_H + 14) + NODE_H / 2;
        var gr = drawNode(N, m, cx, cy, cw / 2, ' off');
        if (!pos[m]) pos[m] = { x: cx, y: cy, hw: cw / 2 };
        drawn.nodes.push({ el: gr, metric: m, x: cx, y: cy, hw: cw / 2, off: true });
      });
      y += Math.ceil(rest.length / per) * (NODE_H + 14);
      w = Math.max(w, PAD * 2 + per * (cw + 16) - 16);
    }
    drawn.nodes.forEach(function (n) { if (!drawn.at[n.metric] || (drawn.at[n.metric].repeated && !n.repeated)) drawn.at[n.metric] = n; });
    // possible causes: dashed curves with the sign of the effect, when both ends are on the canvas
    list(graph && graph.edges).filter(function (e) { return e.kind === 'may_affect' && pos[e.source] && pos[e.target]; }).forEach(function (e) {
      var a = pos[e.source], b = pos[e.target], sx = a.x + a.hw, ex = b.x + b.hw, bend = Math.max(sx, ex) + 34 + Math.abs(a.y - b.y) * 0.06;
      var my = (a.y + b.y) / 2;
      var p = svg('path', { d: 'M' + sx + ',' + a.y + ' C' + bend + ',' + a.y + ' ' + bend + ',' + b.y + ' ' + ex + ',' + b.y, 'class': 'edge may' }, E);
      var sg = svg('g', { 'class': 'sign', transform: 'translate(' + (0.125 * sx + 0.75 * bend + 0.125 * ex) + ',' + my + ')' }, E);
      svg('circle', { r: 10 }, sg); var s = svg('text', { dy: '0.35em' }, sg); s.textContent = e.sign > 0 ? '+' : e.sign < 0 ? '\u2212' : '?';
      svg('title', {}, sg).textContent = e.source + ' may affect ' + e.target + (e.note ? ': ' + e.note : '');
      drawn.edges.push({ el: p, from: e.source, to: e.target, may: true });
    });
    drawn.world = { w: Math.max(w, 200), h: Math.max(y + PAD - 24, 160) };
    $('cv-empty').hidden = !!drawn.nodes.length;
    if (!drawn.nodes.length) $('cv-empty').textContent = 'No metric in this domain.';
    drawMini();
    applyStyles();
  }

  // ------------------------------------------------------------ pan and zoom
  var view = { x: 0, y: 0, k: 1 }, tween = null, dragMoved = false;
  function size() { var r = $('canvas').getBoundingClientRect(); return { w: r.width, h: r.height }; }
  function setView(v) {
    view = { x: v.x, y: v.y, k: Math.max(0.2, Math.min(3, v.k)) };
    $('vp').setAttribute('transform', 'translate(' + view.x.toFixed(1) + ',' + view.y.toFixed(1) + ') scale(' + view.k.toFixed(4) + ')');
    var c = $('canvas'), s = 22 * view.k;
    c.style.backgroundSize = s + 'px ' + s + 'px';
    c.style.backgroundPosition = view.x + 'px ' + view.y + 'px';
    $('z-level').textContent = Math.round(view.k * 100) + '%';
    miniView();
  }
  function animateTo(v, instant) {
    if (tween) cancelAnimationFrame(tween.raf);
    if (instant || still.matches || !window.requestAnimationFrame) { tween = null; setView(v); return; }
    var from = view, t0 = null, T = 620;
    tween = {};
    var step = function (now) {
      if (tween !== mine) return;      // a frame queued before this fly was replaced or finished must not move the view
      if (t0 == null) t0 = now;
      var t = Math.min(1, (now - t0) / T), e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      // zoom moves in log space, so the fly feels even
      var k = Math.exp(Math.log(from.k) + (Math.log(v.k) - Math.log(from.k)) * e);
      setView({ x: from.x + (v.x - from.x) * e, y: from.y + (v.y - from.y) * e, k: k });
      if (t < 1) { if (tween === mine) tween.raf = requestAnimationFrame(step); } else if (tween === mine) tween = null;
    };
    var mine = tween;
    tween.raf = requestAnimationFrame(step);
    // a hidden tab (or a headless capture) gets no frames: the view still ends where it should
    setTimeout(function () { if (tween === mine) { cancelAnimationFrame(mine.raf); tween = null; setView(v); } }, T + 250);
  }
  // the room on the canvas that nothing covers: the replay caption at the top, the tools at the bottom
  function room() {
    var s = size(), top = 56, bottom = 24 + 40 + ($('mini').hasAttribute('hidden') || replay ? 0 : 108);
    if (!$('replay').hidden) top = $('replay').offsetHeight + 28;
    // an unpinned report floats over the right of the canvas: frame the graph in what it leaves
    var right = !pinned && !$('sheet').hidden ? $('sheet').offsetWidth + 12 : 0;
    return { x: 24, y: top, w: Math.max(120, s.w - 48 - right), h: Math.max(120, s.h - top - bottom) };
  }
  // auto: how the view was last framed by the page itself (the incident's path, the trees, everything). While the viewer
  // has not moved the view since, a change of the canvas size (window, report pinned or closed) frames it again
  var auto = null;
  function frame(b, maxK, instant) {
    auto = null;
    var r = room(), k = Math.min(r.w / Math.max(1, b.w), r.h / Math.max(1, b.h), maxK);
    animateTo({ k: k, x: r.x + (r.w - b.w * k) / 2 - b.x * k, y: r.y + (r.h - b.h * k) / 2 - b.y * k }, instant);
  }
  function boxOf(metrics, pad) {
    var ns = metrics.map(function (m) { return drawn && drawn.at[m]; }).filter(Boolean);
    if (!ns.length) return null;
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    ns.forEach(function (n) { x0 = Math.min(x0, n.x - n.hw); x1 = Math.max(x1, n.x + n.hw); y0 = Math.min(y0, n.y - NODE_H / 2); y1 = Math.max(y1, n.y + NODE_H / 2); });
    pad = pad == null ? 60 : pad;
    return { x: x0 - pad, y: y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad };
  }
  function fit(animate) { if (drawn && drawn.world) { frame({ x: 0, y: 0, w: drawn.world.w, h: drawn.world.h }, 1.15, !animate); auto = function () { fit(false); }; } }
  // the first view: the formula trees, without the cluster of metrics off them (the Fit button shows everything)
  function fitTrees() {
    var t = drawn ? drawn.nodes.filter(function (n) { return !n.off; }).map(function (n) { return n.metric; }) : [];
    var b = t.length && boxOf(t, 24); if (b) { frame(b, 1, true); auto = fitTrees; } else fit(false);
  }
  function reset() { auto = null; animateTo({ x: 0, y: 0, k: 1 }); }
  function zoomBy(f, cx, cy) {
    var s = size(); if (cx == null) { cx = s.w / 2; cy = s.h / 2; }
    auto = null;
    var k = Math.max(0.2, Math.min(3, view.k * f)), r = k / view.k;
    setView({ k: k, x: cx - (cx - view.x) * r, y: cy - (cy - view.y) * r });
  }
  function visible(metrics) {
    var b = boxOf(metrics, 8), r = room(); if (!b) return true;
    var x0 = b.x * view.k + view.x, y0 = b.y * view.k + view.y, x1 = (b.x + b.w) * view.k + view.x, y1 = (b.y + b.h) * view.k + view.y;
    return x0 >= r.x && y0 >= r.y && x1 <= r.x + r.w && y1 <= r.y + r.h && view.k >= 0.6;
  }
  (function wire() {
    var c = $('canvas'), start = null;
    c.addEventListener('wheel', function (e) {
      if (e.target.closest && e.target.closest('.replay, .legend, .tools, .flash, .bench')) return;
      e.preventDefault();
      var r = c.getBoundingClientRect(), d = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      if (tween) { cancelAnimationFrame(tween.raf); tween = null; }
      zoomBy(Math.exp(-d * 0.0016), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    c.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 || (e.target.closest && e.target.closest('.replay, .legend, .tools, .flash, .bench, .replay-fab, .cv-title button'))) return;
      start = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, id: e.pointerId }; dragMoved = false;
    });
    window.addEventListener('pointermove', function (e) {
      if (!start || e.pointerId !== start.id) return;
      var dx = e.clientX - start.x, dy = e.clientY - start.y;
      if (!dragMoved && Math.abs(dx) + Math.abs(dy) < 4) return;
      if (!dragMoved) { dragMoved = true; auto = null; c.classList.add('panning'); if (tween) { cancelAnimationFrame(tween.raf); tween = null; } }
      setView({ x: start.vx + dx, y: start.vy + dy, k: view.k });
    });
    window.addEventListener('pointerup', function (e) {
      if (!start || e.pointerId !== start.id) return;
      var moved = dragMoved, onNode = e.target.closest && e.target.closest('.node');
      start = null; c.classList.remove('panning');
      setTimeout(function () { dragMoved = false; }, 0);
      // a click on the empty canvas closes an unpinned report
      if (!moved && !onNode && !pinned && !$('sheet').hidden && replay == null) closeSheet();
    });
    $('z-in').onclick = function () { zoomBy(1.25); };
    $('z-out').onclick = function () { zoomBy(0.8); };
    $('z-fit').onclick = function () { fit(true); };
    $('z-reset').onclick = reset;
    // the minimap: shown by default when the canvas is tall enough to spare it the room
    var mapPref = store('v3-map');
    var showMap = function (on) { if (on) $('mini').removeAttribute('hidden'); else $('mini').setAttribute('hidden', ''); $('z-map').setAttribute('aria-pressed', on ? 'true' : 'false'); miniView(); };
    showMap(mapPref == null ? size().h >= 620 : mapPref === '1');
    $('z-map').onclick = function () { var on = $('mini').hasAttribute('hidden'); store('v3-map', on ? '1' : '0'); showMap(on); };
    var mini = $('mini'), miniDrag = false;
    var miniGo = function (e) {
      if (!drawn || !drawn.world) return;
      var r = mini.getBoundingClientRect(), vb = mini.viewBox.baseVal;
      var sc = Math.max(vb.width / r.width, vb.height / r.height);
      var wx = vb.x + (e.clientX - r.left - (r.width - vb.width / sc) / 2) * sc, wy = vb.y + (e.clientY - r.top - (r.height - vb.height / sc) / 2) * sc;
      var s = size(); auto = null;
      setView({ k: view.k, x: s.w / 2 - wx * view.k, y: s.h / 2 - wy * view.k });
    };
    mini.addEventListener('pointerdown', function (e) { miniDrag = true; mini.setPointerCapture(e.pointerId); miniGo(e); });
    mini.addEventListener('pointermove', function (e) { if (miniDrag) miniGo(e); });
    mini.addEventListener('pointerup', function () { miniDrag = false; });
  })();
  function drawMini() {
    var g = $('mini-nodes'); g.textContent = '';
    if (!drawn || !drawn.world) return;
    $('mini').setAttribute('viewBox', '0 0 ' + drawn.world.w + ' ' + drawn.world.h);
    drawn.edges.forEach(function (e) { if (!e.may) { var p = e.el.cloneNode(false); p.setAttribute('class', 'mini-edge'); g.appendChild(p); } });
    drawn.nodes.forEach(function (n) { n.mini = svg('rect', { x: n.x - n.hw, y: n.y - NODE_H / 2, width: 2 * n.hw, height: NODE_H, rx: 8, 'class': 'mini-node' }, g); });
    miniView();
  }
  function miniView() {
    var r = $('mini-view'); if (!drawn || !drawn.world) return;
    var s = size();
    r.setAttribute('x', -view.x / view.k); r.setAttribute('y', -view.y / view.k);
    r.setAttribute('width', Math.max(1, s.w / view.k)); r.setAttribute('height', Math.max(1, s.h / view.k));
  }

  // ------------------------------------------------------------ state on the graph
  // Kids along the formula tree, from the drawn edges
  function kidsMap() { var k = {}; drawn.edges.forEach(function (e) { if (!e.may) (k[e.from] = k[e.from] || []).push(e.to); }); return k; }
  function pathBetween(from, target) {
    var kids = kidsMap();
    var walk = function (a, seen) {
      if (a === target) return [a];
      if (seen[a]) return null; seen[a] = true;
      var ks = kids[a] || [];
      for (var k = 0; k < ks.length; k++) { var r = walk(ks[k], seen); if (r) return [a].concat(r); }
      return null;
    };
    return walk(from, {}) || [];
  }
  function litSet(inc) {
    var on = {};
    if (!inc) return on;
    on[inc.metric] = true;
    list(inc.contributions).forEach(function (c) { if (!c || !c.node) return; on[c.node] = true; pathBetween(inc.metric, c.node).forEach(function (m) { on[m] = true; }); });
    list(inc.trail).forEach(function (t) { t = obj(t); if (t.action === 'watch' && t.target && meta[t.target]) on[t.target] = true; });
    return on;
  }
  // what the incident says about each metric: moved (with its change) or held (checked by the agent and normal)
  var FACTOR = /([a-z][a-z0-9_]*) (-?[\d.,]+)(?:\/h)? vs (-?[\d.,]+)(?:\/h)?, (normal|moved ([-+\u2212]?[\d.]+) ?%)/g;
  function nodeStates(inc) {
    var s = {};
    if (!inc) return s;
    var put = function (m, obs, exp, p, held, force) {
      m = str(m); if (!m || (s[m] && !force)) return;
      obs = num(obs); exp = num(exp); p = num(p);
      if (p == null && obs != null && exp) p = (obs - exp) / Math.abs(exp) * 100;
      if (p == null && !held) return;
      s[m] = { obs: obs, exp: exp, pct: p, held: !!held };
    };
    var ch = obj(inc.change);
    put(inc.metric, ch.observed, ch.expected, ch.delta_pct);
    list(inc.drivers).forEach(function (d) { d = obj(d); put(d.metric, d.observed, d.expected, d.delta_pct); });
    list(inc.contributions).forEach(function (c) { c = obj(c); put(c.node, c.observed, c.expected, null); });
    list(inc.trail).forEach(function (t) {
      t = obj(t); if (!/^factors of /.test(str(t.target))) return;
      var r = str(t.result), m; FACTOR.lastIndex = 0;
      while ((m = FACTOR.exec(r))) {
        var o = Number(m[2].replace(/,/g, '')), e = Number(m[3].replace(/,/g, ''));
        if (m[4] === 'normal') put(m[1], o, e, null, true);
        else put(m[1], o, e, Number(m[5].replace('\u2212', '-')));
      }
    });
    Object.keys(s).forEach(function (k) { if (s[k].held || (s[k].pct != null && Math.abs(s[k].pct) < 1)) s[k].held = true; });
    return s;
  }
  function badgeText(st) {
    if (st.held) return 'held';
    var a = Math.abs(st.pct);
    return (st.pct < 0 ? '\u25BC ' : '\u25B2 ') + (a >= 10 ? Math.round(a) : a.toFixed(1)) + '%';
  }
  var traceKey = '';
  function applyStyles() {
    if (!drawn) return;
    var h = health(), inc = mode === 'incident' ? selected() : null, on = litSet(inc), states = nodeStates(inc);
    var g = $('g'), lit = Object.keys(on).length > 0;
    g.classList.toggle('dim', lit);
    KNOWN.concat(['other']).forEach(function (k) { g.classList.remove('st-' + k); });
    if (lit) g.classList.add(stOf(inc));
    drawn.nodes.forEach(function (n) {
      var c = n.el.classList, st = states[n.metric];
      ['h-open', 'h-ongoing', 'h-resolved', 'mv', 'held', 'seen'].forEach(function (k) { c.remove(k); });
      if (h[n.metric]) c.add('h-' + h[n.metric]);
      c.toggle('on', !!on[n.metric]);
      if (st && on[n.metric] && !st.held) c.add('mv');
      if (st && !on[n.metric] && !st.held) c.add('seen');      // moved too, but off the incident's path
      if (st && st.held) c.add('held');
      c.toggle('picked', mode === 'node' && selNode === n.metric);
      c.toggle('context', dom !== 'all' && domainOf(n.metric) !== dom);
    });
    drawn.edges.forEach(function (e) { e.el.classList.toggle('on', !e.may && !!(on[e.from] && on[e.to])); });
    drawTags(inc, states, on);
    drawn.nodes.forEach(function (n) {
      if (!n.mini) return;
      n.mini.setAttribute('class', 'mini-node' + (on[n.metric] ? ' on' : h[n.metric] ? ' h-' + h[n.metric] : ''));
    });
    $('g').classList.toggle('mini-lit', lit);
    $('mini').setAttribute('class', 'mini' + (lit ? ' ' + stOf(inc) + ' lit' : ''));
    trace(lit ? inc : null);
    if (replay) paintReplay();
  }
  // the change on each metric of the selected incident: a badge hanging on the box's lower edge
  function drawTags(inc, states, on) {
    var L = $('l-tags'); L.textContent = '';
    if (!inc) return;
    Object.keys(states).forEach(function (m) {
      var n = drawn.at[m], st = states[m]; if (!n) return;
      var label = badgeText(st), w = Math.ceil(label.length * 7.3) + 14, H = 20;
      var cls = st.held ? 'held' : (toneIn(inc, m, st.pct) || 'flat') + (st.pct > 0 ? ' up' : ' down');
      var tg = svg('g', { 'class': 'tag ' + cls, transform: 'translate(' + (n.x + n.hw - w - 10) + ',' + (n.y + NODE_H / 2 - H / 2 + 1) + ')' }, L);
      svg('rect', { width: w, height: H, rx: 9 }, tg);
      var t = svg('text', { x: w / 2, y: H / 2, dy: '0.35em' }, tg); t.textContent = label;
      svg('title', {}, tg).textContent = m + (st.obs != null ? ': ' + short(st.obs) + ' against ' + short(st.exp) + ' expected' : '') + (st.held ? ' (checked, normal)' : '');
    });
  }
  // the path draws itself once, from the moved rate up to the outcome: the direction the investigation explains it
  function trace(inc) {
    var key = inc ? str(inc.id) + '|' + dom + '|' + graphKey.length : '';
    if (key === traceKey) return;
    traceKey = key;
    var layer = $('l-trace'); layer.textContent = '';
    var litE = drawn.edges.filter(function (e) { return e.el.classList.contains('on'); });
    if (!inc || still.matches || !litE.length) return;
    var depths = litE.map(function (e) { return e.depth; }).filter(function (d, k, a) { return a.indexOf(d) === k; }).sort(function (a, b) { return b - a; });
    litE.forEach(function (e) {
      var p = e.el.cloneNode(false); p.setAttribute('class', 'trace-line'); layer.appendChild(p);
      var len = p.getTotalLength();
      p.style.strokeDasharray = len + ' ' + len; p.style.strokeDashoffset = String(-len);
      p.style.animationDelay = (depths.indexOf(e.depth) * 140) + 'ms';
      p.style.setProperty('--len', String(-len));
    });
    setTimeout(function () { if (traceKey === key) layer.textContent = ''; }, depths.length * 140 + 900);
  }
  function flyToIncident(force, instant) {
    var inc = selected(); if (!inc || !drawn) return;
    var on = Object.keys(litSet(inc)).concat(Object.keys(nodeStates(inc)));
    var b = boxOf(on.filter(function (m) { return drawn.at[m]; }), 36);
    if (!b) return;
    if (!force && visible(on)) return;
    frame(b, 1.3, instant);
    auto = function () { if (mode === 'incident' && selected() && !replay) flyToIncident(true, true); };
  }

  // --------------------------------------------------------------- the dock
  var seen = null;
  function renderFeed() {
    var l = inScope();
    $('feed-title').textContent = 'Incidents' + (dom === 'all' ? '' : ' in ' + domainLabel(dom));
    var live = l.filter(function (i) { return LIVE.indexOf(status(i)) >= 0; }).length;
    $('feed-sub').textContent = l.length ? (live ? live + ' live' : 'None live') + ' \u00B7 ' + l.length + ' in all' : '';
    var f = $('feed'), keepScroll = f.scrollLeft; f.textContent = '';
    if (!l.length) f.appendChild(el('div', 'dock-empty', [dom === 'all' ? 'No incidents. The monitor is watching every hour of data.' : 'No incident touches this domain.']));
    var first = seen == null;
    l.forEach(function (i) {
      try {
        var ch = obj(i.change), st = status(i), d = obj(list(i.drivers)[0]);
        var quiet = st === 'explained' || st === 'pending';
        var moved = d.metric ? str(d.metric) : str(i.metric), dp = d.metric ? d.delta_pct : ch.delta_pct, eur = euroOf(i);
        var sev = str(i.severity);
        var top = [el('span', 'pill ' + st, [st])];
        if (!quiet && sev) {
          var meter = el('span', 'sev-meter ' + sev, [el('i'), el('i'), el('i')]);
          top.push(el('span', 'sev ' + sev, [meter, sev]));
        }
        var figs = [el('span', 'card-pct chg ' + toneIn(i, moved, dp), [pct(dp)])];
        if (eur != null) figs.push(el('span', 'card-eur chg ' + euroTone(i, eur), [(eur > 0 ? '+' : '') + short(eur), el('small', null, [' EUR'])]));
        var place = [el('span', null, [segText(i.segment)])];
        if (moved !== str(i.metric)) place.push(el('span', 'muted', [' \u00B7 ' + str(i.metric) + (num(ch.delta_pct) != null ? ' ' + pct(ch.delta_pct) : '')]));
        var foot = [el('span', 'mono muted card-id', [str(i.id)]), el('span', 'card-time mono', [el('span', 'muted', [dayOf(i.first_seen) + ' ']), hourOf(i.first_seen)])];
        if (i.recurrence_of) foot.unshift(el('span', 'again', ['back again']));
        if (quiet && obj(i.innocent).kind) foot.unshift(el('span', 'innocent', ['innocent: ' + str(obj(i.innocent).kind).replace(/_/g, ' ')]));
        var card = el('button', 'card-inc ' + stOf(i) + (quiet ? ' quiet' : '') + (i.id === selInc && mode === 'incident' ? ' sel' : ''), [
          el('span', 'card-top', top),
          el('span', 'card-metric mono', [moved]),
          el('span', 'card-figs', figs),
          el('span', 'card-place', place),
          el('span', 'card-foot', foot)]);
        card.title = 'First seen ' + when(i.first_seen) + ' UTC' + (i.recurrence_of ? '; back again, see ' + str(i.recurrence_of) : '');
        card.setAttribute('aria-pressed', i.id === selInc && mode === 'incident' ? 'true' : 'false');
        if (!first && !seen[i.id]) card.classList.add('arrive');
        if (!first) seen[i.id] = true;
        card.onclick = function () { pickIncident(i.id); };
        f.appendChild(card);
      } catch (err) { f.appendChild(el('div', 'dock-empty', ['An incident (' + str(i.id) + ') could not be shown: ' + err.message])); }
    });
    if (skipped) f.appendChild(el('div', 'dock-empty small', [skipped + ' file' + (skipped > 1 ? 's' : '') + ' in the incidents folder ignored (not an incident, or still being written).']));
    f.scrollLeft = keepScroll;
    var s = f.querySelector('.card-inc.sel');
    if (s && feedSel !== selInc) {
      feedSel = selInc;
      var fb = f.getBoundingClientRect(), rb = s.getBoundingClientRect();
      if (rb.left < fb.left) f.scrollLeft += rb.left - fb.left - 16; else if (rb.right > fb.right) f.scrollLeft += rb.right - fb.right + 16;
    }
  }
  var feedSel = null;

  // ---------------------------------------------------------- the report sheet
  var pinned = store('v3-pinned') == null ? window.innerWidth >= 1200 : store('v3-pinned') === '1';
  function setPinned(p) {
    pinned = p; store('v3-pinned', p ? '1' : '0');
    $('stage').classList.toggle('pinned', p);
    $('pin-btn').setAttribute('aria-pressed', p ? 'true' : 'false');
    $('pin-lbl').textContent = p ? 'Pinned' : 'Pin';
    $('pin-btn').title = p ? 'Pinned: the report keeps its own column. Click to let it float over the graph' : 'Floating over the graph. Click to give the report its own column';
  }
  function openSheet() {
    var was = $('sheet').hidden;
    $('sheet').hidden = false; $('stage').classList.add('sheet-open');
    if (was) { $('sheet').classList.remove('in'); void $('sheet').offsetWidth; $('sheet').classList.add('in'); miniView(); }
  }
  function closeSheet() {
    stopReplay();
    $('sheet').hidden = true; $('stage').classList.remove('sheet-open');
    if (mode === 'node') { selNode = null; mode = 'incident'; applyStyles(); }
    miniView();
  }
  $('pin-btn').onclick = function () { setPinned(!pinned); };
  $('sheet-close').onclick = closeSheet;
  setPinned(pinned);

  function pickIncident(id) {
    var changed = selInc !== id || mode !== 'incident';
    selInc = id; mode = 'incident'; selNode = null;
    if (replay && replay.id !== id) stopReplay();
    openSheet(); renderFeed(); renderDetail(); applyStyles();
    if (changed) { flown = id; setTimeout(function () { flyToIncident(true); }, 30); }
  }
  function pickNode(m) {
    stopReplay();
    selNode = m; mode = 'node'; openSheet(); renderFeed(); renderDetail(); applyStyles();
  }
  function section(title, kids, cls) { var d = el('section', 'sec' + (cls ? ' ' + cls : ''), [el('h3', null, [title])]); kids.forEach(function (k) { d.appendChild(k); }); return d; }
  function kv(pairs, cls) { var k = el('div', 'kv' + (cls ? ' ' + cls : '')); pairs.forEach(function (p) { k.appendChild(el('span', null, [p[0]])); k.appendChild(el('span', null, [p[1]])); }); return k; }
  function mono(s) { return el('span', 'mono', [str(s)]); }
  function linksOf(m) {
    var out = [], E = list(graph && graph.edges);
    var by = function (kind) { return E.filter(function (e) { return e.kind === kind; }); };
    var dims = by('breakdown').filter(function (e) { return e.source === m; }).map(function (e) { return e.dimension; });
    if (dims.length) out.push(['Breaks down by', dims.join(', ')]);
    var parts = by('formula').filter(function (e) { return e.source === m; }).map(function (e) { return e.target + (e.role ? ' (' + e.role + ')' : ''); });
    if (parts.length) out.push(['Made of', parts.join(', ')]);
    var feeds = by('formula').filter(function (e) { return e.target === m; }).map(function (e) { return e.source; });
    if (feeds.length) out.push(['Feeds', feeds.join(', ')]);
    var defs = by('definition').filter(function (e) { return e.source === m; }).map(function (e) { return e.target + (e.role ? ' (' + e.role + ')' : ''); });
    if (defs.length) out.push(['Defined from', defs.join(', ')]);
    var ctx = by('context').filter(function (e) { return e.target === m; }).map(function (e) { return e.source + (e.via ? ' (' + e.via + ')' : ''); });
    if (ctx.length) out.push(['Context', ctx.join(', ')]);
    var cause = function (e, other) { return e[other] + ' ' + (e.sign > 0 ? '+' : e.sign < 0 ? '\u2212' : '?') + (num(e.prior) != null ? ', prior ' + e.prior : '') + (e.status ? ', ' + e.status : '') + (e.note ? ': ' + e.note : ''); };
    var affects = by('may_affect').filter(function (e) { return e.source === m; }).map(function (e) { return cause(e, 'target'); });
    if (affects.length) out.push(['May affect', affects.join('; ')]);
    var affected = by('may_affect').filter(function (e) { return e.target === m; }).map(function (e) { return cause(e, 'source'); });
    if (affected.length) out.push(['May be affected by', affected.join('; ')]);
    return out;
  }
  var answers = {};
  function checksSection(a) {
    var rows = list(a.checks).map(function (c) {
      c = obj(c); var s = str(c.status);
      return el('div', 'hyp check', [el('span', 'verdict ' + s, [s || '-']),
        el('div', null, [el('div', 'hyp-cause', [str(c.check)]), el('div', 'small muted', [str(c.detail)])])]);
    });
    return section('Checks of the extension agent', [el('p', 'about', [str(a.message)])].concat(rows.length ? rows : [el('div', 'empty', ['The answer lists no checks.'])]));
  }
  function loadAnswer(name) {
    return get('/api/metric/' + encodeURIComponent(name)).then(function (r) {
      answers[name] = r; if (mode === 'node' && selNode === name) renderDetail(); return r;
    });
  }
  // the bench would cover the report's head: fold it so the metric and its checks are in view
  function showChecks(name) { toggleBench(false); $('flash').hidden = true; pickNode(name); if (drawn && drawn.at[name]) frame(boxOf([name], 120), 1.2); }
  function answerToast(res) {
    res = obj(res); var name = str(res.name), parts = [str(res.message) || ('Monitor: ' + str(res.status))];
    if (name) answers[name] = res;
    var link = null;
    if (name && list(res.checks).length) { link = el('button', 'linkish', ['Show the checks']); link.onclick = function () { showChecks(name); }; }
    toast(parts[0], link, 12000);
  }
  function renderNode(d) {
    var m = meta[selNode], a = answers[selNode];
    var t = $('detail-title'); t.textContent = '';
    t.appendChild(el('span', 'kicker', ['Metric'])); t.appendChild(el('span', 'head-id mono', [str(selNode)]));
    if (!m) {
      if (a) { $('detail-status').appendChild(el('span', 'pill ' + str(a.status), [str(a.status)])); d.appendChild(checksSection(a)); }
      else { d.appendChild(el('div', 'empty', ['This metric is not in the graph.'])); loadAnswer(selNode).catch(function () {}); }
      return;
    }
    $('detail-status').appendChild(el('span', 'pill ' + (m.status === 'validated' ? 'resolved' : 'pending'), [str(m.status || 'no status')]));
    var pairs = [['Domain', domainLabel(domainOf(selNode))], ['Status', m.status || '-'], ['Origin', m.origin || '-'], ['Unit', m.unit || '-'], ['Direction', m.direction ? 'higher is ' + (m.direction === 'up' ? 'better' : 'worse') : '-']];
    if (m.formula) pairs.push(['Formula', mono(m.formula)]);
    if (m.decomposition) pairs.push(['Made of', mono(m.decomposition)]);
    if (m.column) pairs.push(['Column', mono(m.column)]);
    if (list(m.systems).length) pairs.push(['Systems', m.systems.join(', ')]);
    if (m.source) pairs.push(['Data source', m.source]);
    pairs.push(['Dimensions', list(m.dimensions).join(', ') || '-']);
    d.appendChild(section('Definition', [el('p', 'about lead', [m.description || 'No description.']), kv(pairs)]));
    var links = linksOf(selNode);
    d.appendChild(section('Links by kind', links.length ? [kv(links)] : [el('div', 'empty', ['No links recorded for this metric.'])]));
    if (a) d.appendChild(checksSection(a));
    else if (m.origin === 'contributed') loadAnswer(selNode).catch(function () {});
    var inc = incidents.filter(function (i) { return incidentNodes(i).indexOf(selNode) >= 0; });
    var box = inc.length ? inc.map(function (i) {
      var b = el('button', 'involved ' + stOf(i), [el('span', 'pill ' + status(i), [status(i)]), el('span', 'mono', [str(i.id)]), el('span', 'mono muted', [str(i.metric)])]);
      b.onclick = function () { pickIncident(i.id); }; return b;
    }) : [el('div', 'empty', ['No incident involves this metric.'])];
    d.appendChild(section('Incidents involving it', box));
  }
  function cell(label, value, cls, unit) {
    return el('div', 'cell', [el('span', 'label', [label]), el('span', 'big ' + (cls || ''), [value].concat(unit ? [el('small', 'muted', [' ' + unit])] : []))]);
  }
  function renderIncident(d, i) {
    var t = $('detail-title'); t.textContent = '';
    var dr = obj(list(i.drivers)[0]), moved = dr.metric ? str(dr.metric) : str(i.metric);
    t.appendChild(el('span', 'kicker', ['Report ', el('span', 'mono', [str(i.id)])]));
    t.appendChild(el('span', 'head-id mono', [moved]));
    var ds = $('detail-status');
    ds.appendChild(el('span', 'pill ' + status(i), [status(i)]));
    if (i.severity && status(i) !== 'explained') ds.appendChild(el('span', 'sev ' + str(i.severity), [el('span', 'sev-meter ' + str(i.severity), [el('i'), el('i'), el('i')]), str(i.severity) + ' severity']));
    d.className = 'sheet-body ' + stOf(i);
    var ch = obj(i.change), w = obj(ch.window), imp = obj(i.impact);
    var where = el('p', 'where', [el('span', null, [segText(i.segment)]), el('span', 'muted', [' \u00B7 measured on ']), mono(i.metric), el('span', 'muted', [' \u00B7 first seen ' + when(i.first_seen) + ' UTC'])]);
    d.appendChild(where);
    if (list(i.trail).length) {
      var rb = el('button', 'replay-btn', [svgIcon(), 'Replay the investigation', el('span', 'muted', [' \u00B7 ' + list(i.trail).length + ' steps'])]);
      rb.onclick = function () { startReplay(0); };
      d.appendChild(rb);
    }
    if (obj(i.innocent).reason) d.appendChild(section('Why no alert', [el('p', 'summary', [str(obj(i.innocent).reason)])], 'why'));
    if (i.recurrence_of) {
      var back = el('button', 'linkish back', ['It is back: same metric and place as ' + str(i.recurrence_of) + '. Open ' + str(i.recurrence_of)]);
      back.addEventListener('click', function () { pickIncident(str(i.recurrence_of)); });
      d.appendChild(back);
    }
    var effect = euroOf(i);
    d.appendChild([ch.observed, ch.expected, ch.delta_pct, effect].some(function (v) { return num(v) != null; }) ? el('div', 'readout', [
      cell('Observed', short(ch.observed)), cell('Expected', short(ch.expected)),
      cell('Change', pct(ch.delta_pct), 'chg ' + toneIn(i, i.metric, ch.delta_pct)),
      cell('Effect, EUR', effect == null ? '\u2013' : (effect > 0 ? '+' : '') + short(effect), 'chg ' + euroTone(i, effect))])
      : el('p', 'muted', [status(i) === 'pending' ? 'No figures yet: the data for these hours has not arrived.' : 'No figures in this report.']));
    var story = obj(i.narrative), told = story.source === 'model' && !!str(story.text).trim();
    if (told) {
      var brief = [];
      var asOf = Date.parse(str(story.as_of)), seenAt = Date.parse(str(i.last_seen));
      if (isFinite(asOf) && isFinite(seenAt) && asOf < seenAt)
        brief.push(el('p', 'brief-stale', ['written at ' + ('0' + new Date(asOf).getUTCHours()).slice(-2) + ':00 UTC; the figures above are newer']));
      brief.push(el('p', 'brief', [str(story.text)]),
        el('p', 'brief-note', ['Written by ' + (str(story.model) || 'a language model') + ' from this report' + (story.as_of ? ' at ' + when(story.as_of) + ' UTC' : '')
          + '; every number in it was checked against the evidence.']));
      d.appendChild(section('In brief', brief, 'brief-sec'));
    }
    if (i.report) d.appendChild(section('Summary', [el('p', 'summary', [str(i.report)])]));
    var rows = [['Metric', mono(i.metric)], ['Segment', segText(i.segment)],
                ['Change', num(ch.delta_pct) != null ? pct(ch.delta_pct) + (num(ch.z) != null ? ' (z ' + fmt(ch.z) + ')' : '') : '-']];
    if (num(ch.delta) != null) rows.push(['Difference', fmt(ch.delta)]);
    if (w.start || w.end) rows.push(['Window', when(w.start) + ' to ' + when(w.end) + ' UTC']);
    d.appendChild(section('What changed', [kv(rows)]));
    var ik = Object.keys(imp).map(function (k) { var v = Number(imp[k]); return [k, fmt(v, Number.isInteger(v) ? 0 : 2)]; });
    var dec = function (s) { var p = s.indexOf('.'); return p < 0 ? 0 : s.length - p; }, D = Math.max.apply(null, ik.map(function (p) { return dec(p[1]); }).concat(0));
    if (ik.length) d.appendChild(section('Business impact', [kv(ik.map(function (p) { return [mono(p[0]), mono(p[1] + ' '.repeat(D - dec(p[1])))]; }), 'nums')]));
    var cs = list(i.contributions).map(function (c) {
      c = obj(c);
      var share = (num(c.share_of_delta) || 0) * 100;
      var bar = el('div', 'share-bar'), b = el('i'); b.style.width = Math.max(0, Math.min(100, share)) + '%'; bar.appendChild(b);
      var nb = el('button', 'linkish mono', [str(c.node)]); nb.onclick = function () { if (drawn && drawn.at[c.node]) frame(boxOf([c.node], 140), 1.3); };
      return el('div', 'contrib', [el('div', 'row2', [nb, el('span', null, [fmt(share, 0) + '% of the change'])]), bar,
        el('div', 'small muted mono', [short(c.observed) + ' against ' + short(c.expected) + ' expected'])]);
    });
    if (cs.length) d.appendChild(section('Where along the graph', cs));
    var loc = list(i.localization).map(function (x) {
      x = obj(x);
      return el('div', 'loc' + (x.chosen ? ' chosen' : ''), [
        el('span', 'seg', [mono(str(x.dimension) + ' = ' + str(x.segment))].concat(x.chosen ? [el('span', 'chosen-tag', ['chosen'])] : [])),
        el('span', 'mono', [fmt((num(x.share_of_delta) || 0) * 100, 0) + '%']),
        el('span', 'mono muted', [num(x.surprise) != null ? fmt(x.surprise, 2) : ''])]);
    });
    if (loc.length) d.appendChild(section('Where in the segments', [el('div', 'loc head', [el('span', null, ['Place']), el('span', null, ['Of change']), el('span', null, ['Surprise'])])].concat(loc)));
    var hs = list(i.hypotheses).map(function (h) {
      h = obj(h);
      var about = (h.kind ? str(h.kind) : '') + (num(h.score) != null ? (h.kind ? ' \u00B7 ' : '') + 'score ' + fmt(h.score, 2) : '');
      return el('div', 'hyp ' + str(h.status), [el('span', 'verdict ' + str(h.status), [str(h.status)]),
        el('div', null, [el('div', 'hyp-cause', [str(h.cause)])].concat(about ? [el('div', 'hyp-meta', [about])] : [],
          [el('ul', 'evidence', list(h.evidence).map(function (e) { return el('li', null, [str(e)]); }))]))]);
    });
    if (hs.length) d.appendChild(section('Hypotheses', hs));
    var tr = list(i.trail).map(function (s, k) {
      s = obj(s);
      var li = el('li', replay && replay.id === i.id && replay.i === k ? 'cur' : (replay && replay.id === i.id && k < replay.i ? 'done' : null), [
        el('button', 'step', [el('span', 'step-n mono', [String(k + 1)]),
          el('span', 'step-body', [el('span', 'step-head', [el('span', 'verb', [verbOf(s.action)]), el('span', 'step-target', [str(s.target)])]), el('span', 'step-result', [str(s.result)])])])]);
      li.firstChild.onclick = function () { startReplay(k); };
      li.firstChild.title = 'Replay from this step';
      return li;
    });
    if (tr.length) d.appendChild(section('Investigation trail', [el('p', 'small muted', ['Each step is what the agent did and what it found. Click one to see it on the graph.']), el('ol', 'trail', tr)], 'trail-sec'));
    if (list(i.uncertain).length) d.appendChild(section('Still uncertain', [el('ul', 'uncertain', i.uncertain.map(function (u) { return el('li', null, [str(u)]); }))]));
  }
  function svgIcon() { var s = svg('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' }); svg('path', { d: 'M4 2.5v11l9-5.5z', fill: 'currentColor' }, s); return s; }
  function renderDetail() {
    var d = $('detail'), keep = d.scrollTop, same = d.dataset.key === (mode + '|' + (mode === 'node' ? selNode : selInc));
    d.textContent = ''; d.className = 'sheet-body'; $('detail-status').textContent = '';
    try {
      if (mode === 'node' && selNode) renderNode(d);
      else {
        var i = selected();
        if (!i) { $('detail-title').textContent = 'Details'; d.appendChild(el('div', 'empty', ['Pick an incident in the dock below, or a metric on the graph.'])); }
        else renderIncident(d, i);
      }
    } catch (err) { d.textContent = ''; d.appendChild(el('div', 'empty', ['This could not be shown: ' + err.message])); }
    d.dataset.key = mode + '|' + (mode === 'node' ? selNode : selInc);
    if (same) d.scrollTop = keep; else d.scrollTop = 0;
    $('replay-fab').hidden = !(mode === 'incident' && selected() && list(selected().trail).length) || !!replay;
  }
  function renderAll() { renderChips(); drawGraph(); renderFeed(); renderDetail(); }

  // ---------------------------------------------- replay the investigation
  var VERBS = { watch: 'Watch', compare: 'Compare', drill: 'Drill into places', drill_down: 'Drill into places', decompose: 'Split along the formula',
    check: 'Check', check_context: 'Check context', follow: 'Follow', stop: 'Stop', detect: 'Detect' };
  function verbOf(a) { a = str(a); return VERBS[a] || a.replace(/_/g, ' '); }
  var replay = null, playTimer = null;
  function metricsIn(s) { var seenM = {}; return str(s).split(/[^A-Za-z0-9_]+/).filter(function (w) { if (!meta[w] || seenM[w]) return false; seenM[w] = 1; return true; }); }
  function stepsOf(inc) {
    var on = Object.keys(litSet(inc)), kids = kidsMap(), watched = [], states = nodeStates(inc);
    var movedN = Object.keys(states).filter(function (m) { return !states[m].held; });
    var contribs = list(inc.contributions).map(function (c) { return str(obj(c).node); }).filter(Boolean);
    return list(inc.trail).map(function (t) {
      t = obj(t);
      var a = str(t.action), tg = str(t.target), named = metricsIn(tg), nodes = [], ctx = '', reveal = false;
      if (a === 'watch' || a === 'follow' || a === 'drill' || a === 'drill_down' || a === 'detect') { nodes = named; if (a === 'watch') watched = watched.concat(named); }
      else if (a === 'compare') nodes = /explanation|hypothes/.test(tg) ? movedN : (watched.length ? watched : named.length ? named : movedN);
      else if (a === 'decompose') {
        var p = {}; (named.length ? named : [inc.metric]).forEach(function (m) { p[m] = 1; contribs.forEach(function (c) { pathBetween(m, c).forEach(function (x) { p[x] = 1; }); }); });
        nodes = Object.keys(p);
      } else if (a === 'check' || a === 'check_context') {
        var f = /^factors of ([A-Za-z0-9_]+)/.exec(tg);
        if (f) { nodes = [f[1]].concat(metricsIn(t.result), kids[f[1]] || []); reveal = true; }
        else {
          var linked = list(graph && graph.edges).filter(function (e) { return e.kind === 'context' && e.source === tg; }).map(function (e) { return e.target; })
            .filter(function (m) { return drawn.at[m] && (on.indexOf(m) >= 0 || states[m]); });
          nodes = linked.length ? linked : (named.length ? named : movedN);
          ctx = /^release:/.test(tg) ? 'release' : /fresh/.test(tg) ? 'freshness' : /calendar/.test(tg) ? 'calendar' : 'context';
        }
      } else if (a === 'stop') nodes = on;
      else nodes = named;
      nodes = nodes.filter(function (m, k, l) { return drawn.at[m] && l.indexOf(m) === k; });
      return { action: a, target: tg, result: str(t.result), nodes: nodes, ctx: ctx, reveal: reveal, pin: a !== 'stop' && nodes.length <= 3 };
    });
  }
  function startReplay(k) {
    var inc = selected(); if (!inc || !drawn) return;
    if (!replay || replay.id !== inc.id) replay = { id: inc.id, steps: stepsOf(inc), i: 0 };
    if (!replay.steps.length) { replay = null; return; }
    $('replay').hidden = false; $('replay-fab').hidden = true; $('app').classList.add('replaying');
    goStep(k || 0);
  }
  function stopReplay() {
    if (!replay) return;
    replay = null; pause();
    $('replay').hidden = true; $('app').classList.remove('replaying');
    $('g').classList.remove('replay-on');
    $('l-pins').textContent = '';
    drawn && drawn.nodes.forEach(function (n) { n.el.classList.remove('rp-cur', 'rp-done', 'rp-reveal'); });
    drawn && drawn.edges.forEach(function (e) { e.el.classList.remove('rp-cur'); });
    renderDetail();
    $('replay-fab').hidden = !(mode === 'incident' && selected() && list(selected().trail).length);
  }
  function goStep(k) {
    if (!replay) return;
    replay.i = Math.max(0, Math.min(replay.steps.length - 1, k));
    var s = replay.steps[replay.i];
    $('rp-count').textContent = 'Step ' + (replay.i + 1) + ' of ' + replay.steps.length;
    $('rp-verb').textContent = verbOf(s.action);
    $('rp-verb').className = 'rp-verb v-' + s.action;
    $('rp-target').textContent = s.ctx ? s.target.replace(/^release:/, 'release ') : s.target;
    $('rp-result').textContent = s.result || 'No result recorded.';
    $('rp-prev').disabled = replay.i === 0;
    $('rp-next').disabled = replay.i === replay.steps.length - 1;
    var rail = $('rp-rail'); rail.textContent = '';
    replay.steps.forEach(function (x, j) {
      var b = el('button', 'rp-dot' + (j === replay.i ? ' cur' : j < replay.i ? ' done' : ''), []);
      b.setAttribute('aria-label', 'Step ' + (j + 1) + ': ' + verbOf(x.action) + ' ' + x.target);
      b.title = (j + 1) + '. ' + verbOf(x.action) + ' ' + x.target;
      b.onclick = function () { pause(); goStep(j); };
      rail.appendChild(b);
    });
    paintReplay();
    renderDetail();
    var cur = $('detail').querySelector('.trail li.cur');
    if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest', behavior: still.matches ? 'auto' : 'smooth' });
    if (s.nodes.length && !visible(s.nodes)) frame(boxOf(s.nodes, s.nodes.length > 2 ? 50 : 90), 1.25);
  }
  function paintReplay() {
    if (!replay || !drawn) return;
    var s = replay.steps[replay.i], cur = {}, done = {};
    s.nodes.forEach(function (m) { cur[m] = 1; });
    replay.steps.slice(0, replay.i).forEach(function (x) { x.nodes.forEach(function (m) { done[m] = 1; }); });
    $('g').classList.add('replay-on');
    drawn.nodes.forEach(function (n) {
      n.el.classList.toggle('rp-cur', !!cur[n.metric]);
      n.el.classList.toggle('rp-done', !cur[n.metric] && !!done[n.metric]);
    });
    drawn.edges.forEach(function (e) { e.el.classList.toggle('rp-cur', !e.may && !!(cur[e.from] && cur[e.to])); });
    // the tags (moved, held) appear as the replay reaches the step that found them
    var known = {};
    replay.steps.slice(0, replay.i + 1).forEach(function (x) { x.nodes.forEach(function (m) { known[m] = 1; }); });
    Array.prototype.forEach.call($('l-tags').childNodes, function (t) {
      var m = (t.querySelector('title') || {}).textContent || ''; m = m.split(':')[0].split(' (')[0];
      t.classList.toggle('rp-hide', !known[m]);
    });
    // numbered pins: the steps that touched each metric so far
    var P = $('l-pins'); P.textContent = '';
    var at = {};
    replay.steps.slice(0, replay.i + 1).forEach(function (x, j) { if (x.pin) x.nodes.forEach(function (m) { (at[m] = at[m] || []).push(j + 1); }); });
    Object.keys(at).forEach(function (m) {
      var n = drawn.at[m]; if (!n) return;
      var ns = at[m], show = ns.length > 4 ? ns.slice(0, 3).concat(['+' + (ns.length - 3)]) : ns;
      show.forEach(function (v, j) {
        var isCur = v === replay.i + 1;
        var g = svg('g', { 'class': 'pin' + (isCur ? ' cur' : ''), transform: 'translate(' + (n.x - n.hw + 12 + j * 22) + ',' + (n.y - NODE_H / 2 - 2) + ')' }, P);
        svg('circle', { r: 10 }, g); var t = svg('text', { dy: '0.35em' }, g); t.textContent = String(v);
      });
    });
    if (s.ctx) {
      s.nodes.forEach(function (m) {
        var n = drawn.at[m]; if (!n) return;
        var label = s.ctx === 'release' ? s.target.replace(/^release:/, 'release ') : s.ctx, w = Math.ceil(label.length * 6.6) + 18;
        var g = svg('g', { 'class': 'ctx-flag', transform: 'translate(' + (n.x + n.hw + 12) + ',' + (n.y - 10) + ')' }, P);
        svg('rect', { width: w, height: 20, rx: 4 }, g); var t = svg('text', { x: 9, y: 10, dy: '0.35em' }, g); t.textContent = label;
      });
    }
  }
  function pause() { if (playTimer) { clearInterval(playTimer); playTimer = null; } $('rp-play').textContent = 'Play'; $('rp-play').setAttribute('aria-pressed', 'false'); }
  function play() {
    if (!replay) return;
    if (replay.i >= replay.steps.length - 1) goStep(0);
    $('rp-play').textContent = 'Pause'; $('rp-play').setAttribute('aria-pressed', 'true');
    playTimer = setInterval(function () { if (!replay || replay.i >= replay.steps.length - 1) { pause(); return; } goStep(replay.i + 1); }, 3800);
  }
  $('rp-prev').onclick = function () { pause(); goStep(replay.i - 1); };
  $('rp-next').onclick = function () { pause(); goStep(replay.i + 1); };
  $('rp-play').onclick = function () { if (playTimer) pause(); else play(); };
  $('rp-close').onclick = stopReplay;
  $('replay-fab').onclick = function () { startReplay(0); };
  document.addEventListener('keydown', function (e) {
    var tag = (e.target && e.target.tagName) || '';
    if (/INPUT|SELECT|TEXTAREA/.test(tag)) return;
    if (replay && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) { e.preventDefault(); pause(); goStep(replay.i + (e.key === 'ArrowRight' ? 1 : -1)); return; }
    if (e.key === 'Escape') {
      if (!$('bench').hidden) { toggleBench(false); return; }
      if (replay) { stopReplay(); return; }
      if (!$('sheet').hidden && !pinned) closeSheet();
    }
  });

  // ----------------------------------------------------------- loading
  function takeGraph(g) {
    graph = g; meta = {}; domOf = {}; answers = {};
    list(g.nodes).forEach(function (n) { if (n.kind === 'metric') meta[n.id] = n; });
    domains.forEach(function (d) { d.metrics.forEach(function (m) { domOf[m] = d.id; }); });
  }
  var wallMode = false, clockFirst = null, accelerated = false, HOUR_MS = 3600000;
  function clockMode(s) {
    if (s.clock_mode === 'wall') { clockFirst = null; accelerated = false; return 'Live'; }
    var hour = Date.parse(str(s.last_hour)), now = Date.parse(str(s.server_now));
    if (isFinite(hour) && isFinite(now)) {
      if (hour > now) accelerated = true;
      if (!clockFirst) clockFirst = { hour: hour, now: now };
      else if (hour - clockFirst.hour > now - clockFirst.now + HOUR_MS) accelerated = true;
    }
    return accelerated ? 'Simulated, accelerated' : 'Replay';
  }
  function displayHour(ts) {
    return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Berlin', day: '2-digit', month: '2-digit',
      year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ts)) + ' Europe/Berlin';
  }
  function clockParts(ts) {
    var p = {};
    new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Berlin', weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ts)).forEach(function (x) { p[x.type] = x.value; });
    return { day: p.weekday + ' ' + p.day + ' ' + p.month + ' ' + p.year, hour: p.hour + ':' + p.minute };
  }
  var firstDraw = true, incKey = '';
  function refresh() {
    get('/api/state').then(function (s) {
      wallMode = s.clock_mode === 'wall';
      var c = $('clock'), parts = s.ready ? clockParts(s.last_hour) : null;
      var label = s.ready ? clockMode(s) : (wallMode ? 'Live' : 'Simulated');
      var cm = $('clock-mode');
      cm.textContent = label;
      cm.className = 'clock-mode' + (wallMode ? ' live' : label === 'Simulated, accelerated' ? ' fast' : '');
      cm.title = wallMode ? 'Current hour: one data hour per real hour'
        : label === 'Replay' ? 'A finished database of simulated data, read hour by hour'
        : 'Simulated data: the simulator writes an hour of data every few seconds, faster than real time';
      c.textContent = '';
      if (parts) { c.appendChild(el('span', 'clock-date', [parts.day])); c.appendChild(el('span', 'clock-hour', [parts.hour])); } else c.textContent = str(s.error);
      $('clock-tz').textContent = s.ready ? 'Europe/Berlin' : '';
      fillEnd(list(s.injected).filter(function (i) { return i.active; }));
    }).catch(function (e) { $('clock').textContent = 'Clock unavailable: ' + e.message; });
    get('/api/graph').then(function (g) {
      var k = JSON.stringify(g);
      if (k !== graphKey) {
        graphKey = k; takeGraph(g);
        try { var r = replay ? replay.i : null; stopReplay(); renderAll(); if (firstDraw) { firstDraw = false; fitTrees(); } ensureFlown(); if (r != null) startReplay(r); }
        catch (e) { note('Could not draw the graph: ' + e.message); }
      }
    }).catch(function () {});
    get('/api/incidents').then(function (r) {
      var before = incKey;
      incidents = list(r.incidents); skipped = r.skipped || 0;
      var flipped = example && !r.example; example = !!r.example;
      $('exbadge').textContent = '';
      if (example) $('exbadge').appendChild(el('span', 'example', ['Showing the contract example: no live incident yet']));
      var scope = inScope(), lost = !incidents.some(function (i) { return i.id === selInc; });
      if (flipped || lost) {
        var had = selInc;
        selInc = scope.length ? scope[0].id : null; if (mode === 'incident' || flipped) mode = 'incident';
        if (had !== selInc) { stopReplay(); if (selInc && !firstDraw) openSheet(); }
      }
      if (selInc && mode === 'incident' && $('sheet').hidden && pinned && !sheetClosedByHand) openSheet();
      note('');
      incKey = JSON.stringify([incidents, skipped]);
      if (seen == null) { seen = {}; incidents.forEach(function (i) { seen[i.id] = true; }); }      // the first read: no card slides in
      if (before !== incKey || flipped) {
        try {
          if (replay) { var inc = selected(); if (inc) { var i0 = replay.i; replay.steps = stepsOf(inc); replay.i = Math.min(i0, replay.steps.length - 1); } }
          renderChips(); renderFeed(); renderDetail(); applyStyles();
        } catch (e) { note('Could not draw the incidents: ' + e.message); }
      }
      ensureFlown();
    }).catch(function () { note('Could not read the incidents.'); });
  }
  var sheetClosedByHand = false, flown = null;
  // the view flies to the selected incident's path once per selection, as soon as both the graph and the incident are in
  function ensureFlown() {
    if (!drawn || firstDraw || mode !== 'incident' || !selInc || flown === selInc || !selected()) return;
    // the first framing after the page loads is a cut, not a flight: the page opens on the incident's path
    var first = flown == null;
    flown = selInc; setTimeout(function () { flyToIncident(true, first); }, 80);
    // ?replay=3 opens the replay at that step (for a demo or a screenshot)
    if (askedStep != null) { var k = askedStep; askedStep = null; setTimeout(function () { startReplay(k); }, 900); }
  }
  var askedStep = /^\d+$/.test(new URLSearchParams(location.search).get('replay') || '') ? Math.max(0, parseInt(new URLSearchParams(location.search).get('replay'), 10) - 1) : null;
  $('sheet-close').addEventListener('click', function () { sheetClosedByHand = true; });
  $('feed').addEventListener('click', function () { sheetClosedByHand = false; });

  // ------------------------------------------------------------- test bench
  var KINDS = [
    ['payment_failure', 'Payment failure', ['country', 'platform'], 72, 0.4],
    ['stockout_surge', 'Stockouts', ['category'], 72, 0.4],
    ['search_regression', 'Search regression', ['country'], 72, 0.4],
    ['ad_budget_exhausted', 'Ad budget gone early', ['country'], 12, 0.4],
    ['traffic_drop', 'Traffic drop', ['country', 'platform'], 72, 0.5],
    ['feed_unavailable', 'Feed shows unavailable', [], 72, null],
    ['discount_push', 'Discount push', [], 72, null]];
  function faultBody(kind, value) {
    var k = KINDS.filter(function (x) { return x[0] === kind; })[0], hours = k[3];
    var body = { kind: kind, hours: hours, when: 'now' };
    if (k[4] != null) body.severity = k[4];
    k[2].forEach(function (p) { body[p] = value(p); });
    return body;
  }
  function mixShiftBody(category) { return { kind: 'mix_shift', category: category, hours: 12, when: 'now' }; }
  function fill(id, values) { var s = $(id); values.forEach(function (v) { var o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o); }); }
  KINDS.forEach(function (k) { var o = document.createElement('option'); o.value = k[0]; o.textContent = k[1]; $('f-kind').appendChild(o); });
  fill('f-country', ['DE', 'FR', 'ES', 'PL']); fill('f-platform', ['Android', 'iOS', 'Web']); fill('f-category', ['electronics', 'fashion', 'home', 'beauty', 'sports']);
  fill('pr-country', ['FR', 'DE', 'ES', 'PL']); fill('ms-category', ['fashion', 'electronics', 'home', 'beauty', 'sports']); fill('dd-source', ['payments', 'orders', 'traffic', 'search', 'recommendations', 'ads', 'inventory']);
  function showParams() {
    var need = KINDS.filter(function (k) { return k[0] === $('f-kind').value; })[0][2];
    ['country', 'platform', 'category'].forEach(function (p) { $('f-' + p).hidden = need.indexOf(p) < 0; });
  }
  $('f-kind').onchange = showParams; showParams();
  var endKey = '';
  function fillEnd(l) {
    var key = JSON.stringify(l.map(function (i) { return i.id; }));
    $('end-grp').hidden = !l.length;
    var bb = $('bench-btn'); bb.classList.toggle('busy', l.length > 0);
    bb.title = l.length ? l.length + ' simulated change' + (l.length > 1 ? 's' : '') + ' in progress' : 'Simulated faults for the demo';
    if (key === endKey) return;
    endKey = key;
    var sel = $('end-select'), keep = sel.value;
    sel.textContent = '';
    l.forEach(function (i) {
      var where = ['country', 'platform', 'category', 'source'].map(function (d) { return obj(i.scope)[d]; }).filter(Boolean).join(' ');
      var o = document.createElement('option'); o.value = i.id;
      o.textContent = i.kind + ' ' + where + ' from ' + displayHour(obj(i.scope).start);
      sel.appendChild(o);
    });
    if (keep) sel.value = keep;
  }
  // the bench's message: in the bench while it is open, and as a toast on the canvas when it is closed
  var flashTimer = null;
  function toast(msg, extra, ms) {
    var t = $('toast'); t.textContent = msg || '';
    if (extra) { t.appendChild(text(' ')); t.appendChild(extra); }
    var f = $('flash');
    if (!msg) { f.hidden = true; return; }
    f.textContent = '';
    f.appendChild(el('span', 'demo-tag', ['DEMO'])); f.appendChild(el('span', null, [msg]));
    if (extra) { var e2 = extra.cloneNode(true); e2.onclick = extra.onclick; f.appendChild(e2); }
    var x = el('button', 'flash-x', ['Dismiss']); x.onclick = function () { f.hidden = true; }; f.appendChild(x);
    f.hidden = !$('bench').hidden;
    clearTimeout(flashTimer); flashTimer = setTimeout(function () { f.hidden = true; }, ms || 7000);
  }
  $('end-go').onclick = function () {
    var id = parseInt($('end-select').value, 10);
    if (!id) return;
    toast('Ending...');
    post('/api/end', { id: id }).then(function (r) { toast('Ended from ' + displayHour(r.ended_from) + '. The monitor will see the metric recover in the next hours.'); refresh(); })
      .catch(function (e) { toast('Failed: ' + e.message); });
  };
  function act(body, label) {
    toast(label + '...');
    post('/api/inject', body).then(function (r) { toast(label + ' from ' + displayHour(r.start) + '. Injected into the data; the graph changes when the monitor detects it.'); refresh(); })
      .catch(function (e) { toast('Failed: ' + e.message); });
  }
  $('f-go').onclick = function () {
    var kind = $('f-kind').value, label = KINDS.filter(function (k) { return k[0] === kind; })[0][1];
    act(faultBody(kind, function (p) { return $('f-' + p).value; }), label + ' injected');
  };
  $('pr-go').onclick = function () { act({ kind: 'promo', country: $('pr-country').value, hours: 12, when: 'now' }, 'Promo started'); };
  $('ms-go').onclick = function () { act(mixShiftBody($('ms-category').value), 'Mix shift started'); };
  $('dd-go').onclick = function () { act({ kind: 'data_delay', source: $('dd-source').value, hours: 3, delay_hours: 2, when: 'now' }, 'Late data scheduled'); };
  function handOver(yamlText) {
    post('/api/metric', { yaml_text: yamlText }).then(function (r) {
      toast('Handed to the monitor: ' + r.saved + '. Waiting for its answer...', null, 40000);
      var tries = 0;
      var poll = function () {
        get('/api/metric/' + encodeURIComponent(r.name)).then(function (res) { answerToast(res); refresh(); })
          .catch(function () { if (++tries < 20) setTimeout(poll, 2000); else toast('Handed to the monitor; it has not answered yet.'); });
      };
      setTimeout(poll, 1500);
    }).catch(function (e) { toast('Failed: ' + e.message); });
  }
  $('nm-go').onclick = function () {
    var y = 'name: refund_rate\ndescription: Share of orders refunded\nsql: >\n  SELECT ts, country, platform, category, channel,\n         SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0) AS value\n  FROM facts_hourly GROUP BY ts, country, platform, category, channel\ndimensions: [country, platform, category, channel]\nunit: ratio\n';
    handOver(y);
  };
  $('nm-dup').onclick = function () {
    var dup = 'name: payment_rate\ndescription: Successful payments per payment attempt\nsql: >\n  SELECT ts, country, platform, category, channel,\n         SUM(payment_successes) * 1.0 / NULLIF(SUM(payment_attempts), 0) AS value\n  FROM facts_hourly GROUP BY ts, country, platform, category, channel\ndimensions: [country, platform, category, channel]\nunit: ratio\n';
    handOver(dup);
  };
  function toggleBench(open, byHand) {
    var b = $('bench'); if (open == null) open = b.hidden;
    b.hidden = !open; $('bench-btn').setAttribute('aria-expanded', open ? 'true' : 'false');
    $('bench-btn').classList.toggle('on', open);
    if (open) { $('flash').hidden = true; if (byHand) $('f-kind').focus({ preventScroll: true }); }
    store('v3-bench', open ? '1' : '0');
  }
  $('bench-btn').onclick = function () { toggleBench(null, true); };
  $('bench-close').onclick = function () { toggleBench(false); $('bench-btn').focus(); };

  // --------------------------------------------------------- chrome
  $('legend-btn').onclick = function () {
    var b = $('legend-body'), open = b.hidden; b.hidden = !open;
    $('legend-btn').setAttribute('aria-expanded', open ? 'true' : 'false'); store('v3-legend', open ? '1' : '0');
  };
  if (store('v3-legend') === '1') { $('legend-body').hidden = false; $('legend-btn').setAttribute('aria-expanded', 'true'); }
  $('theme-btn').onclick = function () {
    var r = document.documentElement, dark = r.dataset.theme ? r.dataset.theme === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    r.dataset.theme = dark ? 'light' : 'dark';
    var u = new URL(location.href); u.searchParams.set('theme', r.dataset.theme); history.replaceState(null, '', u);
  };
  var lastSize = null;
  window.addEventListener('resize', function () { miniView(); });
  if (window.ResizeObserver) new ResizeObserver(function () {
    var s = size();
    if (lastSize && drawn && auto && (Math.abs(s.w - lastSize.w) > 1 || Math.abs(s.h - lastSize.h) > 1)) { lastSize = s; auto(); return; }
    if (lastSize && drawn) {
      // keep the middle of the view where it was when the canvas changes size (the report pinned or closed)
      setView({ k: view.k, x: view.x + (s.w - lastSize.w) / 2, y: view.y + (s.h - lastSize.h) / 2 });
    }
    lastSize = s;
  }).observe($('canvas'));

  var params = new URLSearchParams(location.search), asked = params.get('incident');
  // ?domain=payments opens the console scoped to that domain
  if (/^[a-z_]+$/.test(params.get('domain') || '')) dom = params.get('domain');
  if (asked) { selInc = asked; openSheet(); }
  // ?metric=refund_rate opens that metric's definition (for a demo or a screenshot)
  var askedMetric = params.get('metric');
  if (askedMetric) { selNode = askedMetric; mode = 'node'; flown = '-'; openSheet(); }
  if (params.get('bench') === 'open' || (params.get('bench') == null && store('v3-bench') === '1')) toggleBench(true);

  get('/api/domains').then(function (d) { domains = d; }).catch(function () {}).then(function () { refresh(); setInterval(refresh, 2000); });
})();
