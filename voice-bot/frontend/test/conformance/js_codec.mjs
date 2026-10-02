// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// Helper for test_json_serializer.py: runs the browser's own encoder/decoder on stdin JSON.
//   {"encode": [int16...], "parse": ["<text frame>", ...]} -> {"encoded": "...", "parsed": [...]}

import { encodeMediaMessage, parseServerMessage } from '../../src/lib/protocol.js';

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => (input += chunk));
process.stdin.on('end', () => {
    const request = JSON.parse(input);
    const out = {};
    if (request.encode) out.encoded = encodeMediaMessage(Int16Array.from(request.encode));
    if (request.parse) {
        out.parsed = request.parse.map((text) => {
            const m = parseServerMessage(text);
            return m.kind === 'media' ? { kind: 'media', samples: Array.from(m.samples) } : m;
        });
    }
    process.stdout.write(JSON.stringify(out));
});
