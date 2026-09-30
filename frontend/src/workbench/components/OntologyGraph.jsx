import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background, BackgroundVariant, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, ReactFlowProvider, applyNodeChanges, useNodesInitialized, useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { alpha, useTheme } from '@mui/material/styles';
import { Box, Button, Chip, FormControlLabel, MenuItem, Select, Stack, Switch, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography, useMediaQuery } from '@mui/material';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import SearchIcon from '@mui/icons-material/Search';
import HubIcon from '@mui/icons-material/Hub';
import { LAYOUTS, computeLayout, modelToGraph } from '../layout';
import { MONO, useOnt } from '../theme';

/** Class node: kind-coloured header with a concept glyph, then its data properties as "name  datatype" rows. */
function ClassNode({ data, selected }) {
  const { kind, surface } = useOnt();
  const ring = selected ? kind.class : data.match ? kind.unsaved : surface.lineStrong;
  return (
    <Box
      sx={{
        minWidth: 180, maxWidth: 250, border: '1.5px solid', borderColor: ring, borderRadius: 1.5, bgcolor: surface.raised, opacity: data.dim ? 0.28 : 1,
        boxShadow: selected ? `0 0 0 3px ${alpha(kind.class, 0.28)}, 0 8px 28px ${alpha('#000', 0.35)}` : `0 1px 2px ${alpha('#000', 0.25)}`, overflow: 'hidden',
        transition: 'box-shadow 120ms, border-color 120ms, opacity 120ms',
        '& .react-flow__handle': { width: 9, height: 9, bgcolor: kind.class, border: `2px solid ${surface.raised}`, opacity: data.canConnect ? 0.9 : 0 },
      }}
    >
      <Handle type="target" position={Position.Top} />
      <Box sx={{ px: 1.25, py: 0.75, display: 'flex', alignItems: 'center', gap: 0.75, bgcolor: alpha(kind.class, selected ? 0.24 : 0.14), borderBottom: data.showProps && data.propInfo.length ? `1px solid ${surface.line}` : 'none' }}>
        <HubIcon sx={{ fontSize: 16, color: kind.class }} aria-hidden />
        <Typography component="span" sx={{ fontWeight: 650, fontSize: 13.5, color: 'text.primary', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{data.label}</Typography>
      </Box>
      {data.showProps && data.propInfo.length > 0 && (
        <Box sx={{ px: 1.25, py: 0.5 }}>
          {data.propInfo.slice(0, 4).map((p) => (
            <Box key={p.name} sx={{ display: 'flex', justifyContent: 'space-between', gap: 1.5, fontSize: 11.5, lineHeight: 1.7 }}>
              <Box component="span" sx={{ color: 'text.secondary', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}{p.required ? ' *' : ''}</Box>
              <Box component="span" sx={{ color: kind.data, fontFamily: MONO, fontSize: 10.5, flexShrink: 0 }}>{p.datatype}</Box>
            </Box>
          ))}
          {data.propInfo.length > 4 && <Box sx={{ fontSize: 11, color: 'text.secondary', pt: 0.25 }}>+{data.propInfo.length - 4} more</Box>}
        </Box>
      )}
      <Handle type="source" position={Position.Bottom} />
    </Box>
  );
}
const nodeTypes = { cls: ClassNode };

function LegendItem({ children, glyph }) {
  return <Stack direction="row" spacing={0.75} alignItems="center"><svg width="26" height="10" aria-hidden>{glyph}</svg><span>{children}</span></Stack>;
}
function Legend() {
  const { kind, surface } = useOnt();
  return (
    <Box aria-label="Graph legend" sx={{ position: 'absolute', right: 12, top: 12, zIndex: 5, px: 1.25, py: 0.75, borderRadius: 1.5, bgcolor: alpha(surface.panel, 0.92), border: 1, borderColor: surface.line, fontSize: 11.5, color: 'text.secondary', display: { xs: 'none', sm: 'block' } }}>
      <Stack spacing={0.25}>
        <LegendItem glyph={<><circle cx="5" cy="5" r="4" fill={kind.class} /></>}>Class</LegendItem>
        <LegendItem glyph={<><line x1="0" y1="5" x2="20" y2="5" stroke={kind.object} strokeWidth="2" /><path d="M20 1.5 L26 5 L20 8.5 Z" fill={kind.object} /></>}>Relationship (object property)</LegendItem>
        <LegendItem glyph={<><line x1="0" y1="5" x2="20" y2="5" stroke={kind.inherit} strokeWidth="1.6" strokeDasharray="4 3" /><path d="M20 1.5 L26 5 L20 8.5" fill="none" stroke={kind.inherit} strokeWidth="1.4" /></>}>Inheritance (subClassOf)</LegendItem>
        <LegendItem glyph={<><rect x="0" y="1" width="9" height="8" rx="2" fill={kind.data} /></>}>Data property (inside class)</LegendItem>
      </Stack>
    </Box>
  );
}

function Inner({ model, selection, onSelect, onConnectClasses, onMoved, canEdit, initialLayout = 'TB', minimap = true, toolbar = true }) {
  const rf = useReactFlow();
  const theme = useTheme();
  const { kind, surface } = useOnt();
  const [layoutKind, setLayoutKind] = useState(initialLayout);
  const [search, setSearch] = useState('');
  const [showProps, setShowProps] = useState(true);
  const [show, setShow] = useState(['object', 'subclass']);
  const [mode, setMode] = useState('relationship');
  const [positions, setPositions] = useState({});
  const small = useMediaQuery(theme.breakpoints.down('sm'));

  const graph = useMemo(() => modelToGraph(model), [model]);

  // Fit the view once the nodes have been measured. Fitting earlier (before node sizes are known) zooms to the maximum.
  const initialized = useNodesInitialized();
  const idsKey = graph.nodes.map((n) => n.id).join('|');
  const fittedFor = useRef('');
  useEffect(() => {
    if (initialized && fittedFor.current !== idsKey) { fittedFor.current = idsKey; requestAnimationFrame(() => rf.fitView({ padding: 0.2 })); }
  }, [initialized, idsKey, rf]);

  // auto-layout for nodes without saved positions (or when layout kind changes)
  const relayout = useCallback((k) => {
    const pos = computeLayout(graph.nodes.map((n) => n.id), graph.edges, k);
    setPositions(pos);
    onMoved && onMoved(pos);
    setTimeout(() => rf.fitView({ padding: 0.2, duration: 300 }), 50);
  }, [graph, rf, onMoved]);

  useEffect(() => {
    const saved = model.layout || {};
    const missing = graph.nodes.filter((n) => !saved[n.id]);
    if (missing.length === 0) {
      setPositions(saved);
    } else {
      const auto = computeLayout(graph.nodes.map((n) => n.id), graph.edges, layoutKind);
      const merged = { ...auto };
      graph.nodes.forEach((n) => saved[n.id] && (merged[n.id] = saved[n.id]));
      setPositions(merged);
      onMoved && onMoved(merged); // persist the computed layout with the next save
      setTimeout(() => rf.fitView({ padding: 0.2 }), 50);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph.nodes.map((n) => n.id).join('|')]);

  const q = search.trim().toLowerCase();
  const nodes = useMemo(
    () => graph.nodes.map((n) => {
      const match = q && (n.id.toLowerCase().includes(q) || n.label.toLowerCase().includes(q) || n.props.some((p) => p.toLowerCase().includes(q)));
      return {
        id: n.id, type: 'cls', position: positions[n.id] || { x: 0, y: 0 }, ariaLabel: `Class ${n.label}`,
        data: { label: n.label, propInfo: n.propInfo, showProps, match, dim: !!q && !match, canConnect: canEdit },
        selected: selection?.kind === 'class' && selection.name === n.id,
      };
    }),
    [graph.nodes, positions, q, showProps, selection, canEdit],
  );
  const edges = useMemo(
    () => graph.edges.filter((e) => show.includes(e.type)).map((e) => {
      const sub = e.type === 'subclass';
      const sel = selection?.kind === 'relationship' && e.type === 'object' && selection.domain === e.domain && selection.name === e.name;
      const color = sub ? kind.inherit : kind.object;
      return {
        id: e.id, source: e.source, target: e.target, label: e.label, type: 'default', animated: false,
        style: sub ? { strokeDasharray: '6 4', stroke: color, strokeWidth: 1.4 } : { stroke: color, strokeWidth: sel ? 3 : 1.8 },
        markerEnd: { type: sub ? MarkerType.Arrow : MarkerType.ArrowClosed, color, width: 18, height: 18 },
        labelStyle: { fontSize: 11, fill: sub ? kind.inherit : theme.palette.text.primary, fontFamily: MONO, fontWeight: sel ? 700 : 400 },
        labelBgStyle: { fill: surface.raised, fillOpacity: 0.95, stroke: sel ? kind.object : surface.line }, labelBgPadding: [6, 3], labelBgBorderRadius: 4,
        selected: sel, data: e,
      };
    }),
    [graph.edges, show, selection, kind, surface, theme],
  );

  // React Flow needs its dimension/select changes applied to keep measured sizes (minimap, fitView, handles).
  const [rfNodes, setRfNodes] = useState([]);
  useEffect(() => {
    setRfNodes((prev) => nodes.map((n) => {
      const old = prev.find((p) => p.id === n.id);
      return old ? { ...n, measured: old.measured, width: old.width, height: old.height } : n;
    }));
  }, [nodes]);

  const onNodesChange = useCallback((changes) => {
    setRfNodes((ns) => applyNodeChanges(changes, ns));
    setPositions((prev) => {
      const next = { ...prev };
      let moved = false;
      for (const c of changes) if (c.type === 'position' && c.position) { next[c.id] = c.position; moved = true; }
      if (moved && changes.some((c) => c.type === 'position' && c.dragging === false)) onMoved && onMoved(next);
      return moved ? next : prev;
    });
  }, [onMoved]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 420 }} data-testid="ontology-graph">
      {toolbar && <Stack direction="row" spacing={1} alignItems="center" sx={{ px: 1.25, py: 1, flexWrap: 'wrap', rowGap: 1, borderBottom: 1, borderColor: surface.line, bgcolor: surface.panel }}>
        <TextField size="small" placeholder="Search graph…" value={search} onChange={(e) => setSearch(e.target.value)} sx={{ width: { xs: '100%', sm: 200 } }} inputProps={{ 'aria-label': 'Search graph' }}
          InputProps={{ startAdornment: <SearchIcon fontSize="small" sx={{ mr: 0.5, color: 'text.secondary' }} /> }} />
        <Select size="small" value={layoutKind} onChange={(e) => { setLayoutKind(e.target.value); relayout(e.target.value); }} inputProps={{ 'aria-label': 'Layout' }}>
          {LAYOUTS.map((l) => <MenuItem key={l.id} value={l.id}>{l.label}</MenuItem>)}
        </Select>
        <Tooltip title="Re-run the selected layout"><Button size="small" variant="outlined" startIcon={<AccountTreeIcon />} onClick={() => relayout(layoutKind)}>Layout</Button></Tooltip>
        <ToggleButtonGroup size="small" value={show} onChange={(_, v) => v && setShow(v)} aria-label="Visible edge types">
          <ToggleButton value="object">Relationships</ToggleButton>
          <ToggleButton value="subclass">Inheritance</ToggleButton>
        </ToggleButtonGroup>
        <FormControlLabel sx={{ mr: 0 }} control={<Switch size="small" checked={showProps} onChange={(e) => setShowProps(e.target.checked)} />} label="Properties" />
        {canEdit && (
          <ToggleButtonGroup size="small" exclusive value={mode} onChange={(_, v) => v && setMode(v)} aria-label="What dragging between classes creates">
            <ToggleButton value="relationship">Drag = relationship</ToggleButton>
            <ToggleButton value="inheritance">Drag = inheritance</ToggleButton>
          </ToggleButtonGroup>
        )}
        <Chip size="small" variant="outlined" label={`${graph.nodes.length} classes · ${graph.edges.length} links`} />
      </Stack>}
      <Box sx={{ flex: 1, minHeight: 0, position: 'relative', bgcolor: surface.sunken, backgroundImage: `radial-gradient(ellipse 70% 60% at 50% 45%, ${alpha(kind.class, theme.palette.mode === 'dark' ? 0.06 : 0.05)}, transparent 75%)` }}>
        <ReactFlow
          nodes={rfNodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} fitView fitViewOptions={{ padding: 0.2 }} minZoom={0.05} maxZoom={2.5}
          colorMode={theme.palette.mode}
          nodesConnectable={canEdit} deleteKeyCode={null}
          onNodeClick={(_, n) => onSelect({ kind: 'class', name: n.id })}
          onEdgeClick={(_, e) => onSelect(e.data.type === 'object' ? { kind: 'relationship', domain: e.data.domain, name: e.data.name } : { kind: 'class', name: e.source })}
          onPaneClick={() => onSelect(null)}
          onConnect={(c) => canEdit && c.source && c.target && onConnectClasses(c.source, c.target, mode)}
        >
          <Background variant={BackgroundVariant.Lines} gap={40} color={surface.line} lineWidth={0.6} />
          <Controls showInteractive={false} />
          {minimap && !small && <MiniMap pannable zoomable nodeStrokeWidth={3} nodeColor={kind.class} maskColor={alpha(surface.bg, 0.6)} style={{ background: surface.raised, border: `1px solid ${surface.line}` }} />}
        </ReactFlow>
        <Legend />
      </Box>
    </Box>
  );
}

export default function OntologyGraph(props) {
  return <ReactFlowProvider><Inner {...props} /></ReactFlowProvider>;
}
