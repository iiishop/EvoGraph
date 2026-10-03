import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { frontendProvenance } from './tools/frontend_provenance.mjs';
export default defineConfig({
  plugins: [vue(), frontendProvenance()],
  root: 'frontend',
  base: './',
  build: { outDir: '../dist', emptyOutDir: true },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
});
