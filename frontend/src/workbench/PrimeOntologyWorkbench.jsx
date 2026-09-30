import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, CssBaseline, Dialog, DialogActions, DialogContent, DialogTitle, Divider, IconButton, ListItemText, Menu, MenuItem, Paper, Popover, Select,
  Snackbar, Stack, Tab, Tabs, TextField, ThemeProvider, Tooltip, Typography,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import SaveIcon from '@mui/icons-material/Save';
import UndoIcon from '@mui/icons-material/Undo';
import DownloadIcon from '@mui/icons-material/Download';
import AddIcon from '@mui/icons-material/Add';
import StorageIcon from '@mui/icons-material/Storage';
import HubIcon from '@mui/icons-material/Hub';
import SyncAltIcon from '@mui/icons-material/SyncAlt';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import ExploreIcon from '@mui/icons-material/Explore';
import CallSplitIcon from '@mui/icons-material/CallSplit';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import CheckIcon from '@mui/icons-material/Check';
import LockOutlinedIcon from '@mui/icons-material/LockOutlined';
import BadgeOutlinedIcon from '@mui/icons-material/BadgeOutlined';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LightModeIcon from '@mui/icons-material/LightMode';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import LogoutIcon from '@mui/icons-material/Logout';
import { CAPABILITY_LABEL, WorkbenchContext, canRole, downloadBlob, requiredRole } from './context';
import { useOnt } from './theme';
import BrandMark from './components/BrandMark';
import { StatusChip } from './components/ui';
import { createApi } from './api';
import { emptyModel } from './modelOps';
import SourcesTab from './tabs/SourcesTab';
import WorkbenchTab from './tabs/WorkbenchTab';
import MappingTab from './tabs/MappingTab';
import ValidationTab from './tabs/ValidationTab';
import KnowledgeTab from './tabs/KnowledgeTab';
import VersionsTab from './tabs/VersionsTab';
import AssistantTab from './tabs/AssistantTab';
import AgenticTab from './tabs/AgenticTab';

const TABS = [
  { id: 'sources', label: 'Import & Sources', cap: ['sources'], C: SourcesTab, Icon: StorageIcon, kind: 'source' },
  { id: 'workbench', label: 'Workbench', cap: ['workbench'], C: WorkbenchTab, Icon: HubIcon, kind: 'class' },
  { id: 'mapping', label: 'Mapping', cap: ['mapping'], C: MappingTab, Icon: SyncAltIcon, kind: 'mapping' },
  { id: 'validation', label: 'Validation & Reasoning', cap: ['validation'], C: ValidationTab, Icon: FactCheckIcon },
  { id: 'explorer', label: 'Explore & Query', cap: ['explorer', 'query'], C: KnowledgeTab, Icon: ExploreIcon, kind: 'object' },
  { id: 'versions', label: 'Versions & Governance', cap: ['versions'], C: VersionsTab, Icon: CallSplitIcon, kind: 'version' },
  { id: 'assistant', label: 'AI Assistant', cap: ['assistant'], C: AssistantTab, Icon: AutoAwesomeIcon, kind: 'ai' },
  { id: 'agentic', label: 'Agents', cap: ['agentic'], C: AgenticTab, Icon: SmartToyIcon, kind: 'ai' },
];
const EXPORTS = [['turtle', 'Turtle (.ttl)'], ['xml', 'RDF/XML · OWL (.owl)'], ['json-ld', 'JSON-LD'], ['nt', 'N-Triples']];

/** "What can I do?" popover behind the role chip: every capability, whether the current role has it, and which role unlocks it. */
function RoleChip({ role, label, testId }) {
  const [anchor, setAnchor] = useState(null);
  const { kind } = useOnt();
  return (
    <>
      <Tooltip title="What can my role do?">
        <Chip size="small" variant="outlined" icon={<BadgeOutlinedIcon />} label={label} onClick={(e) => setAnchor(e.currentTarget)} data-testid={testId} aria-haspopup="dialog" />
      </Tooltip>
      <Popover open={!!anchor} anchorEl={anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }} transformOrigin={{ vertical: 'top', horizontal: 'right' }}>
        <Box sx={{ p: 2, width: 300 }} role="dialog" aria-label="Role permissions">
          <Typography variant="overline" color="text.secondary">Your role</Typography>
          <Typography variant="h6" sx={{ mt: -0.5, mb: 1 }}>{role}</Typography>
          <Stack spacing={0.75} component="ul" sx={{ listStyle: 'none', p: 0, m: 0 }}>
            {Object.keys(CAPABILITY_LABEL).map((cap) => {
              const ok = canRole(role, cap);
              return (
                <Stack component="li" key={cap} direction="row" spacing={1} alignItems="center">
                  {ok ? <CheckIcon fontSize="small" sx={{ color: kind.data }} titleAccess="Allowed" /> : <LockOutlinedIcon fontSize="small" sx={{ color: 'text.disabled' }} titleAccess="Not allowed" />}
                  <Typography variant="body2" sx={{ flex: 1, color: ok ? 'text.primary' : 'text.secondary' }}>{CAPABILITY_LABEL[cap]}</Typography>
                  {!ok && <Typography variant="caption" color="text.secondary">needs {requiredRole(cap)}</Typography>}
                </Stack>
              );
            })}
          </Stack>
          <Divider sx={{ my: 1.5 }} />
          <Typography variant="caption" color="text.secondary">Four-eyes rule: the author of a version cannot approve or reject it. Roles are enforced by the server; this list mirrors them.</Typography>
        </Box>
      </Popover>
    </>
  );
}

/**
 * <PrimeOntologyWorkbench apiBase ontologyId context permissions getAuthHeaders onEvent theme />
 * The host owns authentication, tenant, branding, navigation; this component owns everything ontology.
 */
function Shell({
  apiBase = '/api/v1/ontology', ontologyId = null, context = '', permissions,
  getAuthHeaders, onEvent, theme, initialTab, height = '100vh', tabs: tabFilter, currentUser, onLogout, colorMode, onToggleColorMode,
}) {
  const { kind, surface } = useOnt();
  const api = useMemo(() => createApi({ apiBase, getAuthHeaders, permissions: permissions || {} }), [apiBase, getAuthHeaders, permissions?.role, permissions?.user, permissions?.tenant]); // eslint-disable-line
  const [host, setHost] = useState(null);
  // Roles/tenant are enforced by the API from the host's own authentication. `permissions` is only a dev/standalone override;
  // when omitted, the UI mirrors the identity the backend resolved for this session.
  const role = permissions?.role || host?.identity?.role || 'viewer';
  const can = useCallback((cap) => canRole(role, cap), [role]);
  const canEdit = can('write');

  const [list, setList] = useState([]);
  const [ontology, setOntology] = useState(null);
  const [model, setModel] = useState(emptyModel());
  const [dirty, setDirty] = useState(false);
  const [history, setHistory] = useState([]);
  const [selection, setSelection] = useState(null);
  const [concept, setConcept] = useState(null);
  const [tab, setTab] = useState(initialTab || 'sources');
  const [toast, setToast] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [exportEl, setExportEl] = useState(null);
  const [loadError, setLoadError] = useState('');
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;

  const notify = useCallback((message, severity = 'info') => setToast({ message, severity, key: Date.now() }), []);
  const emit = useCallback((type, payload = {}) => {
    const evt = { type, host: context, ontologyId: ontology?.id, payload };
    onEvent && onEvent(evt);
    api.emitEvent(evt).catch(() => {});
  }, [api, context, ontology?.id, onEvent]);

  const refreshList = useCallback(() => api.list(context || undefined).then((r) => setList(r.results)).catch((e) => { setLoadError(e.message); }), [api, context]);
  const loadOntology = useCallback(async (id) => {
    if (id == null) { setOntology(null); setModel(emptyModel()); return; }
    try {
      const o = await api.get(id);
      setOntology(o); modelRef.current = o.model || emptyModel(); setModel(modelRef.current); setDirty(false); setHistory([]); setSelection(null); setConcept(null); setLoadError('');
      api.emitEvent({ type: 'ontology.opened', host: context, ontologyId: o.id }).catch(() => {});
      refreshList(); // keep the dropdown's counts/status in step with what was just loaded (after AI apply, publish, merge, ...)
    } catch (e) { notify(e.message, 'error'); }
  }, [api, notify, context, refreshList]);

  useEffect(() => { refreshList(); api.embeddedContext(context || 'primesemonto').then(setHost).catch(() => setHost(null)); }, [api, context, refreshList]);
  useEffect(() => { if (ontologyId != null) loadOntology(ontologyId); }, [ontologyId, loadOntology]);

  // Compute the next model OUTSIDE React's state updater so validation errors are caught here, not thrown during render.
  const modelRef = useRef(model);
  modelRef.current = model;
  const edit = useCallback((fn, opts = {}) => {
    try {
      if (fn) {
        const prev = modelRef.current;
        const next = fn(prev);
        modelRef.current = next;
        setModel(next);
        if (!opts.silent) { setHistory((h) => [...h.slice(-49), prev]); setDirty(true); }
      }
      if (opts.clear) setSelection(null);
      if (opts.select) setSelection(opts.select);
      opts.after && opts.after();
    } catch (e) { notify(e.message, 'error'); }
  }, [notify]);

  const undo = () => setHistory((h) => { if (!h.length) return h; modelRef.current = h[h.length - 1]; setModel(h[h.length - 1]); setDirty(h.length > 1); return h.slice(0, -1); });

  const save = useCallback(async () => {
    if (!ontology) return;
    try {
      const o = await api.save(ontology.id, { model: modelRef.current });
      setOntology(o); modelRef.current = o.model; setModel(o.model); setDirty(false); setHistory([]);
      notify('Ontology saved.', 'success'); refreshList(); emit('ontology.saved', { status: o.status });
    } catch (e) { notify(e.message, 'error'); }
  }, [api, ontology, model, notify, refreshList, emit]);

  useEffect(() => {
    const h = (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); if (dirtyRef.current) save(); } };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [save]);
  useEffect(() => {
    const h = (e) => { if (dirtyRef.current) { e.preventDefault(); e.returnValue = ''; } };
    window.addEventListener('beforeunload', h);
    return () => window.removeEventListener('beforeunload', h);
  }, []);

  const openConcept = useCallback((name) => { setConcept(name); setTab('explorer'); emit('concept.selected', { concept: name }); }, [emit]);
  const switchOntology = (id) => { if (dirty && !window.confirm('Discard unsaved changes?')) return; loadOntology(id); };
  const create = async () => {
    try { const o = await api.create({ name: newName, model: emptyModel(), context }); setNewOpen(false); setNewName(''); await refreshList(); await loadOntology(o.id); setTab('workbench'); }
    catch (e) { notify(e.message, 'error'); }
  };
  const remove = async () => {
    if (!window.confirm(`Permanently delete “${ontology.name}” and all its versions?`)) return;
    try { await api.remove(ontology.id); await refreshList(); await loadOntology(null); notify('Deleted.', 'success'); } catch (e) { notify(e.message, 'error'); }
  };

  const capabilities = host?.capabilities;
  const visible = TABS.filter((t) => (!capabilities || t.cap.some((c) => capabilities.includes(c))) && (!tabFilter || tabFilter.includes(t.id)));
  const active = visible.find((t) => t.id === tab) || visible[0];
  const userName = currentUser?.name || host?.identity?.user;
  const ctx = { api, ontology, model, edit, dirty, save, loadOntology, refreshList, notify, canEdit, can, host, role, userName, selection, setSelection, tab, setTab, concept, openConcept, emit, context };
  const body = (
    <WorkbenchContext.Provider value={ctx}>
      <Box sx={{ display: 'flex', flexDirection: 'column', height, minHeight: 600, bgcolor: surface.bg, color: 'text.primary' }} data-testid="prime-ontology-workbench" data-context={context}>
        <Paper square elevation={0} component="header" sx={{ borderBottom: 1, borderColor: surface.line, bgcolor: surface.panel, px: { xs: 1.5, md: 2 }, pt: 1 }}>
          <Stack direction="row" spacing={{ xs: 1, md: 1.5 }} alignItems="center" flexWrap="wrap" rowGap={1}>
            <Stack direction="row" spacing={1.25} alignItems="center" sx={{ minWidth: 0 }}>
              <BrandMark size={28} />
              <Typography variant="subtitle1" component="h1" noWrap sx={{ fontWeight: 700, display: { xs: 'none', xl: 'block' }, maxWidth: 300 }}>{host?.title || 'Prime Ontology Workbench'}</Typography>
            </Stack>
            <Select size="small" displayEmpty value={ontology?.id ?? ''} onChange={(e) => switchOntology(e.target.value === '' ? null : e.target.value)} sx={{ minWidth: { xs: 160, sm: 240 }, flex: { xs: '1 1 160px', sm: '0 0 auto' }, fontWeight: 600 }} data-testid="ontology-select" inputProps={{ 'aria-label': 'Ontology' }}>
              <MenuItem value=""><em>Select ontology…</em></MenuItem>
              {(ontology && !list.some((o) => o.id === ontology.id) ? [ontology, ...list] : list).map((o) => <MenuItem key={o.id} value={o.id}><ListItemText primary={o.name} secondary={`${o.stats.classes || 0} classes · ${o.status}`} /></MenuItem>)}
            </Select>
            {canEdit && <Button size="small" startIcon={<AddIcon />} onClick={() => setNewOpen(true)}>New</Button>}
            {ontology && <StatusChip status={ontology.status} />}
            {ontology?.currentVersion && <Chip size="small" variant="outlined" color="success" label={`published v${ontology.currentVersion}`} />}
            {ontology && (dirty
              ? <Chip size="small" data-testid="dirty-indicator" label="Unsaved changes" icon={<Box aria-hidden sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: kind.unsaved, ml: '8px !important' }} />} sx={{ color: kind.unsaved, bgcolor: alpha(kind.unsaved, 0.12), border: `1px solid ${alpha(kind.unsaved, 0.5)}`, fontWeight: 600 }} />
              : <Chip size="small" variant="outlined" icon={<CheckIcon />} label={canEdit ? 'All changes saved' : 'Read-only'} sx={{ color: 'text.secondary' }} data-testid="saved-indicator" />)}
            <Box sx={{ flex: 1 }} />
            {ontology && (
              <Stack direction="row" spacing={0.75} alignItems="center">
                <Tooltip title="Undo last edit"><span><IconButton size="small" aria-label="Undo last edit" disabled={!history.length} onClick={undo}><UndoIcon /></IconButton></span></Tooltip>
                <Tooltip title={!canEdit ? `Saving requires the ${requiredRole('write')} role or higher.` : dirty ? 'Save changes (Ctrl+S)' : 'Nothing to save'}>
                  <span><Button size="small" variant={dirty ? 'contained' : 'outlined'} startIcon={<SaveIcon />} disabled={!dirty || !canEdit} onClick={save} aria-label={dirty ? 'Save changes' : 'Saved'}><Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>{dirty ? 'Save changes' : 'Saved'}</Box></Button></span>
                </Tooltip>
                <Button size="small" variant="outlined" startIcon={<DownloadIcon />} onClick={(e) => setExportEl(e.currentTarget)} aria-label="Export" aria-haspopup="menu"><Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>Export</Box></Button>
                <Menu anchorEl={exportEl} open={!!exportEl} onClose={() => setExportEl(null)}>
                  {EXPORTS.map(([f, l]) => <MenuItem key={f} onClick={async () => { setExportEl(null); try { const { blob, filename } = await api.download(ontology.id, f); downloadBlob(blob, filename); } catch (e) { notify(e.message, 'error'); } }}>{l}</MenuItem>)}
                </Menu>
                {can('delete') && <Tooltip title="Delete this ontology and all its versions"><Button size="small" color="error" startIcon={<DeleteOutlineIcon />} onClick={remove} aria-label="Delete"><Box component="span" sx={{ display: { xs: 'none', xl: 'inline' } }}>Delete</Box></Button></Tooltip>}
              </Stack>
            )}
            <Stack direction="row" spacing={0.75} alignItems="center">
              {currentUser
                ? <RoleChip role={role} label={`${currentUser.name} · ${currentUser.role}`} testId="current-user" />
                : host?.identity && <RoleChip role={role} label={role} testId="role-chip" />}
              {onToggleColorMode && <Tooltip title={colorMode === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}><IconButton size="small" onClick={onToggleColorMode} aria-label={colorMode === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>{colorMode === 'dark' ? <LightModeIcon fontSize="small" /> : <DarkModeIcon fontSize="small" />}</IconButton></Tooltip>}
              {onLogout && <Button size="small" data-testid="logout" startIcon={<LogoutIcon />} aria-label="Sign out" sx={{ '& .MuiButton-startIcon': { mr: { xs: 0, xl: 1 } }, minWidth: 0 }} onClick={() => { if (!dirty || window.confirm('Discard unsaved changes and sign out?')) onLogout(); }}><Box component="span" sx={{ display: { xs: 'none', xl: 'inline' } }}>Sign out</Box></Button>}
            </Stack>
          </Stack>
          <Tabs value={active?.id || false} onChange={(_, v) => setTab(v)} variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile aria-label="Workbench sections" sx={{ minHeight: 44, mt: 0.5 }}>
            {visible.map((t) => (
              <Tab key={t.id} value={t.id} label={t.label} icon={<t.Icon sx={{ fontSize: 18, color: t.kind && active?.id === t.id ? kind[t.kind] : undefined }} />} iconPosition="start" sx={{ minHeight: 44, px: 1.75 }} data-testid={`tab-${t.id}`} />
            ))}
          </Tabs>
        </Paper>
        {loadError && <Alert severity="error" sx={{ m: 2 }}>Cannot reach the ontology API at {apiBase}: {loadError}</Alert>}
        <Box component="main" sx={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>{active && <active.C />}</Box>
        <Dialog open={newOpen} onClose={() => setNewOpen(false)}>
          <DialogTitle>New ontology</DialogTitle>
          <DialogContent><TextField autoFocus fullWidth margin="dense" label="Name" value={newName} onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && newName && create()} /></DialogContent>
          <DialogActions><Button onClick={() => setNewOpen(false)}>Cancel</Button><Button variant="contained" disabled={!newName} onClick={create}>Create</Button></DialogActions>
        </Dialog>
        <Snackbar key={toast?.key} open={!!toast} autoHideDuration={6000} onClose={() => setToast(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
          {toast ? <Alert severity={toast.severity} onClose={() => setToast(null)} variant="filled" sx={{ maxWidth: 560 }}>{toast.message}</Alert> : undefined}
        </Snackbar>
      </Box>
    </WorkbenchContext.Provider>
  );
  return body;
}

/** Public component. A `theme` prop (host branding) is applied here so the shell itself already renders inside it. */
export default function PrimeOntologyWorkbench(props) {
  return props.theme ? <ThemeProvider theme={props.theme}><CssBaseline /><Shell {...props} /></ThemeProvider> : <Shell {...props} />;
}
