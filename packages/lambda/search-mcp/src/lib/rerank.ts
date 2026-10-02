import {
  BedrockAgentRuntimeClient,
  RerankCommand,
  type RerankCommandOutput,
  RerankSource,
} from '@aws-sdk/client-bedrock-agent-runtime';
import type { HybridResult } from '../types.js';

const MODEL_ID = process.env.RERANK_MODEL_ID ?? 'amazon.rerank-v1:0';
const REGION =
  process.env.RERANK_REGION ?? process.env.AWS_REGION ?? 'us-east-1';

const client = new BedrockAgentRuntimeClient({ region: REGION });

function toModelArn(modelId: string): string {
  if (modelId.startsWith('arn:')) return modelId;
  return `arn:aws:bedrock:${REGION}::foundation-model/${modelId}`;
}

export interface RerankResult extends HybridResult {
  rerankScore: number;
}

export async function rerankResults(
  query: string,
  results: HybridResult[],
  topN?: number,
): Promise<RerankResult[]> {
  const sources: RerankSource[] = results.map((r) => ({
    type: 'INLINE' as const,
    inlineDocumentSource: {
      type: 'TEXT' as const,
      textDocument: {
        text: r.content,
      },
    },
  }));

  const command = new RerankCommand({
    queries: [{ type: 'TEXT' as const, textQuery: { text: query } }],
    sources,
    rerankingConfiguration: {
      type: 'BEDROCK_RERANKING_MODEL' as const,
      bedrockRerankingConfiguration: {
        modelConfiguration: {
          modelArn: toModelArn(MODEL_ID),
        },
        numberOfResults: topN ?? results.length,
      },
    },
  });

  let response: RerankCommandOutput;
  try {
    response = await client.send(command);
  } catch {
    // Rerank unavailable: keep the hybrid-search order.
    return results
      .slice(0, topN ?? results.length)
      .map((r, i) => ({ ...r, rerankScore: 1 - i / results.length }));
  }

  return (response.results ?? [])
    .map((r) => ({
      ...results[r.index ?? 0],
      rerankScore: r.relevanceScore ?? 0,
    }))
    .sort((a, b) => b.rerankScore - a.rerankScore);
}
