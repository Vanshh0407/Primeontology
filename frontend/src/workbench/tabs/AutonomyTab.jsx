import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { NoOntology, PageHeader, Panel } from '../components/ui';
import { downloadBlob, useWB } from '../context';

const ROLES = ['supervisor', 'research', 'data', 'simulation', 'action', 'custom'];
const OPS = ['read', 'create', 'update', 'delete', 'execute', 'approve'];
const csv = (s) => s.split(',').map((x) => x.trim()).filter(Boolean);

const ST = { succeeded: 'success', running: 'info', awaiting_approval: 'warning', partial: 'warning', failed: 'error', rejected: 'error', blocked: 'error', skipped: 'default', pending: 'default', cancelled: 'default' };

export default function AutonomyTab() {
  const { api, ontology, notify, can, userName } = useWB();
  const [reg, setReg] = useState(null);
  const [goal, setGoal] = useState('What if Acme credit limit goes to 200?');
  const [plan, setPlan] = useState(null);
  const [run, setRun] = useState(null);
  const [approvals, setApprovals] = useState([]);
  const [busy, setBusy] = useState(false);
  const [ed, setEd] = useState(null);
  const [mcp, setMcp] = useState({ name: '', command: 'builtin' });
  const id = ontology?.id;
  const load = useCallback(async () => {
    if (!id) return;
    const [r, a] = await Promise.all([api.agents(id), api.agentApprovals(id)]);
    setReg(r); setApprovals(a.approvals);
  }, [api, id]);
  useEffect(() => { load().catch((e) => notify(e.message, 'error')); /* eslint-disable-next-line */ }, [id]);
  if (!ontology) return <NoOntology />;
  const wrap = async (fn) => { setBusy(true); try { await fn(); await load(); } catch (e) { notify(e.message, 'error'); } finally { setBusy(false); } };
  const show = (r) => setRun(r);
  const exportWf = (fmt) => wrap(async () => {
    const out = await api.agentWorkflow(id, run ? { runId: run.id, format: fmt } : { goal, format: fmt });
    downloadBlob(new Blob([fmt === 'bpmn' ? out : JSON.stringify(out, null, 2)], { type: fmt === 'bpmn' ? 'application/xml' : 'application/json' }), fmt === 'bpmn' ? 'agent-plan.bpmn' : 'agent-plan.n8n.json');
  });
  const saveAgent = () => wrap(async () => {
    const p = { name: ed.name, role: ed.role, description: ed.description, capabilities: csv(ed.capabilities), permissions: ed.permissions, scope: csv(ed.scope), tools: csv(ed.tools), goals: csv(ed.goals), enabled: ed.enabled };
    await api.agentSave(id, ed.id, p); setEd(null); notify('Agent saved', 'success');
  });
  const decide = (a, d) => wrap(async () => { const r = await api.agentApprove(id, a.id, d, ''); show(r.run); notify(d === 'approve' ? 'Approved — run resumed' : 'Rejected', 'success'); });

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="ai" eyebrow="Autonomous Agent Platform" title="Goals in, governed actions out"
        description="A supervisor decomposes the goal, specialist agents do the work, and every step is authorised by agent permissions, your role and enterprise policy — risky ones wait for a second person." />
      <Panel title="Give the agents a goal">
        <Stack direction="row" spacing={1}>
          <TextField fullWidth size="small" value={goal} onChange={(e) => setGoal(e.target.value)} />
          <Button disabled={busy} onClick={() => wrap(async () => setPlan(await api.agentRunPlan(id, goal)))}>Plan</Button>
          <Button variant="contained" disabled={busy} onClick={() => wrap(async () => { setPlan(null); show(await api.agentExecute(id, goal)); })}>Run</Button>
          <Button disabled={busy} onClick={() => exportWf('n8n')}>Export n8n</Button>
          <Button disabled={busy} onClick={() => exportWf('bpmn')}>Export BPMN</Button>
        </Stack>
        {plan && (<Box sx={{ mt: 1 }}>{plan.notes.map((n) => <Alert key={n} severity="warning" sx={{ mb: 0.5 }}>{n}</Alert>)}
          {plan.steps.map((s) => <Typography key={s.key} variant="body2">{s.key}: <b>{s.tool}</b> → {s.agent || 'no agent'} ({s.operation}){s.dependsOn.length ? ` after ${s.dependsOn.join(', ')}` : ''} — {s.title}</Typography>)}</Box>)}
        {run && (
          <Box sx={{ mt: 1.5 }} data-testid="agent-run">
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <Chip color={ST[run.status]} label={run.status.replace('_', ' ')} /><Typography variant="caption">run #{run.id} · cost {run.cost} · {run.toolCalls} tool calls</Typography>
              {run.evaluation?.overall !== undefined && <Chip variant="outlined" size="small" label={`score ${Math.round(run.evaluation.overall * 100)}%`} />}
              {['failed', 'partial'].includes(run.status) && <Button size="small" onClick={() => wrap(async () => show(await api.agentRecover(id, run.id, 'retry')))}>Retry failed</Button>}
            </Stack>
            {run.result?.summary && <Alert severity="info" icon={false} sx={{ mb: 1 }}>{run.result.summary}</Alert>}
            {run.error && <Alert severity="error" sx={{ mb: 1 }}>{run.error}</Alert>}
            <Table size="small"><TableHead><TableRow><TableCell>Step</TableCell><TableCell>Agent</TableCell><TableCell>Tool</TableCell><TableCell>Risk / decision</TableCell><TableCell>Status</TableCell></TableRow></TableHead>
              <TableBody>{run.steps.map((s) => (
                <TableRow key={s.key}><TableCell>{s.key}</TableCell><TableCell>{s.agent || '—'}</TableCell><TableCell>{s.tool}</TableCell>
                  <TableCell>{s.decision?.risk ? `${s.decision.risk.level} · ${s.decision.decision}` : '—'}</TableCell>
                  <TableCell><Chip size="small" color={ST[s.status]} label={s.status.replace('_', ' ')} /></TableCell></TableRow>))}</TableBody></Table>
            <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>Trace: {run.trace.map((e) => e.kind).join(' → ')}</Typography>
          </Box>)}
      </Panel>

      <Panel title={`Approvals waiting (${approvals.length})`} subtitle="Four-eyes: you cannot approve what you requested." sx={{ mt: 2 }}>
        {approvals.map((a) => (
          <Stack key={a.id} direction="row" spacing={1} alignItems="center" sx={{ py: 0.5 }}>
            <Chip size="small" color="warning" label={a.risk.level} /><Typography variant="body2" sx={{ flex: 1 }}>Run #{a.run}: <b>{a.tool}</b> {JSON.stringify(a.args).slice(0, 120)} <Typography component="span" variant="caption" color="text.secondary">requested by {a.requestedBy}</Typography></Typography>
            <Button size="small" disabled={!can('review') || a.requestedBy === userName} onClick={() => decide(a, 'approve')}>Approve</Button>
            <Button size="small" color="error" disabled={!can('review') || a.requestedBy === userName} onClick={() => decide(a, 'reject')}>Reject</Button>
          </Stack>))}
        {!approvals.length && <Typography variant="body2" color="text.secondary">Nothing waiting.</Typography>}
      </Panel>

      <Panel title="Agents" sx={{ mt: 2 }} actions={<Button size="small" disabled={!can('agent_manage')} onClick={() => setEd({ name: '', role: 'custom', description: '', capabilities: '', permissions: ['read'], scope: '', tools: '', goals: '', enabled: true })}>Register agent</Button>}>
        <Table size="small"><TableHead><TableRow><TableCell>Agent</TableCell><TableCell>Role</TableCell><TableCell>Capabilities</TableCell><TableCell>May</TableCell><TableCell>Scope</TableCell><TableCell /></TableRow></TableHead>
          <TableBody>{(reg?.agents || []).map((a) => (
            <TableRow key={a.id}><TableCell>{a.name}{!a.enabled && <Chip size="small" label="disabled" sx={{ ml: 1 }} />}</TableCell><TableCell>{a.role}</TableCell>
              <TableCell><Typography variant="caption">{a.capabilities.join(', ')}</Typography></TableCell><TableCell>{(a.permissions.length ? a.permissions : ['read']).join(', ')}</TableCell><TableCell>{a.scope.join(', ') || 'all'}</TableCell><TableCell align="right"><Button size="small" disabled={!can('agent_manage')} onClick={() => setEd({ id: a.id, name: a.name, role: a.role, description: a.description, capabilities: a.capabilities.join(', '), permissions: a.permissions, scope: a.scope.join(', '), tools: a.tools.join(', '), goals: a.goals.join(', '), enabled: a.enabled })}>Edit</Button><Button size="small" color="error" disabled={!can('agent_manage')} onClick={() => wrap(() => api.agentDelete(id, a.id))}>Delete</Button></TableCell></TableRow>))}</TableBody></Table>
        {!reg?.agents?.length && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>No agents registered yet. Administrators can register them with the button above.</Typography>}
      </Panel>

      <Panel title="Tools" subtitle="Discovered tools stay pending until an administrator approves them." sx={{ mt: 2 }}>
        {(reg?.tools || []).map((t) => (
          <Stack key={t.name} direction="row" spacing={1} alignItems="center">
            <Chip size="small" label={t.kind} /><Typography variant="body2" sx={{ flex: 1 }}>{t.name} <Typography component="span" variant="caption" color="text.secondary">· {t.operation} · {t.description}</Typography></Typography>
            <Chip size="small" color={t.status === 'approved' ? 'success' : 'warning'} label={t.status} />
            {t.kind !== 'builtin' && <Button size="small" disabled={!can('agent_manage')} onClick={() => wrap(() => api.agentGovernTool(id, t.name, t.status === 'approved' ? 'disable' : 'approve'))}>{t.status === 'approved' ? 'Disable' : 'Approve'}</Button>}
          </Stack>))}
      </Panel>

      <Panel title="MCP servers" subtitle="Only commands on the server's allow-list can be started; use “builtin” for this platform's own read-only MCP server." sx={{ mt: 2 }}>
        {(reg?.mcpServers || []).map((m) => (
          <Stack key={m.id} direction="row" spacing={1} alignItems="center">
            <Typography variant="body2" sx={{ flex: 1 }}>{m.name} <Typography component="span" variant="caption" color={m.lastError ? 'error' : 'text.secondary'}>{m.lastError || (m.discoveredAt ? `discovered ${new Date(m.discoveredAt).toLocaleString()}` : 'not discovered yet')}</Typography></Typography>
            <Button size="small" disabled={!can('agent_manage') || busy} onClick={() => wrap(async () => { const r = await api.mcpDiscover(id, m.id); notify(`${r.tools.length} tool(s) found — approve them under Tools`, 'success'); })}>Discover tools</Button>
          </Stack>))}
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          <TextField size="small" label="Name" value={mcp.name} onChange={(e) => setMcp({ ...mcp, name: e.target.value })} />
          <TextField size="small" label="Command (builtin or JSON argv)" value={mcp.command} onChange={(e) => setMcp({ ...mcp, command: e.target.value })} sx={{ flex: 1 }} />
          <Button variant="outlined" disabled={!can('agent_manage') || !mcp.name} onClick={() => wrap(async () => { let c = mcp.command.trim(); if (c !== 'builtin') c = JSON.parse(c); await api.mcpRegister(id, mcp.name, c); setMcp({ name: '', command: 'builtin' }); notify('MCP server registered', 'success'); })}>Register</Button>
        </Stack>
      </Panel>

      <Dialog open={!!ed} onClose={() => setEd(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{ed?.id ? 'Edit agent' : 'Register agent'}</DialogTitle>
        <DialogContent dividers>
          {ed && (<Stack spacing={1.5} sx={{ mt: 0.5 }}>
            <Stack direction="row" spacing={1}>
              <TextField size="small" label="Name" value={ed.name} onChange={(e) => setEd({ ...ed, name: e.target.value })} sx={{ flex: 1 }} />
              <TextField select size="small" label="Role" value={ed.role} onChange={(e) => setEd({ ...ed, role: e.target.value })} sx={{ width: 160 }}>{ROLES.map((r) => <MenuItem key={r} value={r}>{r}</MenuItem>)}</TextField>
            </Stack>
            <TextField size="small" label="Description" value={ed.description} onChange={(e) => setEd({ ...ed, description: e.target.value })} />
            <TextField size="small" label="Capabilities (comma) e.g. rag.ask, twin.simulate" value={ed.capabilities} onChange={(e) => setEd({ ...ed, capabilities: e.target.value })} />
            <Box>{OPS.map((op) => <FormControlLabel key={op} control={<Checkbox size="small" checked={ed.permissions.includes(op)} onChange={(e) => setEd({ ...ed, permissions: e.target.checked ? [...ed.permissions, op] : ed.permissions.filter((x) => x !== op) })} />} label={op} />)}</Box>
            <TextField size="small" label="Ontology scope (classes, comma — empty = all)" value={ed.scope} onChange={(e) => setEd({ ...ed, scope: e.target.value })} />
            <TextField size="small" label="Extra tools (comma)" value={ed.tools} onChange={(e) => setEd({ ...ed, tools: e.target.value })} />
            <TextField size="small" label="Goals (comma)" value={ed.goals} onChange={(e) => setEd({ ...ed, goals: e.target.value })} />
            <FormControlLabel control={<Checkbox checked={ed.enabled} onChange={(e) => setEd({ ...ed, enabled: e.target.checked })} />} label="Enabled" />
          </Stack>)}
        </DialogContent>
        <DialogActions><Button onClick={() => setEd(null)}>Cancel</Button><Button variant="contained" disabled={!ed?.name} onClick={saveAgent}>Save</Button></DialogActions>
      </Dialog>
    </Box>
  );
}
