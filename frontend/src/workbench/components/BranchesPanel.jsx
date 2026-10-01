import { useCallback, useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Paper, Radio, RadioGroup, Stack, Table,
  TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import CallSplitIcon from '@mui/icons-material/CallSplit';
import MergeTypeIcon from '@mui/icons-material/MergeType';
import { DiffLines, Panel, StatusChip, Why, needs } from './ui';
import { useWB } from '../context';
import { MONO, useOnt } from '../theme';

const show = (v) => (v === null || v === undefined ? '(none)' : typeof v === 'string' ? v : JSON.stringify(v));

/** Branches of the open ontology: create, open, and merge back with a conflict-by-conflict decision. */
export default function BranchesPanel({ versions }) {
  const { api, ontology, notify, dirty, can, loadOntology, refreshList } = useWB();
  const { surface, kind } = useOnt();
  const [rows, setRows] = useState([]);
  const [root, setRoot] = useState(null);
  const [name, setName] = useState('');
  const [fromVersion, setFromVersion] = useState('');
  const [merge, setMerge] = useState(null); // {source, preview, resolutions, busy}

  const load = useCallback(async () => {
    const r = await api.branches(ontology.id);
    setRows(r.branches);
    setRoot(r.root);
  }, [api, ontology.id]);
  useEffect(() => { load().catch((e) => notify(e.message, 'error')); /* eslint-disable-next-line */ }, [ontology.id, ontology.modelRevision]);

  const create = async () => {
    try {
      const b = await api.createBranch(ontology.id, name.trim(), fromVersion);
      notify(`Branch “${b.branchName}” created. You are still on “${ontology.name}”.`, 'success');
      setName('');
      await load();
      refreshList();
    } catch (e) { notify(e.message, 'error'); }
  };
  const open = (id) => {
    if (dirty && !window.confirm('Discard unsaved changes and switch ontology?')) return;
    loadOntology(id);
  };
  const preview = async (row, resolutions = {}) => {
    try {
      const p = await api.mergeBranch(ontology.id, row.id, { dryRun: true, resolutions });
      setMerge({ source: row, preview: p, resolutions });
    } catch (e) { notify(e.message, 'error'); }
  };
  const apply = async (allowErrors = false) => {
    setMerge((m) => ({ ...m, busy: true }));
    try {
      const r = await api.mergeBranch(ontology.id, merge.source.id, { dryRun: false, resolutions: merge.resolutions, allowErrors });
      setMerge(null);
      notify(`Merged “${merge.source.branchName || merge.source.name}” into “${ontology.name}” (${r.changes.length} change${r.changes.length === 1 ? '' : 's'}). It is a draft until you commit and publish.`, 'success');
      await loadOntology(ontology.id);
      load();
    } catch (e) {
      setMerge((m) => ({ ...m, busy: false }));
      if (e.status === 409 && e.data?.code === 'merge-introduces-errors') {
        if (window.confirm(`${e.message}\n\nMerge anyway?`)) apply(true);
      } else notify(e.message, 'error');
    }
  };
  // Show the choice immediately (the radio is controlled), then refresh the preview from the server.
  const resolve = (id, choice) => {
    const resolutions = { ...merge.resolutions, [id]: choice };
    setMerge((m) => ({ ...m, resolutions }));
    preview(merge.source, resolutions);
  };
  const unresolved = merge ? merge.preview.conflicts.length : 0;
  const isRootCurrent = root === ontology.id;

  return (
    <Box data-testid="branches-panel">
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, maxWidth: 760 }}>
        A branch is a full copy of this ontology you can change freely — its own history and review workflow — without touching the original. When it is ready, <strong>merge</strong> it back: changes made on only one side are combined automatically; if both sides changed the same thing differently you decide.
      </Typography>
      <Panel kind="version" title="Create a branch" subtitle={`Forks “${ontology.name}” as it is now, or from a committed version`} sx={{ mb: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" rowGap={1}>
          <TextField size="small" label="Branch name" value={name} onChange={(e) => setName(e.target.value)} inputProps={{ 'aria-label': 'Branch name' }} />
          <TextField select size="small" label="Fork from" value={fromVersion} onChange={(e) => setFromVersion(e.target.value)} sx={{ minWidth: 190 }}>
            <MenuItem value="">Current working copy</MenuItem>
            {versions.map((v) => <MenuItem key={v.number} value={v.number}>v{v.number} — {v.message || v.status}</MenuItem>)}
          </TextField>
          <Why reason={needs(can, 'write') || (dirty && !fromVersion ? 'Save your unsaved edits first — a branch forks the saved ontology.' : '')}>
            <Button variant="contained" startIcon={<CallSplitIcon />} disabled={!can('write') || !name.trim() || (dirty && !fromVersion)} onClick={create}>Create branch</Button>
          </Why>
        </Stack>
      </Panel>
      <Paper variant="outlined" sx={{ overflow: 'auto', bgcolor: surface.panel }}>
        <Table size="small" data-testid="branches-table">
          <TableHead><TableRow><TableCell>Branch</TableCell><TableCell>Status</TableCell><TableCell>Size</TableCell><TableCell>Changes since fork</TableCell><TableCell>Actions</TableCell></TableRow></TableHead>
          <TableBody>
            {rows.map((r) => {
              const current = r.id === ontology.id;
              return (
                <TableRow key={r.id} selected={current}>
                  <TableCell>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography sx={{ fontFamily: MONO, fontSize: 13 }}>{r.isRoot ? 'main' : r.branchName}</Typography>
                      {current && <Chip size="small" color="primary" label="open" />}
                      {r.mergedAt && <Chip size="small" variant="outlined" label={`merged ${new Date(r.mergedAt).toLocaleDateString()}`} />}
                    </Stack>
                    {r.isRoot && <Typography variant="caption" color="text.secondary">{r.name}</Typography>}
                  </TableCell>
                  <TableCell><StatusChip status={r.status} /></TableCell>
                  <TableCell>{r.stats.classes || 0} cls · {r.stats.objectProperties || 0} rel</TableCell>
                  <TableCell>{r.isRoot ? '—' : r.changesSinceFork}</TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={0.5} flexWrap="wrap" rowGap={0.5}>
                      {!current && <Button size="small" onClick={() => open(r.id)}>Open</Button>}
                      {!current && (
                        <Why reason={dirty ? 'Save your unsaved edits first.' : ''}>
                          <Button size="small" variant="outlined" startIcon={<MergeTypeIcon />} disabled={dirty} onClick={() => preview(r)} data-testid={`merge-${r.branchName || 'main'}`}>
                            Merge into “{ontology.branchName || 'main'}”
                          </Button>
                        </Why>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Paper>
      {!isRootCurrent && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>You are on a branch. To merge it back, open <strong>main</strong> and use “Merge into main” on this branch’s row — or merge main into this branch to bring in newer changes.</Typography>}

      <Dialog open={!!merge} onClose={() => setMerge(null)} maxWidth="md" fullWidth aria-labelledby="merge-title">
        <DialogTitle id="merge-title">Merge “{merge?.source.branchName || merge?.source.name}” into “{ontology.branchName || 'main'}”</DialogTitle>
        <DialogContent dividers>
          {merge && (
            <Stack spacing={2}>
              {unresolved > 0 ? (
                <Alert severity="warning">{merge.preview.message}</Alert>
              ) : merge.preview.changes?.length === 0 ? (
                <Alert severity="success">Nothing to merge — they already agree.</Alert>
              ) : (
                <Alert severity="info">{merge.preview.message} Health after merge: <strong>{merge.preview.validation.health}/100</strong> · {merge.preview.validation.errors} errors · {merge.preview.validation.warnings} warnings.</Alert>
              )}
              {merge.preview.conflicts.map((c) => (
                <Paper key={c.id} variant="outlined" sx={{ p: 1.5, borderColor: kind.unsaved }} data-testid="merge-conflict">
                  <Typography variant="subtitle2">{c.element}{c.field ? ` — ${c.field}` : ''} <Chip size="small" label={c.kind === 'edit-vs-delete' ? 'edited on one side, deleted on the other' : 'changed differently on both sides'} /></Typography>
                  <RadioGroup value={merge.resolutions[c.id] || ''} onChange={(e) => resolve(c.id, e.target.value)}>
                    <FormControlLabel value="ours" control={<Radio size="small" />} label={<span>Keep <strong>this ontology’s</strong> version: <code>{show(c.ours)}</code></span>} />
                    <FormControlLabel value="theirs" control={<Radio size="small" />} label={<span>Take the <strong>branch’s</strong> version: <code>{show(c.theirs)}</code></span>} />
                  </RadioGroup>
                </Paper>
              ))}
              {unresolved === 0 && merge.preview.changes?.length > 0 && (
                <Paper variant="outlined" sx={{ p: 1.5, bgcolor: surface.sunken }}>
                  <Typography variant="overline" color="text.secondary">Changes that will be applied</Typography>
                  <DiffLines lines={merge.preview.changes} />
                </Paper>
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMerge(null)}>Cancel</Button>
          <Why reason={unresolved > 0 ? `Decide all ${unresolved} conflict(s) first.` : needs(can, 'write')}>
            <Button variant="contained" color="primary" data-testid="apply-merge" disabled={!merge || unresolved > 0 || merge.busy || !can('write') || merge.preview.changes?.length === 0} onClick={() => apply(false)}>Apply merge</Button>
          </Why>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
