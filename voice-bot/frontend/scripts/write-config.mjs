#!/usr/bin/env node
// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// Writes the runtime config.json of the voice web app (no rebuild per environment).
//
//   node scripts/write-config.mjs --idp-stack IDP-V2-Application [--profile <profile>] [--out dist/config.json]
//   VOICE_USER_POOL_ID=... VOICE_USER_POOL_CLIENT_ID=... node scripts/write-config.mjs
//
// --idp-stack reads the Document AI stack's outputs (read-only `aws cloudformation describe-stacks`):
// the user pool id, its app client id and the Document AI CloudFront domain. Environment variables
// override them:
//   VOICE_REGION (ap-south-1)          VOICE_USER_POOL_ID           VOICE_USER_POOL_CLIENT_ID
//   VOICE_WEBSOCKET_URL (/ws)          VOICE_IDP_APP_URL            VOICE_QA_PROJECT_ID
//   VOICE_PIPELINE (transcribe-polly)  VOICE_DEFAULT_LANGUAGE (hi-IN) VOICE_MAX_CALL_SECONDS (600)
//   VOICE_TOKEN_TRANSPORT (subprotocol) VOICE_OUTBOUND_CALLS (false)
// The result is checked with the app's own validator (src/lib/config.js) before it is written.

import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { normalizeConfig } from '../src/lib/config.js';

function parseArgs(argv) {
    const args = { out: 'dist/config.json', region: process.env.VOICE_REGION || 'ap-south-1' };
    for (let i = 0; i < argv.length; i++) {
        const a = argv[i];
        const next = () => {
            if (i + 1 >= argv.length) throw new Error(`${a} needs a value`);
            return argv[++i];
        };
        if (a === '--out') args.out = next();
        else if (a === '--idp-stack') args.idpStack = next();
        else if (a === '--region') args.region = next();
        else if (a === '--profile') args.profile = next();
        else if (a === '--print') args.print = true;
        else if (a === '-h' || a === '--help') args.help = true;
        else throw new Error(`unknown argument ${a}`);
    }
    return args;
}

/** Output values of a CloudFormation stack (read-only call). */
function stackOutputs(stack, region, profile) {
    const cli = ['cloudformation', 'describe-stacks', '--stack-name', stack, '--region', region, '--output', 'json'];
    if (profile) cli.push('--profile', profile);
    const json = JSON.parse(execFileSync('aws', cli, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }));
    const outputs = {};
    for (const o of json.Stacks?.[0]?.Outputs ?? []) outputs[o.OutputKey] = o.OutputValue;
    return outputs;
}

/** Picks the IDP values from its output keys (CDK adds hashes: UserIdentity...UserPoolIdXXXX). */
export function idpValuesFromOutputs(outputs) {
    const find = (test) => Object.entries(outputs).find(([key]) => test(key))?.[1];
    const domain = find((k) => /FrontendDistributionDomainName/i.test(k));
    return {
        userPoolId: find((k) => /UserPoolId/i.test(k) && !/IdentityPool|Client/i.test(k)),
        userPoolClientId: find((k) => /UserPoolClientId/i.test(k)),
        idpAppUrl: domain ? `https://${domain.replace(/^https?:\/\//, '').replace(/\/+$/, '')}` : undefined,
    };
}

function fromEnv(env) {
    const out = {};
    const set = (key, name, cast = (v) => v) => {
        if (env[name] !== undefined && env[name] !== '') out[key] = cast(env[name]);
    };
    set('region', 'VOICE_REGION');
    set('userPoolId', 'VOICE_USER_POOL_ID');
    set('userPoolClientId', 'VOICE_USER_POOL_CLIENT_ID');
    set('websocketUrl', 'VOICE_WEBSOCKET_URL');
    set('idpAppUrl', 'VOICE_IDP_APP_URL');
    set('qaProjectId', 'VOICE_QA_PROJECT_ID');
    set('defaultLanguage', 'VOICE_DEFAULT_LANGUAGE');
    set('maxCallSeconds', 'VOICE_MAX_CALL_SECONDS', Number);
    set('tokenTransport', 'VOICE_TOKEN_TRANSPORT');
    set('outboundCalls', 'VOICE_OUTBOUND_CALLS', (v) => v === 'true' || v === '1');
    if (env.VOICE_PIPELINE !== undefined) out.pipeline = env.VOICE_PIPELINE; // "" = do not send
    return out;
}

function main() {
    const args = parseArgs(process.argv.slice(2));
    if (args.help) {
        console.log('usage: write-config.mjs [--idp-stack NAME] [--profile P] [--region R] [--out FILE] [--print]');
        return 0;
    }
    let config = {
        region: args.region,
        websocketUrl: '/ws',
        pipeline: 'transcribe-polly',
        defaultLanguage: 'hi-IN',
        maxCallSeconds: 600,
        tokenTransport: 'subprotocol',
    };
    if (args.idpStack) {
        const values = idpValuesFromOutputs(stackOutputs(args.idpStack, args.region, args.profile));
        for (const [k, v] of Object.entries(values)) if (v) config[k] = v;
    }
    config = { ...config, ...fromEnv(process.env) };

    const { errors, warnings } = normalizeConfig(config);
    for (const w of warnings) console.warn(`warning: ${w}`);
    if (errors.length) {
        for (const e of errors) console.error(`error: ${e}`);
        return 1;
    }
    const text = `${JSON.stringify(config, null, 4)}\n`;
    if (args.print) process.stdout.write(text);
    const out = resolve(args.out);
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, text);
    console.error(`wrote ${out}`);
    return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
    try {
        process.exit(main());
    } catch (error) {
        console.error(`error: ${error.message}`);
        process.exit(1);
    }
}
