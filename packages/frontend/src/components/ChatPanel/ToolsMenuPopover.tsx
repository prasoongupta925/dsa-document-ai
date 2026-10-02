import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronRight, Mic, Settings2, Sparkles } from 'lucide-react';
import type { Agent, BidiModelType } from './types';
import { isBuiltinAgent, sortAgentsBuiltinFirst } from '../../lib/agents';

interface ToolsMenuVoiceChat {
  available?: boolean;
  mode: boolean;
  selectedModel?: BidiModelType;
  onModelSelect?: (modelType: BidiModelType) => void;
  onDisable: () => void;
  onEnable: () => void;
  setMode: (mode: boolean) => void;
  onDisconnect?: () => void;
}

interface ToolsMenuPopoverProps {
  voiceChat: ToolsMenuVoiceChat;
  /** agent_id, or null for the default assistant. */
  onAgentSelect?: (agentId: string | null) => void;
  selectedAgent: Agent | null;
  agents: Agent[];
  messagesLength: number;
  onAgentClick: () => void;
  onClose: () => void;
  onPendingAgentChange: (agentId: string | null) => void;
  onShowRemoveAgentConfirm: () => void;
}

export default function ToolsMenuPopover({
  voiceChat,
  onAgentSelect,
  selectedAgent,
  agents,
  messagesLength,
  onAgentClick,
  onClose,
  onPendingAgentChange,
  onShowRemoveAgentConfirm,
}: ToolsMenuPopoverProps) {
  const { t } = useTranslation();
  const [showAgentSubmenu, setShowAgentSubmenu] = useState(false);
  const orderedAgents = useMemo(() => sortAgentsBuiltinFirst(agents), [agents]);

  return (
    <div className="absolute bottom-full left-0 mb-2 w-56 bg-[#e4eaf4] dark:bg-slate-800 border border-white/60 dark:border-white/30 rounded-xl shadow-lg z-50 py-1">
      {/* Voice Chat toggle */}
      {voiceChat.available && voiceChat.onModelSelect && (
        <button
          type="button"
          disabled={!!selectedAgent}
          onClick={() => {
            if (voiceChat.mode) {
              voiceChat.onDisable();
            } else {
              if (selectedAgent && onAgentSelect) {
                onAgentSelect(null);
              }
              voiceChat.onEnable();
            }
            onClose();
          }}
          className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
            selectedAgent ? 'opacity-30 cursor-not-allowed' : 'glass-menu-item'
          }`}
        >
          <Mic
            className={`w-4 h-4 ${voiceChat.mode ? 'text-purple-500' : 'text-slate-500 dark:text-slate-400'}`}
          />
          <span
            className={
              voiceChat.mode
                ? 'text-purple-600 dark:text-purple-400'
                : 'text-slate-700 dark:text-slate-300'
            }
          >
            {t('voiceChat.title')}
          </span>
          {voiceChat.mode && (
            <Check className="w-4 h-4 text-purple-500 ml-auto" />
          )}
        </button>
      )}

      {/* Agent submenu */}
      {onAgentSelect && (
        <>
          <div className="my-1 border-t border-black/[0.06] dark:border-white/30" />
          <div className="relative">
            <button
              type="button"
              disabled={voiceChat.mode}
              onClick={() => {
                setShowAgentSubmenu((v) => !v);
              }}
              className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                voiceChat.mode
                  ? 'opacity-30 cursor-not-allowed'
                  : 'text-slate-700 dark:text-slate-300 glass-menu-item'
              }`}
            >
              <Sparkles className="w-4 h-4 text-slate-500 dark:text-slate-400" />
              <span className="flex-1 text-left">{t('chat.useAgent')}</span>
              <ChevronRight className="w-4 h-4 text-slate-400" />
            </button>

            {/* Agent submenu panel */}
            {showAgentSubmenu && (
              <div className="absolute left-full bottom-0 ml-1 w-52 max-h-72 overflow-y-auto bg-[#e4eaf4] dark:bg-slate-800 border border-white/60 dark:border-white/30 rounded-xl shadow-lg z-[60] py-1">
                {/* Default agent */}
                <button
                  type="button"
                  onClick={() => {
                    if (messagesLength > 0 && selectedAgent !== null) {
                      onPendingAgentChange(null);
                      onShowRemoveAgentConfirm();
                    } else {
                      onAgentSelect(null);
                    }
                    onClose();
                    setShowAgentSubmenu(false);
                  }}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors glass-menu-item"
                >
                  <Sparkles
                    className={`w-4 h-4 ${!selectedAgent ? 'text-blue-500' : 'text-slate-400 dark:text-slate-500'}`}
                  />
                  <span
                    className={
                      !selectedAgent
                        ? 'text-blue-600 dark:text-blue-400'
                        : 'text-slate-700 dark:text-slate-300'
                    }
                  >
                    {t('agent.default')}
                  </span>
                  {!selectedAgent && (
                    <Check className="w-4 h-4 text-blue-500 ml-auto" />
                  )}
                </button>

                {/* Agents: built-in first, then custom */}
                {orderedAgents.map((agent) => {
                  const isSelected = selectedAgent?.agent_id === agent.agent_id;
                  const builtin = isBuiltinAgent(agent);
                  return (
                    <button
                      key={agent.agent_id}
                      type="button"
                      title={
                        builtin ? agent.description || undefined : undefined
                      }
                      onClick={() => {
                        if (
                          messagesLength > 0 &&
                          selectedAgent?.agent_id !== agent.agent_id
                        ) {
                          onPendingAgentChange(agent.agent_id);
                          onShowRemoveAgentConfirm();
                        } else {
                          if (voiceChat.mode) {
                            voiceChat.setMode(false);
                            voiceChat.onDisconnect?.();
                          }
                          onAgentSelect(agent.agent_id);
                        }
                        onClose();
                        setShowAgentSubmenu(false);
                      }}
                      className="w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors glass-menu-item"
                    >
                      <Sparkles
                        className={`w-4 h-4 ${isSelected ? 'text-blue-500' : 'text-slate-400 dark:text-slate-500'}`}
                      />
                      <span
                        className={`flex-1 text-left truncate ${
                          isSelected
                            ? 'text-blue-600 dark:text-blue-400'
                            : 'text-slate-700 dark:text-slate-300'
                        }`}
                      >
                        {agent.name}
                      </span>
                      {builtin && (
                        <span className="flex-shrink-0 rounded-full bg-violet-100 px-1.5 py-0.5 text-[9px] font-semibold leading-none text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">
                          {t('agent.builtin')}
                        </span>
                      )}
                      {isSelected && (
                        <Check className="w-4 h-4 text-blue-500 flex-shrink-0" />
                      )}
                    </button>
                  );
                })}

                {/* Manage agents */}
                <div className="my-1 border-t border-black/[0.06] dark:border-white/30" />
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    setShowAgentSubmenu(false);
                    onAgentClick();
                  }}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-slate-700 dark:text-slate-300 glass-menu-item transition-colors"
                >
                  <Settings2 className="w-4 h-4 text-slate-400 dark:text-slate-500" />
                  <span>{t('chat.manageAgents')}</span>
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
