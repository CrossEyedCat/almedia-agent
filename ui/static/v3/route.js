/* Eye of the System, v3: where the links on the metric graph leave and enter their boxes, and the curve between.
   Pure geometry with no page: app.js calls V3Route.layout() on every draw, and tests/test_route.py runs it under node.

   A box is { id, x, y, hw, hh }: its centre and half sizes in world units; id is unique per drawn box.
   A link is { from: box, to: box, kind: 'tree' | 'may' }: a formula link (parent to child) or a possible cause
   (source may affect target). layout(links, boxes) returns one geometry per link, in the same order:
     { d,                                  the SVG path: one cubic Bezier
       p: [[x, y] x 4],                    its four control points, from the link's start to its end
       start: { x, y, nx, ny, side },      where it leaves `from`; (nx, ny) the unit normal out of that side
       end:   { x, y, nx, ny, side },      where it enters `to`; (nx, ny) the unit normal out of that side, so the
                                           link arrives travelling along (-nx, -ny)
       mid:   { x, y, tx, ty } }           the point half way along, with the unit tangent towards `to`
   side is one of 'l', 'r', 't', 'b'. */
(function (root) {
  'use strict';

  function point(p, t) {
    var u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    return [a * p[0][0] + b * p[1][0] + c * p[2][0] + d * p[3][0], a * p[0][1] + b * p[1][1] + c * p[2][1] + d * p[3][1]];
  }
  function tangent(p, t) {
    var u = 1 - t, a = 3 * u * u, b = 6 * u * t, c = 3 * t * t;
    var x = a * (p[1][0] - p[0][0]) + b * (p[2][0] - p[1][0]) + c * (p[3][0] - p[2][0]);
    var y = a * (p[1][1] - p[0][1]) + b * (p[2][1] - p[1][1]) + c * (p[3][1] - p[2][1]);
    var n = Math.sqrt(x * x + y * y) || 1;
    return [x / n, y / n];
  }
  var NORMAL = { l: [-1, 0], r: [1, 0], t: [0, -1], b: [0, 1] };
  function port(box, side) {
    var n = NORMAL[side];
    return { x: box.x + n[0] * box.hw, y: box.y + n[1] * box.hh, nx: n[0], ny: n[1], side: side };
  }
  function round(v) { return Math.round(v * 10) / 10; }
  function geometry(s, e, p1, p2) {
    var p = [[s.x, s.y], p1, p2, [e.x, e.y]];
    var m = point(p, 0.5), t = tangent(p, 0.5);
    return {
      d: 'M' + round(s.x) + ',' + round(s.y) + ' C' + round(p1[0]) + ',' + round(p1[1]) + ' ' + round(p2[0]) + ',' + round(p2[1]) + ' ' + round(e.x) + ',' + round(e.y),
      p: p, start: s, end: e, mid: { x: m[0], y: m[1], tx: t[0], ty: t[1] }
    };
  }

  // the first layout: a formula link from the parent's right to the child's left; a possible cause from the right of
  // both boxes, bent out to the right of the two
  function link(a, b, kind) {
    if (kind === 'tree') {
      var s = port(a, 'r'), e = port(b, 'l'), c = (e.x - s.x) / 2;
      return geometry(s, e, [s.x + c, s.y], [e.x - c, e.y]);
    }
    var s2 = port(a, 'r'), e2 = port(b, 'r'), bend = Math.max(s2.x, e2.x) + 34 + Math.abs(a.y - b.y) * 0.06;
    return geometry(s2, e2, [bend, s2.y], [bend, e2.y]);
  }
  function layout(links, boxes) {
    return links.map(function (l) { return link(l.from, l.to, l.kind, boxes); });
  }

  var api = { layout: layout, point: point, tangent: tangent };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.V3Route = api;
})(this);
