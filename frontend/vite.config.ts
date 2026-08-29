import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// The dev server proxies /api to the backend so the browser only ever talks to
// one origin. That keeps CORS out of the local development loop entirely, and
// means Playwright tests point at a single base URL.
//
// In Docker the built assets are served by nginx, which proxies /api the same
// way - so the frontend code never needs to know the backend's address.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Fail loudly instead of silently moving to 5174: a test suite pointed at
    // 5173 would otherwise run against nothing and produce baffling failures.
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  preview: { host: '0.0.0.0', port: 5173, strictPort: true },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
