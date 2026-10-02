// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import BrandMark from './BrandMark.jsx';

export default function Splash() {
    return (
        <div className="center-screen" role="status" aria-live="polite">
            <BrandMark size={64} className="pulse" />
            <p className="muted">DSA Document AI · Voice</p>
        </div>
    );
}
