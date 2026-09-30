import { useEffect, useState } from 'react';
import { Autocomplete, Badge, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Drawer, IconButton, Paper, Stack, TextField, Typography, useMediaQuery } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import AddIcon from '@mui/icons-material/Add';
import CloseIcon from '@mui/icons-material/Close';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import TuneIcon from '@mui/icons-material/Tune';
import OntologyGraph from '../components/OntologyGraph';
import Explorer from '../components/Explorer';
import { ClassEditor, RelationshipEditor } from '../components/Inspector';
import { EmptyState, NoOntology } from '../components/ui';
import { addClass, addProperty, setLayout, updateClass, wouldCycle } from '../modelOps';
import { useWB } from '../context';
import { useOnt } from '../theme';

/** Pane chrome: a small title row so the three regions (Explorer · Graph · Inspector) read as a hierarchy. */
function PaneTitle({ children, actions, onClose }) {
  const { surface } = useOnt();
  return (
    <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: surface.line, minHeight: 48 }}>
      <Typography variant="overline" component="h2" color="text.secondary">{children}</Typography>
      <Stack direction="row" spacing={0.5} alignItems="center">{actions}{onClose && <IconButton size="small" aria-label={`Close ${children}`} onClick={onClose}><CloseIcon fontSize="small" /></IconButton>}</Stack>
    </Stack>
  );
}

export default function WorkbenchTab() {
  const { model, edit, selection, setSelection, canEdit, ontology, role } = useWB();
  const theme = useTheme();
  const { surface } = useOnt();
  const compact = useMediaQuery(theme.breakpoints.down('lg')); // tablet/mobile: explorer + inspector become drawers
  const [drawer, setDrawer] = useState(null); // 'explorer' | 'inspector'
  const [dlg, setDlg] = useState(null); // {type:'class'} | {type:'link', from, to, mode}
  const [form, setForm] = useState({ name: '', parent: null });

  // On compact screens, selecting something opens the inspector so the choice is never invisible.
  useEffect(() => { if (compact && selection) setDrawer('inspector'); }, [compact, selection]);

  if (!ontology) return <NoOntology />;

  const submit = () => {
    if (dlg.type === 'class') {
      edit((m) => addClass(m, { name: form.name, parents: form.parent ? [form.parent] : [] }), { select: { kind: 'class', name: form.name }, after: () => setDlg(null) });
    } else if (dlg.mode === 'relationship') {
      edit((m) => addProperty(m, 'object', { name: form.name, domain: dlg.from, range: dlg.to }), { select: { kind: 'relationship', domain: dlg.from, name: form.name }, after: () => setDlg(null) });
    }
  };
  const connect = (from, to, mode) => {
    if (mode === 'inheritance') {
      if (wouldCycle(model, from, to)) return edit(() => { throw new Error(`${to} is already below ${from}: that would create a cycle.`); });
      const c = model.classes.find((x) => x.name === from);
      edit((m) => updateClass(m, from, { parents: [...new Set([...(c.parents || []), to])] }));
    } else {
      setForm({ name: `has${to}`, parent: null });
      setDlg({ type: 'link', from, to, mode });
    }
  };

  const addClassButton = canEdit && <Button size="small" variant="outlined" startIcon={<AddIcon />} onClick={() => { setForm({ name: '', parent: null }); setDlg({ type: 'class' }); }}>Class</Button>;
  const explorer = <Explorer model={model} selection={selection} onSelect={(s) => { setSelection(s); if (compact) setDrawer('inspector'); }} />;
  const inspector = (
    <Box sx={{ p: 2 }}>
      {!selection && (
        <EmptyState title="Nothing selected" sx={{ p: 2 }}>
          Select a class or relationship in the graph or explorer to inspect it.{canEdit ? ' Drag from one class’s bottom handle to another to connect them.' : ` Your role (${role}) can inspect but not edit.`}
        </EmptyState>
      )}
      {selection?.kind === 'class' && <ClassEditor key={selection.name} name={selection.name} />}
      {selection?.kind === 'relationship' && <RelationshipEditor key={`${selection.domain}.${selection.name}`} domain={selection.domain} name={selection.name} />}
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', height: '100%', minHeight: 560 }}>
      {!compact && (
        <Paper square variant="outlined" component="aside" aria-label="Explorer" sx={{ width: 264, flexShrink: 0, display: 'flex', flexDirection: 'column', borderTop: 0, borderBottom: 0, borderLeft: 0, borderColor: surface.line }}>
          <PaneTitle actions={addClassButton}>Explorer</PaneTitle>
          <Box sx={{ flex: 1, minHeight: 0 }}>{explorer}</Box>
        </Paper>
      )}
      <Box sx={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {compact && (
          <Stack direction="row" spacing={1} sx={{ p: 1, borderBottom: 1, borderColor: surface.line, bgcolor: 'background.paper' }}>
            <Button size="small" variant="outlined" startIcon={<AccountTreeIcon />} onClick={() => setDrawer('explorer')}>Explorer</Button>
            <Button size="small" variant="outlined" startIcon={<Badge color="primary" variant="dot" invisible={!selection}><TuneIcon /></Badge>} onClick={() => setDrawer('inspector')}>Inspector</Button>
            {addClassButton}
          </Stack>
        )}
        <Box sx={{ flex: 1, minHeight: 0 }}>
          <OntologyGraph model={model} selection={selection} onSelect={setSelection} canEdit={canEdit} onConnectClasses={connect}
            onMoved={(pos) => edit((m) => setLayout(m, pos), { silent: true })} />
        </Box>
      </Box>
      {!compact && (
        <Paper square variant="outlined" component="aside" aria-label="Inspector" sx={{ width: { lg: 360, xl: 400 }, flexShrink: 0, overflow: 'auto', borderTop: 0, borderBottom: 0, borderRight: 0, borderColor: surface.line }}>
          <PaneTitle>Inspector</PaneTitle>
          {inspector}
        </Paper>
      )}
      {compact && (
        <>
          <Drawer anchor="left" open={drawer === 'explorer'} onClose={() => setDrawer(null)} PaperProps={{ sx: { width: 'min(320px, 92vw)' } }}>
            <PaneTitle actions={addClassButton} onClose={() => setDrawer(null)}>Explorer</PaneTitle>
            <Box sx={{ flex: 1, minHeight: 0 }}>{explorer}</Box>
          </Drawer>
          <Drawer anchor="right" open={drawer === 'inspector'} onClose={() => setDrawer(null)} PaperProps={{ sx: { width: 'min(420px, 100vw)' } }}>
            <PaneTitle onClose={() => setDrawer(null)}>Inspector</PaneTitle>
            <Box sx={{ overflow: 'auto' }}>{inspector}</Box>
          </Drawer>
        </>
      )}
      <Dialog open={!!dlg} onClose={() => setDlg(null)} fullWidth maxWidth="xs">
        <DialogTitle>{dlg?.type === 'class' ? 'New class' : `New relationship ${dlg?.from} → ${dlg?.to}`}</DialogTitle>
        <DialogContent>
          <TextField autoFocus fullWidth margin="dense" label={dlg?.type === 'class' ? 'Class name' : 'Relationship name'} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && form.name && submit()} />
          {dlg?.type === 'class' && <Autocomplete sx={{ mt: 1 }} options={model.classes.map((c) => c.name)} value={form.parent} onChange={(_, v) => setForm({ ...form, parent: v })} renderInput={(p) => <TextField {...p} label="Parent class (optional)" />} />}
        </DialogContent>
        <DialogActions><Button onClick={() => setDlg(null)}>Cancel</Button><Button variant="contained" disabled={!form.name} onClick={submit}>Create</Button></DialogActions>
      </Dialog>
    </Box>
  );
}
