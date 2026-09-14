import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Above the async-assertion budget set in setup.ts, so a waitFor that gives up
    // reports which expectation was not met instead of the whole test timing out.
    testTimeout: 20000,
  },
});
