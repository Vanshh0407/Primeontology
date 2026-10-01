import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { NoOntology, PageHeader, Panel, RoleNotice } from '../components/ui';
import { downloadBlob, useWB } from '../context';
import { MONO } from '../theme';

const FRESH = { FRESH: 'success', STALE: 'warning', VERY_STALE: 'error', NEVER_SYNCED: 'default' };
const SPEC = {
  file: { config: '{"inline": {}}', mapping: '{"entities": [{"class": "Customer", "id": "id", "fields": {"name": "customerName"}, "identity": [{"keys": ["email"], "normalize": "email"}]}]}' },
};

export default function FabricTab() {
  const { api, ontology, notify, can } = useWB();
  const [snap, setSnap] = useState(null);
  const [sugg, setSugg] = useState([]);
  const [drift, setDrift] = useState([]);
  const [ents, setEnts] = useState({ total: 0, entities: [] });
  const [q, setQ] = useState('');
  const [detail, setDetail] = useState(null);
  const [edit, setEdit] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sparql, setSparql] = useState('SELECT ?c ?label WHERE { ?c a onto:Customer ; rdfs:label ?label } LIMIT 20');
  const [sres, setSres] = useState(null);
  const id = ontology?.id;

  const load = useCallback(async () => {
    if (!id) return;
    const [s, g, d, e] = await Promise.all([api.fabric(id), api.fabricSuggestions(id), api.fabricDrift(id), api.fabricEntities(id, { q, limit: 25 })]);
    setSnap(s); setSugg(g.suggestions); setDrift(d.drift); setEnts(e);
  }, [api, id, q]);
  useEffect(() => { load().catch((e) => notify(e.message, 'error')); /* eslint-disable-next-line */ }, [id, q]);
  if (!ontology) return <NoOntology />;
  const run = async (fn, ok) => { setBusy(true); try { const r = await fn(); if (ok) notify(typeof ok === 'function' ? ok(r) : ok, 'success'); await load(); } catch (e) { notify(e.message, 'error'); } finally { setBusy(false); } };

  const save = () => run(async () => {
    const p = { name: edit.name, kind: edit.kind, priority: Number(edit.priority), freshnessSlaMinutes: Number(edit.sla), syncIntervalMinutes: Number(edit.interval),
      config: JSON.parse(edit.config || '{}'), mapping: JSON.parse(edit.mapping || '{}') };
    if (edit.creds) p.credentials = JSON.parse(edit.creds);
    await api.fabricSaveSource(id, edit.id, p); setEdit(null);
  }, 'Source saved');

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="source" eyebrow="Enterprise Knowledge Fabric" title="Connected systems → one semantic graph"
        description="Register SAP, Salesforce, Odoo, databases, APIs and files. Records are resolved into one entity per real-world thing, with provenance, freshness and lineage."
        actions={<Button variant="contained" disabled={busy || !can('fabric_sync')} onClick={() => run(() => api.fabricSyncAll(id), (r) => `Synced ${r.runs.length} source(s)`)}>Sync all</Button>} />
      <RoleNotice cap="fabric_manage">Registering sources and credentials needs an administrator; syncing needs an editor.</RoleNotice>
      {snap && (
        <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1} sx={{ mb: 2 }}>
          <Chip color={FRESH[snap.freshness]} label={`freshness: ${snap.freshness}`} />
          <Chip variant="outlined" label={`${snap.entities.total} entities`} />
          <Chip variant="outlined" label={`${snap.entities.linkedAcrossSources} linked across systems`} />
          <Chip variant="outlined" label={`${snap.records} source records`} />
          {snap.pendingSuggestions > 0 && <Chip color="warning" label={`${snap.pendingSuggestions} match suggestions`} />}
          {snap.pendingDrift > 0 && <Chip color="warning" label={`${snap.pendingDrift} schema changes`} />}
        </Stack>
      )}
      <Panel title="Sources" actions={<Button size="small" disabled={!can('fabric_manage')} onClick={() => setEdit({ name: '', kind: 'file', priority: 50, sla: 1440, interval: 0, config: SPEC.file.config, mapping: SPEC.file.mapping, creds: '' })}>Add source</Button>}>
        <Table size="small"><TableHead><TableRow><TableCell>Name</TableCell><TableCell>Kind</TableCell><TableCell>Freshness</TableCell><TableCell>Records</TableCell><TableCell>Last result</TableCell><TableCell align="right">Actions</TableCell></TableRow></TableHead>
          <TableBody>
            {(snap?.sources || []).map((s) => (
              <TableRow key={s.id}>
                <TableCell>{s.name}{s.hasCredentials && <Chip size="small" sx={{ ml: 1 }} label="credentials stored" />}</TableCell>
                <TableCell>{s.kind}</TableCell>
                <TableCell><Chip size="small" color={FRESH[s.freshness.status]} label={s.freshness.status} /></TableCell>
                <TableCell>{s.records}</TableCell>
                <TableCell sx={{ maxWidth: 260 }}><Typography variant="caption" color={s.freshness.lastError ? 'error' : 'text.secondary'}>{s.freshness.lastError || s.freshness.lastStatus || '—'}</Typography></TableCell>
                <TableCell align="right">
                  <Button size="small" disabled={busy || !can('fabric_sync')} onClick={() => run(() => api.fabricSync(id, s.id), (r) => `${r.status}: ${r.stats.new || 0} new, ${r.stats.changed || 0} changed`)}>Sync</Button>
                  <Button size="small" disabled={busy || !can('fabric_manage')} onClick={() => run(() => api.fabricTest(id, s.id), (r) => (r.ok ? `OK — ${r.detail}` : r.error))}>Test</Button>
                  <Button size="small" disabled={!can('fabric_manage')} onClick={() => setEdit({ id: s.id, name: s.name, kind: s.kind, priority: s.priority, sla: s.freshnessSlaMinutes, interval: s.syncIntervalMinutes, config: JSON.stringify(s.config, null, 1), mapping: JSON.stringify(s.mapping, null, 1), creds: '' })}>Edit</Button>
                  <Button size="small" color="error" disabled={!can('fabric_manage')} onClick={() => run(() => api.fabricDeleteSource(id, s.id), 'Source removed')}>Remove</Button>
                </TableCell>
              </TableRow>))}
            {!snap?.sources?.length && <TableRow><TableCell colSpan={6}><Typography variant="body2" color="text.secondary">No sources yet.</Typography></TableCell></TableRow>}
          </TableBody></Table>
      </Panel>

      {sugg.length > 0 && (
        <Panel title="Possible duplicates" subtitle="Never merged automatically — a reviewer decides." sx={{ mt: 2 }}>
          {sugg.map((s) => (
            <Stack key={s.id} direction="row" spacing={1} alignItems="center" sx={{ py: 0.5 }}>
              <Chip size="small" label={`${Math.round(s.score * 100)}%`} /><Typography variant="body2" sx={{ flex: 1 }}>{s.a.name} ↔ {s.b.name} <Typography component="span" variant="caption" color="text.secondary">({s.reasons.join(', ')})</Typography></Typography>
              <Button size="small" disabled={!can('fabric_decide')} onClick={() => run(() => api.fabricDecide(id, s.id, 'accept'), 'Merged')}>Same</Button>
              <Button size="small" color="inherit" disabled={!can('fabric_decide')} onClick={() => run(() => api.fabricDecide(id, s.id, 'reject'), 'Kept separate')}>Different</Button>
            </Stack>))}
        </Panel>)}

      {drift.length > 0 && (
        <Panel title="Schema changes detected" subtitle="A source now delivers fields the ontology does not know." sx={{ mt: 2 }}>
          {drift.map((d) => (
            <Stack key={d.id} direction="row" spacing={1} alignItems="center" sx={{ py: 0.5 }}>
              <Typography variant="body2" sx={{ flex: 1 }}><b>{d.source}</b> · {d.class}.<code>{d.field}</code> ({d.type}) e.g. {d.sample.slice(0, 3).join(', ')}</Typography>
              <Button size="small" disabled={!can('write')} onClick={() => run(() => api.fabricDriftDecide(id, d.id, 'apply'), 'Property added to the draft ontology')}>Add to ontology</Button>
              <Button size="small" color="inherit" onClick={() => run(() => api.fabricDriftDecide(id, d.id, 'dismiss'))}>Ignore</Button>
            </Stack>))}
        </Panel>)}

      <Panel title={`Entities (${ents.total})`} sx={{ mt: 2 }} actions={<TextField size="small" placeholder="search" value={q} onChange={(e) => setQ(e.target.value)} />}>
        <Table size="small"><TableHead><TableRow><TableCell>Entity</TableCell><TableCell>Class</TableCell><TableCell>Systems</TableCell></TableRow></TableHead>
          <TableBody>{ents.entities.map((e) => (
            <TableRow key={e.id} hover sx={{ cursor: 'pointer' }} onClick={async () => setDetail(await api.fabricEntity(id, e.id))}>
              <TableCell>{e.name}</TableCell><TableCell>{e.class}</TableCell><TableCell>{e.recordCount}</TableCell></TableRow>))}</TableBody></Table>
      </Panel>

      <Panel title="Query the fabric graph (SPARQL)" sx={{ mt: 2 }} actions={<Button size="small" onClick={() => api.fabricCypher(id).then((t) => downloadBlob(new Blob([t], { type: 'text/plain' }), 'fabric.cypher')).catch((e) => notify(e.message, 'error'))}>Export for Neo4j (Cypher)</Button>}>
        <TextField multiline minRows={2} fullWidth value={sparql} onChange={(e) => setSparql(e.target.value)} inputProps={{ style: { fontFamily: MONO, fontSize: 12 } }} />
        <Button sx={{ mt: 1 }} size="small" onClick={() => api.fabricSparql(id, sparql).then(setSres).catch((e) => notify(e.message, 'error'))}>Run</Button>
        {sres && <Typography component="pre" variant="caption" sx={{ fontFamily: MONO, mt: 1, maxHeight: 200, overflow: 'auto' }}>{[sres.columns.join(' | '), ...sres.rows.map((r) => r.join(' | '))].join('\n')}</Typography>}
      </Panel>

      <Dialog open={!!detail} onClose={() => setDetail(null)} maxWidth="md" fullWidth>
        <DialogTitle>{detail?.name} <Chip size="small" label={detail?.class} /></DialogTitle>
        <DialogContent dividers>
          {detail && (<>
            <Typography variant="subtitle2">Golden record (value ← system)</Typography>
            <Table size="small"><TableBody>{Object.entries(detail.canonical).map(([k, v]) => (
              <TableRow key={k}><TableCell>{k}</TableCell><TableCell>{String(v)}</TableCell><TableCell><Chip size="small" variant="outlined" label={detail.provenance[k]?.source || '—'} /></TableCell></TableRow>))}</TableBody></Table>
            {detail.conflicts.length > 0 && <Alert severity="warning" sx={{ my: 1 }}>{detail.conflicts.map((c) => `${c.property}: ${c.values.map((v) => `${v.source}=${v.value}`).join(' vs ')} → kept ${c.chosen}`).join(' · ')}</Alert>}
            <Typography variant="subtitle2" sx={{ mt: 2 }}>Source records</Typography>
            {detail.records.map((r) => <Typography key={r.id} variant="body2">{r.source} · {r.externalId} <Typography component="span" variant="caption" color="text.secondary">{JSON.stringify(r.linkReason)}</Typography></Typography>)}
            <Typography variant="subtitle2" sx={{ mt: 2 }}>Related entities</Typography>
            {[...detail.neighbours.outgoing.map((n) => `→ ${n.property} ${n.name}`), ...detail.neighbours.incoming.map((n) => `← ${n.name} ${n.property}`)].map((t) => <Typography key={t} variant="body2">{t}</Typography>)}
          </>)}
        </DialogContent>
        <DialogActions><Button onClick={() => setDetail(null)}>Close</Button></DialogActions>
      </Dialog>

      <Dialog open={!!edit} onClose={() => setEdit(null)} maxWidth="md" fullWidth>
        <DialogTitle>{edit?.id ? 'Edit source' : 'Add source'}</DialogTitle>
        <DialogContent dividers>
          {edit && (<Stack spacing={1.5} sx={{ mt: 0.5 }}>
            <Stack direction="row" spacing={1}>
              <TextField label="Name" size="small" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} sx={{ flex: 1 }} />
              <TextField select label="Kind" size="small" value={edit.kind} disabled={!!edit.id} onChange={(e) => setEdit({ ...edit, kind: e.target.value })} sx={{ width: 180 }}>
                {(snap?.kinds || []).map((k) => <MenuItem key={k.kind} value={k.kind}>{k.label}</MenuItem>)}</TextField>
            </Stack>
            <Stack direction="row" spacing={1}>
              <TextField label="Priority (higher wins conflicts)" size="small" type="number" value={edit.priority} onChange={(e) => setEdit({ ...edit, priority: e.target.value })} />
              <TextField label="Freshness SLA (min)" size="small" type="number" value={edit.sla} onChange={(e) => setEdit({ ...edit, sla: e.target.value })} />
              <TextField label="Auto-sync every (min, 0=manual)" size="small" type="number" value={edit.interval} onChange={(e) => setEdit({ ...edit, interval: e.target.value })} />
            </Stack>
            {edit.kind === 'push' && <Alert severity="info">Systems send records to <code>POST …/fabric/sources/{'{id}'}/push/</code> with <code>{'{"class": "Customer", "records": [...]}'}</code> (editor role or a registered host's service token).</Alert>}
            {edit.kind === 'datalake' && <Alert severity="info">Config: <code>{'{"root": "lake"}'}</code> (a root the administrator configured). In the mapping use <code>"path": "sales/*.parquet"</code>.</Alert>}
            <TextField label="Connection config (JSON)" multiline minRows={2} value={edit.config} onChange={(e) => setEdit({ ...edit, config: e.target.value })} inputProps={{ style: { fontFamily: MONO, fontSize: 12 } }} />
            <TextField label={edit.id ? 'Credentials (JSON — leave empty to keep stored ones)' : 'Credentials (JSON, encrypted at rest, never shown again)'} multiline minRows={2} value={edit.creds}
              onChange={(e) => setEdit({ ...edit, creds: e.target.value })} inputProps={{ style: { fontFamily: MONO, fontSize: 12 } }} />
            <TextField label="Mapping (JSON)" multiline minRows={6} value={edit.mapping} onChange={(e) => setEdit({ ...edit, mapping: e.target.value })} inputProps={{ style: { fontFamily: MONO, fontSize: 12 } }} />
          </Stack>)}
        </DialogContent>
        <DialogActions><Button onClick={() => setEdit(null)}>Cancel</Button><Button variant="contained" onClick={save}>Save</Button></DialogActions>
      </Dialog>
    </Box>
  );
}
