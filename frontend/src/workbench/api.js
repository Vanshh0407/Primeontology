// SDK: framework-free client for the Prime Ontology API (usable from any host, incl. Next.js server code).
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
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
    if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status);
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
    remove: (id) => request('DELETE', `${o(id)}/`),
    inspect: (type, config) => request('POST', '/inspect/', { type, config }),
    generate: (payload) => request('POST', '/generate/', payload),
    ingest: (file) => {
      const fd = new FormData();
      fd.append('file', file);
      return request('POST', '/ingest/', fd);
    },
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
    embeddedContext: (host, ontologyId) => request('GET', `/embedded/context/${q({ host, ontology_id: ontologyId })}`),
    emitEvent: (payload) => request('POST', '/embedded/event/', payload),
  };
}
