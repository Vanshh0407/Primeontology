import { useMemo, useState } from 'react';
import { alpha } from '@mui/material/styles';
import { Box, Chip, Collapse, InputAdornment, List, ListItemButton, ListItemText, TextField, Typography } from '@mui/material';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import SearchIcon from '@mui/icons-material/Search';
import { KindGlyph } from './ui';
import { MONO, useOnt } from '../theme';

function Section({ kind, title, count, children, defaultOpen = true, note }) {
  const [open, setOpen] = useState(defaultOpen);
  const { surface } = useOnt();
  return (
    <>
      <ListItemButton dense onClick={() => setOpen(!open)} aria-expanded={open} sx={{ borderTop: 1, borderColor: surface.line, py: 0.75 }}>
        <Box sx={{ mr: 1, display: 'flex' }}><KindGlyph kind={kind} size={14} /></Box>
        <ListItemText primary={title} primaryTypographyProps={{ fontWeight: 600, fontSize: 13 }} />
        <Chip size="small" label={count} sx={{ height: 20, mr: 0.5, '& .MuiChip-label': { px: 0.9, fontSize: 11 } }} />
        {open ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
      </ListItemButton>
      <Collapse in={open} unmountOnExit>
        {note && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', px: 2, pb: 0.5 }}>{note}</Typography>}
        <List dense disablePadding>{children}</List>
      </Collapse>
    </>
  );
}

/** Hierarchical class tree + property lists. */
export default function Explorer({ model, selection, onSelect }) {
  const [filter, setFilter] = useState('');
  const { kind } = useOnt();
  const f = filter.trim().toLowerCase();
  const match = (s) => !f || s.toLowerCase().includes(f);

  const tree = useMemo(() => {
    const byParent = {};
    const names = new Set(model.classes.map((c) => c.name));
    for (const c of model.classes) {
      const ps = (c.parents || []).filter((p) => names.has(p));
      (ps.length ? ps : ['']).forEach((p) => (byParent[p] = byParent[p] || []).push(c.name));
    }
    return byParent;
  }, [model.classes]);

  const rowSx = (selected) => ({
    borderLeft: 2, borderColor: selected ? kind.class : 'transparent',
    '&.Mui-selected': { bgcolor: alpha(kind.class, 0.14) }, '&.Mui-selected:hover': { bgcolor: alpha(kind.class, 0.2) },
  });

  const renderClass = (name, depth, seen = new Set()) => {
    if (seen.has(name)) return null;
    const kids = tree[name] || [];
    const visible = match(name) || kids.some((k) => match(k));
    if (!visible && f) return null;
    const next = new Set(seen).add(name);
    const sel = selection?.kind === 'class' && selection.name === name;
    return (
      <div key={`${depth}-${name}`}>
        <ListItemButton dense selected={sel} onClick={() => onSelect({ kind: 'class', name })} sx={{ pl: 1.5 + depth * 2, ...rowSx(sel) }}>
          {depth > 0 && <Box aria-hidden sx={{ color: kind.inherit, mr: 0.75, fontSize: 12, fontFamily: MONO }}>└</Box>}
          <ListItemText primary={name} primaryTypographyProps={{ fontSize: 13, noWrap: true }} />
        </ListItemButton>
        {kids.map((k) => renderClass(k, depth + 1, next))}
      </div>
    );
  };

  const objs = model.objectProperties.filter((p) => match(p.name) || match(p.domain));
  const dats = model.dataProperties.filter((p) => match(p.name) || match(p.domain));
  const inds = (model.individuals || []).filter((i) => match(i.name));
  return (
    <Box sx={{ height: '100%', overflow: 'auto' }} data-testid="explorer">
      <Box sx={{ p: 1, position: 'sticky', top: 0, bgcolor: 'background.paper', zIndex: 2 }}>
        <TextField size="small" fullWidth placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} inputProps={{ 'aria-label': 'Filter explorer' }}
          InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> }} />
      </Box>
      <List dense disablePadding component="nav" aria-label="Ontology explorer">
        <Section kind="class" title="Classes" count={model.classes.length}>{(tree[''] || []).map((n) => renderClass(n, 0))}</Section>
        <Section kind="object" title="Object properties" count={objs.length} defaultOpen={false}>
          {objs.map((p) => {
            const sel = selection?.kind === 'relationship' && selection.domain === p.domain && selection.name === p.name;
            return (
              <ListItemButton key={`${p.domain}.${p.name}`} dense sx={{ pl: 2, ...rowSx(sel) }} selected={sel} onClick={() => onSelect({ kind: 'relationship', domain: p.domain, name: p.name })}>
                <ListItemText primary={p.name} secondary={`${p.domain} → ${p.range}`} primaryTypographyProps={{ fontSize: 13 }} secondaryTypographyProps={{ fontSize: 11, fontFamily: MONO }} />
              </ListItemButton>
            );
          })}
        </Section>
        <Section kind="data" title="Data properties" count={dats.length} defaultOpen={false}>
          {dats.slice(0, 500).map((p) => (
            <ListItemButton key={`${p.domain}.${p.name}`} dense sx={{ pl: 2 }} onClick={() => onSelect({ kind: 'class', name: p.domain })}>
              <ListItemText primary={p.name} secondary={`${p.domain} · ${p.datatype}`} primaryTypographyProps={{ fontSize: 13 }} secondaryTypographyProps={{ fontSize: 11, fontFamily: MONO }} />
            </ListItemButton>
          ))}
        </Section>
        <Section kind="provenance" title="Individuals" count={inds.length} defaultOpen={false} note="Read-only: individuals can be viewed and queried, but there is no individual editor.">
          {inds.map((i) => (
            <ListItemButton key={i.name} dense sx={{ pl: 2 }} onClick={() => onSelect({ kind: 'class', name: i.class })}>
              <ListItemText primary={i.name} secondary={i.class} primaryTypographyProps={{ fontSize: 13 }} secondaryTypographyProps={{ fontSize: 11 }} />
            </ListItemButton>
          ))}
        </Section>
      </List>
      {model.classes.length === 0 && <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>No classes yet. Add one with the “Class” button above.</Typography>}
    </Box>
  );
}
