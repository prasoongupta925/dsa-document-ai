// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// The call timeline as a pure reducer (React useReducer): caption bubbles of both sides, the
// bot's lookups ("Checking the file…") and their result cards, in the order they happened.
//
// Captions come in two shapes (see protocol.js):
// - turn: one message per finished turn (the Mumbai backend) -> one closed bubble each;
// - part: interim/final segments or sentences -> joined into the open bubble of that side.

export const MAX_ITEMS = 200;

export const initialCaptions = Object.freeze({ items: [], nextId: 1 });

function join(a, b) {
    if (!a) return b;
    if (!b) return a;
    if (/^[,.!?;:।॥)]/.test(b)) return a + b;
    return `${a} ${b}`;
}

function push(state, item) {
    const items = [...state.items, { id: state.nextId, ...item }];
    return { items: items.length > MAX_ITEMS ? items.slice(-MAX_ITEMS) : items, nextId: state.nextId + 1 };
}

function replaceAt(state, index, item) {
    const items = state.items.slice();
    items[index] = item;
    return { ...state, items };
}

function lastIndex(state) {
    return state.items.length - 1;
}

/** Turns a dangling interim caller text into text and closes it (the other side took over). */
function settle(state) {
    const i = lastIndex(state);
    const last = state.items[i];
    if (last && last.role === 'user' && !last.closed && last.interim) {
        return replaceAt(state, i, { ...last, text: join(last.text, last.interim), interim: '' });
    }
    return state;
}

/** Marks pending lookups as done (their answer has been spoken). */
function finishTools(state) {
    if (!state.items.some((item) => item.role === 'tool' && !item.done)) return state;
    return { ...state, items: state.items.map((item) => (item.role === 'tool' && !item.done ? { ...item, done: true } : item)) };
}

function addCaption(state, { role, text, final = true, turn = false, interrupted = false }) {
    if (!text) return state;
    if (role === 'user') {
        const i = lastIndex(state);
        const last = state.items[i];
        if (turn) return push(settle(state), { role: 'user', text, interim: '', closed: true });
        if (last && last.role === 'user' && !last.closed) {
            return replaceAt(
                state,
                i,
                final ? { ...last, text: join(last.text, text), interim: '' } : { ...last, interim: text },
            );
        }
        return push(state, { role: 'user', text: final ? text : '', interim: final ? '' : text, closed: false });
    }
    if (role === 'bot') {
        const settled = finishTools(settle(state));
        if (turn) return push(settled, { role: 'bot', text, closed: true, interrupted });
        const i = lastIndex(settled);
        const tail = settled.items[i];
        if (tail && tail.role === 'bot' && !tail.closed) {
            return replaceAt(settled, i, { ...tail, text: join(tail.text, text), interrupted: tail.interrupted || interrupted });
        }
        return push(settled, { role: 'bot', text, closed: false, interrupted });
    }
    return state;
}

const TOOL_FOR_CARD = { file_status: 'file_status', eligibility: 'eligibility', reminder: 'reminder' };

function addCard(state, card) {
    if (!card || !card.type) return state;
    // Replace the matching "Checking…" line, if it is still pending among the last few items.
    const tool = TOOL_FOR_CARD[card.type];
    for (let i = lastIndex(state); i >= 0 && i >= state.items.length - 6; i--) {
        const item = state.items[i];
        if (item.role === 'tool' && !item.done && item.name === tool) {
            return replaceAt(state, i, { id: item.id, role: 'card', card });
        }
    }
    return push(settle(state), { role: 'card', card });
}

function addTool(state, name) {
    if (!name) return state;
    const i = lastIndex(state);
    const last = state.items[i];
    if (last && last.role === 'tool' && !last.done && last.name === name) return state; // duplicate start
    return push(settle(state), { role: 'tool', name, done: false });
}

export function captionsReducer(state, action) {
    switch (action.type) {
        case 'reset':
            return initialCaptions;
        case 'caption':
            return addCaption(state, action);
        case 'tool':
            return addTool(state, action.name);
        case 'card':
            return addCard(state, action.card);
        case 'interrupt': {
            // Only a bubble that was still being spoken (part captions) can be cut off.
            const i = lastIndex(state);
            const last = state.items[i];
            if (!last || last.role !== 'bot' || last.closed) return state;
            return replaceAt(state, i, { ...last, closed: true, interrupted: true });
        }
        case 'end': {
            const settled = finishTools(settle(state));
            const i = lastIndex(settled);
            const last = settled.items[i];
            if (last && (last.role === 'bot' || last.role === 'user') && !last.closed) {
                return replaceAt(settled, i, { ...last, closed: true });
            }
            return settled;
        }
        default:
            return state;
    }
}
