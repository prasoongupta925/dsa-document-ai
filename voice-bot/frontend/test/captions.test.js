// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MAX_ITEMS, captionsReducer, initialCaptions } from '../src/lib/captions.js';

const run = (actions, state = initialCaptions) => actions.reduce(captionsReducer, state);
const cap = (role, text, final = true) => ({ type: 'caption', role, text, final }); // part captions
const turn = (role, text, interrupted = false) => ({ type: 'caption', role, text, turn: true, interrupted });
const view = (state) => state.items.map(({ role, text, interim, interrupted }) => ({ role, text, interim, interrupted }));

test('interim caller text is replaced, finals of one turn join into one bubble', () => {
    const s = run([cap('user', 'Sneha', false), cap('user', 'Sneha Kulkarni ki', false), cap('user', 'Sneha Kulkarni ki file'), cap('user', 'mein kya baaki hai?')]);
    assert.deepEqual(view(s), [{ role: 'user', text: 'Sneha Kulkarni ki file mein kya baaki hai?', interim: '', interrupted: undefined }]);
});

test('bot sentences of one answer join; the next caller turn starts a new bubble', () => {
    const s = run([
        cap('bot', 'Namaste!'),
        cap('bot', 'Main Varunika ki AI assistant hoon.'),
        cap('user', 'Haan'),
        cap('bot', 'Ji.'),
    ]);
    assert.deepEqual(
        s.items.map((i) => [i.role, i.text]),
        [
            ['bot', 'Namaste! Main Varunika ki AI assistant hoon.'],
            ['user', 'Haan'],
            ['bot', 'Ji.'],
        ],
    );
});

test('punctuation and danda attach without a space', () => {
    const s = run([cap('bot', 'आपकी फ़ाइल में दो कागज़ बाकी हैं'), cap('bot', '।')]);
    assert.equal(s.items[0].text, 'आपकी फ़ाइल में दो कागज़ बाकी हैं।');
});

test('barge-in closes the bot bubble; more bot text opens a new one', () => {
    const s = run([cap('bot', 'Aapki file mein'), { type: 'interrupt' }, cap('bot', 'Ji, boliye.')]);
    assert.deepEqual(
        s.items.map((i) => [i.text, i.interrupted]),
        [
            ['Aapki file mein', true],
            ['Ji, boliye.', false],
        ],
    );
    assert.equal(run([{ type: 'interrupt' }]), initialCaptions, 'interrupt with no bot bubble is a no-op');
});

test('a dangling interim is kept as text when the bot answers or the call ends', () => {
    const s1 = run([cap('user', 'Amit Patil', false), cap('bot', 'Ek minute.')]);
    assert.deepEqual(s1.items.map((i) => [i.role, i.text, i.interim]), [
        ['user', 'Amit Patil', ''],
        ['bot', 'Ek minute.', undefined],
    ]);
    const s2 = run([cap('user', 'Rahul', false), { type: 'end' }]);
    assert.deepEqual(view(s2), [{ role: 'user', text: 'Rahul', interim: '', interrupted: undefined }]);
});

test('ids are unique and the list is capped', () => {
    let s = initialCaptions;
    for (let i = 0; i < MAX_ITEMS + 30; i++) s = captionsReducer(s, cap(i % 2 ? 'user' : 'bot', `line ${i}`));
    assert.equal(s.items.length, MAX_ITEMS);
    assert.equal(new Set(s.items.map((i) => i.id)).size, MAX_ITEMS);
    assert.equal(s.items[s.items.length - 1].text, `line ${MAX_ITEMS + 29}`);
    assert.equal(captionsReducer(s, { type: 'reset' }), initialCaptions);
    assert.equal(captionsReducer(s, cap('bot', '')), s, 'empty text ignored');
});

test('turn captions (Mumbai backend): one closed bubble per turn, flagged when cut off', () => {
    const s = run([
        turn('bot', 'Namaste, main Varunika Loan Partners ki AI assistant hoon.'),
        turn('bot', 'Kya main Sneha ji se baat kar rahi hoon?'),
        turn('user', 'Haan, main Sneha bol rahi hoon.'),
        { type: 'interrupt' }, // barge-in: the previous bubbles are finished turns, nothing to flag
        turn('bot', 'Aapki file mein', true),
        turn('user', 'Kya baaki hai?'),
    ]);
    assert.deepEqual(
        s.items.map((i) => [i.role, i.text, !!i.interrupted, i.closed]),
        [
            ['bot', 'Namaste, main Varunika Loan Partners ki AI assistant hoon.', false, true],
            ['bot', 'Kya main Sneha ji se baat kar rahi hoon?', false, true],
            ['user', 'Haan, main Sneha bol rahi hoon.', false, true],
            ['bot', 'Aapki file mein', true, true],
            ['user', 'Kya baaki hai?', false, true],
        ],
    );
});

test('a lookup line turns into its result card; other lookups end when the bot answers', () => {
    const card = { type: 'file_status', verdict: 'NOT READY', missing: ['June 2026 salary slip'], mismatches: 1 };
    const s = run([
        turn('user', 'Sneha Kulkarni ki file mein kya baaki hai?'),
        { type: 'tool', name: 'file_status' },
        { type: 'tool', name: 'file_status' }, // duplicate start ignored
        { type: 'card', card },
        { type: 'tool', name: 'switch_language' },
        turn('bot', 'June ki salary slip baaki hai.'),
    ]);
    assert.deepEqual(
        s.items.map((i) => i.role),
        ['user', 'card', 'tool', 'bot'],
    );
    assert.deepEqual(s.items[1].card, card);
    assert.equal(s.items[2].done, true);
    const lonely = run([{ type: 'card', card: { type: 'eligibility', bestLender: 'Demo Bank', amount: '4 lakh' } }]);
    assert.equal(lonely.items[0].role, 'card');
    assert.equal(run([{ type: 'card', card: null }]), initialCaptions);
    assert.equal(run([{ type: 'tool', name: '' }]), initialCaptions);
});
