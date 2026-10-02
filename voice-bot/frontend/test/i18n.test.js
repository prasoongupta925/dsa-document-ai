// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_EXAMPLES, LANGUAGES, STRINGS, htmlLang, isLanguage, t } from '../src/lib/i18n.js';

const placeholders = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

test('picker offers English / हिन्दी / मराठी and sends en-IN / hi-IN / mr-IN', () => {
    assert.deepEqual(
        LANGUAGES.map((l) => [l.code, l.label]),
        [
            ['en-IN', 'English'],
            ['hi-IN', 'हिन्दी'],
            ['mr-IN', 'मराठी'],
        ],
    );
    assert.equal(htmlLang('mr-IN'), 'mr');
    assert.equal(htmlLang('xx'), 'en');
    assert.ok(isLanguage('hi-IN') && !isLanguage('hindi'));
});

test('every string exists in all three languages with the same placeholders', () => {
    const keys = Object.keys(STRINGS['en-IN']);
    for (const code of ['hi-IN', 'mr-IN']) {
        assert.deepEqual(Object.keys(STRINGS[code]).sort(), [...keys].sort(), `${code} keys`);
        for (const key of keys) {
            const text = STRINGS[code][key];
            assert.equal(typeof text, 'string');
            assert.ok(text.trim().length > 0, `${code}.${key} empty`);
            assert.deepEqual(placeholders(text), placeholders(STRINGS['en-IN'][key]), `${code}.${key} placeholders`);
        }
    }
});

test('the recording notice and the 7-day / lender-decides wording are present', () => {
    assert.equal(t('en-IN', 'notice'), 'AI assistant · this call is recorded');
    assert.match(t('hi-IN', 'notice'), /AI/);
    assert.match(t('hi-IN', 'notice'), /रिकॉर्ड/);
    assert.match(t('mr-IN', 'notice'), /रेकॉर्ड/);
    for (const code of ['en-IN', 'hi-IN', 'mr-IN']) {
        assert.match(t(code, 'noticeDetail'), /7/);
        assert.match(t(code, 'noticeDetail'), /lender|लेंडर/i);
    }
});

test('no on-screen text promises approval', () => {
    const all = Object.values(STRINGS).flatMap((table) => Object.values(table));
    const examples = Object.values(DEFAULT_EXAMPLES).flat();
    for (const text of [...all, ...examples]) {
        assert.doesNotMatch(text, /pakka|पक्का|guarantee|approved for sure|गारंटी/i, text);
    }
});

test('t(): fallback to English, then the key; placeholders filled', () => {
    assert.equal(t('ta-IN', 'call'), 'Call');
    assert.equal(t('hi-IN', 'no.such.key'), 'no.such.key');
    assert.equal(t('en-IN', 'timeLimit', { min: 10 }), 'Calls are limited to 10 minutes, so this one was ended.');
    assert.equal(t('en-IN', 'errRejected', {}), 'The voice assistant refused the call: ');
});

test('three demo questions per language', () => {
    for (const { code } of LANGUAGES) assert.equal(DEFAULT_EXAMPLES[code].length, 3);
});
