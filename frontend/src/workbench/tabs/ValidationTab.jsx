import { useEffect, useState } from 'react';
import { useTheme } from '@mui/material/styles';
import {
  Alert, Box, Button, Chip, Grid, List, ListItem, ListItemButton, ListItemText, Paper, Stack, Tab, Tabs, TextField,
  ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import { NoOntology, PageHeader, Panel } from '../components/ui';
import { useWB } from '../context';
import { MONO, useOnt } from '../theme';

const SEV = { error: { color: 'error', Icon: ErrorOutlineIcon }, warning: { color: 'warning', Icon: WarningAmberIcon }, info: { color: 'info', Icon: InfoOutlinedIcon } };

/** Health score as a ring: colour + the number + the words, so it reads without colour. */
function Health({ v }) {
  const { surface } = useOnt();
  const theme = useTheme();
  const tone = v.health >= 85 ? 'success' : v.health >= 60 ? 'warning' : 'error';
  const word = v.health >= 85 ? 'Healthy' : v.health >= 60 ? 'Needs attention' : 'Unhealthy';
  const R = 44; const C = 2 * Math.PI * R;
  return (
    <Stack direction="row" spacing={3} alignItems="center" flexWrap="wrap" rowGap={2} data-testid="health">
      <Box sx={{ position: 'relative', width: 108, height: 108 }} role="img" aria-label={`Ontology health ${v.health} out of 100: ${word}`}>
        <svg viewBox="0 0 108 108" width="108" height="108">
          <circle cx="54" cy="54" r={R} fill="none" stroke={surface.line} strokeWidth="8" />
          <circle cx="54" cy="54" r={R} fill="none" stroke={theme.palette[tone].main} strokeWidth="8" strokeLinecap="round" strokeDasharray={`${(C * v.health) / 100} ${C}`} transform="rotate(-90 54 54)" style={{ transition: 'stroke-dasharray 400ms' }} />
        </svg>
        <Box sx={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
          <Typography variant="h4" component="span" sx={{ fontWeight: 700, lineHeight: 1 }}>{v.health}</Typography>
          <Typography variant="caption" color="text.secondary">/ 100</Typography>
        </Box>
      </Box>
      <Box>
        <Typography variant="overline" color="text.secondary">Ontology health · {word}</Typography>
        <Stack direction="row" spacing={1} sx={{ my: 0.5 }} flexWrap="wrap" rowGap={0.5}>
          <Chip variant="outlined" color={v.errors ? 'error' : 'default'} icon={<ErrorOutlineIcon />} label={`${v.errors} errors`} />
          <Chip variant="outlined" color={v.warnings ? 'warning' : 'default'} icon={<WarningAmberIcon />} label={`${v.warnings} warnings`} />
          <Chip variant="outlined" color="success" icon={<CheckCircleOutlineIcon />} label={`${v.passed} passed`} />
        </Stack>
        <Typography variant="caption" color="text.secondary">Classes {v.counts.classes} · Properties {v.counts.dataProperties} · Relationships {v.counts.relationships}</Typography>
      </Box>
    </Stack>
  );
}

export default function ValidationTab() {
  const { api, ontology, notify, setSelection, setTab, dirty } = useWB();
  const { surface } = useOnt();
  const [tab, setTab2] = useState(0);
  const [val, setVal] = useState(null);
  const [reason, setReason] = useState(null);
  const [profile, setProfile] = useState('owlrl');
  const [shapes, setShapes] = useState('');
  const [data, setData] = useState('');
  const [shacl, setShacl] = useState(null);
  const [filter, setFilter] = useState('all');
  const [busy, setBusy] = useState(false);

  const run = async (fn) => { setBusy(true); try { await fn(); } catch (e) { notify(e.message, 'error'); } finally { setBusy(false); } };
  const validate = () => run(async () => setVal(await api.validate(ontology.id)));
  const doReason = () => run(async () => setReason(await api.reason(ontology.id, profile)));
  useEffect(() => { setVal(null); setReason(null); setShacl(null); setShapes(''); if (ontology) validate(); /* eslint-disable-next-line */ }, [ontology?.id]);
  if (!ontology) return <NoOntology />;

  const jump = (t) => {
    const [a, b] = t.split('.');
    setSelection(b ? { kind: 'relationship', domain: a, name: b } : { kind: 'class', name: a });
    setTab('workbench');
  };
  const issues = (val?.issues || []).filter((i) => filter === 'all' || i.severity === filter);

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader eyebrow="Validation & Reasoning" title={`Is “${ontology.name}” sound?`}
        description="Validation checks structure and consistency; the reasoner derives what follows from it; SHACL checks data against shapes generated from the ontology." />
      {dirty && <Alert severity="info" sx={{ mb: 1.5 }}>You have unsaved edits — validation runs on the last saved version. Save first to include them.</Alert>}
      <Tabs value={tab} onChange={(_, v) => setTab2(v)} sx={{ mb: 2, borderBottom: 1, borderColor: surface.line }}><Tab label="Validation" /><Tab label="Reasoning (RDFS / OWL)" /><Tab label="SHACL" /></Tabs>
      {tab === 0 && (
        <>
          <Stack direction="row" spacing={3} alignItems="center" flexWrap="wrap" rowGap={2} sx={{ mb: 2.5 }}>{val && <Health v={val} />}<Button variant="contained" onClick={validate} disabled={busy}>Re-validate</Button></Stack>
          <ToggleButtonGroup size="small" exclusive value={filter} onChange={(_, v) => v && setFilter(v)} sx={{ mb: 1.5 }} aria-label="Filter findings">
            <ToggleButton value="all">All</ToggleButton><ToggleButton value="error">Errors</ToggleButton><ToggleButton value="warning">Warnings</ToggleButton>
          </ToggleButtonGroup>
          <Paper variant="outlined" sx={{ bgcolor: surface.panel }}><List dense disablePadding>
            {val && issues.length === 0 && <ListItem><ListItemText primary="No issues found." /></ListItem>}
            {issues.map((i) => {
              const { color, Icon } = SEV[i.severity] || SEV.info;
              return (
                <ListItem key={i.id} disablePadding divider secondaryAction={<Chip size="small" variant="outlined" label={i.code} sx={{ fontFamily: MONO, fontSize: 11, display: { xs: 'none', sm: 'inline-flex' } }} />}>
                  <ListItemButton onClick={() => i.targets[0] && jump(i.targets[0])} sx={{ pr: { sm: 22 }, alignItems: 'flex-start' }}>
                    <Chip size="small" color={color} icon={<Icon />} label={i.severity} sx={{ mr: 2, mt: 0.25, minWidth: 92 }} />
                    <ListItemText primary={i.message} secondary={i.targets.length ? `Jump to ${i.targets.join(' · ')} in the workbench` : undefined} />
                  </ListItemButton>
                </ListItem>
              );
            })}
          </List></Paper>
        </>
      )}
      {tab === 1 && (
        <>
          <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }} flexWrap="wrap" rowGap={1}>
            <ToggleButtonGroup size="small" exclusive value={profile} onChange={(_, v) => v && setProfile(v)} aria-label="Reasoning profile"><ToggleButton value="rdfs">RDFS</ToggleButton><ToggleButton value="owlrl">OWL 2 RL</ToggleButton></ToggleButtonGroup>
            <Button variant="contained" onClick={doReason} disabled={busy}>Run reasoner</Button>
          </Stack>
          {!reason && <Typography variant="body2" color="text.secondary">Choose a profile and run the reasoner. Results are inferences over the saved ontology; they are not written back to it.</Typography>}
          {reason && (
            <Grid container spacing={2} data-testid="reasoning">
              <Grid item xs={12}><Alert severity={reason.consistent ? 'success' : 'error'}>{reason.consistent ? 'Ontology is consistent.' : `Inconsistent: ${[...reason.inconsistencies, ...reason.unsatisfiableClasses.map((c) => `${c} is unsatisfiable`)].join('; ')}`} · {reason.triplesBefore} → {reason.triplesAfter} triples after inference</Alert></Grid>
              <Grid item xs={12} md={6}><Panel kind="inherit" title="Inferred class hierarchy" sx={{ height: '100%' }}>
                {reason.inferredHierarchy.length === 0 && <Typography variant="body2" color="text.secondary">No additional subclass relationships inferred.</Typography>}
                {reason.inferredHierarchy.map((h, i) => <Typography key={i} variant="body2" sx={{ mb: 0.5 }}><strong>{h.subject}</strong> ⊑ <strong>{h.object}</strong> <Typography component="span" variant="caption" color="text.secondary">via {h.via}</Typography></Typography>)}</Panel></Grid>
              <Grid item xs={12} md={6}><Panel kind="class" title="Inferred individual types" sx={{ height: '100%' }}>
                {reason.inferredIndividualTypes.length === 0 && <Typography variant="body2" color="text.secondary">None (no individuals, or no new types).</Typography>}
                {reason.inferredIndividualTypes.map((t) => <Typography key={t.individual} variant="body2" sx={{ mb: 0.5 }}>{t.individual}: {t.asserted} ⇒ {t.inferred.join(', ')}</Typography>)}</Panel></Grid>
            </Grid>
          )}
        </>
      )}
      {tab === 2 && (
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <Button variant="outlined" onClick={() => run(async () => setShapes(await api.shapes(ontology.id)))}>Generate SHACL shapes from ontology</Button>
            <TextField fullWidth multiline minRows={10} maxRows={18} sx={{ mt: 1 }} value={shapes} placeholder="Shapes (Turtle) appear here" inputProps={{ 'aria-label': 'Generated SHACL shapes (read-only)' }} InputProps={{ readOnly: true, sx: { fontFamily: MONO, fontSize: 12 } }} />
          </Grid>
          <Grid item xs={12} md={6}>
            <TextField fullWidth multiline minRows={8} maxRows={14} label="Data graph to validate (Turtle) — leave empty to validate the ontology’s own individuals" value={data} onChange={(e) => setData(e.target.value)} InputProps={{ sx: { fontFamily: MONO, fontSize: 12 } }} />
            <Button sx={{ mt: 1 }} variant="contained" disabled={busy} onClick={() => run(async () => setShacl(await api.shacl(ontology.id, data || null)))}>Validate against shapes</Button>
            {shacl && <Alert sx={{ mt: 1 }} severity={shacl.conforms ? 'success' : 'error'}>{shacl.conforms ? 'Data conforms to all shapes.' : `${shacl.violations.length} violation(s)`}
              {shacl.violations.map((v, i) => <Typography key={i} variant="caption" display="block" sx={{ fontFamily: MONO }}>{v.focusNode} · {v.path}: {v.message}</Typography>)}</Alert>}
          </Grid>
        </Grid>
      )}
    </Box>
  );
}
