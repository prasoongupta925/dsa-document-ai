// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { useEffect, useRef, useState } from 'react';
import { t } from '../lib/i18n.js';

const TOOL_LABEL = {
    file_status: 'toolFileStatus',
    eligibility: 'toolEligibility',
    reminder: 'toolReminder',
    switch_language: 'toolSwitchLanguage',
    end_call: 'toolEndCall',
    transfer_to_human: 'toolTransfer',
};

function verdictClass(verdict) {
    const v = (verdict || '').toUpperCase().replace(/[_-]/g, ' ');
    if (v === 'READY') return ['ready', 'verdictReady'];
    if (v === 'NOT READY') return ['not-ready', 'verdictNotReady'];
    return ['neutral', null];
}

function CopyButton({ text, lang }) {
    const [copied, setCopied] = useState(false);
    useEffect(() => {
        if (!copied) return undefined;
        const id = setTimeout(() => setCopied(false), 1800);
        return () => clearTimeout(id);
    }, [copied]);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
        } catch {
            setCopied(false);
        }
    };
    return (
        <button type="button" className="tool-btn small-btn" onClick={copy}>
            {copied ? t(lang, 'copied') : t(lang, 'copy')}
        </button>
    );
}

function ResultCard({ card, lang }) {
    if (card.type === 'file_status') {
        const [cls, label] = verdictClass(card.verdict);
        return (
            <div className="result-card">
                <div className="result-head">
                    <span className="result-title">{t(lang, 'cardFileStatus')}</span>
                    <span className={`verdict ${cls}`}>{label ? t(lang, label) : card.verdict}</span>
                </div>
                {card.missing.length ? (
                    <>
                        <p className="result-sub">{t(lang, 'missingTitle')}</p>
                        <ul className="result-list">
                            {card.missing.map((item) => (
                                <li key={item}>{item}</li>
                            ))}
                        </ul>
                    </>
                ) : (
                    <p className="result-sub">{t(lang, 'nothingMissing')}</p>
                )}
                {card.mismatches > 0 ? <p className="result-note">{t(lang, 'mismatchesCount', { n: card.mismatches })}</p> : null}
            </div>
        );
    }
    if (card.type === 'eligibility') {
        return (
            <div className="result-card">
                <div className="result-head">
                    <span className="result-title">{t(lang, 'cardEligibility')}</span>
                </div>
                <p className="result-big">
                    {card.amount && card.bestLender
                        ? t(lang, 'eligibilityBest', { amount: card.amount, lender: card.bestLender })
                        : card.amount || t(lang, 'eligibilityNone')}
                </p>
                <p className="result-note">{t(lang, 'eligibilityNote')}</p>
            </div>
        );
    }
    if (card.type === 'reminder') {
        const channel = card.channel === 'sms' ? 'SMS' : 'WhatsApp';
        return (
            <div className="result-card">
                <div className="result-head">
                    <span className="result-title">{t(lang, 'cardReminder', { channel })}</span>
                    <CopyButton text={card.text} lang={lang} />
                </div>
                <p className="reminder-text" lang={card.language ? card.language.slice(0, 2) : undefined}>
                    {card.text}
                </p>
                {card.placeholders.length ? (
                    <p className="result-note">{t(lang, 'placeholdersNote', { list: card.placeholders.join(', ') })}</p>
                ) : null}
                <p className="result-note">{t(lang, 'reminderTeam')}</p>
            </div>
        );
    }
    return null;
}

function Item({ item, lang }) {
    if (item.role === 'tool') {
        return (
            <li className={`timeline-tool${item.done ? ' done' : ''}`}>
                {item.done ? null : <span className="spinner" aria-hidden="true" />}
                {t(lang, TOOL_LABEL[item.name] || 'toolOther')}
            </li>
        );
    }
    if (item.role === 'card') {
        return (
            <li className="cap cap-card">
                <ResultCard card={item.card} lang={lang} />
            </li>
        );
    }
    return (
        <li className={`cap cap-${item.role}`}>
            <span className="cap-who">{item.role === 'user' ? t(lang, 'you') : t(lang, 'assistant')}</span>
            <p className="cap-text">
                {item.text}
                {item.interim ? (
                    <span className="cap-interim">
                        {item.text ? ' ' : ''}
                        {item.interim}
                    </span>
                ) : null}
                {item.interrupted ? <span className="cap-flag"> · {t(lang, 'interrupted')}</span> : null}
            </p>
        </li>
    );
}

/** Live captions of both sides plus the bot's lookups; sticks to the bottom unless scrolled up. */
export default function Captions({ items, lang, examples, live, speaking }) {
    const bodyRef = useRef(null);
    const stickRef = useRef(true);

    useEffect(() => {
        const el = bodyRef.current;
        if (el && stickRef.current) el.scrollTop = el.scrollHeight;
    }, [items, speaking]);

    const onScroll = () => {
        const el = bodyRef.current;
        if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    };

    const last = items[items.length - 1];
    // Bot captions arrive when its turn ends: show a "speaking" bubble until then.
    const typing = live && speaking && !(last && last.role === 'bot' && !last.closed);

    return (
        <section className="captions" aria-label={t(lang, 'captions')}>
            <div className="captions-head">
                <span className={`live-dot ${live ? 'on' : ''}`} aria-hidden="true" />
                {t(lang, 'captions')}
            </div>
            <div className="captions-body" ref={bodyRef} onScroll={onScroll}>
                {items.length === 0 && !typing ? (
                    <div className="hints">
                        <p className="hints-title">{t(lang, 'tryHint')}</p>
                        <ul>
                            {examples.map((example) => (
                                <li key={example}>“{example}”</li>
                            ))}
                        </ul>
                    </div>
                ) : (
                    <ol className="cap-list" aria-live="polite" aria-relevant="additions text">
                        {items.map((item) => (
                            <Item key={item.id} item={item} lang={lang} />
                        ))}
                        {typing ? (
                            <li className="cap cap-bot" aria-label={t(lang, 'statusSpeaking')}>
                                <span className="cap-who">{t(lang, 'assistant')}</span>
                                <p className="cap-text typing" aria-hidden="true">
                                    <span />
                                    <span />
                                    <span />
                                </p>
                            </li>
                        ) : null}
                    </ol>
                )}
            </div>
        </section>
    );
}
