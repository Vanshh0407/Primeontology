// Pure, immutable edits for individuals (instance records) and class groups. Mirrors the backend validation rules.
import { ancestors, validName } from './modelOps';

const clone = (m) => JSON.parse(JSON.stringify(m));

/** Data/object properties that apply to instances of `cls` (own + inherited). */
export function propertiesFor(model, cls) {
  const scope = new Set([cls, ...ancestors(model, cls)]);
  return {
    data: model.dataProperties.filter((p) => scope.has(p.domain)),
    object: model.objectProperties.filter((p) => scope.has(p.domain)),
  };
}

export function isInstanceOf(model, individual, cls) {
  return individual.class === cls || ancestors(model, individual.class).has(cls);
}

export function suggestIndividualName(model, cls) {
  const taken = new Set((model.individuals || []).map((i) => i.name.toLowerCase()));
  const base = cls.replace(/[^A-Za-z0-9_-]/g, '').toLowerCase() || 'item';
  for (let i = 1; ; i += 1) if (!taken.has(`${base}_${i}`)) return `${base}_${i}`;
}

export function addIndividual(model, { name, class: cls }) {
  const err = validName(name, (model.individuals || []).map((i) => i.name));
  if (err) throw new Error(err);
  if (!model.classes.some((c) => c.name === cls)) throw new Error(`Unknown class ${cls}`);
  const m = clone(model);
  m.individuals = [...(m.individuals || []), { name, class: cls, label: name, data: {}, links: {}, source: { origin: 'workbench' } }];
  return m;
}

export function renameIndividual(model, oldName, newName) {
  if (oldName === newName) return model;
  const err = validName(newName, (model.individuals || []).map((i) => i.name));
  if (err) throw new Error(err);
  const m = clone(model);
  for (const i of m.individuals) {
    if (i.name === oldName) {
      i.name = newName;
      if (!i.label || i.label === oldName) i.label = newName;
    }
    for (const [k, v] of Object.entries(i.links || {})) i.links[k] = (Array.isArray(v) ? v : [v]).map((t) => (t === oldName ? newName : t));
  }
  return m;
}

const INT_RE = /^[+-]?\d+$/;

export function setIndividualValue(model, name, prop, value) {
  const m = clone(model);
  const i = (m.individuals || []).find((x) => x.name === name);
  if (!i) throw new Error(`Unknown individual ${name}`);
  const p = m.dataProperties.find((x) => x.name === prop && (x.domain === i.class || ancestors(m, i.class).has(x.domain)));
  if (!p) throw new Error(`${i.class} has no data property "${prop}"`);
  i.data = i.data || {};
  if (value === '' || value === null || value === undefined) {
    delete i.data[prop];
    return m;
  }
  const t = p.datatype;
  if (['integer', 'long', 'short'].includes(t)) {
    if (!INT_RE.test(String(value))) throw new Error(`${prop} must be a whole number.`);
    i.data[prop] = Number(value);
  } else if (['decimal', 'float', 'double'].includes(t)) {
    if (Number.isNaN(Number(value))) throw new Error(`${prop} must be a number.`);
    i.data[prop] = t === 'decimal' ? String(value) : Number(value);
  } else if (t === 'boolean') {
    i.data[prop] = value === true || value === 'true';
  } else {
    i.data[prop] = value;
  }
  return m;
}

export function setIndividualLinks(model, name, rel, targets) {
  const m = clone(model);
  const i = (m.individuals || []).find((x) => x.name === name);
  if (!i) throw new Error(`Unknown individual ${name}`);
  i.links = i.links || {};
  if (!targets || targets.length === 0) delete i.links[rel];
  else i.links[rel] = targets;
  return m;
}

export function deleteIndividual(model, name) {
  const m = clone(model);
  m.individuals = (m.individuals || []).filter((i) => i.name !== name);
  for (const i of m.individuals) {
    for (const [k, v] of Object.entries(i.links || {})) {
      const left = (Array.isArray(v) ? v : [v]).filter((t) => t !== name);
      if (left.length) i.links[k] = left;
      else delete i.links[k];
    }
  }
  return m;
}

export const instanceCounts = (model) => {
  const out = {};
  for (const i of model.individuals || []) out[i.class] = (out[i.class] || 0) + 1;
  return out;
};

// ------------------------------------------------------------------ grouping
export function setGroup(model, className, group) {
  const m = clone(model);
  const c = m.classes.find((x) => x.name === className);
  if (!c) throw new Error(`Unknown class ${className}`);
  if (group) c.group = group;
  else delete c.group;
  return m;
}

export const groupsOf = (model) => [...new Set(model.classes.map((c) => c.group).filter(Boolean))].sort();

/** Connected components over relationships + inheritance, largest first. */
export function connectedClusters(model) {
  const adj = new Map(model.classes.map((c) => [c.name, new Set()]));
  const link = (a, b) => {
    if (adj.has(a) && adj.has(b) && a !== b) {
      adj.get(a).add(b);
      adj.get(b).add(a);
    }
  };
  model.objectProperties.forEach((p) => link(p.domain, p.range));
  model.classes.forEach((c) => (c.parents || []).forEach((p) => link(c.name, p)));
  const seen = new Set();
  const comps = [];
  for (const c of model.classes) {
    if (seen.has(c.name)) continue;
    const stack = [c.name];
    const comp = [];
    seen.add(c.name);
    while (stack.length) {
      const n = stack.pop();
      comp.push(n);
      for (const x of adj.get(n)) {
        if (!seen.has(x)) {
          seen.add(x);
          stack.push(x);
        }
      }
    }
    comps.push(comp);
  }
  return comps.sort((a, b) => b.length - a.length);
}

/** mode: 'cluster' | 'source' | 'clear'. */
export function autoGroup(model, mode) {
  const m = clone(model);
  if (mode === 'clear') {
    m.classes.forEach((c) => delete c.group);
    return m;
  }
  if (mode === 'source') {
    for (const c of m.classes) {
      const s = c.source || {};
      const g = s.document || (s.origin && s.origin !== 'workbench' ? s.origin : null) || s.database || null;
      if (g) c.group = String(g).replace(/\.[^.]+$/, '');
      else delete c.group;
    }
    return m;
  }
  const byName = Object.fromEntries(m.classes.map((c) => [c.name, c]));
  let n = 0;
  for (const comp of connectedClusters(model)) {
    const label = comp.length === 1 ? 'Unconnected' : `Cluster ${(n += 1)}`;
    comp.forEach((nm) => {
      byName[nm].group = label;
    });
  }
  return m;
}
