import { createContext, useContext } from 'react';

export const WorkbenchContext = createContext(null);
export const useWB = () => {
  const ctx = useContext(WorkbenchContext);
  if (!ctx) throw new Error('useWB must be used inside <PrimeOntologyWorkbench>');
  return ctx;
};

export const ROLE_RANK = { viewer: 1, editor: 2, reviewer: 3, admin: 4 };
export const CAPABILITY = { read: 'viewer', write: 'editor', commit: 'editor', submit: 'editor', review: 'reviewer', publish: 'admin', rollback: 'admin', delete: 'admin' };
export const canRole = (role, cap) => (ROLE_RANK[role] || 0) >= ROLE_RANK[CAPABILITY[cap]];
/** Human wording for each capability, used to explain what a role can and cannot do. Mirrors backend identity.CAPABILITY. */
export const CAPABILITY_LABEL = {
  read: 'View everything', write: 'Import, edit and map', commit: 'Commit versions', submit: 'Submit for review',
  review: 'Approve or reject', publish: 'Publish', rollback: 'Roll back', delete: 'Delete ontologies',
};
export const requiredRole = (cap) => CAPABILITY[cap];

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
