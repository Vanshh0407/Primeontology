import { useEffect, useMemo, useState } from 'react';
import {
  Alert, Autocomplete, Box, Button, Chip, Divider, Grid, IconButton, List, ListItem, ListItemButton, ListItemText, Paper, Stack, Tab, Table,
  TableBody, TableCell, TableHead, TableRow, Tabs, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import OntologyGraph from '../components/OntologyGraph';
import { KindChip, KindGlyph, NoOntology, PageHeader, Panel, RoleNotice } from '../components/ui';
import { useWB } from '../context';
import { MONO, useOnt } from '../theme';

const EXAMPLES = [
  { label: 'All classes', kind: 'sparql', text: 'SELECT ?class ?label WHERE {\n  ?class a owl:Class ; rdfs:label ?label .\n} ORDER BY ?label' },
  { label: 'Relationships', kind: 'sparql', text: 'SELECT ?property ?domain ?range WHERE {\n  ?property a owl:ObjectProperty ;\n           rdfs:domain ?domain ; rdfs:range ?range .\n}' },
  { label: 'Subclasses', kind: 'sparql', text: 'SELECT ?sub ?super WHERE { ?sub rdfs:subClassOf ?super . FILTER(isIRI(?super)) }' },
  { label: 'Required properties', kind: 'sparql', text: 'SELECT ?class ?property WHERE {\n  ?class rdfs:subClassOf [ a owl:Restriction ; owl:onProperty ?property ; owl:minCardinality ?n ]\n}' },
];

function ColumnTitle({ kind, children }) {
  return <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}><KindGlyph kind={kind} size={14} /><Typography variant="subtitle2" component="h4">{children}</Typography></Stack>;
}

function ConceptCard({ name }) {
  const { api, ontology, openConcept, model, setTab, setSelection } = useWB();
  const [c, setC] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => { setC(null); setErr(''); api.concept(ontology.id, name).then(setC).catch((e) => setErr(e.message)); }, [ontology.id, name, api]);
  if (err) return <Alert severity="warning">{err}</Alert>;
  if (!c) return null;
  const sub = { classes: model.classes.filter((x) => c.neighbors.nodes.includes(x.name)), dataProperties: [], objectProperties: model.objectProperties.filter((p) => c.neighbors.nodes.includes(p.domain) && c.neighbors.nodes.includes(p.range)) };
  return (
    <Panel kind="class" title={c.label} subtitle="Concept" data-testid="concept-card"
      actions={<Button size="small" variant="outlined" onClick={() => { setSelection({ kind: 'class', name }); setTab('workbench'); }}>Open in workbench</Button>}>
      {c.comment && <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{c.comment}</Typography>}
      <Box sx={{ mb: 1.5 }}>{c.ancestors.map((a) => <Chip key={a} size="small" clickable sx={{ mr: 0.5, mb: 0.5 }} label={`is a ${a}`} onClick={() => openConcept(a)} />)}{c.children.map((a) => <Chip key={a} size="small" variant="outlined" clickable sx={{ mr: 0.5, mb: 0.5 }} label={`${a} is a kind of it`} onClick={() => openConcept(a)} />)}</Box>
      <Box sx={{ height: 240, border: 1, borderColor: 'divider', borderRadius: 1, mb: 2, overflow: 'hidden' }}><OntologyGraph model={{ ...sub, layout: {} }} selection={{ kind: 'class', name }} onSelect={(s) => s && openConcept(s.name)} canEdit={false} onConnectClasses={() => {}} minimap={false} toolbar={false} /></Box>
      <Grid container spacing={2}>
        <Grid item xs={12} md={4}><ColumnTitle kind="data">Properties</ColumnTitle>
          {[...c.dataProperties, ...c.inheritedProperties.filter((p) => p.kind === 'data')].map((p) => <Typography key={p.domain + p.name} variant="body2">{p.name} <Typography component="span" variant="caption" color="text.secondary" sx={{ fontFamily: MONO }}>{p.datatype}{p.inheritedFrom ? ` · from ${p.inheritedFrom}` : ''}</Typography></Typography>)}</Grid>
        <Grid item xs={12} md={4}><ColumnTitle kind="object">Relationships</ColumnTitle>
          {c.relationships.outgoing.map((r) => <div key={r.name}><Chip size="small" clickable label={`${r.name} → ${r.target}`} onClick={() => openConcept(r.target)} sx={{ mb: 0.5 }} /></div>)}
          {c.relationships.incoming.map((r) => <div key={r.source + r.name}><Chip size="small" variant="outlined" clickable label={`${r.source} —${r.name}→`} onClick={() => openConcept(r.source)} sx={{ mb: 0.5 }} /></div>)}</Grid>
        <Grid item xs={12} md={4}><ColumnTitle kind="provenance">Sources & provenance</ColumnTitle>
          {c.sources.length === 0 && c.mappings.length === 0 && <Typography variant="body2" color="text.secondary">No recorded source.</Typography>}
          {c.sources.map((s, i) => <Typography key={i} variant="caption" display="block">{s.table ? `${s.database || ''}${s.schema ? '.' + s.schema : ''} table ${s.table}` : s.document ? `${s.document}${s.page ? ` · p.${s.page}` : ''}${s.section ? ` · ${s.section}` : ''}` : s.origin}</Typography>)}
          {c.mappings.map((m, i) => <Typography key={'m' + i} variant="caption" display="block" sx={{ color: 'mapping.main' }}>mapped: {m.source}.{m.field}</Typography>)}</Grid>
      </Grid>
    </Panel>
  );
}

function QueryStudio() {
  const { api, ontology, notify, openConcept, canEdit } = useWB();
  const { surface } = useOnt();
  const [kind, setKind] = useState('natural');
  const [text, setText] = useState('Find customers who placed sales orders for products supplied by suppliers');
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState([]);
  const [saved, setSaved] = useState([]);
  const load = () => { api.queries(ontology.id).then((r) => setHistory(r.results.filter((q) => !q.saved).slice(0, 12))); api.queries(ontology.id, true).then((r) => setSaved(r.results)); };
  useEffect(() => { setRes(null); load(); /* eslint-disable-next-line */ }, [ontology.id]);

  const run = async () => {
    setBusy(true);
    try { setRes(await api.query(ontology.id, kind, text)); load(); } catch (e) { setRes(null); notify(e.message, 'error'); } finally { setBusy(false); }
  };
  const result = res ? (res.result || res) : null;

  return (
    <Grid container spacing={2}>
      <Grid item xs={12} md={8}>
        <ToggleButtonGroup size="small" exclusive value={kind} onChange={(_, v) => v && setKind(v)} sx={{ mb: 1 }} aria-label="Query language"><ToggleButton value="natural">Ask in words</ToggleButton><ToggleButton value="sparql">SPARQL</ToggleButton></ToggleButtonGroup>
        <TextField fullWidth multiline minRows={kind === 'sparql' ? 6 : 2} value={text} onChange={(e) => setText(e.target.value)} InputProps={{ sx: kind === 'sparql' ? { fontFamily: MONO, fontSize: 13 } : {} }} inputProps={{ 'aria-label': kind === 'sparql' ? 'SPARQL query' : 'Question about the ontology' }} data-testid="query-input" />
        <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap" rowGap={1} alignItems="center">
          <Button variant="contained" onClick={run} disabled={busy || !text.trim()}>Run</Button>
          {canEdit && <Button onClick={() => api.saveQuery(ontology.id, { name: text.slice(0, 40), text, kind }).then(load)}>Save query</Button>}
          {kind === 'sparql' && EXAMPLES.map((e) => <Chip key={e.label} size="small" clickable label={e.label} onClick={() => setText(e.text)} />)}
        </Stack>
        {res && res.explanation && (
          <Alert icon={false} severity="info" sx={{ mt: 2 }}><strong>Interpretation:</strong> {res.interpretation.map((i) => i.class).join(' → ')}<br /><strong>Path:</strong> {res.explanation}
            {res.note && <Typography variant="caption" display="block">{res.note}</Typography>}</Alert>)}
        {res && res.sparql && <Paper variant="outlined" sx={{ p: 1.5, mt: 1, bgcolor: surface.sunken }}><Typography variant="caption" color="text.secondary">Generated SPARQL</Typography><pre style={{ margin: 0, fontSize: 12, overflow: 'auto', fontFamily: MONO }}>{res.sparql}</pre></Paper>}
        {result && (
          <Paper variant="outlined" sx={{ mt: 2, maxHeight: 360, overflow: 'auto' }} data-testid="query-results">
            <Table size="small" stickyHeader><TableHead><TableRow>{result.columns.map((c) => <TableCell key={c}>{c}</TableCell>)}</TableRow></TableHead>
              <TableBody>{result.rows.map((r, i) => <TableRow key={i} hover>{r.map((v, j) => {
                const local = typeof v === 'string' && v.startsWith('onto:') ? v.slice(5) : null;
                return <TableCell key={j}>{local ? <Button size="small" onClick={() => ontology && openConcept(local)} sx={{ textTransform: 'none', fontFamily: MONO }}>{v}</Button> : String(v)}</TableCell>; })}</TableRow>)}</TableBody></Table>
            <Typography variant="caption" sx={{ p: 1 }} display="block">{result.count} row(s){result.truncated ? ' (truncated)' : ''}</Typography>
          </Paper>
        )}
      </Grid>
      <Grid item xs={12} md={4}>
        <Typography variant="subtitle2">Saved queries</Typography>
        {saved.length === 0 && <Typography variant="caption" color="text.secondary">None saved.</Typography>}
        <List dense>{saved.map((q) => <ListItem key={q.id} disablePadding secondaryAction={canEdit && <Tooltip title="Delete saved query"><IconButton size="small" aria-label="Delete saved query" onClick={() => api.deleteQuery(ontology.id, q.id).then(load)}><DeleteIcon fontSize="small" /></IconButton></Tooltip>}>
          <ListItemButton onClick={() => { setKind(q.kind); setText(q.text); }}><ListItemText primary={q.name || q.text.slice(0, 30)} secondary={q.kind} /></ListItemButton></ListItem>)}</List>
        <Divider />
        <Typography variant="subtitle2" sx={{ mt: 1 }}>History</Typography>
        {history.length === 0 && <Typography variant="caption" color="text.secondary">No queries run yet.</Typography>}
        <List dense>{history.map((q) => <ListItem key={q.id} disablePadding><ListItemButton onClick={() => { setKind(q.kind); setText(q.text); }}><ListItemText primary={q.text.slice(0, 48)} secondary={`${q.kind} · ${q.resultCount} rows`} /></ListItemButton></ListItem>)}</List>
      </Grid>
    </Grid>
  );
}

export default function KnowledgeTab() {
  const { api, ontology, model, concept, openConcept } = useWB();
  const { kind } = useOnt();
  const [sub, setSub] = useState(0);
  const [q, setQ] = useState('');
  const [hits, setHits] = useState([]);
  const [from, setFrom] = useState(null);
  const [to, setTo] = useState(null);
  const [path, setPath] = useState(null);
  const names = useMemo(() => model.classes.map((c) => c.name), [model]);

  useEffect(() => {
    if (!ontology || !q.trim()) return setHits([]);
    const t = setTimeout(() => api.search(ontology.id, q).then((r) => setHits(r.results)).catch(() => {}), 200);
    return () => clearTimeout(t);
  }, [q, ontology, api]);
  useEffect(() => { if (from && to && ontology) api.path(ontology.id, from, to).then(setPath).catch(() => setPath(null)); else setPath(null); }, [from, to, ontology, api]);
  if (!ontology) return <NoOntology />;

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="object" eyebrow="Explore & Query" title={`Explore “${ontology.name}”`}
        description="Search concepts, see how two of them connect, and ask questions in words or SPARQL. Queries are read-only." />
      <Tabs value={sub} onChange={(_, v) => setSub(v)} sx={{ mb: 2 }}><Tab label="Knowledge explorer" /><Tab label="Query studio" /></Tabs>
      {sub === 0 && (
        <Grid container spacing={2}>
          <Grid item xs={12} md={4}>
            <TextField fullWidth size="small" label="Semantic search (concepts, properties, synonyms)" value={q} onChange={(e) => setQ(e.target.value)} data-testid="semantic-search" />
            {q.trim() && hits.length === 0 && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>No matches.</Typography>}
            <List dense>{hits.map((h) => <ListItemButton key={h.kind + h.id} onClick={() => openConcept(h.kind === 'class' ? h.id : h.domain)}><Box sx={{ mr: 1.25, display: 'flex' }}><KindGlyph kind={h.kind === 'class' ? 'class' : h.kind === 'object' || h.kind === 'objectProperty' ? 'object' : 'data'} size={14} /></Box><ListItemText primary={h.name} secondary={`${h.kind}${h.domain ? ' · ' + h.domain : ''} · ${(h.score * 100) | 0}%`} /></ListItemButton>)}</List>
            <Divider sx={{ my: 1.5 }} />
            <Typography variant="subtitle2">How are two concepts connected?</Typography>
            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <Autocomplete size="small" sx={{ flex: 1 }} options={names} value={from} onChange={(_, v) => setFrom(v)} renderInput={(p) => <TextField {...p} label="From" />} />
              <Autocomplete size="small" sx={{ flex: 1 }} options={names} value={to} onChange={(_, v) => setTo(v)} renderInput={(p) => <TextField {...p} label="To" />} />
            </Stack>
            {path && (path.connected
              ? <Alert icon={false} severity="success" sx={{ mt: 1, '& .MuiAlert-message': { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 0.5 } }}>
                <KindChip kind="class" label={from} />
                {path.path.map((s, i) => (
                  <Stack key={i} direction="row" alignItems="center" spacing={0.5} component="span">
                    <Box component="span" sx={{ color: kind.object, fontFamily: MONO, fontSize: 12 }}>{s.property}{s.forward ? '' : ' (inv)'}</Box><ArrowForwardIcon sx={{ fontSize: 16, color: kind.object }} /><KindChip kind="class" label={s.to} />
                  </Stack>
                ))}
              </Alert>
              : <Alert severity="warning" sx={{ mt: 1 }}>Not connected.</Alert>)}
          </Grid>
          <Grid item xs={12} md={8}>{concept ? <ConceptCard name={concept} /> : <Alert severity="info">Search for a concept, or pick one from the workbench, to see its properties, relationships and sources.</Alert>}</Grid>
        </Grid>
      )}
      {sub === 1 && <QueryStudio />}
    </Box>
  );
}
