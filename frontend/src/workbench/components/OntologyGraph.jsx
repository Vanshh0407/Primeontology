import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background, BackgroundVariant, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, ReactFlowProvider, applyNodeChanges, useNodesInitialized, useReactFlow, useStore,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { alpha, useTheme } from '@mui/material/styles';
import { Box, Button, Chip, FormControlLabel, Menu, MenuItem, Select, Stack, Switch, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography, useMediaQuery } from '@mui/material';
import WorkspacesIcon from '@mui/icons-material/Workspaces';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import SearchIcon from '@mui/icons-material/Search';
import HubIcon from '@mui/icons-material/Hub';
import { LAYOUTS, computeLayout, modelToGraph } from '../layout';
import { MONO, useOnt } from '../theme';
import { BIG_GRAPH, computeFastLayout, computeGroupedLayout, groupBoxes, groupColorIndex } from '../groupLayout';
import { groupsOf, instanceCounts } from '../individuals';

const GROUP_COLORS = ['#3dd6f5', '#7aa9ff', '#4fe3b0', '#ffb86b', '#c792ea', '#ff7a90', '#e5e56f', '#7fdbca'];
const groupColor = (g) => GROUP_COLORS[groupColorIndex(g, GROUP_COLORS.length)];
const NO_GROUP = '\u0000none';

/** Class node: kind-coloured header with a concept glyph, then its data properties as "name  datatype" rows. */
function ClassNode({ data, selected }) {
  const { kind, surface } = useOnt();
  // Level of detail: a big graph zoomed far out only needs coloured, labelled boxes. (boolean selector: re-renders only when crossing the threshold)
  const farOut = useStore((s) => s.transform[2] < 0.32);
  if (data.big && farOut) {
    return (
      <Box sx={{ width: 170, height: 60, border: '2px solid', borderColor: selected ? kind.class : data.groupColor || surface.lineStrong, borderRadius: 1, bgcolor: alpha(data.groupColor || kind.class, 0.28), opacity: data.dim ? 0.25 : 1, display: 'flex', alignItems: 'center', px: 1, fontSize: 22, fontWeight: 600, overflow: 'hidden', whiteSpace: 'nowrap' }}>
        <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />{data.label}<Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      </Box>
    );
  }
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
        <Typography component="span" sx={{ fontWeight: 650, fontSize: 13.5, color: 'text.primary', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{data.label}</Typography>
        {data.instances > 0 && <Tooltip title={`${data.instances} individual${data.instances === 1 ? '' : 's'} (records)`}><Chip size="small" label={data.instances} sx={{ height: 18, fontSize: 10.5, '& .MuiChip-label': { px: 0.75 }, bgcolor: alpha(kind.data, 0.18), color: kind.data }} /></Tooltip>}
      </Box>
      {data.group && <Box sx={{ px: 1.25, py: 0.25, fontSize: 10.5, color: data.groupColor, borderBottom: `1px solid ${surface.line}`, bgcolor: alpha(data.groupColor, 0.08), overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} data-testid="node-group">{data.group}</Box>}
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
/** Background frame drawn around a group of classes (not selectable, never intercepts the pointer). */
function GroupFrame({ data }) {
  return (
    <Box aria-hidden sx={{ width: data.w, height: data.h, border: '1.5px dashed', borderColor: alpha(data.color, 0.7), bgcolor: alpha(data.color, 0.05), borderRadius: 2, pointerEvents: 'none' }}>
      <Typography component="span" sx={{ position: 'absolute', top: 4, left: 12, fontSize: 11.5, fontWeight: 600, color: data.color }}>{data.group} · {data.count}</Typography>
    </Box>
  );
}
const nodeTypes = { cls: ClassNode, groupframe: GroupFrame };

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

function Inner({ model, selection, onSelect, onConnectClasses, onMoved, canEdit, initialLayout = 'TB', minimap = true, toolbar = true, onAutoGroup }) {
  const rf = useReactFlow();
  const theme = useTheme();
  const { kind, surface } = useOnt();
  const [layoutKind, setLayoutKind] = useState(model.classes.length > BIG_GRAPH ? 'fast' : initialLayout);
  const [layoutBusy, setLayoutBusy] = useState(false);
  const [search, setSearch] = useState('');
  const BIG = 80; // beyond this many classes: leaner rendering (property rows hidden by default, only visible elements rendered)
  const [showProps, setShowProps] = useState(model.classes.length <= 60);
  const [groupFilter, setGroupFilter] = useState('');
  const [frames, setFrames] = useState(true);
  const [groupMenu, setGroupMenu] = useState(null);
  const [show, setShow] = useState(['object', 'subclass']);
  const [mode, setMode] = useState('relationship');
  const [positions, setPositions] = useState({});
  const small = useMediaQuery(theme.breakpoints.down('sm'));

  const graph = useMemo(() => modelToGraph(model), [model]);
  const groups = useMemo(() => groupsOf(model), [model]);
  const inst = useMemo(() => instanceCounts(model), [model]);
  const groupOf = useMemo(() => Object.fromEntries(graph.nodes.filter((n) => n.group).map((n) => [n.id, n.group])), [graph.nodes]);
  const big = graph.nodes.length > BIG;
  const manyEdges = graph.edges.length > 150;
  const computePositions = useCallback((k) => {
    const ids = graph.nodes.map((n) => n.id);
    if (k === 'fast') return computeFastLayout(ids, graph.edges, groupOf);
    if (k === 'groups') return computeGroupedLayout(ids, graph.edges, groupOf);
    return computeLayout(ids, graph.edges, k);
  }, [graph, groupOf]);

  // Fit the view once the nodes have been measured. Fitting earlier (before node sizes are known) zooms to the maximum.
  const initialized = useNodesInitialized();
  const idsKey = graph.nodes.map((n) => n.id).join('|');
  const fittedFor = useRef('');
  useEffect(() => {
    if (initialized && fittedFor.current !== idsKey) { fittedFor.current = idsKey; requestAnimationFrame(() => rf.fitView({ padding: 0.2 })); }
  }, [initialized, idsKey, rf]);

  // auto-layout for nodes without saved positions (or when layout kind changes)
  const relayout = useCallback((k) => {
    // Hierarchical layouts of very large graphs take seconds on the main thread: paint a "computing" state first.
    const slow = graph.nodes.length > BIG_GRAPH && !['fast', 'grid', 'circle'].includes(k);
    setLayoutBusy(slow);
    setTimeout(() => {
      const pos = computePositions(k);
      setPositions(pos);
      onMoved && onMoved(pos);
      setLayoutBusy(false);
      setTimeout(() => rf.fitView({ padding: 0.2, duration: 300 }), 50);
    }, slow ? 40 : 0);
  }, [computePositions, rf, onMoved, graph.nodes.length]);

  useEffect(() => {
    const saved = model.layout || {};
    const missing = graph.nodes.filter((n) => !saved[n.id]);
    if (missing.length === 0) {
      setPositions(saved);
    } else {
      const auto = computePositions(layoutKind);
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
      const outOfFocus = groupFilter && (groupFilter === NO_GROUP ? !!n.group : n.group !== groupFilter);
      return {
        id: n.id, type: 'cls', position: positions[n.id] || { x: 0, y: 0 }, ariaLabel: `Class ${n.label}`,
        data: { label: n.label, propInfo: n.propInfo, showProps, match, dim: (!!q && !match) || !!outOfFocus, canConnect: canEdit, big, group: n.group, groupColor: n.group ? groupColor(n.group) : null, instances: inst[n.id] || 0 },
        selected: selection?.kind === 'class' && selection.name === n.id,
      };
    }).concat(frames ? groupBoxes(positions, groupOf).map((b) => ({
      id: `__group:${b.group}`, type: 'groupframe', position: { x: b.x, y: b.y }, draggable: false, selectable: false, connectable: false, focusable: false, zIndex: -1,
      data: { ...b, color: groupColor(b.group) }, style: { width: b.w, height: b.h, pointerEvents: 'none' },
    })) : []),
    [graph.nodes, positions, q, showProps, selection, canEdit, groupFilter, frames, groupOf, inst, big],
  );
  const edges = useMemo(
    () => graph.edges.filter((e) => show.includes(e.type)).map((e) => {
      const sub = e.type === 'subclass';
      const sel = selection?.kind === 'relationship' && e.type === 'object' && selection.domain === e.domain && selection.name === e.name;
      const color = sub ? kind.inherit : kind.object;
      return {
        id: e.id, source: e.source, target: e.target, type: 'default', animated: false,
        label: manyEdges && !sel && !(selection?.kind === 'class' && (e.source === selection.name || e.target === selection.name)) ? undefined : e.label,
        style: sub ? { strokeDasharray: '6 4', stroke: color, strokeWidth: 1.4 } : { stroke: color, strokeWidth: sel ? 3 : 1.8 },
        markerEnd: { type: sub ? MarkerType.Arrow : MarkerType.ArrowClosed, color, width: 18, height: 18 },
        labelStyle: { fontSize: 11, fill: sub ? kind.inherit : theme.palette.text.primary, fontFamily: MONO, fontWeight: sel ? 700 : 400 },
        labelBgStyle: { fill: surface.raised, fillOpacity: 0.95, stroke: sel ? kind.object : surface.line }, labelBgPadding: [6, 3], labelBgBorderRadius: 4,
        selected: sel, data: e,
      };
    }),
    [graph.edges, show, selection, kind, surface, theme, manyEdges],
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
        {(groups.length > 0 || (canEdit && onAutoGroup)) && (
          <>
            <Select size="small" displayEmpty value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)} inputProps={{ 'aria-label': 'Focus on group' }} sx={{ minWidth: 130 }} data-testid="group-filter">
              <MenuItem value=""><em>All groups</em></MenuItem>
              {groups.map((g) => <MenuItem key={g} value={g}><Box component="span" aria-hidden sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: groupColor(g), mr: 1, display: 'inline-block' }} />{g}</MenuItem>)}
              <MenuItem value={NO_GROUP}><em>Ungrouped only</em></MenuItem>
            </Select>
            {groups.length > 0 && <FormControlLabel sx={{ mr: 0 }} control={<Switch size="small" checked={frames} onChange={(e) => setFrames(e.target.checked)} />} label="Frames" />}
          </>
        )}
        {canEdit && onAutoGroup && (
          <>
            <Button size="small" variant="outlined" startIcon={<WorkspacesIcon />} onClick={(e) => setGroupMenu(e.currentTarget)} aria-haspopup="menu" data-testid="auto-group">Group</Button>
            <Menu anchorEl={groupMenu} open={!!groupMenu} onClose={() => setGroupMenu(null)}>
              <MenuItem onClick={() => { setGroupMenu(null); onAutoGroup('cluster'); setLayoutKind('groups'); }}>Group by connected cluster</MenuItem>
              <MenuItem onClick={() => { setGroupMenu(null); onAutoGroup('source'); }}>Group by source (document / database)</MenuItem>
              <MenuItem onClick={() => { setGroupMenu(null); onAutoGroup('clear'); setGroupFilter(''); }}>Clear all groups</MenuItem>
            </Menu>
          </>
        )}
        {layoutBusy && <Chip size="small" color="warning" label="Computing hierarchical layout… (large ontology)" data-testid="layout-busy" />}
        <Chip size="small" variant="outlined" label={`${graph.nodes.length} classes · ${graph.edges.length} links${big ? ' · compact mode' : ''}`} />
      </Stack>}
      <Box sx={{ flex: 1, minHeight: 0, position: 'relative', bgcolor: surface.sunken, backgroundImage: `radial-gradient(ellipse 70% 60% at 50% 45%, ${alpha(kind.class, theme.palette.mode === 'dark' ? 0.06 : 0.05)}, transparent 75%)` }}>
        <ReactFlow
          nodes={rfNodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} fitView fitViewOptions={{ padding: 0.2 }} minZoom={0.05} maxZoom={2.5}
          colorMode={theme.palette.mode}
          nodesConnectable={canEdit} deleteKeyCode={null} onlyRenderVisibleElements={big}
          onNodeClick={(_, n) => n.type === 'cls' && onSelect({ kind: 'class', name: n.id })}
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
