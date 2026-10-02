import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Mic, X } from 'lucide-react';
import type { BidiModelType, VoiceModelConfig } from '../hooks/useVoiceChat';
import { useModal } from '../hooks/useModal';

const VOICE_MODEL_STORAGE_KEY = 'voice_model_config';

export interface VoiceModelSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (config: VoiceModelConfig) => void;
  selectedModel?: BidiModelType; // Unused: Nova Sonic is the only voice model
}

// The only voice model: AWS-sold on Bedrock, signed with IAM, no API key.
const MODEL_LABEL = 'Amazon Nova 2 Sonic';

// Nova 2 Sonic voice ids. Tiffany and Matthew speak every Nova 2 Sonic
// language; Kiara and Arjun are the Indian English and Hindi voices.
const VOICE_OPTIONS: { value: string; label: string }[] = [
  { value: 'tiffany', label: 'Tiffany (Female)' },
  { value: 'matthew', label: 'Matthew (Male)' },
  { value: 'kiara', label: 'Kiara (Female, Hindi / Indian English)' },
  { value: 'arjun', label: 'Arjun (Male, Hindi / Indian English)' },
];

// The voice agent defaults to Hindi in India, where Kiara is the default voice.
const INDIA_TIME_ZONES = ['Asia/Kolkata', 'Asia/Calcutta'];

export function getDefaultVoice(
  timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone,
): string {
  return INDIA_TIME_ZONES.includes(timeZone) ? 'kiara' : 'tiffany';
}

function isVoice(value: unknown): value is string {
  return VOICE_OPTIONS.some((option) => option.value === value);
}

/** The saved Nova Sonic voice, else the default one. */
function getStoredVoice(): string {
  try {
    const stored = localStorage.getItem(VOICE_MODEL_STORAGE_KEY);
    const voice: unknown = stored
      ? (JSON.parse(stored) as { voice?: unknown }).voice
      : undefined;
    if (isVoice(voice)) return voice;
  } catch {
    // ignore
  }
  return getDefaultVoice();
}

export function getStoredVoiceModelConfig(): VoiceModelConfig {
  // Configs saved by older builds may name another model, its voice or an
  // API key: only a Nova Sonic voice is kept.
  return { modelType: 'nova_sonic', voice: getStoredVoice() };
}

export function saveVoiceModelConfig(config: VoiceModelConfig): void {
  // Stored whole, which also drops API keys saved by older builds
  const voice = isVoice(config.voice) ? config.voice : getDefaultVoice();
  localStorage.setItem(
    VOICE_MODEL_STORAGE_KEY,
    JSON.stringify({ modelType: 'nova_sonic', voice }),
  );
}

export default function VoiceModelSettingsModal({
  isOpen,
  onClose,
  onSave,
}: VoiceModelSettingsModalProps) {
  const { t } = useTranslation();
  const [voice, setVoice] = useState(getStoredVoice);

  useEffect(() => {
    if (isOpen) setVoice(getStoredVoice());
  }, [isOpen]);

  useModal({ isOpen, onClose });

  const handleSave = () => {
    const config: VoiceModelConfig = { modelType: 'nova_sonic', voice };
    saveVoiceModelConfig(config);
    onSave(config);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/55 dark:bg-black/65 backdrop-blur-md"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="voice-settings-modal relative rounded-2xl w-full max-w-md mx-4 flex flex-col overflow-hidden border border-white/70 dark:border-indigo-500/20 shadow-[0_25px_60px_-15px_rgba(0,0,0,0.15)] dark:shadow-[0_0_80px_rgba(99,102,241,0.08),0_25px_50px_-12px_rgba(0,0,0,0.5)]">
        {/* Gradient glow */}
        <div
          className="dark:hidden absolute inset-0 pointer-events-none rounded-2xl"
          style={{
            background:
              'radial-gradient(ellipse 60% 50% at 80% 0%, rgba(139, 92, 246, 0.1) 0%, transparent 70%)',
          }}
        />
        <div
          className="hidden dark:block absolute inset-0 pointer-events-none rounded-2xl"
          style={{
            background:
              'radial-gradient(ellipse 60% 50% at 80% 0%, rgba(139, 92, 246, 0.15) 0%, transparent 70%)',
          }}
        />

        {/* Header */}
        <div className="flex items-center gap-3 p-4 border-b border-black/[0.06] dark:border-[#2a2f45] flex-shrink-0">
          <Mic className="h-5 w-5 text-purple-500" />
          <h2 className="text-lg font-semibold text-[#1e293b] dark:text-[#f8fafc]">
            {t('voiceModel.settings', 'Voice Model Settings')}
          </h2>
          <button
            onClick={onClose}
            className="ml-auto p-1.5 hover:bg-white/40 dark:hover:bg-[#1e2235] rounded-lg transition-colors"
          >
            <X className="h-5 w-5 text-[#64748b]" />
          </button>
        </div>

        {/* Content */}
        <div className="relative p-4 space-y-4">
          {/* Model (Nova Sonic only) */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-[#334155] dark:text-[#cbd5e1]">
              {t('voiceModel.model', 'Model')}
            </label>
            <div className="w-full px-3 py-2 text-sm bg-transparent dark:bg-[#0d1117] border border-black/10 dark:border-[#3b4264] rounded-lg text-[#475569] dark:text-[#cbd5e1] select-none cursor-default">
              {MODEL_LABEL}
            </div>
          </div>

          {/* Voice Selection */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-[#334155] dark:text-[#cbd5e1]">
              {t('voiceModel.voice', 'Voice')}
            </label>
            <select
              data-modal-input
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-black/10 dark:border-[#3b4264] rounded-lg bg-transparent dark:bg-[#0d1117] text-[#0f172a] dark:text-[#f1f5f9] focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all"
            >
              {VOICE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-4 border-t border-black/[0.06] dark:border-[#2a2f45] flex-shrink-0">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-[#334155] dark:text-[#cbd5e1] hover:bg-[#f1f5f9] dark:hover:bg-[#0d1117] rounded-lg transition-colors border border-black/10 dark:border-[#3b4264]"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSave}
            className="px-4 py-2 text-sm font-medium text-white bg-purple-600 hover:bg-purple-700 dark:bg-purple-600 dark:hover:bg-purple-500 rounded-lg transition-colors dark:shadow-[0_0_20px_rgba(139,92,246,0.15)]"
          >
            {t('common.save')}
          </button>
        </div>
      </div>
    </div>
  );
}
