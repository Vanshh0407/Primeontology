import dagre from '@dagrejs/dagre';

export const LAYOUTS = [
  { id: 'TB', label: 'Hierarchical (top-down)' },
  { id: 'LR', label: 'Hierarchical (left-right)' },
  { id: 'circle', label: 'Circular' },
  { id: 'grid', label: 'Grid' },
  { id: 'groups', label: 'Cluster by group' },
  { id: 'fast', label: 'Fast grid (large graphs)' },
];
export const NODE_W = 190;
export const NODE_H = 78;

/** Compute {id: {x, y}} for every node id. Pure. */
export function computeLayout(nodeIds, edges, kind = 'TB') {
  const n = nodeIds.length;
  const pos = {};
  if (kind === 'circle') {
    const r = Math.max(180, (n * (NODE_W + 30)) / (2 * Math.PI));
    nodeIds.forEach((id, i) => {
      const a = (2 * Math.PI * i) / Math.max(n, 1) - Math.PI / 2;
      pos[id] = { x: r + r * Math.cos(a), y: r + r * Math.sin(a) };
    });
    return pos;
  }
  if (kind === 'grid') {
    const cols = Math.max(1, Math.ceil(Math.sqrt(n)));
    nodeIds.forEach((id, i) => {
      pos[id] = { x: (i % cols) * (NODE_W + 60), y: Math.floor(i / cols) * (NODE_H + 70) };
    });
    return pos;
  }
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: kind === 'LR' ? 'LR' : 'TB', nodesep: 60, ranksep: 90 });
  g.setDefaultEdgeLabel(() => ({}));
  nodeIds.forEach((id) => g.setNode(id, { width: NODE_W, height: NODE_H }));
  const set = new Set(nodeIds);
  edges.forEach((e) => set.has(e.source) && set.has(e.target) && e.source !== e.target && g.setEdge(e.source, e.target));
  dagre.layout(g);
  nodeIds.forEach((id) => {
    const nd = g.node(id);
    pos[id] = { x: nd.x - NODE_W / 2, y: nd.y - NODE_H / 2 };
  });
  return pos;
}

/** Build graph elements from a model (client-side, so edits show instantly). */
export function modelToGraph(model) {
  const nodes = model.classes.map((c) => ({
    id: c.name,
    label: c.label || c.name,
    props: model.dataProperties.filter((p) => p.domain === c.name).map((p) => p.name),
    propInfo: model.dataProperties.filter((p) => p.domain === c.name).map((p) => ({ name: p.name, datatype: p.datatype, required: !!p.required })),
    comment: c.comment || '',
    group: c.group || '',
  }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = [];
  for (const p of model.objectProperties) {
    if (ids.has(p.domain) && ids.has(p.range)) {
      edges.push({ id: `o:${p.domain}.${p.name}`, source: p.domain, target: p.range, label: p.name, type: 'object', domain: p.domain, name: p.name });
    }
  }
  for (const c of model.classes) {
    for (const par of c.parents || []) {
      if (ids.has(par)) edges.push({ id: `s:${c.name}<${par}`, source: c.name, target: par, label: 'subClassOf', type: 'subclass' });
    }
  }
  return { nodes, edges };
}
