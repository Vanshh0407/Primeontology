// PrimeAgentic OS — the ONLY host-specific frontend file for the Prime Ontology Workbench.
// Semantic control plane for agents: adds the Agents tab, the MCP server and the semantic planner.
//
// Authentication, tenant and roles are enforced by the Django API from PrimeAgentic OS's own session/JWT
// (see django/prime_ontology_settings.py); the workbench mirrors the identity the backend resolves.
// Add getAuthHeaders only if PrimeAgentic OS authenticates API calls with a bearer token instead of a cookie.

const config = {
  context: 'primeagenticos',
  apiBase: '/api/v1/ontology',
  height: 'calc(100vh - 64px)', // below the host's top app bar
  // getAuthHeaders: async () => ({ Authorization: `Bearer ${await getAccessToken()}` }),
  onEvent: (event) => {
    // Optional: bridge workbench events (ontology.saved, ontology.published, concept.selected, ...) into the host.
    if (process.env.NODE_ENV !== 'production') console.debug('[prime-ontology]', event.type, event.payload);
  },
};

export default config;
