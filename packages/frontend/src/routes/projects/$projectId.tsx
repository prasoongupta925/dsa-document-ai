import { createFileRoute, Link } from '@tanstack/react-router';
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { nanoid } from 'nanoid';
import { useAwsClient } from '../../hooks/useAwsClient';
import { useWebSocket } from '../../contexts/WebSocketContext';
import CubeLoader from '../../components/CubeLoader';
import ConfirmModal from '../../components/ConfirmModal';
import ProjectSettingsModal, {
  CARD_COLORS,
} from '../../components/ProjectSettingsModal';
import ProjectNavBar from '../../components/ProjectNavBar';
import ChatPanel, { type AttachedFile } from '../../components/ChatPanel';
import SidePanel from '../../components/SidePanel';
import WorkflowDetailModal from '../../components/WorkflowDetailModal';
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from '../../components/ui/resizable';
import AgentSelectModal from '../../components/AgentSelectModal';
import DocumentUploadModal from '../../components/DocumentUploadModal';
import ArtifactViewer from '../../components/ArtifactViewer';
import FileCheckPanel, {
  type FileCheckFocus,
} from '../../components/FileCheckPanel';
import EligibilityPanel from '../../components/EligibilityPanel';
import type { FileCheckApplicant } from '../../types/fileCheck';
import DsaPainPointsPanel from '../../components/DsaPainPointsPanel';
import type { PainPointId, PainPointTarget } from '../../data/dsaPainPoints';
import SystemPromptModal from '../../components/SystemPromptModal';
import ProjectGraphModal from '../../components/ProjectGraphModal';
import { useSetSidebarSessions } from '../../contexts/SidebarSessionContext';
import { BidiModelType } from '../../hooks/useVoiceChat';
import VoiceModelSettingsModal, {
  getStoredVoiceModelConfig,
} from '../../components/VoiceModelSettingsModal';

// Custom hooks
import { useProjectData } from '../../hooks/useProjectData';
import { usePanelLayout } from '../../hooks/usePanelLayout';
import { useSystemPrompts } from '../../hooks/useSystemPrompts';
import { useChatSession } from '../../hooks/useChatSession';
import { useModelCatalog } from '../../hooks/useModelCatalog';
import { useVoiceChatManager } from '../../hooks/useVoiceChatManager';
import { useAgents } from '../../hooks/useAgents';
import { useArtifacts } from '../../hooks/useArtifacts';
import { useDocuments } from '../../hooks/useDocuments';
import { useFileCheck } from '../../hooks/useFileCheck';
import { useFileCheckAsk } from '../../hooks/useFileCheckAsk';
import { useEligibility } from '../../hooks/useEligibility';
import { eligibilityApplicant } from '../../lib/eligibility';

export const Route = createFileRoute('/projects/$projectId')({
  component: ProjectDetailPage,
});

function ProjectDetailPage() {
  const { t } = useTranslation();
  const { projectId } = Route.useParams();
  const { fetchApi, getArtifactDownloadUrl, bidiAgentRuntimeArn, userId } =
    useAwsClient();
  const { sendMessage, status: wsStatus } = useWebSocket();

  // --- Hook initialization (respecting dependency order) ---

  // 1. Independent hooks
  const projectData = useProjectData({ fetchApi, projectId });
  const panelLayout = usePanelLayout();
  const { systemPromptTabs } = useSystemPrompts({ fetchApi });

  // Chat model catalog (SSM-backed, runtime-loaded so models can be added
  // without redeploying).
  const { models, loaded: modelsLoaded } = useModelCatalog();

  // 2. Chat session (provides handleNewSession, setMessages, setStreamingBlocks)
  const chatSession = useChatSession({ projectId, models, modelsLoaded });

  // 3. Voice chat manager (needs setMessages, setStreamingBlocks)
  const [selectedVoiceModel, setSelectedVoiceModel] = useState<BidiModelType>(
    () => getStoredVoiceModelConfig().modelType,
  );
  const voiceChatManager = useVoiceChatManager({
    currentSessionId: chatSession.currentSessionId,
    projectId,
    userId: userId || '',
    selectedVoiceModel,
    setSelectedVoiceModel,
    setMessages: chatSession.setMessages,
    setStreamingBlocks: chatSession.setStreamingBlocks,
  });

  // 4. Agents (needs handleNewSession)
  const agentsHook = useAgents({
    fetchApi,
    projectId,
    onNewSession: useCallback(() => {
      chatSession.handleNewSession();
      agentsHook_setSelectedAgent(null);
      voiceChatManager.setVoiceChatMode(false);
      voiceChatManager.voiceChatDisconnectRef.current();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [chatSession.handleNewSession]),
  });

  // Workaround: we need a stable reference for the agent setter
  // that is used in the onNewSession callback above.
  // Since useAgents returns setSelectedAgent, we keep a local alias.
  const agentsHook_setSelectedAgent = agentsHook.setSelectedAgent;

  // 5. Artifacts (needs panel layout)
  const artifactsHook = useArtifacts({
    fetchApi,
    getArtifactDownloadUrl,
    projectId,
    sidePanelCollapsed: panelLayout.sidePanelCollapsed,
    setSidePanelCollapsed: panelLayout.setSidePanelCollapsed,
  });

  // 6. Documents (needs wsStatus)
  const documentsHook = useDocuments({
    fetchApi,
    projectId,
    wsStatus,
  });

  // 7. File check (deterministic checklist verdict from the backend)
  const { loadDocuments, loadWorkflows } = documentsHook;
  // Eligibility & lenders (deterministic per-lender eligibility), per applicant.
  const eligibility = useEligibility({ fetchApi, projectId });
  const [eligibilityTarget, setEligibilityTarget] = useState<{
    id: string;
    name: string;
    pan: string | null;
  } | null>(null);
  // Applicant name -> the id their eligibility draft is kept under (PAN or name).
  const eligibilityIds = useRef(new Map<string, string>());
  const { forget: forgetEligibility } = eligibility;
  // An erased applicant's documents are gone: reload the documents list, and
  // drop the applicant's eligibility draft in this tab (every draft when the
  // erase may have run in part without an answer).
  const handleApplicantErased = useCallback(
    (response: { applicant: string } | null) => {
      loadDocuments();
      loadWorkflows();
      setEligibilityTarget(null);
      forgetEligibility(
        response
          ? [response.applicant, eligibilityIds.current.get(response.applicant)]
          : undefined,
      );
    },
    [loadDocuments, loadWorkflows, forgetEligibility],
  );
  const fileCheck = useFileCheck({
    fetchApi,
    projectId,
    onApplicantErased: handleApplicantErased,
  });
  const fileCheckAsk = useFileCheckAsk({ fetchApi, projectId });
  const [showFileCheck, setShowFileCheck] = useState(false);
  const [fileCheckFocus, setFileCheckFocus] = useState<FileCheckFocus | null>(
    null,
  );
  // "Why DSAs need this" overlays the same place; one of the two is open.
  const [showPainPoints, setShowPainPoints] = useState(false);
  const [painPointFocus, setPainPointFocus] = useState<PainPointId | null>(
    null,
  );
  const openFileCheck = useCallback(() => {
    setShowPainPoints(false);
    setEligibilityTarget(null);
    setFileCheckFocus(null);
    setShowFileCheck(true);
  }, []);
  const closeFileCheck = useCallback(() => setShowFileCheck(false), []);
  // Eligibility & lenders replaces the File Check panel; Back returns to it.
  const openEligibility = useCallback((applicant: FileCheckApplicant) => {
    const target = eligibilityApplicant(applicant);
    eligibilityIds.current.set(target.name, target.id);
    setEligibilityTarget(target);
    setShowFileCheck(false);
  }, []);
  const backToFileCheck = useCallback(() => {
    setEligibilityTarget(null);
    setShowFileCheck(true);
  }, []);
  const closeEligibility = useCallback(() => setEligibilityTarget(null), []);
  const openPainPoints = useCallback((id?: PainPointId) => {
    setShowFileCheck(false);
    setEligibilityTarget(null);
    setPainPointFocus(id ?? null);
    setShowPainPoints(true);
  }, []);
  const openPainPointsFromNav = useCallback(
    () => openPainPoints(),
    [openPainPoints],
  );
  const closePainPoints = useCallback(() => setShowPainPoints(false), []);
  const showMeInFileCheck = useCallback((target: PainPointTarget) => {
    setShowPainPoints(false);
    setEligibilityTarget(null);
    setFileCheckFocus((prev) => ({ target, key: (prev?.key ?? 0) + 1 }));
    setShowFileCheck(true);
  }, []);

  // --- System prompt modal state ---
  const [showSystemPrompt, setShowSystemPrompt] = useState(false);
  const [showProjectGraph, setShowProjectGraph] = useState(false);

  // --- Subscribe to project WebSocket notifications ---
  useEffect(() => {
    if (wsStatus === 'connected') {
      sendMessage({ action: 'subscribe', projectId });
    }
  }, [projectId, sendMessage, wsStatus]);

  // --- Reset chat state when project changes ---
  useEffect(() => {
    chatSession.setCurrentSessionId(nanoid(33));
    chatSession.setMessages([]);
    chatSession.setInputMessage('');
    chatSession.setSending(false);
    chatSession.setStreamingBlocks([]);
    agentsHook.setSelectedAgent(null);
    artifactsHook.setSelectedArtifact(null);
    setShowFileCheck(false);
    setFileCheckFocus(null);
    setEligibilityTarget(null);
    eligibilityIds.current.clear();
    setShowPainPoints(false);
    setPainPointFocus(null);
    voiceChatManager.setVoiceChatMode(false);
    chatSession.pendingMessagesRef.current = [];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // --- Initial data load ---
  useEffect(() => {
    const load = async () => {
      projectData.setLoading(true);
      await Promise.all([
        projectData.loadProject(),
        documentsHook.loadDocuments(),
        documentsHook.loadWorkflows(),
        chatSession.loadSessions(),
        agentsHook.loadAgents(),
        artifactsHook.loadArtifacts(),
      ]);
      projectData.setLoading(false);
    };
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    projectData.loadProject,
    documentsHook.loadDocuments,
    documentsHook.loadWorkflows,
    chatSession.loadSessions,
    agentsHook.loadAgents,
    artifactsHook.loadArtifacts,
  ]);

  // Fetch real step progress for in-progress workflows on page load (once)
  useEffect(() => {
    documentsHook.fetchProgressOnLoad(projectData.loading);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectData.loading, documentsHook.fetchProgressOnLoad]);

  // --- Ctrl+Shift+S keyboard shortcut for system prompt modal ---
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.code === 'KeyS') {
        e.preventDefault();
        setShowSystemPrompt(true);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  // --- Wrapped callbacks for ChatPanel/Sidebar compatibility ---

  // handleNewSession that also resets voice/agent state. `persistModelId`
  // remembers the chosen model against the new session id immediately (used
  // when a model change starts a fresh chat).
  const handleNewSession = useCallback(
    (persistModelId?: string) => {
      chatSession.handleNewSession(persistModelId);
      agentsHook.setSelectedAgent(null);
      voiceChatManager.setVoiceChatMode(false);
      voiceChatManager.voiceChatDisconnectRef.current();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chatSession.handleNewSession],
  );

  // --- Model selection ---
  // Changing the model mid-conversation would mix responses from different
  // models in one session, so we confirm and start a fresh chat.
  const [pendingModelChange, setPendingModelChange] = useState<string | null>(
    null,
  );
  const handleModelChange = useCallback(
    (modelValue: string) => {
      if (modelValue === chatSession.modelId) return;
      if (chatSession.messages.length > 0 || chatSession.sending) {
        setPendingModelChange(modelValue);
      } else {
        chatSession.setModelId(modelValue);
      }
    },
    [chatSession],
  );
  const confirmModelChange = useCallback(() => {
    if (pendingModelChange) {
      chatSession.setModelId(pendingModelChange);
      // Start a fresh chat and remember the model for the new session id.
      handleNewSession(pendingModelChange);
    }
    setPendingModelChange(null);
  }, [pendingModelChange, chatSession, handleNewSession]);

  // handleSessionSelect with agent/voice context
  const handleSessionSelect = useCallback(
    (sessionId: string) => {
      chatSession.handleSessionSelect(sessionId, {
        agents: agentsHook.agents,
        setSelectedAgent: agentsHook.setSelectedAgent,
        setVoiceChatMode: voiceChatManager.setVoiceChatMode,
        setSelectedVoiceModel,
        voiceChatDisconnect: voiceChatManager.voiceChat.disconnect,
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chatSession.handleSessionSelect, agentsHook.agents],
  );

  // handleSessionDelete with voice/agent context
  const handleSessionDelete = useCallback(
    async (sessionId: string) => {
      await chatSession.handleSessionDelete(sessionId, {
        voiceChatDisconnect: voiceChatManager.voiceChat.disconnect,
        setVoiceChatMode: voiceChatManager.setVoiceChatMode,
        setSelectedAgent: agentsHook.setSelectedAgent,
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chatSession.handleSessionDelete],
  );

  // handleSendMessage wrapper passing selectedAgent
  const handleSendMessage = useCallback(
    (files: AttachedFile[], message?: string) => {
      chatSession.handleSendMessage(files, message, agentsHook.selectedAgent);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chatSession.handleSendMessage, agentsHook.selectedAgent],
  );

  // ask_user answer: post the user's selection back as their next message so
  // the agent reads it on the following turn.
  const handleAnswer = useCallback(
    (content: string) => {
      if (!content.trim()) return;
      chatSession.handleSendMessage([], content, agentsHook.selectedAgent);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chatSession.handleSendMessage, agentsHook.selectedAgent],
  );

  // --- Sidebar sessions sync ---
  useSetSidebarSessions(
    useMemo(
      () => ({
        sessions: chatSession.sessions,
        currentSessionId: chatSession.currentSessionId,
        onSessionSelect: handleSessionSelect,
        onSessionRename: chatSession.handleSessionRename,
        onSessionDelete: handleSessionDelete,
        onNewSession: handleNewSession,
        hasMoreSessions: !!chatSession.sessionsNextCursor,
        loadingMoreSessions: chatSession.loadingMoreSessions,
        onLoadMoreSessions: chatSession.loadMoreSessions,
      }),
      [
        chatSession.sessions,
        chatSession.currentSessionId,
        handleSessionSelect,
        chatSession.handleSessionRename,
        handleSessionDelete,
        handleNewSession,
        chatSession.sessionsNextCursor,
        chatSession.loadingMoreSessions,
        chatSession.loadMoreSessions,
      ],
    ),
  );

  // --- Render ---

  const projectColorObj =
    CARD_COLORS[(projectData.project?.color ?? 0) % CARD_COLORS.length] ||
    CARD_COLORS[0];

  if (projectData.loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <CubeLoader />
      </div>
    );
  }

  if (!projectData.project) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4">
        <div className="text-slate-500">{t('projects.notFound')}</div>
        <Link
          to="/"
          className="text-blue-600 hover:text-blue-700 hover:underline"
        >
          {t('projects.backToProjects')}
        </Link>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 relative">
      {/* Ambient background glow (dark mode) */}
      <div
        className="absolute inset-0 hidden dark:block pointer-events-none"
        style={{
          background: `radial-gradient(ellipse 800px 400px at 50% 0%, ${projectColorObj.glow}, transparent)`,
        }}
      />

      {/* Navigation Bar */}
      <ProjectNavBar
        project={projectData.project}
        onSettingsClick={() => projectData.setShowProjectSettings(true)}
      />

      {/* Main Content - 2 Column Resizable Layout */}
      <div className="flex-1 min-h-0 flex">
        <ResizablePanelGroup
          key={panelLayout.sidePanelCollapsed ? 'sl' : 'se'}
          orientation="horizontal"
          defaultSize={(() => {
            const sizes = panelLayout.sidePanelSizeBeforeCollapse.current;
            if (panelLayout.sidePanelCollapsed) {
              return [sizes[0] + sizes[1]];
            }
            return sizes;
          })()}
          onResizeEnd={panelLayout.handlePanelResizeEnd}
          onCollapse={(details: { panelId: string }) => {
            if (details.panelId === 'side') {
              panelLayout.setSidePanelCollapsed(true);
            }
          }}
          panels={(() => {
            const panels: {
              id: string;
              minSize: number;
              maxSize: number;
              collapsible?: boolean;
            }[] = [];
            panels.push({ id: 'chat', minSize: 40, maxSize: 100 });
            if (!panelLayout.sidePanelCollapsed) {
              panels.push({
                id: 'side',
                minSize: 15,
                maxSize: 45,
                collapsible: true,
              });
            }
            return panels;
          })()}
          className="h-full flex-1 min-w-0"
        >
          {/* Left - Chat Panel */}
          <ResizablePanel id="chat">
            <div className="h-full">
              <ChatPanel
                projectName={projectData.project?.name}
                projectDescription={projectData.project?.description}
                projectColor={projectData.project?.color ?? 0}
                messages={chatSession.messages}
                inputMessage={chatSession.inputMessage}
                sending={chatSession.sending}
                streamingBlocks={chatSession.streamingBlocks}
                loadingHistory={chatSession.loadingHistory}
                agents={agentsHook.agents}
                selectedAgent={agentsHook.selectedAgent}
                artifacts={artifactsHook.artifacts}
                documents={documentsHook.documents}
                onInputChange={chatSession.setInputMessage}
                onSendMessage={handleSendMessage}
                onStop={chatSession.stopStreaming}
                models={models}
                modelId={chatSession.modelId}
                reasonings={chatSession.reasonings}
                onModelChange={handleModelChange}
                onReasoningChange={chatSession.setReasonings}
                onAgentSelect={agentsHook.handleAgentSelect}
                onAgentClick={() => agentsHook.setShowAgentModal(true)}
                onNewChat={handleNewSession}
                onArtifactView={artifactsHook.handleArtifactSelect}
                onSourceClick={documentsHook.handleSourceClick}
                loadingSourceKey={documentsHook.loadingSourceKey}
                onAnswer={handleAnswer}
                scrollPositionRef={chatSession.chatScrollPositionRef}
                voiceChat={{
                  available: !!bidiAgentRuntimeArn,
                  state: voiceChatManager.voiceChat.state,
                  audioLevel: {
                    input: voiceChatManager.voiceChat.inputAudioLevel,
                    output: voiceChatManager.voiceChat.outputAudioLevel,
                  },
                  mode: voiceChatManager.voiceChatMode,
                  selectedModel: selectedVoiceModel,
                  onModeChange: voiceChatManager.setVoiceChatMode,
                  onConnect: voiceChatManager.handleVoiceChatConnect,
                  onDisconnect: voiceChatManager.voiceChat.disconnect,
                  onText: voiceChatManager.handleVoiceChatText,
                  onToggleMic: voiceChatManager.voiceChat.toggleMic,
                  onSettings: () =>
                    voiceChatManager.setShowVoiceModelSettings(true),
                  onModelSelect: voiceChatManager.handleVoiceModelSelect,
                }}
              />
            </div>
          </ResizablePanel>

          {!panelLayout.sidePanelCollapsed && (
            <>
              <ResizableHandle id="chat:side" />

              {/* Right - Documents & Artifacts */}
              <ResizablePanel id="side">
                <div className="h-full relative">
                  <SidePanel
                    artifacts={artifactsHook.artifacts}
                    currentArtifactId={
                      artifactsHook.selectedArtifact?.artifact_id
                    }
                    onArtifactSelect={artifactsHook.handleArtifactSelect}
                    onArtifactDownload={artifactsHook.handleArtifactDownload}
                    onArtifactDelete={artifactsHook.handleArtifactDelete}
                    onRefreshArtifacts={artifactsHook.loadArtifacts}
                    onCollapse={() => panelLayout.setSidePanelCollapsed(true)}
                    documents={documentsHook.documents}
                    workflows={documentsHook.workflows}
                    workflowProgressMap={documentsHook.workflowProgressMap}
                    uploading={documentsHook.uploading}
                    onAddDocument={() => documentsHook.setShowUploadModal(true)}
                    onRefreshDocuments={documentsHook.loadDocuments}
                    onViewWorkflow={documentsHook.loadWorkflowDetail}
                    onDeleteDocument={documentsHook.handleDeleteDocument}
                    onOpenFileCheck={openFileCheck}
                    onOpenPainPoints={openPainPointsFromNav}
                    // onViewProjectGraph={() => setShowProjectGraph(true)}
                  />
                  {/* File Check - overlays SidePanel (an open artifact stays on top) */}
                  {showFileCheck && !artifactsHook.selectedArtifact && (
                    <FileCheckPanel
                      state={fileCheck}
                      askState={fileCheckAsk}
                      onClose={closeFileCheck}
                      focus={fileCheckFocus}
                      onPainPoint={openPainPoints}
                      onOpenEligibility={openEligibility}
                    />
                  )}
                  {/* Eligibility & lenders - same overlay, opened from an applicant of the verdict */}
                  {eligibilityTarget && !artifactsHook.selectedArtifact && (
                    <EligibilityPanel
                      state={eligibility}
                      projectId={projectId}
                      applicant={eligibilityTarget.id}
                      name={eligibilityTarget.name}
                      pan={eligibilityTarget.pan}
                      onBack={backToFileCheck}
                      onClose={closeEligibility}
                    />
                  )}
                  {/* Why DSAs need this - same overlay; "Show me" opens File Check */}
                  {showPainPoints && !artifactsHook.selectedArtifact && (
                    <DsaPainPointsPanel
                      onClose={closePainPoints}
                      onShowMe={showMeInFileCheck}
                      focusId={painPointFocus}
                    />
                  )}
                  {/* Artifact Viewer - overlays SidePanel */}
                  {artifactsHook.selectedArtifact && (
                    <ArtifactViewer
                      artifact={artifactsHook.selectedArtifact}
                      onClose={artifactsHook.handleArtifactViewerClose}
                      onDownload={artifactsHook.handleArtifactDownload}
                      getDownloadUrl={getArtifactDownloadUrl}
                    />
                  )}
                </div>
              </ResizablePanel>
            </>
          )}
        </ResizablePanelGroup>

        {/* Collapsed Side Bar */}
        {panelLayout.sidePanelCollapsed && (
          <div
            className="side-collapsed-bar"
            onClick={panelLayout.expandSidePanel}
            title={t('nav.expand')}
          >
            <div className="docs-collapsed-badge">
              <svg
                className="w-4 h-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              <span>{documentsHook.documents.length}</span>
            </div>
            <span className="docs-collapsed-label">
              {t('documents.title', 'Documents')}
            </span>
            <div className="docs-collapsed-badge mt-2">
              <svg
                className="w-4 h-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" />
                <path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65" />
                <path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65" />
              </svg>
              <span>{artifactsHook.artifacts.length}</span>
            </div>
            <span className="docs-collapsed-label">
              {t('chat.artifacts', 'Artifacts')}
            </span>
            <div className="docs-collapsed-expand">
              <svg
                className="w-3.5 h-3.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M11 17l-5-5 5-5" />
                <path d="M18 17l-5-5 5-5" />
              </svg>
            </div>
          </div>
        )}
      </div>

      {/* Workflow Detail Modal */}
      {documentsHook.selectedWorkflow && (
        <WorkflowDetailModal
          workflow={documentsHook.selectedWorkflow}
          projectId={projectId}
          projectColor={projectData.project?.color ?? 0}
          loadingWorkflow={documentsHook.loadingWorkflow}
          onClose={() => {
            documentsHook.setSelectedWorkflow(null);
            documentsHook.setInitialSegmentIndex(0);
          }}
          onReanalyze={documentsHook.handleReanalyze}
          reanalyzing={documentsHook.reanalyzing}
          onRegenerateQa={documentsHook.handleRegenerateQa}
          onAddQa={documentsHook.handleAddQa}
          onDeleteQa={documentsHook.handleDeleteQa}
          initialSegmentIndex={documentsHook.initialSegmentIndex}
          onLoadSegment={documentsHook.handleLoadSegment}
        />
      )}

      {/* Project Settings Modal */}
      <ProjectSettingsModal
        project={projectData.project}
        isOpen={projectData.showProjectSettings}
        onClose={() => projectData.setShowProjectSettings(false)}
        onSave={projectData.handleProjectSave}
        fetchApi={fetchApi}
      />

      {/* Delete Document Confirmation Modal */}
      <ConfirmModal
        isOpen={!!documentsHook.deleteTarget}
        onClose={() => documentsHook.setDeleteTarget(null)}
        onConfirm={documentsHook.confirmDeleteDocument}
        title={t('documents.deleteConfirm')}
        message={documentsHook.deleteTarget?.name || ''}
        confirmText={t('common.delete')}
        variant="danger"
        loading={documentsHook.deleting}
      />

      {/* Model Change Confirmation Modal (starts a new chat) */}
      <ConfirmModal
        isOpen={!!pendingModelChange}
        onClose={() => setPendingModelChange(null)}
        onConfirm={confirmModelChange}
        title={t('chat.model.changeTitle', 'Change model')}
        message={t(
          'chat.model.changeConfirm',
          'Changing the model starts a new conversation. Continue?',
        )}
        confirmText={t('agent.startNewChat', 'Start new chat')}
        variant="warning"
      />

      {/* Agent Select Modal */}
      <AgentSelectModal
        isOpen={agentsHook.showAgentModal}
        agents={agentsHook.agents}
        selectedAgentId={agentsHook.selectedAgent?.agent_id ?? null}
        loading={agentsHook.loadingAgents}
        onClose={() => agentsHook.setShowAgentModal(false)}
        onSelect={agentsHook.handleAgentSelect}
        onCreate={agentsHook.handleAgentCreate}
        onUpdate={agentsHook.handleAgentUpdate}
        onDelete={agentsHook.handleAgentDelete}
        onLoadDetail={agentsHook.loadAgentDetail}
      />

      {/* Document Upload Modal */}
      <DocumentUploadModal
        isOpen={documentsHook.showUploadModal}
        uploading={documentsHook.uploading}
        projectLanguage={projectData.project?.language || undefined}
        projectDocumentPrompt={
          projectData.project?.document_prompt || undefined
        }
        onClose={() => documentsHook.setShowUploadModal(false)}
        onUpload={documentsHook.processFiles}
      />

      {/* System Prompt Modal (Ctrl+Shift+S) */}
      <SystemPromptModal
        isOpen={showSystemPrompt}
        onClose={() => setShowSystemPrompt(false)}
        tabs={systemPromptTabs}
      />

      {/* Voice Model Settings Modal */}
      <VoiceModelSettingsModal
        isOpen={voiceChatManager.showVoiceModelSettings}
        onClose={() => voiceChatManager.setShowVoiceModelSettings(false)}
        selectedModel={selectedVoiceModel}
        onSave={(config) => {
          setSelectedVoiceModel(config.modelType);
          if (voiceChatManager.voiceChat.state.status === 'connected') {
            voiceChatManager.voiceChat.disconnect();
            setTimeout(() => {
              voiceChatManager.handleVoiceChatConnect();
            }, 500);
          }
        }}
      />

      {/* Project Graph Modal */}
      {showProjectGraph && (
        <ProjectGraphModal
          projectId={projectId}
          projectName={projectData.project?.name || projectId}
          fetchApi={fetchApi}
          onClose={() => setShowProjectGraph(false)}
        />
      )}
    </div>
  );
}
