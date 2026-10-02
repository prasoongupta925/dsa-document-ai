import type { LlmModel } from './ModelSelectorPrompt';

/**
 * Selectable chat models. This list is the built-in fallback used when the
 * backend catalog (GET /chat/models, backed by SSM) is unavailable. The SSM
 * parameter `/idp-v2/chat/models` is created by CDK (AgentStack) from
 * `packages/infra/src/chat-models.json`; keep this list identical to it.
 *
 * AWS-sold models only. The agent validates `model_id` against the catalog and
 * the runtime IAM role denies non-AWS-sold model providers. The first entry is
 * the default.
 *
 * `metrics` (1-10) drive the preview card's bars; they are illustrative, not
 * measured. `contextWindow`/prices show in the Context/Cost tooltips.
 */
export type ChatModel = LlmModel;

export const CHAT_MODELS: ChatModel[] = [
  {
    value: 'zai.glm-5',
    label: 'GLM-5',
    description: 'Z.ai GLM-5, default chat model (AWS-sold, in-region)',
    contextWindow: '200K tokens',
    inputPrice: '$1.20 / 1M',
    outputPrice: '$3.84 / 1M',
    metrics: { intelligence: 8, speed: 6, context: 7, cost: 6 },
    supportsReasoning: false,
  },
  {
    value: 'global.amazon.nova-2-lite-v1:0',
    label: 'Nova 2 Lite',
    description: 'Amazon first-party model (global inference)',
    contextWindow: '1M tokens',
    inputPrice: '$0.35 / 1M',
    outputPrice: '$2.95 / 1M',
    metrics: { intelligence: 6, speed: 9, context: 10, cost: 9 },
    supportsReasoning: false,
  },
  {
    value: 'deepseek.v3.2',
    label: 'DeepSeek V3.2',
    description: 'DeepSeek V3.2 (AWS-sold, in-region)',
    contextWindow: '128K tokens',
    inputPrice: '$0.74 / 1M',
    outputPrice: '$2.22 / 1M',
    metrics: { intelligence: 7, speed: 7, context: 6, cost: 8 },
    supportsReasoning: false,
  },
  {
    value: 'moonshotai.kimi-k2.5',
    label: 'Kimi K2.5',
    description: 'Moonshot AI Kimi K2.5 (AWS-sold, in-region)',
    contextWindow: '256K tokens',
    inputPrice: '$0.72 / 1M',
    outputPrice: '$3.60 / 1M',
    metrics: { intelligence: 8, speed: 6, context: 8, cost: 7 },
    supportsReasoning: false,
  },
];

export const DEFAULT_MODEL_ID = CHAT_MODELS[0].value;
