import type { Agent } from '../types/project';

/** Agents shipped with the platform have ids starting with this prefix. */
export const BUILTIN_AGENT_PREFIX = 'builtin-';

/** A built-in agent: selectable, never edited or deleted (the API returns 403). */
export function isBuiltinAgent(
  agent: Pick<Agent, 'agent_id' | 'builtin'> | null | undefined,
): boolean {
  if (!agent) return false;
  return (
    agent.builtin === true ||
    (typeof agent.agent_id === 'string' &&
      agent.agent_id.startsWith(BUILTIN_AGENT_PREFIX))
  );
}

/** Built-in agents first, each group in the API's order. */
export function sortAgentsBuiltinFirst<T extends Agent>(agents: T[]): T[] {
  return [
    ...agents.filter((a) => isBuiltinAgent(a)),
    ...agents.filter((a) => !isBuiltinAgent(a)),
  ];
}
