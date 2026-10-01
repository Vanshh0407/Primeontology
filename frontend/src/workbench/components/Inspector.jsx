import { useEffect, useState } from 'react';
import {
  Autocomplete, Box, Button, Checkbox, Chip, FormControlLabel, IconButton, MenuItem, Select, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import {
  CARDINALITIES, XSD_TYPES, addProperty, deleteClass, deleteProperty, renameClass, updateClass, updateProperty,
} from '../modelOps';
import {
  addIndividual, deleteIndividual, groupsOf, isInstanceOf, propertiesFor, renameIndividual, setGroup, setIndividualLinks, setIndividualValue,
  suggestIndividualName,
} from '../individuals';
import { useWB } from '../context';
import { MONO, useOnt } from '../theme';
import { KindChip, KindGlyph, RoleNotice } from './ui';

function Commit({ value, onCommit, ...props }) {
  const [v, setV] = useState(value ?? '');
  useEffect(() => setV(value ?? ''), [value]);
  return <TextField size="small" value={v} onChange={(e) => setV(e.target.value)} onBlur={() => v !== (value ?? '') && onCommit(v)}
    onKeyDown={(e) => e.key === 'Enter' && e.target.blur()} {...props} />;
}

function Section({ kind, title, count, hint, children }) {
  const { surface } = useOnt();
  return (
    <Box component="section" sx={{ pt: 1.5, borderTop: 1, borderColor: surface.line }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
        {kind && <KindGlyph kind={kind} size={14} />}
        <Typography variant="subtitle2" component="h3">{title}{count != null ? ` (${count})` : ''}</Typography>
      </Stack>
      {hint && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1, mt: -0.5 }}>{hint}</Typography>}
      <Stack spacing={1.25}>{children}</Stack>
    </Box>
  );
}

export function ClassEditor({ name }) {
  const { model, edit, canEdit, openConcept } = useWB();
  const { kind } = useOnt();
  const c = model.classes.find((x) => x.name === name);
  const [newProp, setNewProp] = useState({ name: '', datatype: 'string' });
  const [newRel, setNewRel] = useState({ name: '', range: '' });
  if (!c) return <Typography color="text.secondary">Class not found.</Typography>;
  const names = model.classes.map((x) => x.name).filter((n) => n !== name);
  const dps = model.dataProperties.filter((p) => p.domain === name);
  const ops = model.objectProperties.filter((p) => p.domain === name);
  const incoming = model.objectProperties.filter((p) => p.range === name);
  const instances = (model.individuals || []).filter((i) => i.class === name);
  const dis = !canEdit;

  return (
    <Stack spacing={1.75} data-testid="class-editor">
      <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={1}>
        <Stack direction="row" spacing={1.25} alignItems="center" sx={{ minWidth: 0 }}>
          <KindGlyph kind="class" size={20} />
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', lineHeight: 1.2 }}>Class</Typography>
            <Typography variant="h6" component="h2" noWrap>{c.label || c.name}</Typography>
          </Box>
        </Stack>
        <Stack direction="row" spacing={0.5} flexShrink={0}>
          <Button size="small" onClick={() => openConcept(name)}>Explore</Button>
          {canEdit && <Button size="small" color="error" startIcon={<DeleteIcon />} onClick={() => window.confirm(`Delete class ${name} and its properties?`) && edit((m) => deleteClass(m, name), { clear: true })}>Delete</Button>}
        </Stack>
      </Stack>
      <RoleNotice cap="write" sx={{ mb: 0 }}>Fields are read-only for your role.</RoleNotice>

      <Section title="Identity">
        <Commit label="Name (IRI local name)" value={c.name} disabled={dis} onCommit={(v) => edit((m) => renameClass(m, name, v), { select: { kind: 'class', name: v } })} />
        <Commit label="Label" value={c.label} disabled={dis} onCommit={(v) => edit((m) => updateClass(m, name, { label: v }))} />
        <Commit label="Description" value={c.comment} multiline minRows={2} disabled={dis} onCommit={(v) => edit((m) => updateClass(m, name, { comment: v }))} />
        <Autocomplete freeSolo size="small" disabled={dis} options={groupsOf(model)} value={c.group || ''} onChange={(_, v) => edit((m) => setGroup(m, name, (v || '').trim()))}
          onBlur={(e) => { const v = e.target.value.trim(); if (v !== (c.group || '')) edit((m) => setGroup(m, name, v)); }}
          renderInput={(p) => <TextField {...p} label="Group (colour + frame in the graph)" />} />
      </Section>

      <Section kind="inherit" title="Inheritance & logic" hint="Parents define what this class inherits. Equivalent and disjoint classes feed the reasoner.">
        <Autocomplete multiple size="small" disabled={dis} options={names} value={c.parents || []} onChange={(_, v) => edit((m) => updateClass(m, name, { parents: v }))} renderInput={(p) => <TextField {...p} label="Parent classes (inheritance)" />} />
        <Autocomplete multiple size="small" disabled={dis} options={names} value={c.equivalentTo || []} onChange={(_, v) => edit((m) => updateClass(m, name, { equivalentTo: v }))} renderInput={(p) => <TextField {...p} label="Equivalent classes" />} />
        <Autocomplete multiple size="small" disabled={dis} options={names} value={c.disjointWith || []} onChange={(_, v) => edit((m) => updateClass(m, name, { disjointWith: v }))} renderInput={(p) => <TextField {...p} label="Disjoint classes" />} />
        <Autocomplete multiple freeSolo size="small" disabled={dis} options={[]} value={c.synonyms || []} onChange={(_, v) => edit((m) => updateClass(m, name, { synonyms: v }))} renderInput={(p) => <TextField {...p} label="Synonyms (used by search & mapping)" />} />
      </Section>

      {c.source && Object.keys(c.source).length > 0 && (
        <Section kind="provenance" title="Provenance">
          <Box>{Object.entries(c.source).filter(([, v]) => v).map(([k, v]) => <Chip key={k} size="small" variant="outlined" sx={{ mr: 0.5, mb: 0.5 }} label={`${k}: ${v}`} />)}</Box>
        </Section>
      )}

      <Section kind="data" title="Data properties" count={dps.length}>
        {dps.length === 0 && <Typography variant="body2" color="text.secondary">No data properties yet.</Typography>}
        {dps.length > 0 && (
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead><TableRow><TableCell>Name</TableCell><TableCell>Type</TableCell><TableCell>Req</TableCell><TableCell>Min</TableCell><TableCell>Max</TableCell><TableCell /></TableRow></TableHead>
              <TableBody>
                {dps.map((p) => (
                  <TableRow key={p.name}>
                    <TableCell><Commit value={p.name} disabled={dis} inputProps={{ 'aria-label': `Property name ${p.name}` }} onCommit={(v) => edit((m) => updateProperty(m, 'data', name, p.name, { name: v }))} /></TableCell>
                    <TableCell><Select size="small" value={p.datatype} disabled={dis} inputProps={{ 'aria-label': `Datatype of ${p.name}` }} sx={{ fontFamily: MONO, fontSize: 12 }} onChange={(e) => edit((m) => updateProperty(m, 'data', name, p.name, { datatype: e.target.value }))}>{XSD_TYPES.map((t) => <MenuItem key={t} value={t}>{t}</MenuItem>)}</Select></TableCell>
                    <TableCell><Checkbox size="small" disabled={dis} checked={!!p.required} inputProps={{ 'aria-label': `${p.name} required` }} onChange={(e) => edit((m) => updateProperty(m, 'data', name, p.name, { required: e.target.checked }))} /></TableCell>
                    <TableCell><Commit type="number" sx={{ width: 64 }} inputProps={{ 'aria-label': `${p.name} min cardinality` }} value={p.minCardinality ?? ''} disabled={dis} onCommit={(v) => edit((m) => updateProperty(m, 'data', name, p.name, { minCardinality: v === '' ? undefined : Number(v) }))} /></TableCell>
                    <TableCell><Commit type="number" sx={{ width: 64 }} inputProps={{ 'aria-label': `${p.name} max cardinality` }} value={p.maxCardinality ?? ''} disabled={dis} onCommit={(v) => edit((m) => updateProperty(m, 'data', name, p.name, { maxCardinality: v === '' ? undefined : Number(v) }))} /></TableCell>
                    <TableCell>{canEdit && <Tooltip title={`Delete ${p.name}`}><IconButton size="small" aria-label={`Delete property ${p.name}`} onClick={() => edit((m) => deleteProperty(m, 'data', name, p.name))}><DeleteIcon fontSize="small" /></IconButton></Tooltip>}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
        {canEdit && (
          <Stack direction="row" spacing={1}>
            <TextField size="small" label="New property" value={newProp.name} onChange={(e) => setNewProp({ ...newProp, name: e.target.value })} sx={{ flex: 1, minWidth: 0 }} />
            <Select size="small" value={newProp.datatype} inputProps={{ 'aria-label': 'New property datatype' }} onChange={(e) => setNewProp({ ...newProp, datatype: e.target.value })}>{XSD_TYPES.map((t) => <MenuItem key={t} value={t}>{t}</MenuItem>)}</Select>
            <Button size="small" startIcon={<AddIcon />} disabled={!newProp.name} onClick={() => edit((m) => addProperty(m, 'data', { name: newProp.name, domain: name, datatype: newProp.datatype }), { after: () => setNewProp({ name: '', datatype: 'string' }) })}>Add</Button>
          </Stack>
        )}
      </Section>

      <Section kind="object" title={`Relationships from ${name}`} count={ops.length}>
        {ops.length === 0 && <Typography variant="body2" color="text.secondary">No outgoing relationships.</Typography>}
        {ops.map((p) => (
          <Stack key={p.name} direction="row" spacing={1} alignItems="center">
            <Chip size="small" clickable label={`${p.name} → ${p.range}`} onClick={() => edit(null, { select: { kind: 'relationship', domain: name, name: p.name } })} sx={{ borderColor: kind.object, color: 'text.primary' }} variant="outlined" />
            <Typography variant="caption" color="text.secondary" sx={{ fontFamily: MONO }}>{p.cardinality}</Typography>
          </Stack>
        ))}
        {canEdit && (
          <Stack direction="row" spacing={1}>
            <TextField size="small" label="New relationship" value={newRel.name} onChange={(e) => setNewRel({ ...newRel, name: e.target.value })} sx={{ flex: 1, minWidth: 0 }} />
            <Autocomplete size="small" sx={{ minWidth: 130, flex: 1 }} options={model.classes.map((x) => x.name)} value={newRel.range || null} onChange={(_, v) => setNewRel({ ...newRel, range: v || '' })} renderInput={(p) => <TextField {...p} label="Range" />} />
            <Button size="small" startIcon={<AddIcon />} disabled={!newRel.name || !newRel.range} onClick={() => edit((m) => addProperty(m, 'object', { name: newRel.name, domain: name, range: newRel.range }), { after: () => setNewRel({ name: '', range: '' }) })}>Add</Button>
          </Stack>
        )}
      </Section>

      <Section kind="provenance" title="Individuals" count={instances.length} hint="Records (instances) of this class. They make queries return real rows.">
        <Box>{instances.slice(0, 12).map((i) => <Chip key={i.name} size="small" clickable variant="outlined" sx={{ mr: 0.5, mb: 0.5 }} label={i.name} onClick={() => edit(null, { select: { kind: 'individual', name: i.name } })} />)}{instances.length > 12 && <Typography variant="caption" color="text.secondary">+{instances.length - 12} more in the Explorer</Typography>}</Box>
        {canEdit && <Button size="small" startIcon={<AddIcon />} data-testid="add-individual-inline" onClick={() => { const n = suggestIndividualName(model, name); edit((m) => addIndividual(m, { name: n, class: name }), { select: { kind: 'individual', name: n } }); }}>Add individual</Button>}
      </Section>

      {incoming.length > 0 && (
        <Section kind="object" title="Incoming" count={incoming.length}>
          <Box>{incoming.map((p) => <Chip key={`${p.domain}.${p.name}`} size="small" clickable variant="outlined" sx={{ mr: 0.5, mb: 0.5 }} label={`${p.domain} —${p.name}→`} onClick={() => edit(null, { select: { kind: 'relationship', domain: p.domain, name: p.name } })} />)}</Box>
        </Section>
      )}
    </Stack>
  );
}

const CARD_HINT = {
  'many-to-one': (d, r) => `Many ${d} can relate to one ${r}.`,
  'one-to-many': (d, r) => `One ${d} can relate to many ${r}.`,
  'many-to-many': (d, r) => `Many ${d} can relate to many ${r}.`,
  'one-to-one': (d, r) => `One ${d} relates to one ${r}.`,
};

export function RelationshipEditor({ domain, name }) {
  const { model, edit, canEdit } = useWB();
  const { kind, surface } = useOnt();
  const p = model.objectProperties.find((x) => x.domain === domain && x.name === name);
  if (!p) return <Typography color="text.secondary">Relationship not found.</Typography>;
  const classes = model.classes.map((c) => c.name);
  const dis = !canEdit;
  const upd = (patch, sel) => edit((m) => updateProperty(m, 'object', domain, name, patch), sel ? { select: sel } : undefined);
  const card = p.cardinality || 'many-to-many';
  return (
    <Stack spacing={1.75} data-testid="relationship-editor">
      <Stack direction="row" spacing={1.25} alignItems="center">
        <KindGlyph kind="object" size={20} />
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', lineHeight: 1.2 }}>Relationship (object property)</Typography>
          <Typography variant="h6" component="h2">{p.label || p.name}</Typography>
        </Box>
      </Stack>
      {/* domain —name→ range, so the direction is visible at a glance */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ p: 1.25, borderRadius: 1.5, bgcolor: surface.sunken, border: 1, borderColor: surface.line, flexWrap: 'wrap', rowGap: 0.5 }} aria-label={`${p.domain} ${p.name} ${p.range}`}>
        <KindChip kind="class" label={p.domain} />
        <Stack alignItems="center" sx={{ color: kind.object }}><Typography variant="caption" sx={{ fontFamily: MONO, lineHeight: 1 }}>{p.name}</Typography><ArrowForwardIcon fontSize="small" /></Stack>
        <KindChip kind="class" label={p.range} />
        <Chip size="small" variant="outlined" label={card} sx={{ fontFamily: MONO, fontSize: 11 }} />
      </Stack>
      <RoleNotice cap="write" sx={{ mb: 0 }}>Fields are read-only for your role.</RoleNotice>
      <Section title="Identity">
        <Commit label="Name" value={p.name} disabled={dis} onCommit={(v) => upd({ name: v }, { kind: 'relationship', domain, name: v })} />
        <Commit label="Label" value={p.label} disabled={dis} onCommit={(v) => upd({ label: v })} />
        <Commit label="Description" value={p.comment} multiline minRows={2} disabled={dis} onCommit={(v) => upd({ comment: v })} />
      </Section>
      <Section kind="object" title="Domain, range & cardinality">
        <Autocomplete size="small" disabled={dis} options={classes} value={p.domain} disableClearable onChange={(_, v) => upd({ domain: v }, { kind: 'relationship', domain: v, name })} renderInput={(x) => <TextField {...x} label="Domain (from)" InputLabelProps={{ ...x.InputLabelProps, shrink: true }} />} />
        <Autocomplete size="small" disabled={dis} options={classes} value={p.range} disableClearable onChange={(_, v) => upd({ range: v })} renderInput={(x) => <TextField {...x} label="Range (to)" InputLabelProps={{ ...x.InputLabelProps, shrink: true }} />} />
        <TextField select size="small" label="Cardinality" disabled={dis} value={card} onChange={(e) => upd({ cardinality: e.target.value })} helperText={(CARD_HINT[card] || (() => ''))(p.domain, p.range)}>{CARDINALITIES.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}</TextField>
        <Stack direction="row" spacing={1}>
          <Commit label="Min" type="number" value={p.minCardinality ?? ''} disabled={dis} onCommit={(v) => upd({ minCardinality: v === '' ? undefined : Number(v) })} />
          <Commit label="Max" type="number" value={p.maxCardinality ?? ''} disabled={dis} onCommit={(v) => upd({ maxCardinality: v === '' ? undefined : Number(v) })} />
        </Stack>
        <FormControlLabel disabled={dis} control={<Checkbox checked={!!p.required} onChange={(e) => upd({ required: e.target.checked })} />} label="Required" />
        <TextField select size="small" label="Inverse of" disabled={dis} value={p.inverseOf || ''} onChange={(e) => upd({ inverseOf: e.target.value || undefined })}>
          <MenuItem value="">(none)</MenuItem>{model.objectProperties.filter((x) => x !== p).map((x) => <MenuItem key={`${x.domain}.${x.name}`} value={x.name}>{x.domain}.{x.name}</MenuItem>)}
        </TextField>
      </Section>
      {p.source && p.source.table && (
        <Section kind="provenance" title="Provenance">
          <Box><Chip size="small" variant="outlined" label={`from ${p.source.table}${p.source.column ? '.' + p.source.column : ''}${p.source.inferred ? ' (inferred)' : ''}`} /></Box>
        </Section>
      )}
      {canEdit && <Tooltip title="Delete this relationship"><Button color="error" variant="outlined" startIcon={<DeleteIcon />} onClick={() => edit((m) => deleteProperty(m, 'object', domain, name), { clear: true })}>Delete relationship</Button></Tooltip>}
    </Stack>
  );
}

export function IndividualEditor({ name }) {
  const { model, edit, canEdit } = useWB();
  const { kind } = useOnt();
  const ind = (model.individuals || []).find((i) => i.name === name);
  if (!ind) return <Typography color="text.secondary">Individual not found.</Typography>;
  const dis = !canEdit;
  const props = propertiesFor(model, ind.class);
  return (
    <Stack spacing={1.75} data-testid="individual-editor">
      <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={1}>
        <Stack direction="row" spacing={1.25} alignItems="center" sx={{ minWidth: 0 }}>
          <KindGlyph kind="provenance" size={20} />
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', lineHeight: 1.2 }}>Individual (record)</Typography>
            <Typography variant="h6" component="h2" noWrap>{ind.label || ind.name}</Typography>
          </Box>
        </Stack>
        {canEdit && <Button size="small" color="error" startIcon={<DeleteIcon />} onClick={() => window.confirm(`Delete individual ${name}? Links to it are removed.`) && edit((m) => deleteIndividual(m, name), { clear: true })}>Delete</Button>}
      </Stack>
      <RoleNotice cap="write" sx={{ mb: 0 }}>Fields are read-only for your role.</RoleNotice>
      <Section title="Identity">
        <Commit label="Name" value={ind.name} disabled={dis} onCommit={(v) => edit((m) => renameIndividual(m, name, v), { select: { kind: 'individual', name: v } })} />
        <Box><Typography variant="caption" color="text.secondary">Instance of </Typography><KindChip kind="class" label={ind.class} onClick={() => edit(null, { select: { kind: 'class', name: ind.class } })} /></Box>
        {ind.source && ind.source.table && <Box><Chip size="small" variant="outlined" label={`from ${ind.source.table}${ind.source.row ? ' · row ' + ind.source.row : ''}`} /></Box>}
      </Section>
      <Section kind="data" title="Values" count={props.data.length} hint="Typed per the property's datatype. Empty removes the value.">
        {props.data.length === 0 && <Typography variant="body2" color="text.secondary">{ind.class} has no data properties.</Typography>}
        {props.data.map((p) => p.datatype === 'boolean' ? (
          <FormControlLabel key={p.name} disabled={dis} label={`${p.name}${p.required ? ' *' : ''}`} control={<Checkbox checked={ind.data?.[p.name] === true || ind.data?.[p.name] === 'true' || ind.data?.[p.name] === 1} onChange={(e) => edit((m) => setIndividualValue(m, name, p.name, e.target.checked))} />} />
        ) : (
          <Commit key={`${name}.${p.name}`} label={`${p.name}${p.required ? ' *' : ''}`} value={ind.data?.[p.name] ?? ''} disabled={dis}
            type={p.datatype === 'date' ? 'date' : 'text'} InputLabelProps={p.datatype === 'date' ? { shrink: true } : undefined}
            helperText={p.datatype} FormHelperTextProps={{ sx: { fontFamily: MONO, color: kind.data } }}
            onCommit={(v) => edit((m) => setIndividualValue(m, name, p.name, v))} />
        ))}
      </Section>
      <Section kind="object" title="Links" count={props.object.length} hint="Connect to other individuals through the class's relationships.">
        {props.object.length === 0 && <Typography variant="body2" color="text.secondary">{ind.class} has no relationships.</Typography>}
        {props.object.map((p) => {
          const options = (model.individuals || []).filter((i) => i.name !== name || p.range === ind.class).filter((i) => isInstanceOf(model, i, p.range)).map((i) => i.name);
          const value = [].concat(ind.links?.[p.name] || []);
          return (
            <Autocomplete key={`${p.domain}.${p.name}`} multiple size="small" disabled={dis} options={options} value={value}
              onChange={(_, v) => edit((m) => setIndividualLinks(m, name, p.name, v))}
              renderInput={(params) => <TextField {...params} label={`${p.name} → ${p.range}`} />} />
          );
        })}
      </Section>
    </Stack>
  );
}
