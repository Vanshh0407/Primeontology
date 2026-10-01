import { NODE_H, NODE_W, computeLayout } from './layout';

const UNGROUPED = '\u0000ungrouped';
const CELL_DAGRE_MAX = 250;
export const BIG_GRAPH = 400; // above this many classes the default layout is the fast cluster grid

/** Layout that keeps each group together: dagre per group, groups placed in a grid of cells. */
export function computeGroupedLayout(nodeIds, edges, groupOf) {
  const buckets = new Map();
  for (const id of nodeIds) {
    const g = groupOf[id] || UNGROUPED;
    if (!buckets.has(g)) buckets.set(g, []);
    buckets.get(g).push(id);
  }
  const cells = [...buckets.entries()].sort((a, b) => b[1].length - a[1].length).map(([g, ids]) => {
    const set = new Set(ids);
    // dagre's ordering step is super-linear (≈2 s at 1,000 nodes, ≈8 s at 2,000), so very large cells use a plain grid
    const pos = ids.length > CELL_DAGRE_MAX ? computeLayout(ids, [], 'grid') : computeLayout(ids, edges.filter((e) => set.has(e.source) && set.has(e.target)), 'TB');
    const xs = ids.map((i) => pos[i].x);
    const ys = ids.map((i) => pos[i].y);
    const minX = Math.min(...xs);
    const minY = Math.min(...ys);
    return { g, ids, pos, minX, minY, w: Math.max(...xs) - minX + NODE_W, h: Math.max(...ys) - minY + NODE_H };
  });
  const cols = Math.max(1, Math.ceil(Math.sqrt(cells.length)));
  const GAP = 90;
  const colW = Array(cols).fill(0);
  const rowH = [];
  cells.forEach((c, i) => {
    colW[i % cols] = Math.max(colW[i % cols], c.w);
    rowH[Math.floor(i / cols)] = Math.max(rowH[Math.floor(i / cols)] || 0, c.h);
  });
  const out = {};
  cells.forEach((c, i) => {
    const ox = colW.slice(0, i % cols).reduce((a, b) => a + b + GAP, 0);
    const oy = rowH.slice(0, Math.floor(i / cols)).reduce((a, b) => a + b + GAP, 0);
    c.ids.forEach((id) => {
      out[id] = { x: ox + c.pos[id].x - c.minX, y: oy + c.pos[id].y - c.minY };
    });
  });
  return out;
}

/** Bounding rectangle per named group from node positions (with padding). */
export function groupBoxes(positions, groupOf, pad = 28) {
  const acc = {};
  for (const [id, g] of Object.entries(groupOf)) {
    const p = positions[id];
    if (!g || !p) continue;
    const a = (acc[g] = acc[g] || { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity, n: 0 });
    a.minX = Math.min(a.minX, p.x);
    a.minY = Math.min(a.minY, p.y);
    a.maxX = Math.max(a.maxX, p.x + NODE_W);
    a.maxY = Math.max(a.maxY, p.y + NODE_H);
    a.n += 1;
  }
  return Object.entries(acc).map(([g, a]) => ({ group: g, x: a.minX - pad, y: a.minY - pad - 14, w: a.maxX - a.minX + 2 * pad, h: a.maxY - a.minY + 2 * pad + 14, count: a.n }));
}

/** Stable colour index per group name. */
export function groupColorIndex(name, n = 8) {
  let h = 0;
  for (let i = 0; i < name.length; i += 1) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return h % n;
}

/** Connected components (union-find), largest first. Returns {id: 'Component k'}. */
export function componentsOf(nodeIds, edges) {
  const parent = new Map(nodeIds.map((id) => [id, id]));
  const find = (x) => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r);
    let c = x;
    while (parent.get(c) !== r) { const nx = parent.get(c); parent.set(c, r); c = nx; }
    return r;
  };
  for (const e of edges) if (parent.has(e.source) && parent.has(e.target)) parent.set(find(e.source), find(e.target));
  const sizes = new Map();
  for (const id of nodeIds) sizes.set(find(id), (sizes.get(find(id)) || 0) + 1);
  const order = [...sizes.entries()].sort((a, b) => b[1] - a[1]).map(([r], i) => [r, `Component ${i + 1}`]);
  const label = new Map(order);
  return Object.fromEntries(nodeIds.map((id) => [id, label.get(find(id))]));
}

/** O(n) layout for big ontologies: one grid cell per user group (or per connected component), nodes in a grid inside each cell. */
export function computeFastLayout(nodeIds, edges, groupOf = {}) {
  const grouped = new Set(Object.values(groupOf).filter(Boolean));
  const keys = grouped.size >= 2 ? groupOf : componentsOf(nodeIds, edges);
  const buckets = new Map();
  for (const id of nodeIds) {
    const k = keys[id] || UNGROUPED;
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k).push(id);
  }
  const cells = [...buckets.values()].sort((a, b) => b.length - a.length).map((ids) => {
    const pos = computeLayout(ids, [], 'grid');
    const xs = ids.map((i) => pos[i].x);
    const ys = ids.map((i) => pos[i].y);
    return { ids, pos, w: Math.max(...xs) + NODE_W, h: Math.max(...ys) + NODE_H };
  });
  const GAP = 120;
  const totalArea = cells.reduce((a, c) => a + (c.w + GAP) * (c.h + GAP), 0);
  const rowWidth = Math.max(Math.sqrt(totalArea) * 1.4, ...cells.map((c) => c.w));
  const out = {};
  let x = 0;
  let y = 0;
  let rowH = 0;
  for (const c of cells) {
    if (x > 0 && x + c.w > rowWidth) { x = 0; y += rowH + GAP; rowH = 0; }
    c.ids.forEach((id) => { out[id] = { x: x + c.pos[id].x, y: y + c.pos[id].y }; });
    x += c.w + GAP;
    rowH = Math.max(rowH, c.h);
  }
  return out;
}
