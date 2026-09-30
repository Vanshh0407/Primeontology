// Merge into the host's next.config.js (identical for all three hosts except the backend URL env var).
const ONTOLOGY_BACKEND = process.env.PRIME_ONTOLOGY_BACKEND || 'http://localhost:8008';

module.exports = {
  skipTrailingSlashRedirect: true, // keep '/api/.../' intact when proxying to Django
  transpilePackages: ['@prime/ontology-workbench'],
  async rewrites() {
    return [{ source: '/api/v1/ontology/:path*', destination: `${ONTOLOGY_BACKEND}/api/v1/ontology/:path*` }];
  },
};
