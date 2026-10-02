// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));

// Production pages get a Content-Security-Policy. connect-src: Cognito (any region) and the voice
// WebSocket (wss: any host, so config.json can point anywhere; ws://localhost for local tests).
const CSP = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self' https://*.amazonaws.com wss: ws://localhost:* ws://127.0.0.1:*",
    "media-src 'self' blob:",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
].join('; ');

function contentSecurityPolicy() {
    return {
        name: 'sd-content-security-policy',
        apply: 'build',
        transformIndexHtml(html) {
            return html.replace('<!-- CSP -->', `<meta http-equiv="Content-Security-Policy" content="${CSP}" />`);
        },
    };
}

export default defineConfig({
    plugins: [react(), contentSecurityPolicy()],
    define: {
        __APP_VERSION__: JSON.stringify(pkg.version),
    },
    server: {
        port: 3000,
    },
    preview: {
        port: 4173,
    },
    build: {
        outDir: 'dist',
        emptyOutDir: true,
        sourcemap: false,
        chunkSizeWarningLimit: 1000,
    },
});
