import { useMemo, useState, useCallback } from 'react';
import { X, Network, ChevronDown, ChevronUp } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useModal } from '../hooks/useModal';
import GraphView from './GraphView/GraphView';
import { GRAPH_BG } from './GraphView/constants';
import type { GraphData } from './GraphView/useGraphData';
import type { GraphSearchResult } from './ChatPanel/types';

interface GraphSearchResultModalProps {
  isOpen: boolean;
  onClose: () => void;
  data: GraphSearchResult;
}

/** Parse origin qa_id to { segIdx, segKey } */
function parseOriginQaId(qaId: string, fallbackDocId: string) {
  const parts = qaId.split('_');
  const hasQaIdx = parts.length >= 4;
  const segIdx = hasQaIdx
    ? parseInt(parts[parts.length - 2], 10)
    : parseInt(parts[parts.length - 1] || '0', 10);
  return { segIdx, segKey: `${fallbackDocId}_${segIdx}` };
}

function toGraphData(
  result: GraphSearchResult,
  selectedOrigin?: string | null,
): GraphData {
  const nodes: GraphData['nodes'] = [];
  const edges: GraphData['edges'] = [];
  const docIds = new Set<string>();
  const segKeys = new Set<string>();
  const fallbackDocId = result.sources[0]?.document_id ?? '';

  // Build origin segKey -> entity mapping for filtering
  // When selectedOrigin is set, only show entities/sources connected to that origin
  const originEntities = new Set<string>();
  if (selectedOrigin) {
    // All entities are connected to all origins for now,
    // so we show all entities but filter sources to those mentioned by these entities
    for (const entity of result.entities) {
      originEntities.add(entity.name);
    }
  }

  // Entity nodes
  for (const entity of result.entities) {
    nodes.push({
      id: `entity-${entity.name}`,
      name: entity.name,
      label: 'entity',
      properties: {},
    });
  }

  // Analysis, Segment, Document nodes + structural edges
  for (const source of result.sources) {
    const qaId = source.qa_id || source.segment_id;
    const qaIdx = source.qa_index ?? 0;
    const segKey = `${source.document_id}_${source.segment_index}`;

    // Analysis node
    nodes.push({
      id: `qa-${qaId}`,
      name: qaIdx === 0 ? 'Analysis' : `Extra ${qaIdx}`,
      label: 'analysis',
      properties: {
        segment_index: source.segment_index,
        qa_index: qaIdx,
        is_base: qaIdx === 0,
      },
    });

    // Analysis -> Segment
    edges.push({
      source: `qa-${qaId}`,
      target: `seg-${segKey}`,
      label: 'BELONGS_TO',
      properties: null,
    });

    // Segment node (deduplicate)
    if (!segKeys.has(segKey)) {
      segKeys.add(segKey);
      nodes.push({
        id: `seg-${segKey}`,
        name: `Page ${source.segment_index + 1}`,
        label: 'segment',
        properties: {
          segment_index: source.segment_index,
          document_id: source.document_id,
        },
      });
      // Segment -> Document
      edges.push({
        source: `seg-${segKey}`,
        target: `doc-${source.document_id}`,
        label: 'BELONGS_TO',
        properties: null,
      });
    }

    // Document node (deduplicate)
    if (!docIds.has(source.document_id)) {
      docIds.add(source.document_id);
      nodes.push({
        id: `doc-${source.document_id}`,
        name: 'Document',
        label: 'document',
        properties: {},
      });
    }
  }

  // Origin nodes
  const originSegKeys = new Set<string>();
  for (const qaId of result.origin_qa_ids ?? []) {
    const { segIdx, segKey } = parseOriginQaId(qaId, fallbackDocId);
    if (!originSegKeys.has(segKey)) {
      originSegKeys.add(segKey);
      const isSelected = selectedOrigin === segKey;
      nodes.push({
        id: `origin-seg-${segKey}`,
        name: `Page ${segIdx + 1}`,
        label: 'segment',
        properties: {
          segment_index: segIdx,
          is_origin: true,
          is_selected: isSelected,
        },
      });
    }
  }

  // Entity -> Analysis edges (discovered)
  for (const entity of result.entities) {
    for (const source of result.sources) {
      const qaId = source.qa_id || source.segment_id;
      edges.push({
        source: `entity-${entity.name}`,
        target: `qa-${qaId}`,
        label: 'MENTIONED_IN',
        properties: null,
      });
    }
  }

  // Origin -> Entity edges
  if (selectedOrigin) {
    // Only show edges from selected origin
    for (const entity of result.entities) {
      edges.push({
        source: `origin-seg-${selectedOrigin}`,
        target: `entity-${entity.name}`,
        label: 'ORIGIN',
        properties: null,
      });
    }
  } else {
    for (const segKey of originSegKeys) {
      for (const entity of result.entities) {
        edges.push({
          source: `origin-seg-${segKey}`,
          target: `entity-${entity.name}`,
          label: 'ORIGIN',
          properties: null,
        });
      }
    }
  }

  return { nodes, edges };
}

/** Unique origin pages from qa_ids */
function getOriginPages(
  qaIds: string[],
  fallbackDocId: string,
): Array<{ segIdx: number; segKey: string }> {
  const seen = new Set<string>();
  const pages: Array<{ segIdx: number; segKey: string }> = [];
  for (const qaId of qaIds) {
    const { segIdx, segKey } = parseOriginQaId(qaId, fallbackDocId);
    if (!seen.has(segKey)) {
      seen.add(segKey);
      pages.push({ segIdx, segKey });
    }
  }
  return pages;
}

export default function GraphSearchResultModal({
  isOpen,
  onClose,
  data,
}: GraphSearchResultModalProps) {
  const { t } = useTranslation();
  useModal({ isOpen, onClose });
  const [entitiesExpanded, setEntitiesExpanded] = useState(false);
  const [selectedOrigin, setSelectedOrigin] = useState<string | null>(null);

  const graphData = useMemo(
    () => toGraphData(data, selectedOrigin),
    [data, selectedOrigin],
  );

  const originPages = useMemo(
    () =>
      getOriginPages(
        data.origin_qa_ids ?? [],
        data.sources[0]?.document_id ?? '',
      ),
    [data],
  );

  const handleOriginClick = useCallback((segKey: string) => {
    setSelectedOrigin((prev) => (prev === segKey ? null : segKey));
  }, []);

  if (!isOpen) return null;

  const hasAnswer = !!data.answer;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 dark:bg-black/70 backdrop-blur-md animate-in fade-in duration-200"
      onClick={onClose}
    >
      <div
        className="w-full max-w-7xl mx-4 rounded-2xl border border-slate-200 dark:border-blue-500/30 shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 bg-white dark:bg-transparent"
        style={{ height: '85vh' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2.5 px-5 py-3.5 border-b border-slate-200 dark:border-blue-500/20 bg-gradient-to-r from-slate-50 to-slate-100 dark:from-blue-900/30 dark:to-indigo-900/30">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 shadow-sm">
            <Network className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="text-sm font-semibold text-slate-700 dark:text-blue-200">
            {t('chat.graphSearchResult', 'Knowledge Graph')}
          </span>
          <span className="text-xs text-slate-500 dark:text-slate-400">
            {data.entities.length} {t('chat.entities', 'entities')},{' '}
            {data.sources.length} {t('chat.segments', 'segments')}
          </span>
          <div className="flex-1" />
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-slate-200 dark:hover:bg-blue-800/40 transition-colors"
          >
            <X className="w-4.5 h-4.5 text-slate-500 dark:text-slate-400" />
          </button>
        </div>

        {/* Content: split or graph only */}
        <div
          className={hasAnswer ? 'flex' : ''}
          style={{ height: 'calc(85vh - 52px)' }}
        >
          {/* Left: Answer content */}
          {hasAnswer && (
            <div className="w-2/5 border-r border-black/[0.08] dark:border-blue-500/20 overflow-y-auto p-5 bg-[#e8ecf4]/90 dark:bg-slate-900">
              {/* Origin pages (clickable) */}
              {originPages.length > 0 && (
                <div className="mb-3">
                  <p className="text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1.5">
                    {t('chat.originPages', 'Starting from')}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {originPages.map(({ segIdx, segKey }) => (
                      <button
                        key={segKey}
                        type="button"
                        onClick={() => handleOriginClick(segKey)}
                        className={`text-[10px] font-medium px-2 py-1 rounded-full border transition-colors ${
                          selectedOrigin === segKey
                            ? 'border-amber-400 bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300'
                            : 'border-amber-300/50 dark:border-amber-500/30 bg-amber-50/50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-900/30'
                        }`}
                      >
                        p.{segIdx + 1}
                      </button>
                    ))}
                    {selectedOrigin && (
                      <button
                        type="button"
                        onClick={() => setSelectedOrigin(null)}
                        className="text-[10px] text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 px-1"
                      >
                        {t('chat.showAll', 'Show all')}
                      </button>
                    )}
                  </div>
                </div>
              )}

              {/* Keywords (query input) */}
              {data.keywords && data.keywords.length > 0 && (
                <div className="mb-3">
                  <p className="text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1.5">
                    {t('chat.searchKeyword', 'Keyword')}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {data.keywords.map((kw, i) => (
                      <span
                        key={i}
                        className="text-[10px] font-medium px-2 py-1 rounded-full border border-blue-300 dark:border-blue-500/40 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
                      >
                        {kw}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Connected entities (collapsible) */}
              {data.entities?.length > 0 && (
                <div className="mb-4">
                  <button
                    onClick={() => setEntitiesExpanded(!entitiesExpanded)}
                    className="flex items-center gap-1.5 text-xs font-medium text-violet-600 dark:text-violet-400 hover:text-violet-800 dark:hover:text-violet-300 transition-colors mb-2"
                  >
                    {entitiesExpanded ? (
                      <ChevronUp className="w-3.5 h-3.5" />
                    ) : (
                      <ChevronDown className="w-3.5 h-3.5" />
                    )}
                    {t('chat.connectedEntities', 'Connected Entities')} (
                    {data.entities.length})
                  </button>
                  {entitiesExpanded && (
                    <div className="flex flex-wrap gap-1.5">
                      {data.entities.map((entity, i) => (
                        <span
                          key={i}
                          className="text-[10px] font-medium px-2 py-1 rounded-full border border-violet-300 dark:border-violet-500/40 bg-violet-50 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300"
                        >
                          {entity.name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Answer markdown */}
              <div className="prose prose-sm dark:prose-invert max-w-none text-slate-700 dark:text-slate-200 [&_strong]:!text-inherit [&_p]:leading-relaxed">
                <ReactMarkdown
                  remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
                >
                  {data.answer ?? ''}
                </ReactMarkdown>
              </div>

              {/* Sources list (deduplicated by page) */}
              {data.sources?.length > 0 &&
                (() => {
                  const seen = new Set<number>();
                  const uniqueSources = data.sources.filter((s) => {
                    if (seen.has(s.segment_index)) return false;
                    seen.add(s.segment_index);
                    return true;
                  });
                  return (
                    <div className="mt-4 pt-3 border-t border-slate-200 dark:border-blue-500/20">
                      <p className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-2">
                        {t(
                          'chat.graphSourcesFound',
                          '{{count}} additional pages discovered',
                          { count: uniqueSources.length },
                        )}
                      </p>
                      <div className="space-y-1">
                        {uniqueSources.map((source, i) => (
                          <div
                            key={i}
                            className="text-[10px] text-slate-400 dark:text-slate-500 font-mono"
                          >
                            Page {source.segment_index + 1} &middot;{' '}
                            {source.match_type ?? 'graph'}
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })()}
            </div>
          )}

          {/* Right (or full): Graph */}
          <div
            className={hasAnswer ? 'flex-1' : 'w-full h-full'}
            style={{ backgroundColor: GRAPH_BG }}
          >
            <GraphView data={graphData} />
          </div>
        </div>
      </div>
    </div>
  );
}
