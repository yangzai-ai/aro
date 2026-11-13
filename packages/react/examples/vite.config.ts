import { defineConfig } from 'vite';

// Local example only. Production uses the business application's authorized proxy.
export default defineConfig({
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
});
