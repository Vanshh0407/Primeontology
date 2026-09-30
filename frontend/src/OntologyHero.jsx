import { useEffect, useMemo, useRef, useState } from 'react';
import { Box } from '@mui/material';
import { MONO, buildTokens } from './workbench/theme';

/**
 * Rotating 3D ontology graph for the login page. Dependency-free WebGL: the scene is a few dozen points and lines, projected in JS
 * and drawn as glowing point sprites + fading lines. The five labelled concepts and their four relationships are the demo
 * database's actual model (customer, sales_order, sales_order_item, product, supplier — see demo/demo_schema.sql); the small
 * green satellites stand for data properties and the dim points are decorative depth.
 *
 * Decorative only: aria-hidden, pointer-events none, paused when hidden, single static frame under prefers-reduced-motion,
 * and an SVG rendering of the same scene when WebGL is unavailable.
 */
const T = buildTokens('dark');
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const COLORS = { class: hex(T.kind.class), object: hex(T.kind.object), data: hex(T.kind.data), cloud: hex('#8ea6c2') };

const CONCEPTS = [
  { id: 'Customer', p: [-1.15, -0.1, 0.25] },
  { id: 'SalesOrder', p: [-0.3, 0.45, -0.25] },
  { id: 'SalesOrderItem', p: [0.5, -0.2, 0.55] },
  { id: 'Product', p: [1.05, 0.5, -0.2] },
  { id: 'Supplier', p: [0.55, -0.95, -0.65] },
];
const RELATIONS = [[0, 1], [1, 2], [2, 3], [3, 4]];

function rng(seed) { let a = seed; return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const unit = (r) => { const u = r() * 2 - 1; const a = r() * Math.PI * 2; const s = Math.sqrt(1 - u * u); return [s * Math.cos(a), u, s * Math.sin(a)]; };

function buildScene() {
  const r = rng(7);
  const nodes = CONCEPTS.map((c, i) => ({ p: c.p, kind: 'class', size: 46, label: c.id, id: i }));
  const edges = RELATIONS.map(([a, b]) => ({ a, b, kind: 'object', pulse: r() }));
  CONCEPTS.forEach((c, ci) => {
    const n = 5 + Math.floor(r() * 3);
    for (let k = 0; k < n; k++) {
      const d = unit(r); const rad = 0.38 + r() * 0.3;
      nodes.push({ p: [c.p[0] + d[0] * rad, c.p[1] + d[1] * rad, c.p[2] + d[2] * rad], kind: 'data', size: 15 });
      edges.push({ a: ci, b: nodes.length - 1, kind: 'data' });
    }
  });
  const anchors = nodes.length;
  for (let k = 0; k < 30; k++) {
    const d = unit(r); const rad = 1.3 + r() * 0.9;
    nodes.push({ p: [d[0] * rad * 1.15, d[1] * rad * 0.8, d[2] * rad], kind: 'cloud', size: 7 });
    let best = -1; let bd = 1e9;
    for (let i = 0; i < anchors; i++) { const q = nodes[i].p; const p = nodes[nodes.length - 1].p; const dd = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2; if (dd < bd) { bd = dd; best = i; } }
    if (bd < 1.1) edges.push({ a: best, b: nodes.length - 1, kind: 'cloud' });
  }
  return { nodes, edges };
}

const CAM = 3.6;
/** Rotate about Y then tilt about X, then perspective. Returns per-node {x, y (unit space), s (scale), d (0 far … 1 near)}. */
function project(scene, yaw, tilt) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw), cx = Math.cos(tilt), sx = Math.sin(tilt);
  return scene.nodes.map((n) => {
    const [x0, y0, z0] = n.p;
    const x1 = x0 * cy + z0 * sy; const z1 = -x0 * sy + z0 * cy;
    const y2 = y0 * cx - z1 * sx; const z2 = y0 * sx + z1 * cx;
    const s = CAM / (CAM - z2);
    return { x: x1 * s, y: y2 * s, s, d: Math.min(1, Math.max(0, (z2 + 1.9) / 3.8)) };
  });
}

const VS_POINT = `attribute vec2 p; attribute float size; attribute vec3 col; attribute float a; varying vec3 vc; varying float va;
void main(){ gl_Position = vec4(p, 0.0, 1.0); gl_PointSize = size; vc = col; va = a; }`;
const FS_POINT = `precision mediump float; varying vec3 vc; varying float va;
void main(){ float d = length(gl_PointCoord - 0.5); if (d > 0.5) discard; float halo = smoothstep(0.5, 0.0, d); float core = smoothstep(0.16, 0.06, d);
  vec3 c = vc * (halo * halo * 0.9 + halo * 0.35 + core * 1.1) * va; gl_FragColor = vec4(c, max(c.r, max(c.g, c.b))); }`;
const VS_LINE = `attribute vec2 p; attribute vec4 c; varying vec4 vcol; void main(){ gl_Position = vec4(p, 0.0, 1.0); vcol = c; }`;
const FS_LINE = `precision mediump float; varying vec4 vcol; void main(){ vec3 c = vcol.rgb * vcol.a; gl_FragColor = vec4(c, max(c.r, max(c.g, c.b))); }`;

function program(gl, vs, fs) {
  const mk = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
  const p = gl.createProgram(); gl.attachShader(p, mk(gl.VERTEX_SHADER, vs)); gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  return p;
}

function StaticGraph({ scene }) {
  const pr = useMemo(() => project(scene, 0.55, 0.28), [scene]);
  const col = (k) => ({ class: T.kind.class, object: T.kind.object, data: T.kind.data, cloud: '#8ea6c2' }[k]);
  return (
    <svg viewBox="-2 -1.5 4 3" role="presentation" aria-hidden style={{ width: '100%', height: '100%' }} data-testid="hero-fallback">
      {scene.edges.map((e, i) => <line key={i} x1={pr[e.a].x} y1={pr[e.a].y} x2={pr[e.b].x} y2={pr[e.b].y} stroke={col(e.kind)} strokeOpacity={e.kind === 'object' ? 0.8 : e.kind === 'data' ? 0.3 : 0.14} strokeWidth={e.kind === 'object' ? 0.014 : 0.006} />)}
      {scene.nodes.map((n, i) => <circle key={i} cx={pr[i].x} cy={pr[i].y} r={(n.size / 46) * 0.045 * pr[i].s} fill={col(n.kind)} fillOpacity={0.35 + pr[i].d * 0.6} />)}
      {scene.nodes.filter((n) => n.label).map((n) => <text key={n.label} x={pr[n.id].x + 0.09} y={pr[n.id].y - 0.05} fontSize="0.085" fontFamily={MONO} fill={T.surface.text} fillOpacity="0.85">{n.label}</text>)}
    </svg>
  );
}

export default function OntologyHero() {
  const scene = useMemo(buildScene, []);
  const wrap = useRef(null);
  const labelRefs = useRef([]);
  const [fallback, setFallback] = useState(false);
  const [reduced, setReduced] = useState(() => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);

  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!mq) return undefined;
    const on = () => setReduced(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);

  useEffect(() => {
    if (!wrap.current) return undefined;
    // The canvas is created per effect run: a canvas whose context was released (React StrictMode re-runs effects) cannot be reused.
    const cv = document.createElement('canvas');
    cv.style.cssText = 'width:100%;height:100%;display:block';
    wrap.current.prepend(cv);
    let gl = null;
    try { gl = cv.getContext('webgl', { antialias: true, alpha: true, premultipliedAlpha: true, powerPreference: 'low-power' }); } catch { gl = null; }
    if (!gl) { cv.remove(); setFallback(true); return undefined; }
    let pp; let lp;
    try { pp = program(gl, VS_POINT, FS_POINT); lp = program(gl, VS_LINE, FS_LINE); } catch (err) { console.warn('[ontology-hero] WebGL unavailable, using static graph:', err.message); cv.remove(); setFallback(true); return undefined; }

    const N = scene.nodes.length; const E = scene.edges.length; const PULSES = scene.edges.filter((e) => e.kind === 'object');
    const pointData = new Float32Array((N + PULSES.length) * 7);
    const lineData = new Float32Array(E * 2 * 6);
    const pbuf = gl.createBuffer(); const lbuf = gl.createBuffer();
    const pl = { p: gl.getAttribLocation(pp, 'p'), size: gl.getAttribLocation(pp, 'size'), col: gl.getAttribLocation(pp, 'col'), a: gl.getAttribLocation(pp, 'a') };
    const ll = { p: gl.getAttribLocation(lp, 'p'), c: gl.getAttribLocation(lp, 'c') };
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE); // additive: glow on the dark backdrop
    gl.clearColor(0, 0, 0, 0);

    let w = 1, h = 1, dpr = 1, raf = 0, start = performance.now(), yaw = 0.55;
    const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
    const resize = () => {
      const r = wrap.current.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = Math.max(1, r.width); h = Math.max(1, r.height);
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
      gl.viewport(0, 0, cv.width, cv.height);
    };

    const frame = (now) => {
      const t = (now - start) / 1000;
      if (!reduced) { yaw = 0.55 + t * 0.09; pointer.x += (pointer.tx - pointer.x) * 0.04; pointer.y += (pointer.ty - pointer.y) * 0.04; }
      const pr = project(scene, yaw + pointer.x * 0.35, 0.28 + pointer.y * 0.2);
      const U = Math.min(w, h) * (w < 700 ? 0.3 : 0.27);
      const clip = (q) => [((q.x * U + w / 2) / w) * 2 - 1, 1 - ((q.y * U + h / 2) / h) * 2];

      let o = 0;
      scene.nodes.forEach((n, i) => {
        const [x, y] = clip(pr[i]); const c = COLORS[n.kind];
        const a = (n.kind === 'cloud' ? 0.35 : n.kind === 'data' ? 0.55 : 0.95) * (0.35 + pr[i].d * 0.65);
        pointData.set([x, y, n.size * pr[i].s * dpr, c[0], c[1], c[2], a], o); o += 7;
        if (n.label && labelRefs.current[n.id]) {
          const el = labelRefs.current[n.id];
          el.style.transform = `translate(${(pr[i].x * U + w / 2 + 14).toFixed(1)}px, ${(pr[i].y * U + h / 2 - 8).toFixed(1)}px)`;
          el.style.opacity = (0.45 + pr[i].d * 0.55).toFixed(2);
        }
      });
      PULSES.forEach((e) => {
        const k = reduced ? 0.5 : (t * 0.16 + e.pulse) % 1; const A = pr[e.a], B = pr[e.b];
        const [x, y] = clip({ x: A.x + (B.x - A.x) * k, y: A.y + (B.y - A.y) * k });
        const c = COLORS.object; pointData.set([x, y, 14 * dpr, c[0], c[1], c[2], reduced ? 0 : 0.9], o); o += 7;
      });
      let q = 0;
      scene.edges.forEach((e) => {
        const A = pr[e.a], B = pr[e.b]; const c = COLORS[e.kind];
        const al = (e.kind === 'object' ? 1 : e.kind === 'data' ? 0.5 : 0.24);
        [[A, clip(A)], [B, clip(B)]].forEach(([P, xy]) => { lineData.set([xy[0], xy[1], c[0], c[1], c[2], al * (0.3 + P.d * 0.7)], q); q += 6; });
      });

      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(lp); gl.bindBuffer(gl.ARRAY_BUFFER, lbuf); gl.bufferData(gl.ARRAY_BUFFER, lineData, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(ll.p); gl.vertexAttribPointer(ll.p, 2, gl.FLOAT, false, 24, 0);
      gl.enableVertexAttribArray(ll.c); gl.vertexAttribPointer(ll.c, 4, gl.FLOAT, false, 24, 8);
      gl.drawArrays(gl.LINES, 0, E * 2);
      gl.useProgram(pp); gl.bindBuffer(gl.ARRAY_BUFFER, pbuf); gl.bufferData(gl.ARRAY_BUFFER, pointData, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(pl.p); gl.vertexAttribPointer(pl.p, 2, gl.FLOAT, false, 28, 0);
      gl.enableVertexAttribArray(pl.size); gl.vertexAttribPointer(pl.size, 1, gl.FLOAT, false, 28, 8);
      gl.enableVertexAttribArray(pl.col); gl.vertexAttribPointer(pl.col, 3, gl.FLOAT, false, 28, 12);
      gl.enableVertexAttribArray(pl.a); gl.vertexAttribPointer(pl.a, 1, gl.FLOAT, false, 28, 24);
      gl.drawArrays(gl.POINTS, 0, N + PULSES.length);
      if (!reduced) raf = requestAnimationFrame(frame);
    };

    const onLost = (e) => { e.preventDefault(); cancelAnimationFrame(raf); cv.remove(); setFallback(true); };
    const onMove = (e) => { pointer.tx = (e.clientX / window.innerWidth - 0.5) * 2; pointer.ty = (e.clientY / window.innerHeight - 0.5) * 2; };
    cv.addEventListener('webglcontextlost', onLost);
    const ro = new ResizeObserver(() => { resize(); if (reduced) frame(performance.now()); });
    ro.observe(wrap.current);
    resize();
    if (!reduced) window.addEventListener('pointermove', onMove, { passive: true });
    frame(performance.now());
    return () => { cancelAnimationFrame(raf); ro.disconnect(); window.removeEventListener('pointermove', onMove); cv.removeEventListener('webglcontextlost', onLost); gl.getExtension('WEBGL_lose_context')?.loseContext(); cv.remove(); };
  }, [scene, reduced]);

  return (
    <Box ref={wrap} aria-hidden data-testid="ontology-hero" data-mode={fallback ? 'fallback' : reduced ? 'static' : 'webgl'} sx={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'hidden' }}>
      {fallback ? <StaticGraph scene={scene} /> : (
        <>
          {scene.nodes.filter((n) => n.label).map((n) => (
            <Box key={n.label} ref={(el) => { labelRefs.current[n.id] = el; }} sx={{ position: 'absolute', left: 0, top: 0, fontFamily: MONO, fontSize: 12, color: T.surface.text, whiteSpace: 'nowrap', willChange: 'transform', textShadow: '0 0 8px #070b11, 0 0 2px #070b11', display: { xs: 'none', sm: 'block' } }}>
              {n.label}
            </Box>
          ))}
        </>
      )}
    </Box>
  );
}
