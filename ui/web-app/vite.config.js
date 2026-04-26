import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';

const API_BASE = process.env.VITE_API_BASE_URL || 'http://127.0.0.1:8010';

export default defineConfig({
  plugins: [preact()],
  server: {
    port: 5174,
    proxy: {
      '/api': { target: API_BASE, changeOrigin: true, secure: false },
      '/auth': { target: API_BASE, changeOrigin: true, secure: false },
      '/v1': { target: API_BASE, changeOrigin: true, secure: false },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    target: 'es2022',
  },
  test: {
    environment: 'happy-dom',
    globals: true,
  },
});
