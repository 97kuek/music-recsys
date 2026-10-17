import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  if (Object.keys(env).some(key => key.startsWith('VITE_') && !['VITE_API_BASE','VITE_PUBLIC_DEMO'].includes(key))) {
    throw new Error('Only VITE_API_BASE and VITE_PUBLIC_DEMO are allowed in the browser build; keep secrets in server settings');
  }
  return {
    build: { outDir: '../src/interfaces/http/static', emptyOutDir: true, sourcemap: false },
    server: { proxy: { '/api': { target: env.DEV_BACKEND_ORIGIN || 'http://127.0.0.1:8765', changeOrigin: false } } },
  };
});
