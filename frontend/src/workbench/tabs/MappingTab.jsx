import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, IconButton, LinearProgress, MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Tooltip, Typography,
} from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import { KindChip, NoOntology, PageHeader, Panel, RoleNotice, Why, needs } from '../components/ui';
import { useWB } from '../context';
import { MONO, useOnt } from '../theme';

const COLORS = { HIGH: 'success', MEDIUM: 'warning', LOW: 'default' };
const STATUS_COLOR = { accepted: 'success', rejected: 'error', overridden: 'info', proposed: 'default', unmapped: 'default' };

export default function MappingTab() {
  const { api, ontology, model, notify, canEdit, can, loadOntology, emit } = useWB();
  const { surface } = useOnt();
  const [rows, setRows] = useState([]);
  const [dups, setDups] = useState([]);
  const [busy, setBusy] = useState(false);
  const [sets, setSets] = useState([]);
  const [setName, setSetName] = useState('Mapping set');
  const [manual, setManual] = useState('CRM: customer_name, cust_id\nERP: cust_nm, qty\nContract: Buyer, Seller');
  const fileRef = useRef();

  const targets = useMemo(() => [
    ...model.classes.map((c) => c.name),
    ...model.dataProperties.map((p) => `${p.domain}.${p.name}`),
    ...model.objectProperties.map((p) => `${p.domain}.${p.name}`),
  ], [model]);

  const loadSets = () => ontology && api.mappingSets(ontology.id).then((r) => setSets(r.results)).catch(() => {});
  useEffect(() => { setRows([]); setDups([]); loadSets(); /* eslint-disable-next-line */ }, [ontology?.id]);
  if (!ontology) return <NoOntology />;

  const propose = async (sources) => {
    setBusy(true);
    try {
      const r = await api.mappingPropose(ontology.id, sources);
      setRows(r.proposals);
      setDups(r.duplicates);
      notify(`${r.summary.proposed} of ${r.summary.fields} fields mapped (${r.summary.high} high confidence). Nothing is applied until you accept and commit.`, 'info');
    } catch (e) { notify(e.message, 'error'); } finally { setBusy(false); }
  };
  const fromText = () => propose(manual.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
    const [name, rest = ''] = l.split(':');
    return { name: name.trim(), fields: rest.split(',').map((f) => f.trim()).filter(Boolean).map((f) => ({ name: f })) };
  }));
  const fromFiles = async (files) => {
    setBusy(true);
    try { const r = await api.mappingExtract(ontology.id, files); await propose(r.sources); } catch (e) { notify(e.message, 'error'); setBusy(false); }
  };
  const set = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const accepted = rows.filter((r) => ['accepted', 'overridden'].includes(r.status)).length;

  const save = async () => {
    try {
      const ms = await api.saveMappingSet(ontology.id, { name: setName, mappings: rows.map(({ candidates, best, ...r }) => r) });
      notify(`Saved “${ms.name}”.`, 'success');
      loadSets();
    } catch (e) { notify(e.message, 'error'); }
  };
  const commit = async (ms) => {
    try { const r = await api.commitMappingSet(ontology.id, ms.id); notify(`Recorded ${r.committed} mapping lineage entries in the ontology (draft).`, 'success'); await loadOntology(ontology.id); emit('mapping.committed', { name: ms.name }); }
    catch (e) { notify(e.message, 'error'); }
  };

  const next = rows.length === 0 ? 'Next: list your sources and fields, then “Propose mappings”.'
    : accepted === 0 ? 'Next: review each proposal — accept, reject or override it. “Accept all HIGH” accepts only high-confidence rows.'
      : `Next: name the set and save it (${accepted} accepted). Then “Commit” on the saved set records the lineage in the ontology draft.`;

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="mapping" eyebrow="Mapping" title={`Map source fields to “${ontology.name}”`}
        description="The system proposes a target for each source field; you decide. Accepted mappings are saved as a set and only change the ontology when you commit that set." />
      <RoleNotice cap="write">You can review proposals but not accept, save or commit them.</RoleNotice>
      <Stack direction={{ xs: 'column', lg: 'row' }} spacing={2}>
        <Panel kind="mapping" title="Sources" subtitle="One per line — Name: field, field" sx={{ flex: 1 }}>
          <TextField fullWidth multiline minRows={3} size="small" label="Sources — one per line: Name: field, field" value={manual} onChange={(e) => setManual(e.target.value)} InputProps={{ sx: { fontFamily: MONO, fontSize: 12.5 } }} />
          <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" rowGap={1}>
            <Button variant="contained" disabled={busy} onClick={fromText}>Propose mappings</Button>
            <Why reason={needs(can, 'write')}><Button variant="outlined" disabled={busy || !canEdit} onClick={() => fileRef.current.click()}>From files (CSV, Excel, JSON, SQL…)</Button></Why>
            <input ref={fileRef} type="file" multiple hidden onChange={(e) => { fromFiles([...e.target.files]); e.target.value = ''; }} />
          </Stack>
          {busy && <LinearProgress sx={{ mt: 1.5 }} />}
        </Panel>
        <Panel kind="mapping" title="Saved mapping sets" sx={{ width: { lg: 340 } }}>
          {sets.length === 0 && <Typography variant="body2" color="text.secondary">None yet. Saved sets appear here, ready to commit.</Typography>}
          {sets.map((ms) => (
            <Stack key={ms.id} direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
              <Chip size="small" clickable label={`${ms.name} (${ms.mappings.filter((m) => ['accepted', 'overridden'].includes(m.status)).length}✓)`} onClick={() => setRows(ms.mappings.map((m) => ({ ...m, candidates: m.candidates || [] })))} />
              {canEdit && <Button size="small" onClick={() => commit(ms)}>Commit</Button>}
              {canEdit && <Button size="small" color="error" aria-label={`Delete mapping set ${ms.name}`} onClick={() => api.deleteMappingSet(ontology.id, ms.id).then(loadSets)}>×</Button>}
            </Stack>
          ))}
        </Panel>
      </Stack>
      <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>{next}</Alert>
      {dups.map((d) => <Alert key={d.target} severity="info" sx={{ mt: 1 }}><strong>{d.target}</strong> ← {d.fields.join(', ')} (likely the same attribute across sources)</Alert>)}
      {rows.length > 0 && (
        <Paper variant="outlined" sx={{ mt: 2, overflow: 'hidden', bgcolor: surface.panel }}>
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small" data-testid="mapping-table">
              <TableHead><TableRow><TableCell>Source field</TableCell><TableCell>Proposed ontology target</TableCell><TableCell>Confidence</TableCell><TableCell>Why</TableCell><TableCell>Decision</TableCell></TableRow></TableHead>
              <TableBody>
                {rows.map((r, i) => (
                  <TableRow key={`${r.source}.${r.field}`} sx={{ opacity: r.status === 'rejected' ? 0.5 : 1 }}>
                    <TableCell><KindChip kind="source" label={r.source} sx={{ mr: 0.75 }} /><span style={{ fontFamily: MONO }}>{r.field}</span>{r.type ? <Typography component="span" variant="caption" color="text.secondary"> ({r.type})</Typography> : null}</TableCell>
                    <TableCell>
                      <Select size="small" displayEmpty value={r.target || ''} disabled={!canEdit} sx={{ minWidth: 220 }} inputProps={{ 'aria-label': `Target for ${r.source}.${r.field}` }}
                        onChange={(e) => set(i, { target: e.target.value, status: e.target.value === r.best?.target ? 'proposed' : e.target.value ? 'overridden' : 'unmapped', score: e.target.value === r.best?.target ? r.best.score : 1 })}>
                        <MenuItem value=""><em>(unmapped)</em></MenuItem>
                        {(r.candidates || []).map((c) => <MenuItem key={c.target} value={c.target}>{c.target} · {Math.round(c.score * 100)}%</MenuItem>)}
                        {targets.filter((t) => !(r.candidates || []).some((c) => c.target === t)).map((t) => <MenuItem key={t} value={t}>{t}</MenuItem>)}
                      </Select>
                    </TableCell>
                    <TableCell>{r.best && r.target === r.best.target ? <Chip size="small" color={COLORS[r.best.confidence]} variant={r.best.confidence === 'LOW' ? 'outlined' : 'filled'} label={`${r.best.confidence} ${(r.score * 100).toFixed(1)}%`} /> : r.status === 'overridden' ? <Chip size="small" color="info" label="manual" /> : '—'}</TableCell>
                    <TableCell sx={{ fontSize: 12, minWidth: 200 }}>{r.best && r.target === r.best.target ? r.best.reasons.join('; ') : ''}</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      <Chip size="small" color={STATUS_COLOR[r.status]} variant={r.status === 'proposed' || r.status === 'unmapped' ? 'outlined' : 'filled'} label={r.status} sx={{ mr: 1 }} />
                      {canEdit && r.target && <><Tooltip title="Accept"><IconButton size="small" color="success" onClick={() => set(i, { status: r.status === 'overridden' ? 'overridden' : 'accepted' })}><CheckIcon fontSize="small" /></IconButton></Tooltip>
                        <Tooltip title="Reject"><IconButton size="small" color="error" onClick={() => set(i, { status: 'rejected' })}><CloseIcon fontSize="small" /></IconButton></Tooltip></>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
          <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" rowGap={1} sx={{ p: 2, borderTop: 1, borderColor: surface.line }}>
            <Why reason={needs(can, 'write')}><Button size="small" disabled={!canEdit} onClick={() => setRows((rs) => rs.map((r) => (r.best && r.best.confidence === 'HIGH' && r.status === 'proposed' ? { ...r, status: 'accepted' } : r)))}>Accept all HIGH</Button></Why>
            <TextField size="small" label="Mapping set name" value={setName} onChange={(e) => setSetName(e.target.value)} />
            <Why reason={needs(can, 'write')}><Button variant="contained" disabled={!canEdit || rows.length === 0} onClick={save}>Save mapping set ({accepted} accepted)</Button></Why>
          </Stack>
        </Paper>
      )}
    </Box>
  );
}
