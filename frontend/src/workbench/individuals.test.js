import { describe, expect, it } from 'vitest';
import { addClass, addProperty, emptyModel } from './modelOps';
import {
  addIndividual, autoGroup, connectedClusters, deleteIndividual, groupsOf, instanceCounts, propertiesFor, renameIndividual, setGroup,
  setIndividualLinks, setIndividualValue, suggestIndividualName,
} from './individuals';
import { computeGroupedLayout, groupBoxes } from './groupLayout';

const base = () => {
  let m = emptyModel();
  m = addClass(m, { name: 'Customer' });
  m = addClass(m, { name: 'SalesOrder' });
  m = addProperty(m, 'object', { name: 'places', domain: 'Customer', range: 'SalesOrder' });
  return m;
};

describe('individuals', () => {
  const withInds = () => {
    let m = base();
    for (const [name, datatype] of [['name', 'string'], ['age', 'integer'], ['vip', 'boolean'], ['balance', 'decimal']]) {
      m = addProperty(m, 'data', { name, domain: 'Customer', datatype });
    }
    m = addIndividual(m, { name: 'c1', class: 'Customer' });
    m = addIndividual(m, { name: 'o1', class: 'SalesOrder' });
    return m;
  };

  it('adds with validation and suggests unique names', () => {
    const m = withInds();
    expect(suggestIndividualName(m, 'Customer')).toBe('customer_1');
    expect(suggestIndividualName(addIndividual(m, { name: 'customer_1', class: 'Customer' }), 'Customer')).toBe('customer_2');
    expect(() => addIndividual(m, { name: 'C1', class: 'Customer' })).toThrow(/exists/);
    expect(() => addIndividual(m, { name: 'x', class: 'Ghost' })).toThrow();
    expect(() => addIndividual(m, { name: 'bad name', class: 'Customer' })).toThrow();
  });

  it('types values per datatype and rejects bad input', () => {
    let m = withInds();
    m = setIndividualValue(m, 'c1', 'age', '42');
    m = setIndividualValue(m, 'c1', 'vip', true);
    m = setIndividualValue(m, 'c1', 'balance', '12.50');
    m = setIndividualValue(m, 'c1', 'name', 'Initech');
    expect(m.individuals[0].data).toEqual({ age: 42, vip: true, balance: '12.50', name: 'Initech' });
    expect(() => setIndividualValue(m, 'c1', 'age', 'forty')).toThrow(/whole number/);
    expect(() => setIndividualValue(m, 'c1', 'balance', 'lots')).toThrow(/number/);
    expect(() => setIndividualValue(m, 'c1', 'ghost', 'x')).toThrow(/no data property/);
    expect(setIndividualValue(m, 'c1', 'age', '').individuals[0].data.age).toBeUndefined();
  });

  it('links, renames (cascading) and deletes (cleaning references)', () => {
    let m = withInds();
    m = setIndividualLinks(m, 'c1', 'places', ['o1']);
    m = renameIndividual(m, 'o1', 'order_9');
    expect(m.individuals[0].links.places).toEqual(['order_9']);
    const d = deleteIndividual(m, 'order_9');
    expect(d.individuals.map((i) => i.name)).toEqual(['c1']);
    expect(d.individuals[0].links.places).toBeUndefined();
    expect(instanceCounts(m)).toEqual({ Customer: 1, SalesOrder: 1 });
  });

  it('inherited properties apply to instances of subclasses', () => {
    const m = addClass(withInds(), { name: 'Premium', parents: ['Customer'] });
    expect(propertiesFor(m, 'Premium').data.map((p) => p.name)).toContain('age');
    expect(propertiesFor(m, 'Premium').object.map((p) => p.name)).toContain('places');
  });
});

describe('groups', () => {
  it('sets, lists and auto-groups by connected cluster', () => {
    let m = base();
    m = addClass(m, { name: 'Lonely' });
    m = addClass(m, { name: 'Child', parents: ['Customer'] });
    expect([...connectedClusters(m)[0]].sort()).toEqual(['Child', 'Customer', 'SalesOrder']);
    const g = autoGroup(m, 'cluster');
    expect(g.classes.find((c) => c.name === 'Lonely').group).toBe('Unconnected');
    expect(g.classes.find((c) => c.name === 'Customer').group).toBe('Cluster 1');
    expect(groupsOf(g)).toEqual(['Cluster 1', 'Unconnected']);
    expect(autoGroup(g, 'clear').classes.some((c) => c.group)).toBe(false);
    expect(setGroup(m, 'Customer', 'Core').classes[0].group).toBe('Core');
    expect(() => setGroup(m, 'Ghost', 'x')).toThrow();
  });

  it('groups by source document or database, ignoring workbench-made classes', () => {
    let m = base();
    m = { ...m, classes: m.classes.map((c, i) => ({ ...c, source: i === 0 ? { document: 'contract.pdf' } : { database: 'shop' } })) };
    expect(autoGroup(m, 'source').classes.map((c) => c.group)).toEqual(['contract', 'shop']);
  });

  it('grouped layout keeps groups in separate, non-overlapping regions', () => {
    const ids = ['a1', 'a2', 'a3', 'b1', 'b2', 'z'];
    const groupOf = { a1: 'A', a2: 'A', a3: 'A', b1: 'B', b2: 'B' };
    const edges = [{ source: 'a1', target: 'a2' }, { source: 'a1', target: 'a3' }, { source: 'b1', target: 'b2' }];
    const pos = computeGroupedLayout(ids, edges, groupOf);
    expect(Object.keys(pos).sort()).toEqual([...ids].sort());
    const boxes = Object.fromEntries(groupBoxes(pos, groupOf, 0).map((b) => [b.group, b]));
    const overlap = (p, q) => p.x < q.x + q.w && q.x < p.x + p.w && p.y < q.y + q.h && q.y < p.y + p.h;
    expect(overlap(boxes.A, boxes.B)).toBe(false);
    expect(boxes.A.count).toBe(3);
  });
});
