import { alpha } from '@mui/material/styles';
import { Alert, Box, Chip, Paper, Stack, Tooltip, Typography } from '@mui/material';
import HubIcon from '@mui/icons-material/Hub';
import ShareIcon from '@mui/icons-material/Share';
import LabelIcon from '@mui/icons-material/Label';
import SubdirectoryArrowRightIcon from '@mui/icons-material/SubdirectoryArrowRight';
import StorageIcon from '@mui/icons-material/Storage';
import SyncAltIcon from '@mui/icons-material/SyncAlt';
import FingerprintIcon from '@mui/icons-material/Fingerprint';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import CallSplitIcon from '@mui/icons-material/CallSplit';
import LockOutlinedIcon from '@mui/icons-material/LockOutlined';
import CheckIcon from '@mui/icons-material/Check';
import { MONO, useOnt } from '../theme';
import { CAPABILITY, requiredRole, useWB } from '../context';

/** Every ontology concept has a fixed colour AND a fixed icon + label, so state is never conveyed by colour alone. */
export const KINDS = {
  class: { label: 'Class', Icon: HubIcon },
  object: { label: 'Relationship', Icon: ShareIcon },
  data: { label: 'Data property', Icon: LabelIcon },
  inherit: { label: 'Inheritance', Icon: SubdirectoryArrowRightIcon },
  source: { label: 'Source', Icon: StorageIcon },
  mapping: { label: 'Mapping', Icon: SyncAltIcon },
  provenance: { label: 'Provenance', Icon: FingerprintIcon },
  ai: { label: 'AI suggestion', Icon: AutoAwesomeIcon },
  version: { label: 'Version', Icon: CallSplitIcon },
};

export const useKindColor = (kind) => useOnt().kind[kind] || undefined;

export function KindChip({ kind, label, size = 'small', variant = 'tinted', sx, ...rest }) {
  const color = useKindColor(kind);
  const { Icon, label: fallback } = KINDS[kind];
  return (
    <Chip
      size={size} icon={<Icon style={{ color }} />} label={label ?? fallback} variant="outlined"
      sx={{ color, borderColor: alpha(color, 0.45), bgcolor: variant === 'tinted' ? alpha(color, 0.1) : 'transparent', '& .MuiChip-label': { color: 'text.primary' }, ...sx }}
      {...rest}
    />
  );
}

/** Small square glyph in the kind's colour (used in lists, tree rows, headers). */
export function KindGlyph({ kind, size = 18 }) {
  const color = useKindColor(kind);
  const { Icon } = KINDS[kind];
  return (
    <Box aria-hidden sx={{ width: size + 8, height: size + 8, borderRadius: 1, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, color, bgcolor: alpha(color, 0.12), border: 1, borderColor: alpha(color, 0.35) }}>
      <Icon sx={{ fontSize: size }} />
    </Box>
  );
}

const STATUS = { draft: 'default', review: 'warning', approved: 'info', published: 'success', archived: 'default' };
export const STATUS_COLOR = STATUS;
export function StatusChip({ status, prefix, size = 'small' }) {
  return <Chip size={size} color={STATUS[status] || 'default'} variant={status === 'archived' ? 'outlined' : 'filled'} label={prefix ? `${prefix}${status}` : status} />;
}

/** Page header used at the top of every tab: what this screen is for, in one line, plus its primary actions. */
export function PageHeader({ kind, eyebrow, title, description, actions, sx }) {
  const color = useKindColor(kind);
  return (
    <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems={{ md: 'flex-end' }} justifyContent="space-between" sx={{ mb: 2.5, ...sx }}>
      <Box sx={{ minWidth: 0 }}>
        {eyebrow && <Typography variant="overline" sx={{ color: color || 'text.secondary', display: 'block' }}>{eyebrow}</Typography>}
        <Typography variant="h5" component="h2">{title}</Typography>
        {description && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, maxWidth: 760 }}>{description}</Typography>}
      </Box>
      {actions && <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1}>{actions}</Stack>}
    </Stack>
  );
}

/** Bordered panel with an optional kind-coloured header. */
export function Panel({ kind, title, subtitle, actions, children, sx, bodySx, ...rest }) {
  const { surface } = useOnt();
  const color = useKindColor(kind);
  return (
    <Paper variant="outlined" sx={{ bgcolor: surface.panel, borderColor: surface.line, overflow: 'hidden', ...(color ? { borderTop: `2px solid ${alpha(color, 0.8)}` } : {}), ...sx }} {...rest}>
      {(title || actions) && (
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1} sx={{ px: 2, py: 1.25, borderBottom: 1, borderColor: surface.line }}>
          <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0 }}>
            {kind && <KindGlyph kind={kind} size={16} />}
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="subtitle1" component="h3" noWrap sx={{ lineHeight: 1.25 }}>{title}</Typography>
              {subtitle && <Typography variant="caption" color="text.secondary">{subtitle}</Typography>}
            </Box>
          </Stack>
          {actions && <Stack direction="row" spacing={1}>{actions}</Stack>}
        </Stack>
      )}
      <Box sx={{ p: 2, ...bodySx }}>{children}</Box>
    </Paper>
  );
}

/** Empty / no-ontology state with a small static graph motif. */
export function EmptyState({ title, children, action, sx }) {
  const { kind } = useOnt();
  return (
    <Box sx={{ p: { xs: 3, md: 6 }, textAlign: 'center', ...sx }}>
      <svg width="88" height="64" viewBox="0 0 88 64" aria-hidden style={{ marginBottom: 12 }}>
        <g stroke={kind.object} strokeOpacity="0.6" strokeWidth="1.5" fill="none"><path d="M22 20 L44 44 L68 18" /><path d="M22 20 L68 18" strokeDasharray="3 3" strokeOpacity="0.35" /></g>
        <circle cx="22" cy="20" r="7" fill={kind.class} fillOpacity="0.85" /><circle cx="68" cy="18" r="5" fill={kind.data} fillOpacity="0.85" /><circle cx="44" cy="44" r="8" fill={kind.class} fillOpacity="0.85" />
      </svg>
      <Typography variant="h6" component="p">{title}</Typography>
      {children && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, maxWidth: 520, mx: 'auto' }}>{children}</Typography>}
      {action && <Box sx={{ mt: 2 }}>{action}</Box>}
    </Box>
  );
}

export const NoOntology = () => <EmptyState title="No ontology open">Select an ontology in the top bar, or create one with “New”, to use this screen.</EmptyState>;

/**
 * Honest permission messaging: a short banner when the current role cannot use a screen's actions.
 * `cap` is a capability from context.CAPABILITY (e.g. 'write'). Renders nothing when the role has it.
 */
export function RoleNotice({ cap = 'write', children, sx }) {
  const { can, role } = useWB();
  if (can(cap)) return null;
  return (
    <Alert icon={<LockOutlinedIcon fontSize="inherit" />} severity="info" variant="outlined" sx={{ mb: 2, ...sx }} data-testid="role-notice">
      You are signed in as <strong>{role}</strong>. {children || `This screen is read-only for your role; it needs ${requiredRole(cap)} or higher.`}
    </Alert>
  );
}

/** Tooltip wrapper for a disabled control that says WHY it is disabled (required role, unsaved edits, four-eyes…). */
export function Why({ reason, children }) {
  if (!reason) return children;
  return <Tooltip title={reason}><span style={{ display: 'inline-flex' }}>{children}</span></Tooltip>;
}
export const needs = (can, cap) => (can(cap) ? '' : `Requires ${requiredRole(cap)} role or higher.`);

/** +/-/~ diff lines (shared by Versions and the AI Assistant). Marker glyph + colour, so it also reads without colour. */
export function DiffLines({ lines, sx }) {
  const { kind } = useOnt();
  const color = { '+': 'success.main', '-': 'error.main', '~': 'warning.main' };
  return (
    <Box role="list" aria-label="Changes" sx={{ fontFamily: MONO, fontSize: 12.5, lineHeight: 1.7, ...sx }}>
      {lines.map((l, i) => (
        <Box role="listitem" key={i} sx={{ color: color[l[0]] || 'text.primary', pl: 1, borderLeft: 2, borderColor: color[l[0]] || kind.inherit, mb: 0.25 }}>{l}</Box>
      ))}
    </Box>
  );
}

/** Horizontal step track ("Connect → Inspect → Review → Approve") — states: done / current / todo. */
export function StepTrack({ steps }) {
  const { kind, surface } = useOnt();
  return (
    <Stack component="ol" direction="row" spacing={0} sx={{ listStyle: 'none', p: 0, m: 0, overflowX: 'auto' }} aria-label="Progress">
      {steps.map((s, i) => {
        const color = s.state === 'done' ? kind.data : s.state === 'current' ? kind.class : surface.muted;
        return (
          <Stack component="li" key={s.label} direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0, pr: 1 }} aria-current={s.state === 'current' ? 'step' : undefined}>
            <Box sx={{ width: 22, height: 22, borderRadius: '50%', border: 1.5, borderColor: color, color, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, bgcolor: s.state === 'current' ? alpha(color, 0.15) : 'transparent' }}>
              {s.state === 'done' ? <CheckIcon sx={{ fontSize: 14 }} /> : i + 1}
            </Box>
            <Typography variant="body2" sx={{ fontWeight: s.state === 'current' ? 650 : 500, color: s.state === 'todo' ? 'text.secondary' : 'text.primary' }}>{s.label}</Typography>
            {i < steps.length - 1 && <Box aria-hidden sx={{ width: 28, height: '1px', flexShrink: 0, bgcolor: surface.lineStrong, mx: 0.5 }} />}
          </Stack>
        );
      })}
    </Stack>
  );
}

export { CAPABILITY };
