import { useEffect, useState } from 'react';
import {
  Alert, Autocomplete, Box, Button, Chip, Grid, IconButton, MenuItem, Stack, TextField, Tooltip, Typography,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import { KindChip, NoOntology, PageHeader, Panel, RoleNotice } from '../components/ui';
import { useWB } from '../context';
import { MONO, useOnt } from '../theme';

const OPS = ['read', 'create', 'update', 'delete', 'approve', 'execute'];
const DECISION = { ready: 'success', partial: 'warning', needs_approval: 'warning', blocked: 'error', unreachable: 'error', clarify: 'default' };
const EFFECT = { allow: 'success', require_approval: 'warning', deny: 'error' };

const Card = ({ children }) => {
  const { surface } = useOnt();
  return <Stack spacing={1} sx={{ mt: 1.5, p: 1.5, borderRadius: 1.5, border: 1, borderColor: surface.line, bgcolor: surface.sunken }}>{children}</Stack>;
};

export default function AgenticTab() {
  const { api, ontology, model, notify, canEdit: canWrite, loadOntology } = useWB();
  const { kind } = useOnt();
  const [reg, setReg] = useState({ tools: [], policies: [], rules: [] });
  const [request, setRequest] = useState('Find all contracts with suppliers and their products');
  const [plan, setPlan] = useState(null);
  const [ctx, setCtx] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const canEdit = canWrite && loaded; // no edits until the registry has loaded (prevents a late response clobbering them)
  const classes = model.classes.map((c) => c.name);

  useEffect(() => {
    if (!ontology) return undefined;
    let alive = true;
    setLoaded(false);
    api.agentic(ontology.id).then((r) => { if (alive) { setReg(r); setLoaded(true); } }).catch((e) => notify(e.message, 'error'));
    return () => { alive = false; };
    // eslint-disable-next-line
  }, [ontology?.id]);
  if (!ontology) return <NoOntology />;

  const setList = (key, i, patch) => setReg((r) => ({ ...r, [key]: r[key].map((x, j) => (j === i ? { ...x, ...patch } : x)) }));
  const save = async () => { try { setReg(await api.saveAgentic(ontology.id, reg)); notify('Agent registry saved to the ontology (draft).', 'success'); await loadOntology(ontology.id); } catch (e) { notify(e.message, 'error'); } };
  const run = async () => { try { setPlan(await api.agentPlan(ontology.id, request, 'agent')); setCtx(await api.agentContext(ontology.id, request)); } catch (e) { notify(e.message, 'error'); } };

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="ai" eyebrow="Agents" title="Ontology-powered agents"
        description="Bind tools, workflows, APIs and MCP servers to concepts, and govern them with ontology-level policies. Agents plan through the ontology before acting."
        actions={canEdit && <Button variant="contained" onClick={save}>Save registry</Button>} />
      <RoleNotice cap="write">You can inspect the registry and run the planner, but only an editor or higher can change or save it.</RoleNotice>
      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <Panel kind="source" title={`Tools & workflows (${reg.tools.length})`} subtitle="What agents can call, and on which concepts"
            actions={canEdit && <Button size="small" variant="outlined" onClick={() => setReg({ ...reg, tools: [...reg.tools, { name: `tool_${reg.tools.length + 1}`, type: 'api', concepts: [], operations: ['read'], endpoint: '' }] })}>+ Tool</Button>}>
            {!loaded && <Typography variant="body2" color="text.secondary">Loading registry…</Typography>}
            {loaded && reg.tools.length === 0 && <Typography variant="body2" color="text.secondary">No tools registered.{canWrite ? ' Add one with “+ Tool”.' : ''}</Typography>}
            {reg.tools.map((t, i) => (
              <Card key={i}>
                <Stack direction="row" spacing={1}>
                  <TextField size="small" label="Name" value={t.name} disabled={!canEdit} onChange={(e) => setList('tools', i, { name: e.target.value })} sx={{ flex: 1, minWidth: 0 }} />
                  <TextField select size="small" label="Type" value={t.type || 'api'} disabled={!canEdit} onChange={(e) => setList('tools', i, { type: e.target.value })} sx={{ minWidth: 110 }}>{['api', 'mcp', 'workflow', 'function'].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}</TextField>
                  {canEdit && <Tooltip title={`Remove ${t.name}`}><IconButton aria-label={`Remove tool ${t.name}`} onClick={() => setReg({ ...reg, tools: reg.tools.filter((_, j) => j !== i) })}><DeleteIcon /></IconButton></Tooltip>}
                </Stack>
                <Autocomplete multiple size="small" disabled={!canEdit} options={classes} value={t.concepts || []} onChange={(_, v) => setList('tools', i, { concepts: v })} renderInput={(p) => <TextField {...p} label="Operates on concepts" />} />
                <Autocomplete multiple size="small" disabled={!canEdit} options={OPS} value={t.operations || []} onChange={(_, v) => setList('tools', i, { operations: v })} renderInput={(p) => <TextField {...p} label="Operations" />} />
                <TextField size="small" label="Endpoint / MCP server / workflow id" value={t.endpoint || ''} disabled={!canEdit} onChange={(e) => setList('tools', i, { endpoint: e.target.value })} inputProps={{ style: { fontFamily: MONO, fontSize: 12.5 } }} />
              </Card>))}
          </Panel>
        </Grid>
        <Grid item xs={12} md={6}>
          <Panel kind="inherit" title={`Policies & business rules (${reg.policies.length})`} subtitle="What is allowed, needs approval, or is denied"
            actions={canEdit && <Button size="small" variant="outlined" onClick={() => setReg({ ...reg, policies: [...reg.policies, { name: `policy_${reg.policies.length + 1}`, effect: 'require_approval', concepts: [], operations: ['delete'], description: '' }] })}>+ Policy</Button>}>
            {!loaded && <Typography variant="body2" color="text.secondary">Loading registry…</Typography>}
            {loaded && reg.policies.length === 0 && <Typography variant="body2" color="text.secondary">No policies defined.{canWrite ? ' Add one with “+ Policy”.' : ''}</Typography>}
            {reg.policies.map((p, i) => (
              <Card key={i}>
                <Stack direction="row" spacing={1}>
                  <TextField size="small" label="Name" value={p.name} disabled={!canEdit} onChange={(e) => setList('policies', i, { name: e.target.value })} sx={{ flex: 1, minWidth: 0 }} />
                  <TextField select size="small" label="Effect" value={p.effect} disabled={!canEdit} onChange={(e) => setList('policies', i, { effect: e.target.value })} sx={{ minWidth: 160 }}>{['allow', 'require_approval', 'deny'].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}</TextField>
                  {canEdit && <Tooltip title={`Remove ${p.name}`}><IconButton aria-label={`Remove policy ${p.name}`} onClick={() => setReg({ ...reg, policies: reg.policies.filter((_, j) => j !== i) })}><DeleteIcon /></IconButton></Tooltip>}
                </Stack>
                <Autocomplete multiple size="small" disabled={!canEdit} options={classes} value={p.concepts || []} onChange={(_, v) => setList('policies', i, { concepts: v })} renderInput={(x) => <TextField {...x} label="Applies to concepts (and subclasses)" />} />
                <Autocomplete multiple size="small" disabled={!canEdit} options={OPS} value={p.operations || []} onChange={(_, v) => setList('policies', i, { operations: v })} renderInput={(x) => <TextField {...x} label="Operations" />} />
                <TextField size="small" label="Description" value={p.description || ''} disabled={!canEdit} onChange={(e) => setList('policies', i, { description: e.target.value })} />
                <Box><Chip size="small" color={EFFECT[p.effect]} variant="outlined" label={`effect: ${p.effect.replace('_', ' ')}`} /></Box>
              </Card>))}
          </Panel>
        </Grid>
      </Grid>
      <Panel kind="ai" title="Semantic planner" subtitle="Plans a request through the ontology, tools and policies — it does not execute anything" sx={{ mt: 3 }}>
        <Stack direction="row" spacing={1}>
          <TextField fullWidth size="small" value={request} onChange={(e) => setRequest(e.target.value)} label="Agent request" data-testid="plan-input" />
          <Button variant="contained" onClick={run}>Plan</Button>
        </Stack>
        {plan && (
          <Box sx={{ mt: 2 }} data-testid="plan">
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" rowGap={0.5}><Chip color={DECISION[plan.decision]} label={plan.decision.replace('_', ' ')} />{plan.explanation && <Typography variant="body2">{plan.explanation}</Typography>}</Stack>
            {plan.message && <Alert severity="warning" sx={{ mt: 1 }}>{plan.message}</Alert>}
            {plan.policy?.matched.map((m) => <Alert key={m.policy} severity={m.effect === 'deny' ? 'error' : 'warning'} sx={{ mt: 1 }}><strong>{m.policy}</strong> ({m.effect}) {m.description}</Alert>)}
            {plan.unboundConcepts?.length > 0 && <Alert severity="info" sx={{ mt: 1 }}>No tool bound for: {plan.unboundConcepts.join(', ')}</Alert>}
            <Box component="ol" sx={{ pl: 0, listStyle: 'none', mt: 1.5 }}>
              {plan.steps.map((s) => (
                <Stack component="li" key={s.step} direction="row" spacing={1} alignItems="center" flexWrap="wrap" rowGap={0.5} sx={{ mb: 0.75 }}>
                  <Typography variant="body2" sx={{ fontFamily: MONO, color: 'text.secondary', minWidth: 20 }}>{s.step}.</Typography>
                  <Typography variant="body2"><strong>{s.operation}</strong></Typography>
                  <KindChip kind="class" label={s.concept} />
                  {s.via && <Typography variant="caption" color="text.secondary">via {s.via.relationship}{s.via.inverse ? ' (inverse)' : ''} from {s.via.from}</Typography>}
                  <ArrowForwardIcon sx={{ fontSize: 16, color: kind.object }} />
                  {s.tool ? <Chip size="small" color="primary" variant="outlined" label={`${s.tool.type}: ${s.tool.name}`} /> : <Chip size="small" variant="outlined" label="unbound" />}
                </Stack>
              ))}
            </Box>
            {ctx && <details style={{ marginTop: 12 }}><summary>Semantic context pack for the LLM/agent</summary><pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', fontFamily: MONO }}>{ctx.text}</pre></details>}
          </Box>
        )}
      </Panel>
    </Box>
  );
}
