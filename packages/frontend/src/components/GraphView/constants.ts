/** Entity color (entity types removed). */
const ENTITY_COLOR = '#10b981';

/**
 * Get entity color. Entity types have been removed,
 * so this returns a single consistent color.
 */
export function getEntityColor(_type?: string): string {
  return ENTITY_COLOR;
}

export const DEFAULT_ENTITY_COLOR = '#6b7280';
export const SEGMENT_COLOR = '#60a5fa';
export const ANALYSIS_COLOR = '#c084fc';
export const ANALYSIS_BASE_COLOR = '#a78bfa';
export const ANALYSIS_EXTRA_COLOR = '#f472b6';
export const DOCUMENT_COLOR = '#22c55e';
export const ORIGIN_SEGMENT_COLOR = '#f59e0b';
export const GRAPH_BG = '#1e2235';

export const LINK_TYPE_COLORS: Record<string, string> = {
  NEXT: '#3b82f6',
  BELONGS_TO: '#22c55e',
  MENTIONED_IN: '#8b5cf6',
  RELATES_TO: '#6b7280',
  APPEARS_IN: '#f59e0b',
  ORIGIN: '#f59e0b',
};
