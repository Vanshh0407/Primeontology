import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { NoOntology, PageHeader, Panel } from '../components/ui';
import { useWB } from '../context';
import { useOnt } from '../theme';

const VIEW_LABEL = { entity: 'All entities', customer: 'Customers', supplier: 'Suppliers', financial: 'Financial', contract: 'Contracts', organization: 'Organization', process: 'Processes', asset: 'Assets' };

/** Small dependency-free graph picture: focus nodes on an inner ring, context nodes on an outer ring. */
function MiniGraph({ g }) {
  const { kind } = useOnt();
  const W = 760, H = 380, cx = W / 2, cy = H / 2;
  const focus = g.nodes.filter((n) => n.focus), ctx = g.nodes.filter((n) => !n.focus);
  const pos = {};
  const ring = (list, r) => list.forEach((n, i) => { const a = (2 * Math.PI * i) / Math.max(list.length, 1) - Math.PI / 2; pos[n.id] = [cx + r * 1.7 * Math.cos(a), cy + r * Math.sin(a)]; });
  ring(focus, ctx.length ? 80 : 140); ring(ctx, 165);
  const classes = [...new Set(g.nodes.map((n) => n.class))];
  const color = (c) => [kind.class, kind.object, kind.data, '#f59e0b', '#a78bfa', '#34d399'][classes.indexOf(c) % 6];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxHeight: 400 }} role="img" aria-label="Graph view">
      {g.edges.map((e, i) => pos[e.from] && pos[e.to] && <line key={i} x1={pos[e.from][0]} y1={pos[e.from][1]} x2={pos[e.to][0]} y2={pos[e.to][1]} stroke="#8884" strokeWidth="1"><title>{e.label}</title></line>)}
      {g.nodes.map((n) => pos[n.id] && (<g key={n.id}><circle cx={pos[n.id][0]} cy={pos[n.id][1]} r={n.focus ? 9 : 6} fill={color(n.class)} fillOpacity={n.focus ? 1 : 0.55}><title>{`${n.class} · ${n.label}`}</title></circle>
        <text x={pos[n.id][0]} y={pos[n.id][1] - 12} fontSize="10" textAnchor="middle" fill="currentColor">{String(n.label).slice(0, 18)}</text></g>))}
    </svg>
  );
}

const SEV = { info: 'info', warning: 'warning', critical: 'error' };
const fmt = (v) => (v === null || v === undefined ? '—' : typeof v === 'number' ? Math.round(v * 100) / 100 : String(v));

export default function TwinTab() {
  const { api, ontology, notify, can } = useWB();
  const [snap, setSnap] = useState(null);
  const [asOf, setAsOf] = useState('');
  const [sim, setSim] = useState({ entityId: '', property: '', value: '' });
  const [res, setRes] = useState(null);
  const [rule, setRule] = useState({ name: '', class: '', kind: 'derive', target: '', expression: '', message: '' });
  const [hist, setHist] = useState(null);
  const [views, setViews] = useState({});
  const [view, setView] = useState('');
  const [graph, setGraph] = useState(null);
  const id = ontology?.id;
  const load = useCallback(async () => { if (id) setSnap(await api.twin(id, asOf ? new Date(asOf).toISOString() : undefined)); }, [api, id, asOf]);
  useEffect(() => { load().catch((e) => notify(e.message, 'error')); /* eslint-disable-next-line */ }, [id, asOf]);
  useEffect(() => { if (id) api.twinGraphViews(id).then((r) => setViews(r.views)).catch(() => {}); /* eslint-disable-next-line */ }, [id]);
  useEffect(() => { if (id && view) api.twinGraph(id, view).then(setGraph).catch((e) => notify(e.message, 'error')); /* eslint-disable-next-line */ }, [id, view]);
  if (!ontology) return <NoOntology />;
  const simulate = () => api.twinSimulate(id, [{ entityId: Number(sim.entityId), property: sim.property, value: Number.isNaN(Number(sim.value)) ? sim.value : Number(sim.value) }]).then(setRes).catch((e) => notify(e.message, 'error'));
  const addRule = () => api.twinAddRule(id, rule).then(() => { notify('Rule added', 'success'); load(); }).catch((e) => notify(e.message, 'error'));

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="object" eyebrow="Semantic Digital Twin" title="A live, time-aware model of the enterprise"
        description="Entities, processes and assets with full attribute history. Run what-if simulations that never touch real data."
        actions={<TextField size="small" type="datetime-local" label="View as of" InputLabelProps={{ shrink: true }} value={asOf} onChange={(e) => setAsOf(e.target.value)} />} />
      {snap && (
        <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1} sx={{ mb: 2 }}>
          <Chip label={`${snap.entities.total} entities`} variant="outlined" /><Chip label={`${snap.relationships} relationships`} variant="outlined" />
          <Chip label={`${snap.alerts.length} alerts`} color={snap.alerts.length ? 'warning' : 'default'} />
          {snap.asOf && <Chip color="info" label={`as of ${new Date(snap.asOf).toLocaleString()}`} />}
        </Stack>)}
      {snap?.alerts.map((a, i) => <Alert key={i} severity={SEV[a.severity]} sx={{ mb: 0.5 }}>{a.class} “{a.name}”: {a.message}</Alert>)}

      <Panel title="What-if simulation" subtitle="Reads only — nothing is written." sx={{ mt: 2 }}>
        <Stack direction="row" spacing={1}>
          <TextField size="small" label="Entity id" value={sim.entityId} onChange={(e) => setSim({ ...sim, entityId: e.target.value })} sx={{ width: 110 }} />
          <TextField size="small" label="Attribute" value={sim.property} onChange={(e) => setSim({ ...sim, property: e.target.value })} />
          <TextField size="small" label="New value" value={sim.value} onChange={(e) => setSim({ ...sim, value: e.target.value })} />
          <Button variant="contained" disabled={!sim.entityId || !sim.property} onClick={simulate}>Simulate</Button>
        </Stack>
        {res && (
          <Box sx={{ mt: 1.5 }} data-testid="twin-sim">
            <Chip color="success" size="small" label="not persisted" sx={{ mr: 1 }} />
            <Typography variant="body2" component="span">{res.summary.impactedEntities} entities affected · {res.summary.derivedChanged} derived value(s) change · {res.summary.newAlerts} new / {res.summary.clearedAlerts} cleared alert(s)</Typography>
            {res.derivedChanges.map((d, i) => <Typography key={i} variant="body2">{d.class} {d.name}: <b>{d.attribute}</b> {fmt(d.before)} → {fmt(d.after)}</Typography>)}
            {res.alertsTriggered.map((a, i) => <Alert key={i} severity="warning" sx={{ mt: 0.5 }}>Would trigger: {a.name} — {a.message}</Alert>)}
            {res.alertsCleared.map((a, i) => <Alert key={i} severity="success" sx={{ mt: 0.5 }}>Would clear: {a.name} — {a.message}</Alert>)}
          </Box>)}
      </Panel>

      <Panel title="Enterprise graphs" subtitle="Customer, supplier, financial, contract, organization, process and asset views of the same twin." sx={{ mt: 2 }}>
        <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1} sx={{ mb: 1 }}>
          {Object.entries(views).map(([v, n]) => <Chip key={v} label={`${VIEW_LABEL[v] || v} (${n})`} color={view === v ? 'primary' : 'default'} variant={view === v ? 'filled' : 'outlined'} disabled={!n} onClick={() => setView(v)} />)}
        </Stack>
        {graph && view && (graph.nodes.length ? (<><MiniGraph g={graph} /><Typography variant="caption" color="text.secondary">{graph.counts.nodes} nodes · {graph.counts.edges} relationships · bright = {VIEW_LABEL[view]}, faded = connected context</Typography></>) : <Typography variant="body2" color="text.secondary">Nothing in this view yet.</Typography>)}
        {!view && <Typography variant="body2" color="text.secondary">Pick a view. Greyed-out views have no matching classes or data in this ontology.</Typography>}
      </Panel>

      <Panel title="Entities" sx={{ mt: 2 }}>
        <Table size="small"><TableHead><TableRow><TableCell>id</TableCell><TableCell>Entity</TableCell><TableCell>Attributes</TableCell><TableCell>Derived</TableCell></TableRow></TableHead>
          <TableBody>{(snap?.entities.items || []).map((e) => (
            <TableRow key={e.id} hover sx={{ cursor: 'pointer' }} onClick={async () => setHist(await api.twinHistory(id, e.id))}>
              <TableCell>{e.id}</TableCell><TableCell>{e.class} · {e.name}{e.alerts.map((a) => <Chip key={a} size="small" color="warning" label={a} sx={{ ml: 0.5 }} />)}</TableCell>
              <TableCell><Typography variant="caption">{Object.entries(e.attributes).map(([k, v]) => `${k}=${fmt(v)}`).join(' · ')}</Typography></TableCell>
              <TableCell><Typography variant="caption">{Object.entries(e.derived).map(([k, v]) => `${k}=${fmt(v)}`).join(' · ')}</Typography></TableCell></TableRow>))}</TableBody></Table>
        {hist && (<Box sx={{ mt: 1.5 }}><Typography variant="subtitle2">History of {hist.name}</Typography>
          {hist.history.map((h, i) => <Typography key={i} variant="caption" component="div">{h.property} = {fmt(h.value)} · {new Date(h.validFrom).toLocaleString()} → {h.validTo ? new Date(h.validTo).toLocaleString() : 'now'} ({h.source})</Typography>)}</Box>)}
      </Panel>

      <Panel title="Rules (derived values & alerts)" subtitle="e.g.  creditLimit - sum(Invoice.amount)   ·   availableCredit < 20" sx={{ mt: 2 }}>
        {(snap?.rules || []).map((r) => (
          <Stack key={r.id} direction="row" spacing={1} alignItems="center"><Chip size="small" label={r.kind} /><Typography variant="body2" sx={{ flex: 1 }}>{r.class}: {r.name} — <code>{r.target ? `${r.target} = ` : ''}{r.expression}</code></Typography>
            <Button size="small" color="error" disabled={!can('write')} onClick={() => api.twinDeleteRule(id, r.id).then(load)}>Delete</Button></Stack>))}
        <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap" rowGap={1}>
          <TextField size="small" label="Name" value={rule.name} onChange={(e) => setRule({ ...rule, name: e.target.value })} />
          <TextField size="small" label="Class" value={rule.class} onChange={(e) => setRule({ ...rule, class: e.target.value })} sx={{ width: 120 }} />
          <TextField size="small" select SelectProps={{ native: true }} value={rule.kind} onChange={(e) => setRule({ ...rule, kind: e.target.value })}><option value="derive">derive</option><option value="alert">alert</option></TextField>
          {rule.kind === 'derive' && <TextField size="small" label="Target attribute" value={rule.target} onChange={(e) => setRule({ ...rule, target: e.target.value })} />}
          <TextField size="small" label="Expression" value={rule.expression} onChange={(e) => setRule({ ...rule, expression: e.target.value })} sx={{ flex: 1, minWidth: 220 }} />
          <Button variant="outlined" disabled={!can('write')} onClick={addRule}>Add rule</Button>
        </Stack>
      </Panel>
    </Box>
  );
}
