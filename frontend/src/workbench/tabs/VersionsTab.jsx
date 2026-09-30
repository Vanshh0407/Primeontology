import { useCallback, useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Paper, Stack, Tab, Table, TableBody, TableCell,
  TableHead, TableRow, Tabs, TextField, Typography,
} from '@mui/material';
import { DiffLines, NoOntology, PageHeader, Panel, RoleNotice, StatusChip, StepTrack, Why, needs } from '../components/ui';
import { downloadBlob, useWB } from '../context';
import { MONO, useOnt } from '../theme';

const NEXT = { draft: [['submit', 'Submit for review', 'submit']], review: [['approve', 'Approve', 'review'], ['reject', 'Reject', 'review']], approved: [['publish', 'Publish', 'publish']], published: [], archived: [] };
const FLOW = ['draft', 'review', 'approved', 'published'];
const FLOW_LABEL = { draft: 'Draft', review: 'In review', approved: 'Approved', published: 'Published' };

export default function VersionsTab() {
  const { api, ontology, notify, dirty, can, loadOntology, emit, userName } = useWB();
  const { surface } = useOnt();
  const [sub, setSub] = useState(0);
  const [versions, setVersions] = useState([]);
  const [audit, setAudit] = useState([]);
  const [msg, setMsg] = useState('');
  const [major, setMajor] = useState(false);
  const [diff, setDiff] = useState(null);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('working');
  const [confirm, setConfirm] = useState(null);

  const load = useCallback(async () => {
    if (!ontology) return;
    const v = await api.versions(ontology.id);
    setVersions(v.results);
    setAudit((await api.audit(ontology.id)).results);
  }, [api, ontology]);
  useEffect(() => { load().catch((e) => notify(e.message, 'error')); /* eslint-disable-next-line */ }, [ontology?.id, ontology?.status, ontology?.updatedAt]);
  useEffect(() => { if (versions.length && !from) setFrom(versions[0].number); }, [versions, from]);
  useEffect(() => { setDiff(null); }, [ontology?.id]);
  if (!ontology) return <NoOntology />;

  const act = async (fn, ok) => { try { await fn(); notify(ok, 'success'); await loadOntology(ontology.id); await load(); } catch (e) { notify(e.message, 'error'); } };
  const showDiff = async () => { try { setDiff(await api.diff(ontology.id, from, to)); } catch (e) { notify(e.message, 'error'); } };

  const at = FLOW.indexOf(ontology.status);
  // The server enforces the four-eyes rule; the UI mirrors it so the control explains itself instead of failing after the click.
  const fourEyes = (v, a) => (a === 'approve' || a === 'reject') && !!userName && userName !== 'anonymous' && v.createdBy === userName;
  const blockedReason = (v, a, cap) => needs(can, cap) || (fourEyes(v, a) ? 'Four-eyes rule: you authored this version, so another reviewer must approve or reject it.' : '');

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="version" eyebrow="Versions & Governance" title={`Release workflow for “${ontology.name}”`}
        description="Commit a snapshot, submit it for review, approve, then publish. Every step is recorded in the audit trail." />
      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" rowGap={1} sx={{ mb: 1.5 }}>
        <Typography variant="subtitle1" component="h3">Lifecycle</Typography>
        <StatusChip status={ontology.status} prefix="working copy: " />
        {ontology.currentVersion && <Chip color="success" variant="outlined" size="small" label={`published v${ontology.currentVersion}`} />}
        {dirty && <Chip color="warning" size="small" label="unsaved edits — save before committing" />}
      </Stack>
      <Box sx={{ mb: 2.5 }}>
        {at >= 0
          ? <StepTrack steps={FLOW.map((s, i) => ({ label: FLOW_LABEL[s], state: i < at ? 'done' : i === at ? 'current' : 'todo' }))} />
          : <Typography variant="body2" color="text.secondary">Working copy is {ontology.status}.</Typography>}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.75 }}>Reviewers approve or reject; admins publish. Editing a published ontology reopens it as a draft.</Typography>
      </Box>
      <RoleNotice cap="commit">You can read the history, diffs and audit trail, but not commit or move versions through review.</RoleNotice>
      <Panel kind="version" title="Commit a version" subtitle="Snapshot the saved working copy" sx={{ mb: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" rowGap={1}>
          <TextField size="small" label="Commit message" value={msg} onChange={(e) => setMsg(e.target.value)} sx={{ minWidth: { sm: 320 }, flex: { xs: 1, sm: 'none' } }} />
          <TextField select size="small" value={major ? 'major' : 'minor'} onChange={(e) => setMajor(e.target.value === 'major')} inputProps={{ 'aria-label': 'Version increment' }}><MenuItem value="minor">Minor (x.y+1)</MenuItem><MenuItem value="major">Major (x+1.0)</MenuItem></TextField>
          <Why reason={needs(can, 'commit') || (dirty ? 'Save your unsaved edits first — a commit snapshots the saved ontology.' : '')}>
            <Button variant="contained" disabled={!can('commit') || dirty} onClick={() => act(() => api.commitVersion(ontology.id, msg, major).then(() => setMsg('')), 'Version committed.')}>Commit version</Button>
          </Why>
        </Stack>
      </Panel>
      <Tabs value={sub} onChange={(_, v) => setSub(v)} sx={{ mb: 1.5, borderBottom: 1, borderColor: surface.line }}><Tab label={`Versions (${versions.length})`} /><Tab label="Diff" /><Tab label={`Audit trail (${audit.length})`} /></Tabs>
      {sub === 0 && (
        <Paper variant="outlined" sx={{ overflow: 'auto', bgcolor: surface.panel }}>
          {versions.length === 0 && <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>No versions yet. Save your edits, then commit the first version above.</Typography>}
          {versions.length > 0 && (
            <Table size="small" data-testid="versions-table">
              <TableHead><TableRow><TableCell>Version</TableCell><TableCell>Status</TableCell><TableCell>Message</TableCell><TableCell>Author / reviewer</TableCell><TableCell>Size</TableCell><TableCell>Actions</TableCell></TableRow></TableHead>
              <TableBody>{versions.map((v) => (
                <TableRow key={v.number}>
                  <TableCell sx={{ fontFamily: MONO }}>v{v.number}</TableCell><TableCell><StatusChip status={v.status} /></TableCell>
                  <TableCell>{v.message}</TableCell><TableCell>{v.createdBy}{v.reviewedBy ? ` / ${v.reviewedBy}` : ''}</TableCell>
                  <TableCell>{v.stats.classes} cls · {v.stats.objectProperties} rel</TableCell>
                  <TableCell>
                    <Stack direction="row" flexWrap="wrap" rowGap={0.5} alignItems="center">
                      {NEXT[v.status].map(([a, label, cap]) => {
                        const why = blockedReason(v, a, cap);
                        return <Why key={a} reason={why}><Button size="small" variant="outlined" disabled={!!why} onClick={() => act(() => api.versionAction(ontology.id, v.number, a).then(() => a === 'publish' && emit('ontology.published', { version: v.number })), `${label}: done.`)}>{label}</Button></Why>;
                      })}
                      <Button size="small" onClick={() => { setFrom(v.number); setTo('working'); setSub(1); }}>Diff</Button>
                      <Button size="small" onClick={() => api.download(ontology.id, 'turtle', v.number).then(({ blob, filename }) => downloadBlob(blob, filename))}>Export</Button>
                      <Why reason={needs(can, 'rollback')}><Button size="small" color="warning" disabled={!can('rollback')} onClick={() => setConfirm(v)}>Rollback</Button></Why>
                    </Stack>
                    {v.status === 'review' && NEXT[v.status].some(([a]) => fourEyes(v, a)) && <Typography variant="caption" color="text.secondary" display="block" data-testid="four-eyes-note">Four-eyes: you authored this version, so another reviewer must approve or reject it.</Typography>}
                  </TableCell>
                </TableRow>))}
              </TableBody>
            </Table>
          )}
        </Paper>
      )}
      {sub === 1 && (
        <>
          <Stack direction="row" spacing={1} sx={{ mb: 2 }} flexWrap="wrap" rowGap={1}>
            <TextField select size="small" label="From" value={from} onChange={(e) => setFrom(e.target.value)} sx={{ minWidth: 140 }}>{versions.map((v) => <MenuItem key={v.number} value={v.number}>v{v.number}</MenuItem>)}</TextField>
            <TextField select size="small" label="To" value={to} onChange={(e) => setTo(e.target.value)} sx={{ minWidth: 160 }}><MenuItem value="working">Working copy</MenuItem>{versions.map((v) => <MenuItem key={v.number} value={v.number}>v{v.number}</MenuItem>)}</TextField>
            <Button variant="contained" onClick={showDiff} disabled={!from}>Compare</Button>
          </Stack>
          {!diff && <Typography variant="body2" color="text.secondary">Pick two versions (or the working copy) and compare. + added · − removed · ~ changed.</Typography>}
          {diff && (diff.lines.length === 0 ? <Alert severity="success">No differences.</Alert> : (
            <Paper variant="outlined" sx={{ p: 2, bgcolor: surface.sunken }} data-testid="diff">
              <Typography variant="overline" color="text.secondary">v{diff.from} → {diff.to === 'working' ? 'working copy' : `v${diff.to}`}</Typography>
              <DiffLines lines={diff.lines} />
            </Paper>))}
        </>
      )}
      {sub === 2 && (
        <Paper variant="outlined" sx={{ overflow: 'auto', bgcolor: surface.panel }}><Table size="small"><TableHead><TableRow><TableCell>When</TableCell><TableCell>Who</TableCell><TableCell>Action</TableCell><TableCell>Detail</TableCell></TableRow></TableHead>
          <TableBody>{audit.map((e) => <TableRow key={e.id}><TableCell sx={{ whiteSpace: 'nowrap' }}>{new Date(e.at).toLocaleString()}</TableCell><TableCell>{e.actor}</TableCell><TableCell sx={{ fontFamily: MONO, fontSize: 12 }}>{e.action}</TableCell><TableCell sx={{ fontSize: 12, maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis' }}>{Object.entries(e.detail).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v).slice(0, 80) : v}`).join(' · ')}</TableCell></TableRow>)}</TableBody></Table></Paper>
      )}
      <Dialog open={!!confirm} onClose={() => setConfirm(null)}>
        <DialogTitle>Roll back to v{confirm?.number}?</DialogTitle>
        <DialogContent>The working copy is replaced by that snapshot and committed as a new draft version. Published versions are not deleted.</DialogContent>
        <DialogActions><Button onClick={() => setConfirm(null)}>Cancel</Button><Button color="warning" variant="contained" onClick={() => { const v = confirm; setConfirm(null); act(() => api.versionAction(ontology.id, v.number, 'rollback'), `Rolled back to v${v.number}.`); }}>Roll back</Button></DialogActions>
      </Dialog>
    </Box>
  );
}
