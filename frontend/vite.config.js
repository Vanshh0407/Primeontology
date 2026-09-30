import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { port: 3008, proxy: { '/api': { target: process.env.PRIME_ONTOLOGY_BACKEND || 'http://localhost:8008', changeOrigin: true } } },
  build: { chunkSizeWarningLimit: 2000 },
  test: { environment: 'node' },
});
