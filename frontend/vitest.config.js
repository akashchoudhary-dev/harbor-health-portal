import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: ['src/App.jsx'],
      exclude: ['src/main.jsx', 'src/App.test.jsx', 'src/test/**'],
      thresholds: {
        lines: 100,
        functions: 85,
        branches: 95,
        statements: 100,
      },
    },
  },
});
