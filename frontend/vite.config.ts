import { defineConfig } from 'vite';

// Orthanc and the backend are proxied so the browser sees a single origin (no CORS setup).
export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/dicom-web': 'http://127.0.0.1:8042',
      '/api': {
        target: 'http://127.0.0.1:8000',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  // The image loader (workers + WASM) must not be pre-bundled, but its codecs' Emscripten JS
  // glue is CommonJS: without pre-bundling, dev mode fails with "does not provide an export
  // named 'default'" (production builds convert CJS anyway). The docs' alternative is
  // @originjs/vite-plugin-commonjs; listing the modules here avoids the extra plugin.
  optimizeDeps: {
    exclude: ['@cornerstonejs/dicom-image-loader'],
    include: [
      'dicom-parser',
      '@cornerstonejs/codec-charls/decodewasmjs',
      '@cornerstonejs/codec-libjpeg-turbo-8bit/decodewasmjs',
      '@cornerstonejs/codec-libjxl/decodewasmjs',
      '@cornerstonejs/codec-openjpeg/decodewasmjs',
      '@cornerstonejs/codec-openjph/wasmjs',
    ],
  },
  worker: { format: 'es' },
  build: { target: 'es2022' },
});
