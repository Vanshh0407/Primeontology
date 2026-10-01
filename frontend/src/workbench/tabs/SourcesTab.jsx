import { useRef, useState } from 'react';
import { alpha } from '@mui/material/styles';
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Collapse, Divider, FormControlLabel, Grid, List, ListItem, ListItemText,
  Paper, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, TextField, Typography,
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import OntologyGraph from '../components/OntologyGraph';
import { KindChip, PageHeader, Panel, RoleNotice, StepTrack, Why, needs } from '../components/ui';
import { useWB } from '../context';
import { MONO, useOnt } from '../theme';

const FORMATS = 'CSV · TSV · Excel · Parquet · SQLite · JSON · XML · YAML · SQL · RDF/OWL · Turtle · JSON-LD · N-Triples · PDF (incl. scans) · DOCX · PPTX · TXT · Markdown · HTML';
const RECORD_FORMATS = ['csv', 'tsv', 'xlsx', 'xlsm', 'parquet', 'sqlite', 'sqlite3', 'db'];

function SchemaPreview({ schema }) {
  const { kind } = useOnt();
  if (!schema) return null;
  return (
    <Box sx={{ maxHeight: 300, overflow: 'auto' }}>
      {schema.tables.map((t) => (
        <Box key={t.name} sx={{ mb: 1.5 }}>
          <Typography variant="subtitle2" sx={{ fontFamily: MONO }}>{t.name} <Typography component="span" variant="caption" color="text.secondary">({t.columns.length} columns)</Typography></Typography>
          <div>
            {t.columns.map((c) => (
              <Chip key={c.name} size="small" sx={{ mr: 0.5, mb: 0.5, ...(c.pk ? { borderColor: kind.class, color: kind.class } : {}) }} variant="outlined"
                label={`${c.name}: ${c.type}${c.pk ? ' · PK' : ''}${t.foreign_keys.some((f) => f.columns.includes(c.name)) ? ' → FK' : ''}`} />
            ))}
          </div>
          {t.foreign_keys.map((f, i) => (
            <Typography key={i} variant="caption" display="block" color="text.secondary">
              FK {f.columns.join(',')} → {f.ref_table}({f.ref_columns.join(',')}){f.inferred ? ' · inferred from naming' : ''}
            </Typography>
          ))}
        </Box>
      ))}
    </Box>
  );
}

function CandidateReview({ candidate, onDone }) {
  const { api, ontology, notify, loadOntology, refreshList, emit, canEdit, can, context } = useWB();
  const [name, setName] = useState(candidate.name || 'New ontology');
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState(0);
  const [selected, setSelected] = useState(null);
  const model = candidate.model;
  const hasMapping = (candidate.mapping || []).length > 0;
  const evidence = (model.classes.find((c) => c.name === selected?.name) || {}).evidence || [];

  const commit = async (mergeInto) => {
    setBusy(true);
    try {
      const res = await api.create({ name, model, mergeInto, sourceType: candidate.format, context: ontology?.context || context });
      notify(mergeInto ? `Merged into “${res.name}” (${res.mergeReport.classesAdded} classes added, ${res.mergeReport.classesMerged} merged).` : 'Ontology saved.', 'success');
      await refreshList();
      await loadOntology(res.id);
      emit('import.completed', { ontologyId: res.id, source: candidate.name });
      onDone(res);
    } catch (e) { notify(e.message, 'error'); } finally { setBusy(false); }
  };

  return (
    <Panel kind="ai" title="Review candidate ontology" subtitle="A proposal generated from your source — nothing is saved until you approve it." sx={{ mt: 2 }} data-testid="candidate-review"
      actions={<><Chip color="primary" size="small" label={`${candidate.stats.classes} classes`} /><Chip size="small" variant="outlined" label={`${candidate.stats.dataProperties} properties`} /><Chip size="small" variant="outlined" label={`${candidate.stats.objectProperties} relationships`} />{(model.individuals || []).length > 0 && <Chip size="small" variant="outlined" label={`${model.individuals.length} individuals`} />}<KindChip kind="source" label={`source: ${candidate.format || candidate.type}`} /></>}>
      {(candidate.warnings || []).map((w) => <Alert key={w} severity="warning" sx={{ mb: 1 }}>{w}</Alert>)}
      {candidate.recordsReport && (
        <Alert severity="info" sx={{ mb: 1 }} data-testid="records-report">
          <strong>{candidate.recordsReport.individuals} sample record{candidate.recordsReport.individuals === 1 ? '' : 's'} will be copied into the ontology as individuals</strong> (up to {candidate.recordsReport.maxRowsPerTable} per table). They become visible to everyone who can open this ontology.
          {candidate.recordsReport.skippedSensitiveColumns?.length > 0 && <Typography variant="caption" display="block">Columns never copied (look sensitive): {candidate.recordsReport.skippedSensitiveColumns.join(', ')}.</Typography>}
          {candidate.recordsReport.truncatedTables?.length > 0 && <Typography variant="caption" display="block">More rows exist than were sampled in: {candidate.recordsReport.truncatedTables.join(', ')}.</Typography>}
          {candidate.recordsReport.linksOutsideSample > 0 && <Typography variant="caption" display="block">{candidate.recordsReport.linksOutsideSample} link(s) point to rows outside the sample and were left out.</Typography>}
        </Alert>
      )}
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ minHeight: 40 }} variant="scrollable" scrollButtons="auto">
        <Tab label="Graph" />{hasMapping && <Tab label="Source → ontology mapping" />}
        {candidate.schema && <Tab label="Source structure" />}{candidate.kind === 'document' && <Tab label="Document sections" />}
      </Tabs>
      <Box sx={{ mt: 1 }}>
        {tab === 0 && (
          <Grid container spacing={2}>
            <Grid item xs={12} md={selected ? 8 : 12}><Box sx={{ height: 520, border: 1, borderColor: 'divider', borderRadius: 1, overflow: 'hidden' }}>
              <OntologyGraph model={model} selection={selected} onSelect={setSelected} canEdit={false} minimap={false} onConnectClasses={() => {}} /></Box></Grid>
            {selected?.kind === 'class' && (
              <Grid item xs={12} md={4}>
                <Typography variant="subtitle1">{selected.name}</Typography>
                {evidence.length === 0 && <Typography variant="body2" color="text.secondary">No document evidence (structured source).</Typography>}
                {evidence.map((e, i) => (
                  <Alert key={i} icon={false} severity="info" sx={{ mb: 1 }}>
                    <strong>Found in {e.document}{e.page ? `: Page ${e.page}` : ''}{e.section ? `, Section “${e.section}”` : ''}</strong>
                    <Typography variant="caption" display="block">“…{e.snippet}…”</Typography>
                  </Alert>
                ))}
                {(model.classes.find((c) => c.name === selected.name) || {}).confidence != null && <Chip size="small" label={`confidence ${Math.round(model.classes.find((c) => c.name === selected.name).confidence * 100)}%`} />}
              </Grid>
            )}
          </Grid>
        )}
        {hasMapping && tab === 1 && (
          <Box sx={{ maxHeight: 380, overflow: 'auto' }}><Table size="small" stickyHeader>
            <TableHead><TableRow><TableCell>Source</TableCell><TableCell>Column</TableCell><TableCell>Ontology element</TableCell><TableCell>Kind</TableCell></TableRow></TableHead>
            <TableBody>{candidate.mapping.map((m, i) => <TableRow key={i}><TableCell>{m.source}</TableCell><TableCell>{m.column || '—'}</TableCell><TableCell>{m.target}</TableCell><TableCell>{m.kind}{m.datatype ? ` (${m.datatype})` : ''}</TableCell></TableRow>)}</TableBody></Table></Box>
        )}
        {candidate.schema && tab === (hasMapping ? 2 : 1) && <SchemaPreview schema={candidate.schema} />}
        {candidate.kind === 'document' && tab === (hasMapping ? 3 : candidate.schema ? 2 : 1) && (
          <List dense sx={{ maxHeight: 380, overflow: 'auto' }}>{(candidate.preview.sections || []).map((s, i) => (
            <ListItem key={i} divider><ListItemText primary={`${s.page ? `p.${s.page} · ` : ''}${s.heading || '(untitled section)'}`} secondary={s.text} /></ListItem>))}</List>
        )}
      </Box>
      <Divider sx={{ my: 2 }} />
      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" rowGap={1}>
        <TextField size="small" label="Ontology name" value={name} onChange={(e) => setName(e.target.value)} />
        <Why reason={needs(can, 'write')}><Button variant="contained" disabled={busy || !canEdit || !name} onClick={() => commit(null)}>Approve & create ontology</Button></Why>
        {ontology && <Why reason={needs(can, 'write')}><Button variant="outlined" disabled={busy || !canEdit} onClick={() => commit(ontology.id)}>Merge into “{ontology.name}”</Button></Why>}
        <Button onClick={() => onDone(null)}>Discard</Button>
        {busy && <CircularProgress size={20} />}
      </Stack>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>Nothing is saved until you approve. Merging keeps existing definitions and accumulates provenance.</Typography>
    </Panel>
  );
}

export default function SourcesTab() {
  const { api, notify, canEdit, can, features } = useWB();
  const { kind, surface } = useOnt();
  const [withRecords, setWithRecords] = useState(false);
  const [recordRows, setRecordRows] = useState(50);
  const [aiExtra, setAiExtra] = useState(false);
  const [url, setUrl] = useState('');
  const [urlAuth, setUrlAuth] = useState('');
  const maxRows = features?.sampleRecordsMax || 500;
  const rows = Math.max(1, Math.min(Number(recordRows) || 50, maxRows));
  const [cfg, setCfg] = useState({ host: 'localhost', port: '3306', user: '', password: '', database: '' });
  const [infer, setInfer] = useState(true);
  const [script, setScript] = useState('');
  const [schema, setSchema] = useState(null);
  const [candidate, setCandidate] = useState(null);
  const [busy, setBusy] = useState('');
  const [mode, setMode] = useState(0);
  const fileRef = useRef();
  const [drag, setDrag] = useState(false);

  const config = () => (mode === 0 ? { type: 'mysql', config: { ...cfg, inferRelationships: infer } } : { type: 'sql', config: { script } });
  const run = async (label, fn) => { setBusy(label); try { await fn(); } catch (e) { notify(e.message, 'error'); } finally { setBusy(''); } };

  const inspect = () => run('inspect', async () => { const { type, config: c } = config(); const r = await api.inspect(type, c); setSchema(r.schema); notify(`Found ${r.schema.tables.length} tables.`, 'success'); });
  const analyze = () => run('generate', async () => {
    const { type, config: c } = config();
    const r = await api.generate({ type, config: c, save: false, ...(type === 'mysql' && withRecords ? { sampleRows: rows } : {}) });
    setCandidate({ ...r, name: (cfg.database || 'SQL script') + ' ontology', format: type === 'mysql' ? 'MySQL' : 'SQL script', kind: 'schema' });
  });
  const upload = (files) => run('upload', async () => {
    const f = files[0];
    if (!f) return;
    const ext = (f.name.split('.').pop() || '').toLowerCase();
    const r = await api.ingest(f, { records: withRecords && RECORD_FORMATS.includes(ext) ? rows : 0, ai: aiExtra });
    setCandidate({ ...r, name: f.name.replace(/\.[^.]+$/, '') + ' ontology' });
  });
  const fromUrl = () => run('url', async () => {
    const r = await api.ingestUrl(url.trim(), urlAuth.trim());
    setCandidate({ ...r, name: (url.replace(/^https?:\/\//, '').split(/[/?#]/)[0] || 'api') + ' ontology', format: 'REST API' });
  });
  const pick = () => canEdit && fileRef.current.click();

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="source" eyebrow="Import & Sources" title="Bring in a source, review what it implies, then approve"
        description="Databases, files and documents are analysed into a candidate ontology. You review the candidate first; only Approve or Merge writes anything." />
      <RoleNotice cap="write">Importing needs the editor role or higher, so the source controls below are disabled.</RoleNotice>
      <Box sx={{ mb: 2.5 }}><StepTrack steps={[{ label: 'Choose a source', state: candidate ? 'done' : 'current' }, { label: 'Review candidate', state: candidate ? 'current' : 'todo' }, { label: 'Approve or merge', state: 'todo' }]} /></Box>
      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <Panel kind="source" title="Database → Ontology" subtitle="MySQL connection or a CREATE TABLE script" sx={{ height: '100%' }}>
            <Tabs value={mode} onChange={(_, v) => { setMode(v); setSchema(null); }} sx={{ mb: 1.5, minHeight: 40 }}><Tab label="MySQL" /><Tab label="SQL script" /></Tabs>
            {mode === 0 ? (
              <Grid container spacing={1.25}>
                <Grid item xs={8}><TextField size="small" fullWidth label="Host" value={cfg.host} onChange={(e) => setCfg({ ...cfg, host: e.target.value })} /></Grid>
                <Grid item xs={4}><TextField size="small" fullWidth label="Port" value={cfg.port} onChange={(e) => setCfg({ ...cfg, port: e.target.value })} /></Grid>
                <Grid item xs={6}><TextField size="small" fullWidth label="User" value={cfg.user} onChange={(e) => setCfg({ ...cfg, user: e.target.value })} autoComplete="off" /></Grid>
                <Grid item xs={6}><TextField size="small" fullWidth type="password" label="Password" value={cfg.password} onChange={(e) => setCfg({ ...cfg, password: e.target.value })} autoComplete="new-password" /></Grid>
                <Grid item xs={12}><TextField size="small" fullWidth label="Database / schema" value={cfg.database} onChange={(e) => setCfg({ ...cfg, database: e.target.value })} /></Grid>
                <Grid item xs={12}><FormControlLabel control={<Checkbox size="small" checked={infer} onChange={(e) => setInfer(e.target.checked)} />} label="Infer relationships from column names when the database declares no foreign keys" /></Grid>
                <Grid item xs={12}>
                  <FormControlLabel control={<Checkbox size="small" checked={withRecords} onChange={(e) => setWithRecords(e.target.checked)} inputProps={{ 'aria-label': 'Copy sample records' }} />}
                    label={<span>Also copy sample <strong>records</strong> as individuals, up to <input aria-label="Rows per table" type="number" min={1} max={maxRows} value={recordRows} onChange={(e) => setRecordRows(e.target.value)} onClick={(e) => e.stopPropagation()} style={{ width: 60 }} /> rows per table</span>} />
                  {withRecords && <Typography variant="caption" color="text.secondary" display="block" sx={{ ml: 4 }}>Lets plain-word queries return real rows. Data is copied into the ontology (visible to everyone with access); columns that look sensitive (passwords, tokens, card numbers…) are never copied.</Typography>}
                </Grid>
              </Grid>
            ) : (
              <TextField fullWidth multiline minRows={8} maxRows={14} label="CREATE TABLE … statements (MySQL dialect)" value={script} onChange={(e) => setScript(e.target.value)} InputProps={{ sx: { fontFamily: MONO, fontSize: 12 } }} />
            )}
            <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
              <Why reason={needs(can, 'write')}><Button variant="outlined" disabled={!!busy || !canEdit} onClick={inspect}>{busy === 'inspect' ? 'Inspecting…' : 'Inspect schema'}</Button></Why>
              <Why reason={needs(can, 'write')}><Button variant="contained" disabled={!!busy || !canEdit} onClick={analyze}>{busy === 'generate' ? 'Analyzing…' : 'Generate ontology'}</Button></Why>
            </Stack>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>Credentials are used for this request only and are never stored. Only schema metadata is read. “Inspect schema” is optional; “Generate ontology” goes straight to the review step.</Typography>
            <Collapse in={!!schema}><Box sx={{ mt: 2 }}><Typography variant="subtitle2" gutterBottom>Schema preview — {schema?.tables.length} tables</Typography><SchemaPreview schema={schema} /></Box></Collapse>
          </Panel>
        </Grid>
        <Grid item xs={12} md={6}>
          <Panel kind="source" title="Files & documents → Ontology" subtitle="Structured files, RDF or documents with page/section provenance" sx={{ height: '100%' }}>
            <Box
              role="button" tabIndex={canEdit ? 0 : -1} aria-disabled={!canEdit} aria-label="Drop a file here or browse for one"
              onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); canEdit && upload([...e.dataTransfer.files]); }}
              onClick={pick} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } }}
              sx={{
                p: 4, border: 2, borderStyle: 'dashed', borderColor: drag ? kind.class : surface.lineStrong, borderRadius: 2, textAlign: 'center',
                cursor: canEdit ? 'pointer' : 'not-allowed', bgcolor: drag ? alpha(kind.class, 0.08) : surface.sunken, opacity: canEdit ? 1 : 0.6,
                '&:hover': canEdit ? { borderColor: kind.class } : undefined,
              }}
            >
              {busy === 'upload' ? <CircularProgress /> : <><UploadFileIcon sx={{ fontSize: 34, color: kind.source }} /><Typography>Drop a file here or click to browse</Typography><Typography variant="caption" color="text.secondary">{FORMATS}</Typography></>}
              <input ref={fileRef} data-testid="file-input" type="file" hidden onChange={(e) => { upload([...e.target.files]); e.target.value = ''; }} />
            </Box>
            <Stack spacing={0.25} sx={{ mt: 1.25 }}>
              <FormControlLabel control={<Checkbox size="small" checked={withRecords} onChange={(e) => setWithRecords(e.target.checked)} />} label={<Typography variant="body2">Copy sample records as individuals <Typography component="span" variant="caption" color="text.secondary">(CSV, Excel, Parquet, SQLite · up to {rows} rows/table · sensitive columns skipped)</Typography></Typography>} />
              <FormControlLabel control={<Checkbox size="small" checked={aiExtra} disabled={!features?.aiConfigured} onChange={(e) => setAiExtra(e.target.checked)} />} label={<Typography variant="body2">Ask the AI for extra concepts in documents <Typography component="span" variant="caption" color="text.secondary">{features?.aiConfigured === undefined ? '' : features.aiConfigured ? '(each must quote the document; low confidence; you review)' : '(not configured on this server: set ANTHROPIC_API_KEY)'}</Typography></Typography>} />
              <Typography variant="caption" color="text.secondary" data-testid="ocr-note">Scanned PDFs: {features?.ocr === undefined ? 'checking OCR support…' : features.ocr ? 'read with OCR (page numbers kept; recognised text may contain errors)' : 'OCR is not installed on this server, so image-only pages cannot be read'}.</Typography>
            </Stack>
            {features?.urlSources && (
              <Box sx={{ mt: 2 }} data-testid="url-source">
                <Divider sx={{ mb: 1.5 }}>or a REST API</Divider>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <TextField size="small" fullWidth label="JSON endpoint URL (https)" value={url} onChange={(e) => setUrl(e.target.value)} />
                  <TextField size="small" label="Authorization header (optional)" type="password" value={urlAuth} onChange={(e) => setUrlAuth(e.target.value)} autoComplete="off" sx={{ minWidth: 200 }} />
                  <Why reason={needs(can, 'write')}><Button variant="outlined" disabled={!!busy || !canEdit || !url.trim()} onClick={fromUrl}>{busy === 'url' ? 'Fetching…' : 'Fetch'}</Button></Why>
                </Stack>
                <Typography variant="caption" color="text.secondary">Only hosts allow-listed by an administrator can be fetched. The header is used once and never stored.</Typography>
              </Box>
            )}
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
              Files are analysed into a reviewable candidate. Documents keep page/section provenance for every discovered concept.
            </Typography>
          </Panel>
        </Grid>
      </Grid>
      {candidate && <CandidateReview candidate={candidate} onDone={() => setCandidate(null)} />}
    </Box>
  );
}
