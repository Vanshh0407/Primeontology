import { useOnt } from '../theme';

/** Prime Ontology logomark: a concept linked to two others (class → relationship → class), drawn in the token colours. */
export default function BrandMark({ size = 28, title }) {
  const { kind, surface } = useOnt();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role={title ? 'img' : 'presentation'} aria-label={title} aria-hidden={title ? undefined : true} focusable="false">
      <rect x="0.75" y="0.75" width="30.5" height="30.5" rx="8" fill={surface.raised} stroke={surface.lineStrong} strokeWidth="1.2" />
      <g stroke={kind.object} strokeWidth="1.6" strokeLinecap="round"><path d="M9.5 21.5 L16 10.5 L23 21" /><path d="M9.5 21.5 L23 21" strokeDasharray="1.5 3" strokeOpacity="0.7" /></g>
      <circle cx="16" cy="10.5" r="3.6" fill={kind.class} /><circle cx="9.5" cy="21.5" r="2.6" fill={kind.data} /><circle cx="23" cy="21" r="2.6" fill={kind.data} />
    </svg>
  );
}
