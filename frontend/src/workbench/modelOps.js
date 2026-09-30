// Pure, immutable ontology-model edits (mirrors backend model_ops; the server re-validates on save).
export const NAME_RE = /^[A-Za-z_][A-Za-z0-9_-]*$/;
export const XSD_TYPES = ['string', 'integer', 'long', 'short', 'decimal', 'float', 'double', 'boolean', 'date', 'dateTime', 'time', 'anyURI', 'base64Binary'];
export const CARDINALITIES = ['many-to-one', 'one-to-many', 'many-to-many', 'one-to-one'];

const clone = (m) => JSON.parse(JSON.stringify(m));
export const emptyModel = () => ({ classes: [], dataProperties: [], objectProperties: [] });

export function toLabel(name) {
  return name.replace(/[_-]+/g, ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function validName(name, existing = []) {
  if (!name || !NAME_RE.test(name)) return 'Use letters, digits, _ or - and start with a letter.';
  if (existing.some((e) => e.toLowerCase() === name.toLowerCase())) return `"${name}" already exists.`;
  return null;
}

export function addClass(model, { name, parents = [], comment = '' }) {
  const err = validName(name, model.classes.map((c) => c.name));
  if (err) throw new Error(err);
  const m = clone(model);
  m.classes.push({ name, label: toLabel(name), comment, parents, source: { origin: 'workbench' } });
  return m;
}

export function updateClass(model, name, patch) {
  const m = clone(model);
  const c = m.classes.find((x) => x.name === name);
  if (!c) throw new Error(`Unknown class ${name}`);
  if (patch.parents && patch.parents.includes(name)) throw new Error('A class cannot be its own parent.');
  if (patch.parents) {
    for (const p of patch.parents) if (wouldCycle(model, name, p)) throw new Error(`${p} is a descendant of ${name}: this would create a cycle.`);
  }
  Object.assign(c, patch);
  return m;
}

export function renameClass(model, oldName, newName) {
  const err = oldName === newName ? null : validName(newName, model.classes.map((c) => c.name));
  if (err) throw new Error(err);
  const m = clone(model);
  for (const c of m.classes) {
    if (c.name === oldName) {
      if (!c.label || c.label === oldName || c.label === toLabel(oldName)) c.label = toLabel(newName);
      c.name = newName;
    }
    for (const k of ['parents', 'equivalentTo', 'disjointWith']) if (c[k]) c[k] = c[k].map((x) => (x === oldName ? newName : x));
  }
  for (const p of [...m.dataProperties, ...m.objectProperties]) {
    if (p.domain === oldName) p.domain = newName;
    if (p.range === oldName) p.range = newName;
  }
  for (const i of m.individuals || []) if (i.class === oldName) i.class = newName;
  if (m.layout && m.layout[oldName]) {
    m.layout[newName] = m.layout[oldName];
    delete m.layout[oldName];
  }
  return m;
}

export function deleteClass(model, name) {
  const m = clone(model);
  m.classes = m.classes.filter((c) => c.name !== name);
  for (const c of m.classes) c.parents = (c.parents || []).filter((p) => p !== name);
  m.dataProperties = m.dataProperties.filter((p) => p.domain !== name);
  m.objectProperties = m.objectProperties.filter((p) => p.domain !== name && p.range !== name);
  m.individuals = (m.individuals || []).filter((i) => i.class !== name);
  if (m.layout) delete m.layout[name];
  return m;
}

const key = (kind) => (kind === 'data' ? 'dataProperties' : 'objectProperties');

export function addProperty(model, kind, prop) {
  const err = validName(prop.name);
  if (err) throw new Error(err);
  if (!model.classes.some((c) => c.name === prop.domain)) throw new Error(`Unknown domain ${prop.domain}`);
  if (kind === 'object' && !model.classes.some((c) => c.name === prop.range)) throw new Error(`Unknown range ${prop.range}`);
  if (model[key(kind)].some((p) => p.domain === prop.domain && p.name.toLowerCase() === prop.name.toLowerCase())) {
    throw new Error(`${prop.domain} already has a property "${prop.name}".`);
  }
  const m = clone(model);
  const base = { label: toLabel(prop.name), required: false, source: { origin: 'workbench' }, ...prop };
  m[key(kind)].push(kind === 'data' ? { datatype: 'string', identifier: false, ...base } : { cardinality: 'many-to-many', ...base });
  return m;
}

export function updateProperty(model, kind, domain, name, patch) {
  const m = clone(model);
  const p = m[key(kind)].find((x) => x.domain === domain && x.name === name);
  if (!p) throw new Error('Unknown property');
  if (patch.name && patch.name !== name) {
    const err = validName(patch.name, m[key(kind)].filter((x) => x.domain === (patch.domain || domain) && x !== p).map((x) => x.name));
    if (err) throw new Error(err);
  }
  if (patch.range && kind === 'object' && !m.classes.some((c) => c.name === patch.range)) throw new Error('Unknown range');
  Object.assign(p, patch);
  return m;
}

export function deleteProperty(model, kind, domain, name) {
  const m = clone(model);
  m[key(kind)] = m[key(kind)].filter((p) => !(p.domain === domain && p.name === name));
  return m;
}

export function setLayout(model, positions) {
  return { ...model, layout: { ...(model.layout || {}), ...positions } };
}

export function ancestors(model, name, seen = new Set()) {
  const c = model.classes.find((x) => x.name === name);
  for (const p of (c && c.parents) || []) {
    if (!seen.has(p)) {
      seen.add(p);
      ancestors(model, p, seen);
    }
  }
  return seen;
}

/** True if making `parent` a parent of `child` would create a cycle. */
export function wouldCycle(model, child, parent) {
  return child === parent || ancestors(model, parent).has(child);
}

export function stats(model) {
  return { classes: model.classes.length, dataProperties: model.dataProperties.length, objectProperties: model.objectProperties.length };
}
