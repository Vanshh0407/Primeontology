import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Chip, Collapse, MenuItem, Stack, Switch, FormControlLabel, TextField, Typography } from '@mui/material';
import { NoOntology, PageHeader, Panel } from '../components/ui';
import { downloadBlob, useWB } from '../context';

const GROUND = { grounded: 'success', partially_grounded: 'warning', ungrounded: 'default' };

export default function RagTab() {
  const { api, ontology, notify, can } = useWB();
  const [docs, setDocs] = useState({ documents: [], chunks: 0 });
  const [question, setQuestion] = useState('contracts with suppliers whose invoices were overdue more than 60 days');
  const [out, setOut] = useState(null);
  const [useLlm, setUseLlm] = useState(false);
  const [show, setShow] = useState(false);
  const [doc, setDoc] = useState({ title: '', text: '', docType: 'document', classification: 'internal' });
  const id = ontology?.id;
  const load = useCallback(async () => { if (id) setDocs(await api.ragDocuments(id)); }, [api, id]);
  useEffect(() => { load().catch((e) => notify(e.message, 'error')); /* eslint-disable-next-line */ }, [id]);
  if (!ontology) return <NoOntology />;
  const ask = () => api.ragAsk(id, { question, useLlm }).then(setOut).catch((e) => notify(e.message, 'error'));
  const add = () => api.ragAddDocument(id, doc).then((d) => { notify(`Indexed ${d.chunks} passages`, 'success'); setDoc({ ...doc, title: '', text: '' }); load(); }).catch((e) => notify(e.message, 'error'));

  return (
    <Box sx={{ p: { xs: 1.5, md: 3 }, overflow: 'auto', height: '100%' }}>
      <PageHeader kind="ai" eyebrow="Semantic RAG" title="Ask the enterprise — grounded, cited, or not at all"
        description="Answers come from your documents and the knowledge graph. Every claim cites its source; with weak evidence the system abstains instead of guessing." />
      <Panel title="Ask">
        <Stack direction="row" spacing={1}>
          <TextField fullWidth size="small" value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && ask()} />
          <Button variant="contained" onClick={ask}>Ask</Button>
        </Stack>
        <FormControlLabel control={<Switch size="small" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />} label="Let an LLM phrase the answer (verified against the citations)" />
        {out && (
          <Box sx={{ mt: 1 }} data-testid="rag-answer">
            <Stack direction="row" spacing={1} sx={{ mb: 1 }} flexWrap="wrap" rowGap={1}>
              <Chip color={GROUND[out.grounding]} label={out.grounding.replace('_', ' ')} />
              <Chip variant="outlined" label={`confidence ${Math.round(out.confidence.score * 100)}% (${out.confidence.label})`} />
              <Chip variant="outlined" label={out.mode} />
              {out.withheldByPolicy > 0 && <Chip color="info" variant="outlined" label={`${out.withheldByPolicy} passages hidden by your role`} />}
            </Stack>
            <Alert severity={out.abstained ? 'warning' : 'success'} icon={false}>{out.answer}</Alert>
            {out.note && <Typography variant="caption" color="text.secondary">{out.note}</Typography>}
            {out.citations.map((c) => (
              <Typography key={c.cite} variant="body2" sx={{ mt: 0.5 }}><b>[{c.cite}]</b> {c.kind === 'entity' ? `Entity · ${c.title} (systems: ${(c.sources || []).join(', ')})` : `${c.title}${c.heading ? ` › ${c.heading}` : ''}`}
                <Typography component="span" variant="caption" color="text.secondary"> — {c.excerpt.slice(0, 160)}</Typography></Typography>))}
            <Button size="small" onClick={() => setShow(!show)}>{show ? 'Hide' : 'Show'} how it was found</Button>
            <Collapse in={show}><Typography variant="caption" component="div">
              Signals: {Object.entries(out.signals).map(([k, v]) => `${k} (${v})`).join(', ') || 'none'} · embeddings: {out.embedding} · expanded with: {out.expansion.added.join(', ') || '—'}<br />
              {out.structured && `Graph pattern: ${out.structured.resultClass} where ${out.structured.constraints.map((c) => `${c.class}.${c.property} ${c.op} ${c.value}`).join(', ')} → ${out.structured.total} match(es)`}<br />
              Why: {out.confidence.reasons.join('; ')}</Typography></Collapse>
          </Box>)}
      </Panel>
      <Panel title={`Documents (${docs.documents.length}, ${docs.chunks} passages)`} sx={{ mt: 2 }}
        actions={<><Button size="small" onClick={() => api.ragVectors(ontology.id).then((t) => downloadBlob(new Blob([t], { type: 'application/x-ndjson' }), 'rag-vectors.jsonl')).catch((e) => notify(e.message, 'error'))}>Export vectors</Button><Button size="small" disabled={!can('write')} onClick={() => api.ragReindex(ontology.id).then(() => notify('Re-indexed', 'success'))}>Re-index</Button></>}>
        {docs.documents.map((d) => (
          <Stack key={d.id} direction="row" spacing={1} alignItems="center" sx={{ py: 0.3 }}>
            <Typography variant="body2" sx={{ flex: 1 }}>{d.title} <Typography component="span" variant="caption" color="text.secondary">· {d.docType} · {d.chunks} passages</Typography></Typography>
            <Chip size="small" variant="outlined" label={d.classification} />
            <Button size="small" color="error" disabled={!can('write')} onClick={() => api.ragDeleteDocument(id, d.id).then(load)}>Delete</Button>
          </Stack>))}
        <Stack spacing={1} sx={{ mt: 2 }}>
          <Stack direction="row" spacing={1}>
            <TextField size="small" label="Title" value={doc.title} onChange={(e) => setDoc({ ...doc, title: e.target.value })} sx={{ flex: 1 }} />
            <TextField size="small" label="Type" value={doc.docType} onChange={(e) => setDoc({ ...doc, docType: e.target.value })} sx={{ width: 140 }} />
            <TextField select size="small" label="Classification" value={doc.classification} onChange={(e) => setDoc({ ...doc, classification: e.target.value })} sx={{ width: 160 }}>
              {['public', 'internal', 'confidential', 'restricted'].map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}</TextField>
          </Stack>
          <TextField size="small" multiline minRows={3} label="Text (use lines like “# Heading” to mark sections)" value={doc.text} onChange={(e) => setDoc({ ...doc, text: e.target.value })} />
          <Box><Button variant="outlined" disabled={!can('write') || !doc.title || !doc.text} onClick={add}>Index document</Button></Box>
        </Stack>
      </Panel>
    </Box>
  );
}
