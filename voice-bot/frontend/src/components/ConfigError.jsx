// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import BrandMark from './BrandMark.jsx';

/** Shown when config.json is missing or invalid: tells the operator exactly what to fix. */
export default function ConfigError({ errors }) {
    return (
        <div className="center-screen">
            <div className="card config-error" role="alert">
                <div className="card-head">
                    <BrandMark size={44} />
                    <div>
                        <h1>Voice app is not configured</h1>
                        <p className="muted">DSA Document AI · Voice</p>
                    </div>
                </div>
                <ul>
                    {errors.map((error) => (
                        <li key={error}>{error}</li>
                    ))}
                </ul>
                <p className="muted small">
                    The deploy writes <code>config.json</code> next to <code>index.html</code> with the Document AI
                    user pool id, its app client id and the voice WebSocket address. See{' '}
                    <code>frontend/README.md</code>.
                </p>
            </div>
        </div>
    );
}
