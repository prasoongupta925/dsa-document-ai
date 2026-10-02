// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import logoUrl from '../assets/logo.png';

/** The DSA Document AI "DA" mark (same asset as the Document AI app). */
export default function BrandMark({ size = 32, className = '' }) {
    return <img className={`brand-mark ${className}`} src={logoUrl} width={size} height={size} alt="DSA Document AI" />;
}
