import { Suspense, lazy, useMemo, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, Divider, IconButton, InputAdornment, Stack, TextField, ThemeProvider, Typography,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import Visibility from '@mui/icons-material/Visibility';
import VisibilityOff from '@mui/icons-material/VisibilityOff';
import { createOntologyTheme } from './workbench/theme';
import BrandMark from './workbench/components/BrandMark';
import { KindChip } from './workbench/components/ui';

// The 3D graph is decorative and loaded lazily, so the form is interactive before (and regardless of) the graph.
const OntologyHero = lazy(() => import('./OntologyHero.jsx'));

// Demo credentials are filled in by the "Demo login" button (seeded by `manage.py seed_demo_users`).
const DEMO = { username: 'admin', password: 'admin123' };

export default function LoginPage({ api, onLoggedIn }) {
  const theme = useMemo(() => createOntologyTheme({ mode: 'dark' }), []); // the sign-in screen is always the dark "control room"
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e && e.preventDefault();
    setError('');
    setBusy(true);
    try {
      onLoggedIn(await api.login(username, password));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ThemeProvider theme={theme}>
      <Box component="main" sx={{
        minHeight: '100vh', display: 'flex', flexDirection: { xs: 'column', md: 'row' }, position: 'relative', overflow: 'hidden', bgcolor: 'background.default', color: 'text.primary',
        backgroundImage: `radial-gradient(ellipse 60% 55% at 30% 50%, ${alpha('#3dd6f5', 0.07)}, transparent 70%), linear-gradient(${alpha('#94accc', 0.05)} 1px, transparent 1px), linear-gradient(90deg, ${alpha('#94accc', 0.05)} 1px, transparent 1px)`,
        backgroundSize: 'auto, 48px 48px, 48px 48px',
      }}>
        {/* Hero: the graph fills the left side on desktop and sits behind the form on small screens. */}
        <Box sx={{ position: { xs: 'absolute', md: 'relative' }, inset: { xs: 0, md: 'auto' }, flex: { md: '1 1 60%' }, minHeight: { md: '100vh' }, }}>
          <Box sx={{ position: 'absolute', inset: 0, opacity: { xs: 0.75, md: 1 } }}><Suspense fallback={null}><OntologyHero /></Suspense></Box>
          <Stack direction="row" spacing={1.25} alignItems="center" sx={{ position: 'absolute', left: { xs: 20, md: 48 }, top: { xs: 20, md: 40 } }}>
            <BrandMark size={30} /><Typography variant="subtitle1" sx={{ letterSpacing: '0.02em' }}>Prime Ontology</Typography>
          </Stack>
          <Stack spacing={1.5} sx={{ position: 'absolute', left: { xs: 20, md: 48 }, bottom: 44, maxWidth: 520, pr: 2, display: { xs: 'none', md: 'flex' } }}>
            <Typography variant="overline" sx={{ color: 'primary.main' }}>Ontology workbench</Typography>
            <Typography variant="h3" component="p" sx={{ fontWeight: 650, letterSpacing: '-0.02em', lineHeight: 1.1, fontSize: 44 }}>Model what your data means.</Typography>
            <Typography variant="body1" color="text.secondary">Generate, edit, map, validate, query and govern one enterprise ontology.</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1} aria-label="Graph legend">
              <KindChip kind="class" /><KindChip kind="object" /><KindChip kind="data" />
            </Stack>
          </Stack>
        </Box>

        <Box sx={{ position: 'relative', zIndex: 1, flex: { md: '0 0 460px' }, display: 'flex', alignItems: { xs: 'flex-end', md: 'center' }, justifyContent: 'center', p: { xs: 2, md: 5 }, pt: { xs: 14, md: 5 }, minHeight: { xs: '100vh', md: 'auto' } }}>
          <Box component="form" onSubmit={submit} data-testid="login-page" noValidate sx={{
            width: '100%', maxWidth: 400, p: { xs: 3, sm: 4 }, borderRadius: 3, bgcolor: alpha('#0c131b', 0.94), border: 1, borderColor: 'divider',
            boxShadow: `0 24px 80px ${alpha('#000', 0.55)}, inset 0 1px 0 ${alpha('#fff', 0.04)}`,
          }}>
            <Stack spacing={0.5} sx={{ mb: 3 }}>
              <Typography variant="h5" component="h1">Prime Ontology Workbench</Typography>
              <Typography variant="body2" color="text.secondary">Sign in to continue</Typography>
            </Stack>
            {error && <Alert severity="error" sx={{ mb: 2 }} data-testid="login-error">{error}</Alert>}
            <Stack spacing={2}>
              <TextField label="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus fullWidth size="medium" />
              <TextField
                label="Password" type={show ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password" fullWidth size="medium"
                InputProps={{ endAdornment: (
                  <InputAdornment position="end">
                    <IconButton aria-label={show ? 'Hide password' : 'Show password'} onClick={() => setShow(!show)} edge="end">{show ? <VisibilityOff /> : <Visibility />}</IconButton>
                  </InputAdornment>
                ) }}
              />
              <Button type="submit" variant="contained" size="large" disabled={busy || !username || !password}>
                {busy ? <CircularProgress size={24} /> : 'Sign in'}
              </Button>
              <Divider sx={{ color: 'text.secondary', fontSize: 12 }}>or</Divider>
              <Button variant="outlined" size="large" disabled={busy} onClick={() => { setError(''); setUsername(DEMO.username); setPassword(DEMO.password); setShow(true); }}>
                Demo login
              </Button>
              <Typography variant="caption" color="text.secondary" align="center">
                “Demo login” fills in the demo credentials — then press Sign in.
              </Typography>
            </Stack>
          </Box>
        </Box>
      </Box>
    </ThemeProvider>
  );
}
