import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Source of truth for the app version: file_quacker/__init__.py.
// Needs to be in lockstep with the release.yml
const initPath = resolve(__dirname, '..', 'file_quacker', '__init__.py')
const initText = readFileSync(initPath, 'utf-8')
const match = initText.match(/__version__\s*=\s*['"]([^'"]+)['"]/)
if (!match) throw new Error('could not parse __version__ from file_quacker/__init__.py')
const APP_VERSION = match[1]

// base: './' makes the build work when loaded via file:// inside pywebview
// (asset paths become relative). server.strictPort keeps the dev URL stable.
export default defineConfig({
  plugins: [vue()],
  base: './',
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
  },
})
