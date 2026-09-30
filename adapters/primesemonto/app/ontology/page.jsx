'use client';
// IDENTICAL in UniContractAI, PrimeSemOnto and PrimeAgentic OS. Only prime-ontology.config.js differs per host.
import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { PrimeOntologyWorkbench } from '@prime/ontology-workbench';
import config from '../../prime-ontology.config';

function Workbench() {
  const params = useSearchParams();
  const id = params.get('ontology');
  return (
    <PrimeOntologyWorkbench
      apiBase={config.apiBase}
      context={config.context}
      ontologyId={id ? Number(id) : null}
      getAuthHeaders={config.getAuthHeaders}
      onEvent={config.onEvent}
      height={config.height}
    />
  );
}

export default function OntologyPage() {
  return (
    <Suspense fallback={null}>
      <Workbench />
    </Suspense>
  );
}
