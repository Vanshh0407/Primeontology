import { describe, expect, it } from 'vitest';
import { addClass, addProperty, deleteClass, emptyModel, renameClass, updateClass, updateProperty, validName, wouldCycle } from './modelOps';
import { computeLayout, modelToGraph } from './layout';

const base = () => {
  let m = emptyModel();
  m = addClass(m, { name: 'Customer' });
  m = addClass(m, { name: 'SalesOrder' });
  m = addProperty(m, 'object', { name: 'places', domain: 'Customer', range: 'SalesOrder' });
  m = addProperty(m, 'data', { name: 'orderDate', domain: 'SalesOrder', datatype: 'date' });
  return m;
};

describe('modelOps', () => {
  it('renames a relationship (places -> createsOrder) and the graph reflects it', () => {
    const m = updateProperty(base(), 'object', 'Customer', 'places', { name: 'createsOrder' });
    expect(m.objectProperties[0].name).toBe('createsOrder');
    expect(modelToGraph(m).edges[0].label).toBe('createsOrder');
  });
  it('cascades class rename and delete', () => {
    const r = renameClass(base(), 'Customer', 'Client');
    expect(r.objectProperties[0].domain).toBe('Client');
    const d = deleteClass(base(), 'Customer');
    expect(d.objectProperties).toHaveLength(0);
    expect(d.classes.map((c) => c.name)).toEqual(['SalesOrder']);
  });
  it('rejects invalid and duplicate names and stays immutable', () => {
    const m = base();
    expect(() => addClass(m, { name: 'bad name' })).toThrow();
    expect(() => addClass(m, { name: 'customer' })).toThrow(/exists/);
    expect(() => addProperty(m, 'object', { name: 'x', domain: 'Customer', range: 'Ghost' })).toThrow();
    expect(m.classes).toHaveLength(2);
    expect(validName('ok_1', [])).toBeNull();
  });
  it('blocks inheritance cycles', () => {
    let m = updateClass(base(), 'SalesOrder', { parents: ['Customer'] });
    expect(wouldCycle(m, 'Customer', 'SalesOrder')).toBe(true);
    expect(() => updateClass(m, 'Customer', { parents: ['SalesOrder'] })).toThrow(/cycle/);
    expect(() => updateClass(m, 'Customer', { parents: ['Customer'] })).toThrow();
  });
});

describe('layout', () => {
  it('positions every node for every layout', () => {
    const g = modelToGraph(base());
    for (const k of ['TB', 'LR', 'circle', 'grid']) {
      const pos = computeLayout(g.nodes.map((n) => n.id), g.edges, k);
      expect(Object.keys(pos).sort()).toEqual(['Customer', 'SalesOrder']);
      expect(pos.Customer).not.toEqual(pos.SalesOrder);
    }
  });
  it('handles empty graphs and self loops', () => {
    expect(computeLayout([], [], 'TB')).toEqual({});
    expect(computeLayout(['A'], [{ source: 'A', target: 'A' }], 'TB').A).toBeTruthy();
  });
});
