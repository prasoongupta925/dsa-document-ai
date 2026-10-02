// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Amplify } from 'aws-amplify';

import '@aws-amplify/ui-react/styles.css';
import './styles.css';

import { loadConfig } from './lib/config.js';
import App from './components/App.jsx';
import ConfigError from './components/ConfigError.jsx';
import Splash from './components/Splash.jsx';

const root = createRoot(document.getElementById('root'));
root.render(<Splash />);

loadConfig({
    fetchImpl: (...args) => window.fetch(...args),
    baseUrl: import.meta.env.BASE_URL,
    pageHref: window.location.href,
})
    .then(({ config, errors, warnings }) => {
        for (const warning of warnings) console.warn(`[config] ${warning}`);
        if (!config) {
            root.render(<ConfigError errors={errors} />);
            return;
        }
        Amplify.configure({
            Auth: {
                Cognito: {
                    userPoolId: config.userPoolId,
                    userPoolClientId: config.userPoolClientId,
                    loginWith: { username: true, email: true },
                },
            },
        });
        root.render(
            <StrictMode>
                <App config={config} />
            </StrictMode>,
        );
    })
    .catch((error) => root.render(<ConfigError errors={[String((error && error.message) || error)]} />));
