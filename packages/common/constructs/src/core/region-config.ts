import { Stack, Token } from 'aws-cdk-lib';
import { Construct } from 'constructs';

/**
 * Per-region deployment settings. Every value can be overridden with CDK
 * context (`-c <key>=<value>`); otherwise the defaults for the stack region
 * apply. Cross-region values exist only for models that the deploy region
 * does not offer (e.g. ap-south-1 has no Amazon embedding or rerank model).
 */
export interface RegionConfig {
  region: string;
  lancedbExpressAzId: string;
  embeddingRegion: string;
  rerankRegion: string;
  voiceModelRegion: string;
  /** Create the AgentCore Web Search gateway target (context enableWebSearch). */
  webSearchEnabled: boolean;
}

type RegionDefaults = Omit<RegionConfig, 'region' | 'webSearchEnabled'>;

const REGION_DEFAULTS: Record<string, RegionDefaults> = {
  'us-east-1': {
    lancedbExpressAzId: 'use1-az4',
    embeddingRegion: 'us-east-1',
    rerankRegion: 'us-west-2',
    voiceModelRegion: 'us-east-1',
  },
  'ap-south-1': {
    // S3 Express One Zone zones in ap-south-1: aps1-az1 and aps1-az3.
    lancedbExpressAzId: 'aps1-az1',
    embeddingRegion: 'us-east-1',
    rerankRegion: 'ap-northeast-1',
    // Nova Sonic is not offered in ap-south-1. Voice stays in-region (off);
    // opt in with -c voiceModelRegion=ap-northeast-1 (audio leaves India).
    voiceModelRegion: 'ap-south-1',
  },
};

const FALLBACK_EMBEDDING_REGION = 'us-east-1';
const FALLBACK_RERANK_REGION = 'us-west-2';

/**
 * Regions that offer the AgentCore Web Search Tool (AgentCore Regions page,
 * checked 2026-09-27). ap-south-1 is not one of them.
 */
const WEB_SEARCH_REGIONS = ['us-east-1', 'eu-west-1', 'ap-northeast-1'];

/** Region direction -> AZ ID letters, e.g. ap-south-1 -> aps1-az1. */
const AZ_ID_DIRECTIONS: Record<string, string> = {
  north: 'n',
  south: 's',
  east: 'e',
  west: 'w',
  central: 'c',
  northeast: 'ne',
  northwest: 'nw',
  southeast: 'se',
  southwest: 'sw',
};

function contextString(scope: Construct, key: string): string | undefined {
  const value: unknown = scope.node.tryGetContext(key);
  if (value === undefined || value === null) {
    return undefined;
  }
  const text = String(value).trim();
  return text === '' ? undefined : text;
}

function contextBoolean(scope: Construct, key: string): boolean | undefined {
  const text = contextString(scope, key);
  if (text === undefined) {
    return undefined;
  }
  if (text.toLowerCase() === 'true') {
    return true;
  }
  if (text.toLowerCase() === 'false') {
    return false;
  }
  throw new Error(`CDK context ${key} must be true or false (got "${text}").`);
}

/** AZ ID prefix of a region (ap-south-1 -> aps1), undefined if unknown. */
function azIdPrefix(region: string): string | undefined {
  const match = /^([a-z]{2})-([a-z]+)-(\d+)$/.exec(region);
  const direction = match ? AZ_ID_DIRECTIONS[match[2]] : undefined;
  return match && direction ? `${match[1]}${direction}${match[3]}` : undefined;
}

export function getRegionConfig(scope: Construct): RegionConfig {
  const region = Stack.of(scope).region;
  const defaults: Partial<RegionDefaults> = Token.isUnresolved(region)
    ? {}
    : (REGION_DEFAULTS[region] ?? {});

  const lancedbExpressAzId =
    contextString(scope, 'lancedbExpressAzId') ?? defaults.lancedbExpressAzId;
  if (!lancedbExpressAzId) {
    const regionName = Token.isUnresolved(region) ? '(unresolved)' : region;
    throw new Error(
      `No S3 Express availability zone ID is known for region ${regionName}. ` +
        'Pass it as CDK context: -c lancedbExpressAzId=... ' +
        '(an S3 Express zone ID such as aps1-az1).',
    );
  }
  // Catch a zone ID of another region at synth time, not mid-deploy.
  const prefix = Token.isUnresolved(region) ? undefined : azIdPrefix(region);
  if (prefix && !new RegExp(`^${prefix}-az\\d+$`).test(lancedbExpressAzId)) {
    throw new Error(
      `lancedbExpressAzId ${lancedbExpressAzId} is not a zone ID of ` +
        `region ${region} (expected ${prefix}-az<N>).`,
    );
  }

  return {
    region,
    lancedbExpressAzId,
    embeddingRegion:
      contextString(scope, 'embeddingRegion') ??
      defaults.embeddingRegion ??
      FALLBACK_EMBEDDING_REGION,
    rerankRegion:
      contextString(scope, 'rerankRegion') ??
      defaults.rerankRegion ??
      FALLBACK_RERANK_REGION,
    voiceModelRegion:
      contextString(scope, 'voiceModelRegion') ??
      defaults.voiceModelRegion ??
      region,
    webSearchEnabled:
      contextBoolean(scope, 'enableWebSearch') ??
      (!Token.isUnresolved(region) && WEB_SEARCH_REGIONS.includes(region)),
  };
}
