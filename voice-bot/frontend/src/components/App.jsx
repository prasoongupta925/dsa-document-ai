// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { useMemo } from 'react';
import { Authenticator, useAuthenticator } from '@aws-amplify/ui-react';
import BrandMark from './BrandMark.jsx';
import CallScreen from './CallScreen.jsx';
import Splash from './Splash.jsx';
import { IconExternal } from './Icons.jsx';

// Same Cognito user pool as Document AI: users sign in with their username (or e-mail alias).
const FORM_FIELDS = {
    signIn: {
        username: { label: 'Username or email', placeholder: 'Your username or email' },
        password: { label: 'Password', placeholder: 'Your password' },
    },
};

function SignIn({ config }) {
    const components = useMemo(
        () => ({
            Header() {
                return (
                    <div className="auth-head">
                        <BrandMark size={60} />
                        <h1>DSA Document AI</h1>
                        <p>
                            <span className="voice-badge">Voice</span> Loan file assistant
                        </p>
                    </div>
                );
            },
            Footer() {
                return (
                    <div className="auth-foot">
                        <p className="auth-notice">
                            <span className="rec-dot" aria-hidden="true" />
                            AI assistant · calls are recorded
                        </p>
                        <p>Sign in with your DSA Document AI login.</p>
                        {config.idpAppUrl ? (
                            <a href={config.idpAppUrl} target="_blank" rel="noopener noreferrer">
                                Open Document AI <IconExternal size={14} />
                            </a>
                        ) : null}
                    </div>
                );
            },
        }),
        [config.idpAppUrl],
    );

    return (
        <div className="auth-shell">
            <Authenticator hideSignUp loginMechanisms={['username']} formFields={FORM_FIELDS} components={components} />
        </div>
    );
}

function Gate({ config }) {
    const { authStatus, user, signOut } = useAuthenticator((context) => [context.authStatus, context.user]);
    if (authStatus === 'configuring') return <Splash />;
    if (authStatus === 'authenticated' && user) return <CallScreen config={config} user={user} signOut={signOut} />;
    return <SignIn config={config} />;
}

export default function App({ config }) {
    return (
        <Authenticator.Provider>
            <Gate config={config} />
        </Authenticator.Provider>
    );
}
