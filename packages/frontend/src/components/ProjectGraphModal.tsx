import { useState, useEffect, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Loader2,
  Network,
  Cloud,
  ChevronDown,
  Search,
  X,
  RefreshCw,
} from 'lucide-react';
import GraphView from './GraphView/GraphView';
import TagCloudView from './GraphView/TagCloudView';
import type { GraphData } from './GraphView/useGraphData';

function ToggleSwitch({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: () => void;
}) {
  return (
    <button
      onClick={onChange}
      className={`relative w-8 h-[18px] rounded-full transition-colors flex-shrink-0 cursor-pointer ${
        checked ? 'bg-blue-500' : 'bg-slate-300 dark:bg-[#363b50]'
      }`}
    >
      <span
        className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow-sm transition-transform ${
          checked ? 'left-[16px]' : 'left-[2px]'
        }`}
      />
    </button>
  );
}

function ToggleRow({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[12px] text-slate-600 dark:text-slate-300">
        {label}
      </span>
      <ToggleSwitch checked={checked} onChange={() => onChange(!checked)} />
    </div>
  );
}

interface SharedEntity {
  name: string;
}

interface EdgeDetailPopover {
  sourceLabel: string;
  targetLabel: string;
  sharedCount: number;
  entities: SharedEntity[];
  x: number;
  y: number;
}

interface ProjectGraphModalProps {
  projectId: string;
  projectName: string;
  fetchApi: <T = unknown>(path: string, options?: RequestInit) => Promise<T>;
  onClose: () => void;
}

export default function ProjectGraphModal({
  projectId,
  projectName,
  fetchApi,
  onClose,
}: ProjectGraphModalProps) {
  const { t } = useTranslation();
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [totalEntities, setTotalEntities] = useState(0);

  const [subMode, setSubMode] = useState<'force' | 'tagcloud'>('tagcloud');
  const [searchFilter, setSearchFilter] = useState('');
  const [showEdgeLabels, setShowEdgeLabels] = useState(true);
  const [edgeDetail, setEdgeDetail] = useState<EdgeDetailPopover | null>(null);

  // Tag Cloud controls
  const [tagCloudMinConn, setTagCloudMinConn] = useState(1);
  const [tagCloudMaxTags, setTagCloudMaxTags] = useState(100);
  const [tagCloudRotation, setTagCloudRotation] = useState(true);

  const [rebuilding, setRebuilding] = useState(false);

  const [panelSections, setPanelSections] = useState({
    filters: true,
    display: false,
  });

  const fetchGraph = useCallback(
    (search?: string) => {
      setLoading(true);
      const params = new URLSearchParams();
      if (search) params.set('search', search);
      const qs = params.toString();
      const url = `projects/${projectId}/graph${qs ? `?${qs}` : ''}`;
      fetchApi<GraphData & { total_entities?: number }>(url)
        .then((data) => {
          setGraphData(data);
          setTotalEntities(data.total_entities ?? 0);
        })
        .catch(() => setGraphData({ nodes: [], edges: [] }))
        .finally(() => setLoading(false));
    },
    [fetchApi, projectId],
  );

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  const docCount = useMemo(() => {
    if (!graphData) return 0;
    return graphData.nodes.filter((n) => n.label === 'document').length;
  }, [graphData]);

  const handleTagClick = useCallback(
    (label: string) => {
      setSearchFilter(label);
      setSubMode('force');
      fetchGraph(label);
    },
    [fetchGraph],
  );

  const handleSearch = useCallback(() => {
    if (searchFilter.trim()) {
      fetchGraph(searchFilter.trim());
    } else {
      fetchGraph();
    }
  }, [fetchGraph, searchFilter]);

  const handleLinkClick = useCallback(
    (link: {
      source: string;
      target: string;
      label: string;
      properties?: Record<string, unknown> | null;
    }) => {
      if (!graphData || !link.properties) return;
      const sourceNode = graphData.nodes.find((n) => n.id === link.source);
      const targetNode = graphData.nodes.find((n) => n.id === link.target);
      const entities =
        (link.properties.shared_entities as SharedEntity[]) ?? [];
      setEdgeDetail({
        sourceLabel: sourceNode?.name ?? link.source,
        targetLabel: targetNode?.name ?? link.target,
        sharedCount:
          (link.properties.shared_count as number) ?? entities.length,
        entities,
        x: window.innerWidth / 2,
        y: window.innerHeight / 2,
      });
    },
    [graphData],
  );

  const handleRebuild = useCallback(() => {
    if (rebuilding) return;
    setRebuilding(true);
    fetchApi(`projects/${projectId}/graph/rebuild`, { method: 'POST' })
      .then(() => {
        setTimeout(() => {
          fetchGraph();
          setRebuilding(false);
        }, 3000);
      })
      .catch(() => setRebuilding(false));
  }, [fetchApi, fetchGraph, projectId, rebuilding]);

  return (
    <div
      className="fixed inset-0 bg-black/55 dark:bg-black/65 backdrop-blur-md flex items-center justify-center z-50 p-6"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="document-detail-modal rounded-2xl w-full max-w-7xl h-[90vh] flex flex-col overflow-hidden relative">
        {/* Header */}
        <div className="h-12 flex-shrink-0 flex items-center justify-between px-5 border-b border-slate-200 dark:border-[#363b50] bg-white/80 dark:bg-[#0d1117]/80 backdrop-blur-sm z-[5]">
          <div className="flex items-center gap-3">
            <Network className="w-4 h-4 text-slate-500 dark:text-slate-400" />
            <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              {projectName} — {t('workflow.graph.title', 'Knowledge Graph')}
            </span>
            {!loading && (
              <span className="text-[11px] text-slate-400 dark:text-slate-500">
                {docCount} {t('workflow.graph.documents', 'docs')}
                {totalEntities > 0 && (
                  <>
                    {' / '}
                    {totalEntities} {t('workflow.graph.entities', 'entities')}
                  </>
                )}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleRebuild}
              disabled={rebuilding}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium text-slate-600 dark:text-slate-300 bg-transparent dark:bg-[#0d1117] hover:bg-black/[0.06] dark:hover:bg-[#1e2235] rounded-lg transition-colors border border-black/10 dark:border-[#3b4264] disabled:opacity-50"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 ${rebuilding ? 'animate-spin' : ''}`}
              />
              {rebuilding
                ? t('workflow.graph.rebuilding', 'Rebuilding...')
                : t('workflow.graph.rebuild', 'Rebuild')}
            </button>
            <button
              onClick={onClose}
              className="p-2 bg-transparent dark:bg-[#0d1117] hover:bg-black/[0.06] dark:hover:bg-[#1e2235] rounded-lg transition-colors border border-black/10 dark:border-[#3b4264]"
            >
              <svg
                className="h-5 w-5 text-slate-600 dark:text-slate-300"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
          </div>
        </div>

        <div className="flex flex-1 min-h-0">
          {/* Left Panel */}
          <div className="w-[240px] flex-shrink-0 border-r border-slate-200 dark:border-[#363b50] flex flex-col overflow-hidden">
            {/* Sub mode toggle */}
            <div className="flex items-center gap-1 px-4 py-3 border-b border-black/[0.06] dark:border-[#363b50]">
              <button
                onClick={() => {
                  setSubMode('force');
                  if (!searchFilter) fetchGraph();
                }}
                className={`flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] font-medium rounded-md transition-colors ${
                  subMode === 'force'
                    ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
                    : 'text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-white/[0.06]'
                }`}
              >
                <Network className="w-3.5 h-3.5" />
                Graph
              </button>
              <button
                onClick={() => setSubMode('tagcloud')}
                className={`flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] font-medium rounded-md transition-colors ${
                  subMode === 'tagcloud'
                    ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
                    : 'text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-white/[0.06]'
                }`}
              >
                <Cloud className="w-3.5 h-3.5" />
                Tags
              </button>
            </div>

            <div className="flex-1 overflow-y-auto">
              {/* Filters */}
              <div className="border-b border-black/[0.08] dark:border-[#363b50]">
                <button
                  onClick={() =>
                    setPanelSections((s) => ({ ...s, filters: !s.filters }))
                  }
                  className="flex items-center justify-between w-full px-4 py-2.5 text-[13px] font-semibold text-slate-700 dark:text-slate-200 hover:bg-black/[0.03] dark:hover:bg-white/[0.03] transition-colors"
                >
                  {t('workflow.graph.filters', 'Filters')}
                  <ChevronDown
                    className={`w-3.5 h-3.5 text-slate-400 dark:text-slate-500 transition-transform ${panelSections.filters ? '' : '-rotate-90'}`}
                  />
                </button>
                {panelSections.filters &&
                  (subMode === 'tagcloud' ? (
                    <div className="px-4 pb-4 space-y-3">
                      <div>
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[12px] text-slate-500 dark:text-slate-400">
                            Min connections
                          </span>
                          <span className="text-[11px] text-slate-400 dark:text-slate-500 tabular-nums">
                            {tagCloudMinConn}
                          </span>
                        </div>
                        <input
                          type="range"
                          min={1}
                          max={20}
                          value={tagCloudMinConn}
                          onChange={(e) =>
                            setTagCloudMinConn(Number(e.target.value))
                          }
                          className="w-full h-1 appearance-none bg-slate-200 dark:bg-[#363b50] rounded-full cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-blue-500 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:cursor-pointer"
                        />
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[12px] text-slate-500 dark:text-slate-400">
                            Max tags
                          </span>
                          <span className="text-[11px] text-slate-400 dark:text-slate-500 tabular-nums">
                            {tagCloudMaxTags}
                          </span>
                        </div>
                        <input
                          type="range"
                          min={10}
                          max={200}
                          step={10}
                          value={tagCloudMaxTags}
                          onChange={(e) =>
                            setTagCloudMaxTags(Number(e.target.value))
                          }
                          className="w-full h-1 appearance-none bg-slate-200 dark:bg-[#363b50] rounded-full cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-blue-500 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:cursor-pointer"
                        />
                      </div>
                      <ToggleRow
                        label="Rotation"
                        checked={tagCloudRotation}
                        onChange={setTagCloudRotation}
                      />
                    </div>
                  ) : (
                    <div className="px-4 pb-4 space-y-3">
                      <div className="relative">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 dark:text-slate-500" />
                        <input
                          type="text"
                          placeholder={t('workflow.graph.searchPlaceholder')}
                          value={searchFilter}
                          onChange={(e) => setSearchFilter(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleSearch();
                          }}
                          className="w-full pl-8 pr-3 py-1.5 text-[12px] bg-slate-100 dark:bg-[#1e2235] border border-slate-200 dark:border-[#363b50] rounded-md text-slate-700 dark:text-slate-300 placeholder:text-slate-400 dark:placeholder:text-slate-600 focus:outline-none focus:border-blue-400 dark:focus:border-[#4a5070]"
                        />
                      </div>
                      <button
                        onClick={handleSearch}
                        disabled={loading}
                        className="w-full px-3 py-1.5 text-[12px] font-medium bg-blue-600 hover:bg-blue-500 text-white rounded-md transition-colors disabled:opacity-50"
                      >
                        {t('workflow.graph.searchButton', 'Search')}
                      </button>
                    </div>
                  ))}
              </div>

              {/* Display (force graph only) */}
              {subMode !== 'tagcloud' && (
                <div className="border-b border-black/[0.08] dark:border-[#363b50]">
                  <button
                    onClick={() =>
                      setPanelSections((s) => ({ ...s, display: !s.display }))
                    }
                    className="flex items-center justify-between w-full px-4 py-2.5 text-[13px] font-semibold text-slate-700 dark:text-slate-200 hover:bg-black/[0.03] dark:hover:bg-white/[0.03] transition-colors"
                  >
                    {t('workflow.graph.display', 'Display')}
                    <ChevronDown
                      className={`w-3.5 h-3.5 text-slate-400 dark:text-slate-500 transition-transform ${panelSections.display ? '' : '-rotate-90'}`}
                    />
                  </button>
                  {panelSections.display && (
                    <div className="px-4 pb-3 space-y-1">
                      <ToggleRow
                        label={t('workflow.graph.edgeLabels', 'Edge labels')}
                        checked={showEdgeLabels}
                        onChange={setShowEdgeLabels}
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Main content */}
          <div
            className="flex-1 relative"
            onClick={() => edgeDetail && setEdgeDetail(null)}
          >
            {loading ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
              </div>
            ) : graphData && graphData.nodes.length > 0 ? (
              subMode === 'tagcloud' ? (
                <TagCloudView
                  data={graphData}
                  minConnections={tagCloudMinConn}
                  maxTags={tagCloudMaxTags}
                  rotation={tagCloudRotation}
                  onTagClick={handleTagClick}
                />
              ) : (
                <GraphView
                  data={graphData}
                  searchFilter={searchFilter}
                  showEdgeLabels={showEdgeLabels}
                  onLinkClick={handleLinkClick}
                  onNodeClick={(nodeId, nodeType) => {
                    if (nodeType !== 'entity') return;
                    const node = graphData.nodes.find((n) => n.id === nodeId);
                    if (node) {
                      handleTagClick(node.name);
                    }
                  }}
                />
              )
            ) : (
              <div className="flex items-center justify-center h-full text-slate-400">
                {t('workflow.graph.noData', 'No graph data')}
              </div>
            )}

            {/* Edge detail popover */}
            {edgeDetail && (
              <div
                className="absolute z-30 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[360px] max-h-[400px] bg-white dark:bg-[#1a1f2e] rounded-xl shadow-2xl border border-slate-200 dark:border-[#363b50] flex flex-col overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-[#363b50]">
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-semibold text-slate-800 dark:text-slate-100 truncate">
                      {edgeDetail.sourceLabel}
                    </div>
                    <div className="text-[10px] text-slate-400 dark:text-slate-500 my-0.5">
                      &harr;
                    </div>
                    <div className="text-[12px] font-semibold text-slate-800 dark:text-slate-100 truncate">
                      {edgeDetail.targetLabel}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-3">
                    <span className="text-[11px] text-slate-400 dark:text-slate-500 whitespace-nowrap">
                      {edgeDetail.sharedCount}{' '}
                      {t('workflow.graph.sharedEntities', 'shared')}
                    </span>
                    <button
                      onClick={() => setEdgeDetail(null)}
                      className="p-1 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded transition-colors"
                    >
                      <X className="w-3.5 h-3.5 text-slate-400" />
                    </button>
                  </div>
                </div>
                <div className="flex-1 overflow-y-auto px-4 py-2">
                  {edgeDetail.entities.length > 0 ? (
                    <div className="space-y-1">
                      {edgeDetail.entities.map((entity, i) => (
                        <div
                          key={i}
                          className="flex items-center gap-2 py-1.5 px-2 rounded-md hover:bg-slate-50 dark:hover:bg-white/[0.03]"
                        >
                          <span className="text-[12px] text-slate-700 dark:text-slate-300 flex-1 truncate">
                            {entity.name}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center text-[12px] text-slate-400 py-4">
                      {t(
                        'workflow.graph.noSharedDetails',
                        'No entity details available',
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
