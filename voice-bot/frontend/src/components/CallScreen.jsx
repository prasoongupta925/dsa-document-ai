// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { fetchAuthSession } from 'aws-amplify/auth';
import { VoiceSession, microphonePermission } from '../audio/session.js';
import { captionsReducer, initialCaptions } from '../lib/captions.js';
import { NO_CALL_ERRORS } from '../lib/outcome.js';
import { DEFAULT_EXAMPLES, LANGUAGES, htmlLang, isLanguage, t } from '../lib/i18n.js';
import BrandMark from './BrandMark.jsx';
import Captions from './Captions.jsx';
import OutboundPanel from './OutboundPanel.jsx';
import { IconAlert, IconExternal, IconLogout, IconMic, IconMicOff, IconPhone, IconPhoneOff } from './Icons.jsx';

const LANGUAGE_KEY = 'sdv.language';
// 'ending': the bot hung up and its last words are still playing.
const IN_CALL = new Set(['starting', 'connecting', 'live', 'ending']);

export function formatDuration(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds || 0));
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

const END_REASON = {
    time_cap: 'reasonTimeCap',
    silence: 'reasonSilence',
    transferred: 'reasonTransferred',
    transfer: 'reasonTransferred',
    end_call: 'reasonEnded',
    bot_ended: 'reasonEnded',
    completed: 'reasonEnded',
};

function initialLanguage(fallback) {
    try {
        const saved = window.localStorage.getItem(LANGUAGE_KEY);
        if (isLanguage(saved)) return saved;
    } catch {
        /* storage blocked */
    }
    return fallback;
}

function displayName(user) {
    return (user && (user.signInDetails?.loginId || user.username)) || '';
}

function initials(name) {
    const parts = name.split(/[\s._@-]+/).filter(Boolean);
    return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '?';
}

export default function CallScreen({ config, user, signOut }) {
    const [lang, setLang] = useState(() => initialLanguage(config.defaultLanguage));
    const [phase, setPhase] = useState('idle'); // idle | starting | connecting | live | ending | ended
    const [speaking, setSpeaking] = useState(false);
    const [muted, setMuted] = useState(false);
    const [audioSuspended, setAudioSuspended] = useState(false);
    const [error, setError] = useState(null); // { key, vars }
    const [result, setResult] = useState(null); // { reason, seconds }
    const [elapsed, setElapsed] = useState(0);
    const [micPermission, setMicPermission] = useState('unknown');
    const [callInfo, setCallInfo] = useState({}); // { callId, endedReason, recording }
    const [captions, dispatch] = useReducer(captionsReducer, initialCaptions);
    const sessionRef = useRef(null);
    const orbRef = useRef(null);

    const inCall = IN_CALL.has(phase);
    const s = (key, vars) => t(lang, key, vars);
    const name = displayName(user);
    const examples = config.examples?.[lang] ?? DEFAULT_EXAMPLES[lang];

    useEffect(() => {
        document.documentElement.lang = htmlLang(lang);
        try {
            window.localStorage.setItem(LANGUAGE_KEY, lang);
        } catch {
            /* storage blocked */
        }
    }, [lang]);

    useEffect(() => {
        let alive = true;
        microphonePermission().then((state) => alive && setMicPermission(state));
        return () => {
            alive = false;
        };
    }, [phase]);

    // End the call if this screen goes away (sign-out, hot reload).
    useEffect(() => () => sessionRef.current?.stop('unmount'), []);

    useEffect(() => {
        if (phase !== 'live') return undefined;
        const id = setInterval(() => setElapsed(sessionRef.current?.seconds ?? 0), 500);
        return () => clearInterval(id);
    }, [phase]);

    useEffect(() => {
        if (!inCall) return undefined;
        const warn = (event) => {
            event.preventDefault();
            event.returnValue = '';
        };
        window.addEventListener('beforeunload', warn);
        return () => window.removeEventListener('beforeunload', warn);
    }, [inCall]);

    const setLevels = useCallback(({ mic, bot }) => {
        const el = orbRef.current;
        if (!el) return;
        el.style.setProperty('--mic', mic.toFixed(3));
        el.style.setProperty('--bot', bot.toFixed(3));
    }, []);

    const startCall = () => {
        if (sessionRef.current && !sessionRef.current.ended) return;
        setError(null);
        setResult(null);
        setElapsed(0);
        setSpeaking(false);
        setMuted(false);
        setAudioSuspended(false);
        setCallInfo({});
        dispatch({ type: 'reset' });
        const session = new VoiceSession({
            websocketUrl: config.websocketUrl,
            pipeline: config.pipeline,
            tokenTransport: config.tokenTransport,
            language: lang,
            maxCallSeconds: config.maxCallSeconds,
            pageHref: window.location.href,
            getToken: async () => (await fetchAuthSession()).tokens?.idToken?.toString() ?? null,
            on: {
                phase: setPhase,
                caption: (c) =>
                    dispatch({
                        type: 'caption',
                        role: c.role,
                        text: c.text,
                        final: c.final,
                        turn: c.turn,
                        interrupted: c.interrupted,
                    }),
                interrupt: () => dispatch({ type: 'interrupt' }),
                event: (m) => {
                    if (m.kind === 'tool') dispatch({ type: 'tool', name: m.name });
                    else if (m.kind === 'result') dispatch({ type: 'card', card: m.card });
                    else if (m.kind === 'language') setLang(m.language); // the bot switched: follow it
                    else if (m.kind === 'callStarted') setCallInfo((info) => ({ ...info, callId: m.callId }));
                    else if (m.kind === 'callEnded') setCallInfo((info) => ({ ...info, endedReason: m.reason }));
                    else if (m.kind === 'recording') setCallInfo((info) => ({ ...info, recording: m.status }));
                },
                speaking: setSpeaking,
                levels: setLevels,
                audioSuspended: setAudioSuspended,
                end: (outcome) => {
                    dispatch({ type: 'end' });
                    setSpeaking(false);
                    setAudioSuspended(false);
                    setLevels({ mic: 0, bot: 0 });
                    setElapsed(outcome.seconds);
                    if (outcome.error) setError({ key: outcome.error, vars: { reason: outcome.detail || '' } });
                    else if (outcome.reason === 'limit') {
                        setError({ key: 'timeLimit', vars: { min: Math.round(config.maxCallSeconds / 60) }, info: true });
                    }
                    setResult(outcome);
                    setPhase('ended');
                },
            },
        });
        sessionRef.current = session;
        session.start(); // synchronous part creates the AudioContext inside this tap
    };

    const endCall = () => sessionRef.current?.stop('user');

    const toggleMute = () => {
        const next = !muted;
        setMuted(next);
        sessionRef.current?.setMuted(next);
    };

    const onSignOut = () => {
        sessionRef.current?.stop('user');
        signOut();
    };

    let status;
    if (phase === 'starting') status = micPermission === 'granted' ? s('statusStarting') : s('statusAllowMic');
    else if (phase === 'connecting') status = s('statusConnecting');
    else if (phase === 'live') status = muted ? s('statusMuted') : speaking ? s('statusSpeaking') : s('statusListening');
    else if (phase === 'ending') status = s('statusEnding');
    else status = s('statusIdle');

    const qaUrl = config.qaProjectUrl;
    // The post-call card only for a call that really happened (not a refused or failed connect).
    const showEndCard = result && phase === 'ended' && result.seconds >= 1 && !NO_CALL_ERRORS.includes(result.error);
    const endReasonKey = END_REASON[callInfo.endedReason || (result && result.detail) || ''];

    return (
        <div className={`shell phase-${phase}${inCall ? ' in-call' : ''}${speaking ? ' bot-speaking' : ''}`}>
            <header className="topbar">
                <div className="brand">
                    <BrandMark size={34} />
                    <div className="brand-text">
                        <span className="brand-name">
                            <span className="name-full">{s('appTitle')}</span>
                            <span className="name-short">Document AI</span>
                        </span>
                        <span className="voice-badge">{s('voice')}</span>
                    </div>
                </div>
                <nav className="topbar-actions" aria-label="Account">
                    {config.idpAppUrl ? (
                        <a
                            className="link-btn"
                            href={config.idpAppUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={s('openIdp')}
                            aria-label={s('openIdp')}
                        >
                            <IconExternal size={16} />
                            <span className="hide-sm">Document AI</span>
                        </a>
                    ) : null}
                    <span className="user-chip" title={s('signedInAs', { user: name })}>
                        <span className="avatar" aria-hidden="true">
                            {initials(name)}
                        </span>
                        <span className="user-name hide-sm">{name}</span>
                    </span>
                    <button
                        type="button"
                        className="ghost-btn"
                        onClick={onSignOut}
                        title={s('signOut')}
                        aria-label={s('signOut')}
                    >
                        <IconLogout size={16} />
                        <span className="hide-sm">{s('signOut')}</span>
                    </button>
                </nav>
            </header>

            <main className="stage">
                <section className="call-panel">
                    <div className={`notice${phase === 'live' ? ' is-live' : ''}`} role="note">
                        <span className="rec-dot" aria-hidden="true" />
                        <div>
                            <strong>{s('notice')}</strong>
                            {lang !== 'en-IN' ? <span className="notice-en">{t('en-IN', 'notice')}</span> : null}
                            <span className="notice-detail">{s('noticeDetail')}</span>
                        </div>
                    </div>

                    <div className="lang-picker" role="radiogroup" aria-label={s('language')}>
                        {LANGUAGES.map((l) => (
                            <button
                                key={l.code}
                                type="button"
                                role="radio"
                                aria-checked={lang === l.code}
                                lang={l.html}
                                className={lang === l.code ? 'active' : ''}
                                disabled={inCall}
                                onClick={() => setLang(l.code)}
                            >
                                {l.label}
                            </button>
                        ))}
                    </div>

                    <div className="call-area">
                        <p className="status">
                            <span aria-live="polite">{status}</span>
                            {phase === 'live' ? (
                                <span className="timer" role="timer">
                                    {formatDuration(elapsed)}
                                </span>
                            ) : null}
                            {inCall ? (
                                <span className="lang-chip" lang={htmlLang(lang)}>
                                    {LANGUAGES.find((l) => l.code === lang)?.label}
                                </span>
                            ) : null}
                        </p>
                        <div className="orb" ref={orbRef}>
                            <span className="ring ring-bot" aria-hidden="true" />
                            <span className="ring ring-mic" aria-hidden="true" />
                            <button
                                type="button"
                                className={`call-btn${inCall ? ' end' : ''}`}
                                onClick={inCall ? endCall : startCall}
                                aria-label={inCall ? s('endAria') : s('callAria')}
                            >
                                {inCall ? <IconPhoneOff size={34} /> : <IconPhone size={34} />}
                                <span>{inCall ? s('end') : s('call')}</span>
                            </button>
                        </div>
                        <div className="call-tools">
                            {inCall && phase !== 'ending' ? (
                                <button
                                    type="button"
                                    className={`tool-btn${muted ? ' on' : ''}`}
                                    onClick={toggleMute}
                                    aria-pressed={muted}
                                >
                                    {muted ? <IconMicOff size={18} /> : <IconMic size={18} />}
                                    <span>{muted ? s('unmute') : s('mute')}</span>
                                </button>
                            ) : null}
                            {audioSuspended ? (
                                <button type="button" className="tool-btn warn" onClick={() => sessionRef.current?.resumeAudio()}>
                                    {s('resumeAudio')}
                                </button>
                            ) : null}
                        </div>
                    </div>

                    {error ? (
                        <div className={`alert${error.info ? ' info' : ''}`} role={error.info ? 'status' : 'alert'}>
                            <IconAlert size={18} />
                            <div>
                                <p>{s(error.key, error.vars)}</p>
                                {error.key === 'errAuth' ? (
                                    <button type="button" className="link-like" onClick={onSignOut}>
                                        {s('signOut')}
                                    </button>
                                ) : null}
                            </div>
                        </div>
                    ) : null}

                    {showEndCard ? (
                        <div className="end-card" role="status">
                            <div className="end-head">
                                <strong>{result.reason === 'assistant' ? s('assistantEnded') : s('callEnded')}</strong>
                                <span className="end-duration">
                                    {s('duration')} {formatDuration(result.seconds)}
                                </span>
                            </div>
                            {endReasonKey ? <p className="end-reason">{s(endReasonKey)}</p> : null}
                            <p>
                                {s('postCall')}
                                {callInfo.callId ? ` ${s('recordingHint', { ref: callInfo.callId.slice(0, 6) })}` : ''}
                            </p>
                            <div className="end-foot">
                                {qaUrl || config.idpAppUrl ? (
                                    <a
                                        className="link-btn"
                                        href={qaUrl || config.idpAppUrl}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                    >
                                        {qaUrl ? s('openQa') : s('openIdp')} <IconExternal size={14} />
                                    </a>
                                ) : null}
                                {callInfo.callId ? (
                                    <span className="call-id">
                                        {s('callId')} <code>{callInfo.callId}</code>
                                    </span>
                                ) : null}
                            </div>
                        </div>
                    ) : null}

                    {config.outboundCalls ? <OutboundPanel config={config} lang={lang} /> : null}

                    <p className="tip">{s('headphones')}</p>
                </section>

                <Captions
                    items={captions.items}
                    lang={lang}
                    examples={examples}
                    live={phase === 'live'}
                    speaking={speaking}
                />
            </main>

            <footer className="footer">
                DSA Document AI · Voice {__APP_VERSION__} ·{' '}
                {config.region === 'ap-south-1' ? 'Mumbai (ap-south-1)' : config.region} · {s('footer')}
            </footer>
        </div>
    );
}
