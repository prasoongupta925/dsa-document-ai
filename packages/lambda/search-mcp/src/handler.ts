import { handler as summarize } from './actions/search_summarize.js';
import type { SearchInput, OverviewInput } from './types.js';
import type { GraphTraverseInput } from './graph-traverse.js';
import type { GraphKeywordInput } from './graph-keyword.js';

interface LambdaContext {
  clientContext?: {
    custom?: {
      bedrockAgentCoreToolName?: string;
    };
  };
}

export const handler = async (event: unknown, context: LambdaContext) => {
  const toolName =
    context.clientContext?.custom?.bedrockAgentCoreToolName ?? '';
  const action = toolName.includes('___')
    ? toolName.split('___').pop()
    : toolName;

  switch (action) {
    case 'summarize':
      return summarize(event as SearchInput);
    case 'rerank': {
      const { handler: rerank } = await import('./actions/search_rerank.js');
      return rerank(event as SearchInput);
    }
    case 'overview': {
      const { handler: overview } = await import('./actions/overview.js');
      return overview(event as OverviewInput);
    }
    case 'graph_traverse': {
      const { handler: graphTraverse } = await import('./graph-traverse.js');
      return graphTraverse(event as GraphTraverseInput);
    }
    case 'graph_keyword': {
      const { handler: graphKeyword } = await import('./graph-keyword.js');
      return graphKeyword(event as GraphKeywordInput);
    }
    default:
      throw new Error(`Unknown tool: ${toolName}`);
  }
};
