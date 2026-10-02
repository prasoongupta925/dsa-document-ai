// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';

export default [
    { ignores: ['dist/**', 'node_modules/**', 'e2e/.out/**'] },
    js.configs.recommended,
    {
        files: ['**/*.{js,jsx,mjs}'],
        languageOptions: {
            ecmaVersion: 'latest',
            sourceType: 'module',
            parserOptions: { ecmaFeatures: { jsx: true } },
            globals: { ...globals.browser, __APP_VERSION__: 'readonly' },
        },
        plugins: { 'react-hooks': reactHooks },
        rules: {
            ...reactHooks.configs.recommended.rules,
            // Components are only referenced from JSX, which core no-unused-vars cannot see.
            'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^_' }],
        },
    },
    {
        files: ['src/audio/worklet.js'],
        languageOptions: { globals: { ...globals.audioWorklet } },
    },
    {
        files: ['test/**', 'e2e/**', 'scripts/**', 'vite.config.js', 'eslint.config.js'],
        languageOptions: { globals: { ...globals.node } },
    },
];
