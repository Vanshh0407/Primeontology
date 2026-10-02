// SDK: framework-free client for the Prime Ontology API (usable from any host, incl. Next.js server code).
export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data || null; // full error body (e.g. the 409 conflict details)
  }
}

export function createApi({ apiBase = '/api/v1/ontology', getAuthHeaders, permissions = {} } = {}) {
  const base = apiBase.replace(/\/$/, '');

  const headers = async (extra = {}) => ({
    'X-Prime-Client': 'workbench', // CSRF guard: custom header cannot be sent cross-site
    ...(permissions.user ? { 'X-Prime-User': permissions.user } : {}),
    ...(permissions.role ? { 'X-Prime-Role': permissions.role } : {}),
    ...(permissions.tenant ? { 'X-Prime-Tenant': permissions.tenant } : {}),
    ...((getAuthHeaders && (await getAuthHeaders())) || {}),
    ...extra,
  });

  async function request(method, path, body, { raw = false } = {}) {
    const isForm = typeof FormData !== 'undefined' && body instanceof FormData;
    const res = await fetch(`${base}${path}`, {
      method,
      headers: await headers(body && !isForm ? { 'Content-Type': 'application/json' } : {}),
      body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
    });
    if (res.status === 204) return null;
    if (raw && res.ok) return res;
    const text = await res.text();
    let data;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { error: text.slice(0, 200) };
    }
    if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status, data);
    return data;
  }
  const q = (params) => {
    const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''));
    return s.toString() ? `?${s}` : '';
  };
  const o = (id) => `/${id}`;

  return {
    base,
    health: () => request('GET', '/health/'),
    // Standalone session login (embedded hosts authenticate through their own session/JWT instead)
    login: (username, password) => request('POST', '/auth/login/', { username, password }),
    logout: () => request('POST', '/auth/logout/'),
    me: () => request('GET', '/auth/me/'),
    supported: () => request('GET', '/supported/'),
    list: (context) => request('GET', `/${q({ context })}`),
    create: (payload) => request('POST', '/', payload),
    get: (id) => request('GET', `${o(id)}/`),
    save: (id, payload) => request('PUT', `${o(id)}/`, payload),
    saveModel: (id, model, baseRevision, force = false) => request('PUT', `${o(id)}/`, { model, baseRevision, ...(force ? { force: true } : {}) }),
    remove: (id) => request('DELETE', `${o(id)}/`),
    inspect: (type, config) => request('POST', '/inspect/', { type, config }),
    generate: (payload) => request('POST', '/generate/', payload),
    ingest: (file, { records = 0, ai = false } = {}) => {
      const fd = new FormData();
      fd.append('file', file);
      if (records) fd.append('records', String(records));
      if (ai) fd.append('ai', '1');
      return request('POST', '/ingest/', fd);
    },
    ingestUrl: (url, authorization) => request('POST', '/ingest-url/', { url, authorization: authorization || undefined }),
    branches: (id) => request('GET', `${o(id)}/branches/`),
    createBranch: (id, name, fromVersion) => request('POST', `${o(id)}/branches/`, { name, fromVersion: fromVersion || undefined }),
    mergeBranch: (id, sourceId, { dryRun = true, resolutions, allowErrors } = {}) => request('POST', `${o(id)}/merge/`, { sourceId, dryRun, resolutions, allowErrors }),
    download: async (id, format = 'turtle', version) => {
      const res = await request('GET', `${o(id)}/export/${q({ format, version })}`, null, { raw: true });
      const cd = res.headers.get('Content-Disposition') || '';
      return { blob: await res.blob(), filename: (/filename="([^"]+)"/.exec(cd) || [])[1] || `ontology.${format}` };
    },
    graph: (id, version) => request('GET', `${o(id)}/graph/${q({ version })}`),
    validate: (id, version) => request('GET', `${o(id)}/validate/${q({ version })}`),
    reason: (id, profile) => request('GET', `${o(id)}/reason/${q({ profile })}`),
    shapes: async (id) => (await request('GET', `${o(id)}/shapes/`, null, { raw: true })).text(),
    shacl: (id, data) => request('POST', `${o(id)}/shacl/`, { data }),
    search: (id, text) => request('GET', `${o(id)}/search/${q({ q: text })}`),
    concept: (id, name, version) => request('GET', `${o(id)}/concepts/${encodeURIComponent(name)}/${q({ version })}`),
    path: (id, from, to) => request('GET', `${o(id)}/path/${q({ from, to })}`),
    query: (id, kind, text) => request('POST', `${o(id)}/query/`, { kind, text }),
    queries: (id, saved) => request('GET', `${o(id)}/queries/${q({ saved: saved ? 1 : '' })}`),
    saveQuery: (id, payload) => request('POST', `${o(id)}/queries/`, payload),
    deleteQuery: (id, qid) => request('DELETE', `${o(id)}/queries/${qid}/`),
    mappingExtract: (id, files) => {
      const fd = new FormData();
      files.forEach((f) => fd.append('file', f));
      return request('POST', `${o(id)}/mapping/extract/`, fd);
    },
    mappingPropose: (id, sources) => request('POST', `${o(id)}/mapping/propose/`, { sources }),
    mappingSets: (id) => request('GET', `${o(id)}/mappings/`),
    saveMappingSet: (id, payload) => request('POST', `${o(id)}/mappings/`, payload),
    updateMappingSet: (id, mid, payload) => request('PUT', `${o(id)}/mappings/${mid}/`, payload),
    deleteMappingSet: (id, mid) => request('DELETE', `${o(id)}/mappings/${mid}/`),
    commitMappingSet: (id, mid) => request('POST', `${o(id)}/mappings/${mid}/commit/`),
    versions: (id) => request('GET', `${o(id)}/versions/`),
    commitVersion: (id, message, major) => request('POST', `${o(id)}/versions/`, { message, major }),
    version: (id, number) => request('GET', `${o(id)}/versions/${number}/`),
    versionAction: (id, number, action) => request('POST', `${o(id)}/versions/${number}/${action}/`),
    diff: (id, from, to) => request('GET', `${o(id)}/diff/${q({ from, to })}`),
    audit: (id) => request('GET', `${o(id)}/audit/`),
    aiPropose: (id, prompt, useLlm = true) => request('POST', `${o(id)}/ai/`, { prompt, useLlm }),
    aiApply: (id, ops) => request('POST', `${o(id)}/ai/apply/`, { ops }),
    agentic: (id) => request('GET', `${o(id)}/agentic/`),
    saveAgentic: (id, reg) => request('PUT', `${o(id)}/agentic/`, reg),
    agentPlan: (id, text, role) => request('POST', `${o(id)}/agent/plan/`, { request: text, role }),
    agentContext: (id, text) => request('GET', `${o(id)}/agent/context/${q({ q: text })}`),
    embeddedManifest: () => request('GET', '/embedded/manifest/'),
    // R11 Knowledge Fabric
    fabric: (id) => request('GET', `${o(id)}/fabric/`),
    fabricSaveSource: (id, sid, payload) => (sid ? request('PUT', `${o(id)}/fabric/sources/${sid}/`, payload) : request('POST', `${o(id)}/fabric/sources/`, payload)),
    fabricDeleteSource: (id, sid) => request('DELETE', `${o(id)}/fabric/sources/${sid}/`),
    fabricTest: (id, sid) => request('POST', `${o(id)}/fabric/sources/${sid}/test/`, {}),
    fabricSync: (id, sid, full = false) => request('POST', `${o(id)}/fabric/sources/${sid}/sync/`, { full }),
    fabricSyncAll: (id, full = false) => request('POST', `${o(id)}/fabric/sync/`, { full }),
    fabricUpload: (id, sid, format, text) => request('POST', `${o(id)}/fabric/sources/${sid}/upload/`, { format, text }),
    fabricEntities: (id, params) => request('GET', `${o(id)}/fabric/entities/${q(params || {})}`),
    fabricEntity: (id, eid) => request('GET', `${o(id)}/fabric/entities/${eid}/`),
    fabricLineage: (id, eid) => request('GET', `${o(id)}/fabric/entities/${eid}/lineage/`),
    fabricSuggestions: (id) => request('GET', `${o(id)}/fabric/suggestions/`),
    fabricDecide: (id, sid, decision) => request('POST', `${o(id)}/fabric/suggestions/${sid}/decide/`, { decision }),
    fabricDrift: (id) => request('GET', `${o(id)}/fabric/drift/`),
    fabricDriftDecide: (id, did, action) => request('POST', `${o(id)}/fabric/drift/${did}/`, { action }),
    fabricSparql: (id, query) => request('POST', `${o(id)}/fabric/sparql/`, { query }),
    // R12 Semantic RAG
    ragDocuments: (id) => request('GET', `${o(id)}/rag/documents/`),
    ragAddDocument: (id, payload) => request('POST', `${o(id)}/rag/documents/`, payload),
    ragDeleteDocument: (id, did) => request('DELETE', `${o(id)}/rag/documents/${did}/`),
    ragAsk: (id, payload) => request('POST', `${o(id)}/rag/ask/`, payload),
    ragRetrieve: (id, payload) => request('POST', `${o(id)}/rag/retrieve/`, payload),
    ragReindex: (id) => request('POST', `${o(id)}/rag/reindex/`, {}),
    // R13 Digital Twin
    twin: (id, asOf) => request('GET', `${o(id)}/twin/${q({ asOf })}`),
    twinCreate: (id, payload) => request('POST', `${o(id)}/twin/`, payload),
    twinEvent: (id, payload) => request('POST', `${o(id)}/twin/events/`, payload),
    twinEvents: (id, params) => request('GET', `${o(id)}/twin/events/${q(params || {})}`),
    twinHistory: (id, eid, property) => request('GET', `${o(id)}/twin/entities/${eid}/history/${q({ property })}`),
    twinSimulate: (id, changes, hops = 2) => request('POST', `${o(id)}/twin/simulate/`, { changes, hops }),
    twinRules: (id) => request('GET', `${o(id)}/twin/rules/`),
    twinAddRule: (id, payload) => request('POST', `${o(id)}/twin/rules/`, payload),
    twinDeleteRule: (id, rid) => request('DELETE', `${o(id)}/twin/rules/${rid}/`),
    twinAddProcess: (id, payload) => request('POST', `${o(id)}/twin/processes/`, payload),
    twinAddAsset: (id, payload) => request('POST', `${o(id)}/twin/assets/`, payload),
    // R14 Agents
    agents: (id) => request('GET', `${o(id)}/agents/`),
    agentSave: (id, aid, payload) => (aid ? request('PUT', `${o(id)}/agents/${aid}/`, payload) : request('POST', `${o(id)}/agents/`, payload)),
    agentDelete: (id, aid) => request('DELETE', `${o(id)}/agents/${aid}/`),
    agentRunPlan: (id, goal) => request('POST', `${o(id)}/agents/plan/`, { goal }),
    agentExecute: (id, goal) => request('POST', `${o(id)}/agents/execute/`, { goal }),
    agentRun: (id, runId) => request('GET', `${o(id)}/agents/runs/${runId}/`),
    agentApprovals: (id, status = 'pending') => request('GET', `${o(id)}/agents/approvals/${q({ status })}`),
    agentApprove: (id, approvalId, decision, comment) => request('POST', `${o(id)}/agents/approve/`, { approvalId, decision, comment }),
    agentRecover: (id, runId, strategy) => request('POST', `${o(id)}/agents/recover/`, { runId, strategy }),
    agentWorkflow: async (id, payload) => {
      const res = await request('POST', `${o(id)}/agents/workflow/`, payload, { raw: true });
      return payload.format === 'bpmn' ? res.text() : res.json();
    },
    agentN8n: (id) => request('GET', `${o(id)}/agents/n8n/`),
    agentN8nPush: (id, payload) => request('POST', `${o(id)}/agents/n8n/`, payload),
    mcpRegister: (id, name, command) => request('POST', `${o(id)}/agents/mcp/`, { name, command }),
    mcpDiscover: (id, sid) => request('POST', `${o(id)}/agents/mcp/${sid}/discover/`, {}),
    twinGraphViews: (id) => request('GET', `${o(id)}/twin/graph/`),
    twinGraph: (id, view) => request('GET', `${o(id)}/twin/graph/${q({ view })}`),
    fabricPush: (id, sid, cls, records) => request('POST', `${o(id)}/fabric/sources/${sid}/push/`, { class: cls, records }),
    fabricCypher: async (id) => (await request('GET', `${o(id)}/fabric/export/${q({ format: 'cypher' })}`, undefined, { raw: true })).text(),
    ragVectors: async (id) => (await request('GET', `${o(id)}/rag/export/`, undefined, { raw: true })).text(),
    agentGovernTool: (id, name, action) => request('POST', `${o(id)}/agents/tools/${encodeURIComponent(name)}/govern/`, { action }),
    // R15 Semantic Enterprise OS (global routes; the base is the API root, so we step out of /ontology)
    os: (path, method = 'GET', body) => request(method, `/../os/${path}`, body),
    embeddedContext: (host, ontologyId) => request('GET', `/embedded/context/${q({ host, ontology_id: ontologyId })}`),
    emitEvent: (payload) => request('POST', '/embedded/event/', payload),
  };
}
