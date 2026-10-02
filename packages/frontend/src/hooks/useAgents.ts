import { useState, useCallback } from 'react';
import type { Agent } from '../types/project';
import { isBuiltinAgent } from '../lib/agents';

/** Built-in agents are read-only: the API answers 403 to PUT and DELETE. */
const BUILTIN_READ_ONLY = 'Built-in agents cannot be edited or deleted';

interface UseAgentsOptions {
  fetchApi: <T>(url: string, init?: RequestInit) => Promise<T>;
  projectId: string;
  onNewSession: () => void;
}

export function useAgents({
  fetchApi,
  projectId,
  onNewSession,
}: UseAgentsOptions) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [showAgentModal, setShowAgentModal] = useState(false);
  const [loadingAgents, setLoadingAgents] = useState(false);

  const loadAgents = useCallback(async () => {
    setLoadingAgents(true);
    try {
      const data = await fetchApi<Agent[]>(`projects/${projectId}/agents`);
      setAgents(data);
    } catch (error) {
      console.error('Failed to load agents:', error);
      setAgents([]);
    } finally {
      setLoadingAgents(false);
    }
  }, [fetchApi, projectId]);

  const loadAgentDetail = useCallback(
    async (agentId: string): Promise<Agent | null> => {
      try {
        return await fetchApi<Agent>(
          `projects/${projectId}/agents/${encodeURIComponent(agentId)}`,
        );
      } catch (error) {
        console.error('Failed to load agent detail:', error);
        return null;
      }
    },
    [fetchApi, projectId],
  );

  const handleAgentCreate = useCallback(
    async (name: string, content: string) => {
      await fetchApi(`projects/${projectId}/agents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, content }),
      });
      await loadAgents();
    },
    [fetchApi, projectId, loadAgents],
  );

  const handleAgentUpdate = useCallback(
    async (agentId: string, content: string) => {
      const agent = agents.find((a) => a.agent_id === agentId);
      if (!agent) return;
      if (isBuiltinAgent(agent)) throw new Error(BUILTIN_READ_ONLY);

      await fetchApi(
        `projects/${projectId}/agents/${encodeURIComponent(agentId)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: agent.name, content }),
        },
      );
      await loadAgents();
    },
    [fetchApi, projectId, loadAgents, agents],
  );

  const handleAgentDelete = useCallback(
    async (agentId: string) => {
      const agent = agents.find((a) => a.agent_id === agentId);
      if (isBuiltinAgent(agent ?? { agent_id: agentId })) {
        throw new Error(BUILTIN_READ_ONLY);
      }
      await fetchApi(
        `projects/${projectId}/agents/${encodeURIComponent(agentId)}`,
        {
          method: 'DELETE',
        },
      );
      await loadAgents();
      if (selectedAgent?.agent_id === agentId) {
        setSelectedAgent(null);
        onNewSession();
      }
    },
    [fetchApi, projectId, loadAgents, selectedAgent, onNewSession, agents],
  );

  // By id: a custom agent may have the same name as a built-in one.
  const handleAgentSelect = useCallback(
    (agentId: string | null) => {
      onNewSession();
      if (agentId === null) {
        setSelectedAgent(null);
      } else {
        const agent = agents.find((a) => a.agent_id === agentId);
        setSelectedAgent(agent || null);
      }
    },
    [agents, onNewSession],
  );

  return {
    agents,
    setAgents,
    selectedAgent,
    setSelectedAgent,
    showAgentModal,
    setShowAgentModal,
    loadingAgents,
    loadAgents,
    loadAgentDetail,
    handleAgentCreate,
    handleAgentUpdate,
    handleAgentDelete,
    handleAgentSelect,
  };
}
