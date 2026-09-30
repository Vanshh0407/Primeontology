import { alpha, createTheme, useTheme } from '@mui/material/styles';

/**
 * Ontology design tokens.
 *
 * One semantic colour per ontology concept, defined for both light and dark. Components read them through `useOnt()`, which
 * uses `theme.palette.ontology` when the theme came from `createOntologyTheme` and otherwise derives them from the host theme's
 * palette mode — so the workbench stays readable inside any host theme (light or dark) without the host adopting ours.
 *
 * Colour is never the only signal: every kind also has an icon and a text label (see components/ui.jsx).
 */
const KIND = {
  dark: {
    class: '#3dd6f5', object: '#7aa9ff', data: '#4fe3b0', inherit: '#a3b3c9', source: '#8ea6c2',
    mapping: '#f27fc0', ai: '#a996ff', provenance: '#8ea6c2', version: '#7aa9ff', unsaved: '#fbbf24',
  },
  light: {
    class: '#00728f', object: '#1565c0', data: '#0a7a58', inherit: '#586a80', source: '#586a80',
    mapping: '#b0226b', ai: '#6a42d6', provenance: '#586a80', version: '#1565c0', unsaved: '#9a5800',
  },
};

const SURFACE = {
  dark: { bg: '#070b11', panel: '#0c131b', raised: '#111b26', sunken: '#080e14', line: 'rgba(148,172,204,0.16)', lineStrong: 'rgba(148,172,204,0.30)', text: '#e7eef8', muted: '#9db0c8' },
  light: { bg: '#f3f6fa', panel: '#ffffff', raised: '#ffffff', sunken: '#eaf0f6', line: 'rgba(20,40,70,0.13)', lineStrong: 'rgba(20,40,70,0.28)', text: '#0f1b2b', muted: '#4a5b70' },
};

export const buildTokens = (mode = 'light') => {
  const m = mode === 'dark' ? 'dark' : 'light';
  return { mode: m, kind: KIND[m], surface: SURFACE[m] };
};

/** Tokens for the current theme (works with host themes that know nothing about them). */
export function useOnt() {
  const theme = useTheme();
  return theme.palette.ontology || buildTokens(theme.palette.mode);
}

export const MONO = '"JetBrains Mono", "Cascadia Mono", "SFMono-Regular", Consolas, "Liberation Mono", monospace';
const SANS = '"Inter", "Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", Arial, sans-serif';

/** Per-host accent for the chrome (tabs, focus, primary buttons). These are configuration defaults, not brand assets. */
export const HOST_ACCENTS = { primesemonto: '#3dd6f5', unicontractai: '#7aa9ff', primeagenticos: '#4fe3b0' };

/**
 * createOntologyTheme({ mode, accent }) — the "ontology control room" theme.
 * Light mode keeps the original #1565c0 primary, so it is a drop-in for the previous light theme.
 */
export function createOntologyTheme({ mode = 'dark', accent } = {}) {
  const t = buildTokens(mode);
  const dark = t.mode === 'dark';
  const primary = accent || (dark ? '#3dd6f5' : '#1565c0');
  const S = t.surface;
  return createTheme({
    palette: {
      mode: t.mode,
      primary: { main: primary, contrastText: dark ? '#04121a' : '#ffffff' },
      secondary: { main: t.kind.ai },
      success: { main: dark ? '#4ade80' : '#1b7a3a' },
      warning: { main: dark ? '#fbbf24' : '#9a5800' },
      error: { main: dark ? '#ff7b7b' : '#c62828' },
      info: { main: dark ? '#7aa9ff' : '#1565c0' },
      background: { default: S.bg, paper: S.panel },
      text: { primary: S.text, secondary: S.muted },
      divider: S.line,
      ontology: t,
    },
    shape: { borderRadius: 8 },
    typography: {
      fontFamily: SANS,
      fontSize: 13,
      h5: { fontWeight: 650, letterSpacing: '-0.01em' },
      h6: { fontWeight: 650, letterSpacing: '-0.005em' },
      subtitle1: { fontWeight: 600 },
      subtitle2: { fontWeight: 600 },
      button: { textTransform: 'none', fontWeight: 600, letterSpacing: 0 },
      overline: { fontWeight: 600, letterSpacing: '0.09em', lineHeight: 1.6 },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: { backgroundColor: S.bg },
          '*:focus-visible': { outline: `2px solid ${primary}`, outlineOffset: 2 },
          '::selection': { background: alpha(primary, 0.35) },
          '@media (prefers-reduced-motion: reduce)': {
            '*, *::before, *::after': { animationDuration: '0.001ms !important', animationIterationCount: '1 !important', transitionDuration: '0.001ms !important', scrollBehavior: 'auto !important' },
          },
        },
      },
      MuiPaper: { styleOverrides: { root: { backgroundImage: 'none' } } },
      MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { borderRadius: 8 } } },
      MuiTextField: { defaultProps: { size: 'small' } },
      MuiOutlinedInput: { styleOverrides: { root: { backgroundColor: dark ? alpha('#fff', 0.03) : alpha('#0f1b2b', 0.015) } } },
      MuiChip: { styleOverrides: { root: { borderRadius: 6, fontWeight: 500 } } },
      MuiTabs: { styleOverrides: { indicator: { height: 2, borderRadius: 2 } } },
      MuiTab: { styleOverrides: { root: { textTransform: 'none', fontWeight: 600, minHeight: 44 } } },
      MuiTooltip: { styleOverrides: { tooltip: { fontSize: 12, backgroundColor: dark ? '#1a2634' : '#1b2a3d', border: `1px solid ${S.lineStrong}` } } },
      MuiTableCell: { styleOverrides: { root: { borderColor: S.line }, head: { fontWeight: 600, color: S.muted, fontSize: 11.5, letterSpacing: '0.04em', textTransform: 'uppercase' } } },
      MuiDialog: { styleOverrides: { paper: { border: `1px solid ${S.lineStrong}` } } },
      MuiAlert: { styleOverrides: { root: { borderRadius: 8 } } },
    },
  });
}
