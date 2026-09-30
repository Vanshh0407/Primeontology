import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CircularProgress, CssBaseline, ThemeProvider } from '@mui/material';
import { PrimeOntologyWorkbench, createApi } from './workbench';
import { HOST_ACCENTS, createOntologyTheme } from './workbench/theme';
import LoginPage from './LoginPage.jsx';

// Standalone host. Authentication is a Django session (embedded hosts bring their own).
// Optional URL params: ?context=unicontractai|primesemonto|primeagenticos & ontology=<id> & tab=<tab>
const params = new URLSearchParams(window.location.search);
const context = params.get('context') || 'primesemonto';
// Colour mode: the standalone app defaults to the dark control-room theme and remembers the user's choice.
// Light mode keeps the original #1565c0 primary. Embedded hosts pass their own MUI theme and are unaffected.
const MODE_KEY = 'prime-ontology-color-mode';
const readMode = () => { try { return localStorage.getItem(MODE_KEY) === 'light' ? 'light' : 'dark'; } catch { return 'dark'; } };

function App({ mode, onToggleMode }) {
  const api = useMemo(() => createApi({ apiBase: '/api/v1/ontology' }), []);
  const [user, setUser] = useState(undefined); // undefined = checking, null = logged out

  useEffect(() => { api.me().then(setUser).catch(() => setUser(null)); }, [api]);
  const logout = useCallback(async () => { try { await api.logout(); } finally { setUser(null); } }, [api]);

  if (user === undefined) return <CircularProgress sx={{ m: 'auto', display: 'block', mt: 20 }} />;
  if (user === null) return <LoginPage api={api} onLoggedIn={setUser} />;
  return (
    <PrimeOntologyWorkbench
      apiBase="/api/v1/ontology"
      context={context}
      ontologyId={params.get('ontology') ? Number(params.get('ontology')) : null}
      initialTab={params.get('tab') || undefined}
      currentUser={{ name: user.username, role: user.role }}
      onLogout={logout}
      colorMode={mode}
      onToggleColorMode={onToggleMode}
    />
  );
}

function Root() {
  const [mode, setMode] = useState(readMode);
  const theme = useMemo(() => createOntologyTheme({ mode, accent: mode === 'dark' ? HOST_ACCENTS[context] : undefined }), [mode]);
  const toggle = useCallback(() => setMode((m) => { const n = m === 'dark' ? 'light' : 'dark'; try { localStorage.setItem(MODE_KEY, n); } catch { /* storage unavailable */ } return n; }), []);
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App mode={mode} onToggleMode={toggle} />
    </ThemeProvider>
  );
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
