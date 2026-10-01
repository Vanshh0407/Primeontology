import { useCallback, useEffect, useState } from 'react';
import { Box, Button, Chip, MenuItem, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { NoOntology, PageHeader, Panel } from '../components/ui';
import { useWB } from '../context';

const HC = { ok: 'success', attention: 'warning', empty: 'default' };
const EFFECT = { allow: 'success', require_approval: 'warning', deny: 'error' };

export default function OsTab() {
  const { api, ontology, notify, can } = useWB();
  const [snap, setSnap] = useState(null);
  const [policies, setPolicies] = useState([]);
  const [audit, setAudit] = useState([]);
  const [ask, setAsk] = useState('');
  const [answer, setAnswer] = useState(null);
  const [pol, setPol] = useState({ name: '', effect: 'require_approval', operations: 'update', concepts: '' });
  const [ev, setEv] = useState({ operation: 'update', concepts: '' });
  const [evr, setEvr] = useState(null);
  const id = ontology?.id;
  const qs = `?ontologyId=${id}`;
  const load = useCallback(async () => {
    if (!id) return;
    const [s, p, a] = await Promise.all([api.os(`snapshot/${qs}`), api.os(`policies/${qs}`), api.os(`audit/${qs}&limit=15`)]);
    setSnap(s); setPolicies(p.policies); setAudit(a.events);
  }, [api, id, qs]);
  useEffect(() => { load().catch((e) => notify(e.message, 'error')); /* eslint-disable-next-line */ }, [id]);
  if (!ontology) return <NoOntology />;
  const list = (s) => s.split(',').map((x) => x.trim()).filter(Boolean);
  const addPolicy = () => api.os('policies/', 'POST', { ontologyId: id, name: pol.name, effect: pol.effect, operations: list(pol.operations), concepts: list(pol.concepts) })
    .then(() => { notify('Policy added', 'success'); load(); }).catch((e) => notify(e.message, 'error'));

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="version" eyebrow="Semantic Enterprise OS" title="One control plane for every semantic capability"
        description="Health of the ontology, fabric, RAG, twin and agents; enterprise policies; and an audit trail of what happened." />
      {snap && (<>
        <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1} sx={{ mb: 2 }}>
          {snap.health.map((h) => <Chip key={h.area} color={HC[h.status]} label={`${h.area}: ${h.detail}`} />)}
        </Stack>
        <Stack direction="row" spacing={2} flexWrap="wrap" sx={{ mb: 2 }}>
          {[['Entities', snap.fabric.entities.total], ['Documents', snap.rag.documents], ['Twin events', snap.twin.events], ['Agents', snap.agents.registered], ['Agent runs', snap.agents.runs], ['Approvals waiting', snap.agents.awaitingApproval], ['Policies', snap.policies.enabled]]
            .map(([k, v]) => <Box key={k} sx={{ minWidth: 110 }}><Typography variant="h5">{v}</Typography><Typography variant="caption" color="text.secondary">{k}</Typography></Box>)}
        </Stack></>)}

      <Panel title="Ask the enterprise" subtitle="Routed to the twin (what-if), the fabric (source status) or RAG automatically.">
        <Stack direction="row" spacing={1}><TextField fullWidth size="small" value={ask} onChange={(e) => setAsk(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && api.os('ask/', 'POST', { ontologyId: id, question: ask }).then(setAnswer).catch((x) => notify(x.message, 'error'))} />
          <Button variant="contained" disabled={!ask} onClick={() => api.os('ask/', 'POST', { ontologyId: id, question: ask }).then(setAnswer).catch((x) => notify(x.message, 'error'))}>Ask</Button></Stack>
        {answer && <Typography variant="body2" component="div" sx={{ mt: 1 }}><Chip size="small" label={`routed to ${answer.module}`} sx={{ mr: 1 }} />{answer.module === 'rag' ? answer.result.answer : JSON.stringify(answer.result).slice(0, 400)}</Typography>}
      </Panel>

      <Panel title="Enterprise policies" subtitle="Deny beats require-approval beats allow. With no matching policy, risk decides (high → approval, critical → deny)." sx={{ mt: 2 }}>
        <Table size="small"><TableHead><TableRow><TableCell>Policy</TableCell><TableCell>Effect</TableCell><TableCell>Operations</TableCell><TableCell>Concepts</TableCell><TableCell /></TableRow></TableHead>
          <TableBody>{policies.map((p) => (
            <TableRow key={p.id}><TableCell>{p.name}</TableCell><TableCell><Chip size="small" color={EFFECT[p.effect]} label={p.effect.replace('_', ' ')} /></TableCell><TableCell>{p.operations.join(', ') || 'any'}</TableCell><TableCell>{p.concepts.join(', ') || 'any'}</TableCell>
              <TableCell align="right"><Button size="small" color="error" disabled={!can('os_manage')} onClick={() => api.os(`policies/${p.id}/${qs}`, 'DELETE').then(load)}>Delete</Button></TableCell></TableRow>))}</TableBody></Table>
        <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap" rowGap={1}>
          <TextField size="small" label="Name" value={pol.name} onChange={(e) => setPol({ ...pol, name: e.target.value })} />
          <TextField size="small" select label="Effect" value={pol.effect} onChange={(e) => setPol({ ...pol, effect: e.target.value })} sx={{ width: 170 }}>{Object.keys(EFFECT).map((e) => <MenuItem key={e} value={e}>{e}</MenuItem>)}</TextField>
          <TextField size="small" label="Operations (comma)" value={pol.operations} onChange={(e) => setPol({ ...pol, operations: e.target.value })} />
          <TextField size="small" label="Concepts (comma)" value={pol.concepts} onChange={(e) => setPol({ ...pol, concepts: e.target.value })} />
          <Button variant="outlined" disabled={!can('os_manage') || !pol.name} onClick={addPolicy}>Add policy</Button>
        </Stack>
        <Stack direction="row" spacing={1} sx={{ mt: 2 }} alignItems="center">
          <Typography variant="body2">Test a policy:</Typography>
          <TextField size="small" label="Operation" value={ev.operation} onChange={(e) => setEv({ ...ev, operation: e.target.value })} sx={{ width: 120 }} />
          <TextField size="small" label="Concepts" value={ev.concepts} onChange={(e) => setEv({ ...ev, concepts: e.target.value })} />
          <Button size="small" onClick={() => api.os('evaluate/', 'POST', { ontologyId: id, operation: ev.operation, concepts: list(ev.concepts), actorType: 'agent' }).then(setEvr).catch((e) => notify(e.message, 'error'))}>Evaluate</Button>
          {evr && <Chip color={EFFECT[evr.decision]} label={`${evr.decision} · risk ${evr.risk.level}${evr.matched.length ? ` · ${evr.matched.map((m) => m.policy).join(', ')}` : ' · by risk'}`} />}
        </Stack>
      </Panel>

      <Panel title="Audit trail" sx={{ mt: 2 }}>
        {audit.map((e) => <Typography key={e.id} variant="caption" component="div">{new Date(e.at).toLocaleString()} · <b>{e.action}</b> · {e.actor} · {JSON.stringify(e.detail).slice(0, 120)}</Typography>)}
      </Panel>
    </Box>
  );
}
