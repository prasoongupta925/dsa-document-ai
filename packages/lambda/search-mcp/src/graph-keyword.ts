import { createHash } from 'node:crypto';
import { ConverseCommand } from '@aws-sdk/client-bedrock-runtime';
import {
  invokeGraphService,
  invokeLanceDB,
  bedrockClient,
} from './lib/clients.js';

const SUMMARY_MODEL_ID = 'global.amazon.nova-2-lite-v1:0';

export interface GraphKeywordInput {
  project_id: string;
  query: string;
  document_id?: string;
  limit?: number;
}

export interface GraphKeywordAnswer {
  answer?: string;
  sources: Array<{
    document_id: string;
    segment_id: string;
    qa_id?: string;
    segment_index: number;
    qa_index?: number;
    source: 'graph_keyword';
  }>;
  entities: Array<{ name: string }>;
  keywords: string[];
}

interface SegmentContent {
  segment_id: string;
  qa_id?: string;
  document_id: string;
  segment_index: number;
  qa_index?: number;
  question?: string;
  content: string;
}

/** Replicate Python's entity_id hash: sha256(project_id:name.lower().strip())[:16] */
function entityIdHash(projectId: string, name: string): string {
  const key = `${projectId}:${name.toLowerCase().trim()}`;
  return createHash('sha256').update(key).digest('hex').slice(0, 16);
}

export async function handler(
  input: GraphKeywordInput,
): Promise<GraphKeywordAnswer> {
  const { project_id, query, limit = 30 } = input;

  // 1. Search graph keywords in LanceDB by query similarity
  console.log(`[graph_keyword] query: ${query}, project_id: ${project_id}`);
  const kwResult = await invokeLanceDB('search_graph_keywords', {
    project_id,
    query,
    limit: 3,
  });

  const results = (kwResult.results ?? []) as Array<{
    entity_id: string;
    project_id: string;
    name: string;
    score: number;
  }>;
  console.log(
    `[graph_keyword] keywords found: ${results.length}`,
    results.map((r) => `${r.name} (${r.score})`),
  );

  const keywordNames = [...new Set(results.map((r) => r.name))];
  if (keywordNames.length === 0) {
    console.log('[graph_keyword] no keywords found, returning empty');
    return { sources: [], entities: [], keywords: [] };
  }

  // 2. Hash entity names to Neptune ~id and query for connected qa_ids
  const entityIds = keywordNames.map((name) => entityIdHash(project_id, name));
  console.log(
    `[graph_keyword] entity hashes:`,
    keywordNames.map((n, i) => `${n} -> ${entityIds[i]}`),
  );

  const graphResult = await invokeGraphService('raw_query', {
    query:
      'UNWIND $eids AS eid ' +
      'MATCH (e:Entity {`~id`: eid})-[:MENTIONED_IN]->(a:Analysis) ' +
      'RETURN DISTINCT a.`~id` AS qa_id',
    parameters: { eids: entityIds },
  });

  const qaIds = ((graphResult.results ?? []) as Array<{ qa_id: string }>).map(
    (r) => r.qa_id,
  );
  console.log(
    `[graph_keyword] qa_ids found: ${qaIds.length}`,
    qaIds.slice(0, 10),
  );

  if (qaIds.length === 0) {
    console.log('[graph_keyword] no qa_ids found, returning keywords only');
    const keywordEntities = keywordNames.map((name) => ({ name }));
    return { sources: [], entities: keywordEntities, keywords: keywordNames };
  }

  // 3. Fetch content from LanceDB by qa_ids
  console.log(
    `[graph_keyword] fetching ${Math.min(qaIds.length, limit)} qa_ids from LanceDB`,
  );
  const lanceResult = await invokeLanceDB('get_by_qa_ids', {
    project_id,
    qa_ids: qaIds.slice(0, limit),
  });

  const contentsToUse = (lanceResult.segments ?? []) as SegmentContent[];
  console.log(`[graph_keyword] segments fetched: ${contentsToUse.length}`);
  if (contentsToUse.length === 0) {
    const keywordEntities = keywordNames.map((name) => ({ name }));
    return { sources: [], entities: keywordEntities, keywords: keywordNames };
  }

  // Deduplicate sources by page (segment_index + document_id)
  const seenPages = new Set<string>();
  const sources = contentsToUse
    .map((s) => ({
      document_id: s.document_id,
      segment_id: s.segment_id,
      qa_id: s.qa_id,
      segment_index: s.segment_index,
      qa_index: s.qa_index ?? 0,
      source: 'graph_keyword' as const,
    }))
    .filter((s) => {
      const key = `${s.document_id}_${s.segment_index}`;
      if (seenPages.has(key)) return false;
      seenPages.add(key);
      return true;
    });

  // 5. Summarize with Amazon Nova 2 Lite
  const resultsText = contentsToUse
    .map(
      (s, i) =>
        `[${i + 1}] document_id: ${s.document_id}, segment_id: ${s.segment_id}\n${s.content}`,
    )
    .join('\n\n');

  const prompt = `You are an assistant that organizes document search results found through keyword matching in a knowledge graph.

<query>
${query}
</query>

<matched_keywords>
${keywordNames.join(', ')}
</matched_keywords>

<search_results>
${resultsText}
</search_results>

These results were found through keyword similarity matching in a knowledge graph.

Organize the information related to the query.

Rules:
1. Start with the matched keywords (e.g. "Keywords: keyword1, keyword2").
2. Preserve the original document's expressions and content as much as possible.
3. Exclude results that are not related to the query.
4. Always specify the source (document_id, segment_id) for each piece of information.
5. If no related information is found, respond with "No related information found."
6. Respond in the same language as the document content.

Output format:
**Keywords:** keyword1, keyword2, ...

[Source: document_id=xxx, segment_id=yyy]
(Information based on original content)`;

  const command = new ConverseCommand({
    modelId: SUMMARY_MODEL_ID,
    messages: [{ role: 'user', content: [{ text: prompt }] }],
    inferenceConfig: { maxTokens: 2048 },
  });

  const response = await bedrockClient.send(command);
  const answer = response.output?.message?.content?.[0]?.text ?? '';

  const citedSegmentIds = new Set(
    [...answer.matchAll(/segment_id[=:]\s*([^\s,\])\n]+)/g)].map((m) =>
      m[1].trim(),
    ),
  );
  const citedSources = sources.filter((s) => citedSegmentIds.has(s.segment_id));

  return {
    answer,
    sources: citedSources.length > 0 ? citedSources : sources,
    entities: keywordNames.map((name) => ({ name })),
    keywords: [query],
  };
}
