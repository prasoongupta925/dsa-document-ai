import { useTranslation } from 'react-i18next';

export const TRANSCRIBE_LANGUAGES = [
  { code: 'af-ZA', name: 'Afrikaans' },
  { code: 'ar-AE', name: 'Arabic (Gulf)' },
  { code: 'ar-SA', name: 'Arabic (Modern Standard)' },
  { code: 'zh-CN', name: 'Chinese (Simplified)' },
  { code: 'zh-TW', name: 'Chinese (Traditional)' },
  { code: 'da-DK', name: 'Danish' },
  { code: 'nl-NL', name: 'Dutch' },
  { code: 'en-AU', name: 'English (Australian)' },
  { code: 'en-GB', name: 'English (British)' },
  { code: 'en-IN', name: 'English (Indian)' },
  { code: 'en-IE', name: 'English (Irish)' },
  { code: 'en-NZ', name: 'English (New Zealand)' },
  { code: 'en-AB', name: 'English (Scottish)' },
  { code: 'en-ZA', name: 'English (South African)' },
  { code: 'en-US', name: 'English (US)' },
  { code: 'en-WL', name: 'English (Welsh)' },
  { code: 'fi-FI', name: 'Finnish' },
  { code: 'fr-FR', name: 'French' },
  { code: 'fr-CA', name: 'French (Canadian)' },
  { code: 'de-DE', name: 'German' },
  { code: 'de-CH', name: 'German (Swiss)' },
  { code: 'he-IL', name: 'Hebrew' },
  { code: 'hi-IN', name: 'Hindi' },
  { code: 'id-ID', name: 'Indonesian' },
  { code: 'it-IT', name: 'Italian' },
  { code: 'ja-JP', name: 'Japanese' },
  { code: 'ko-KR', name: 'Korean' },
  { code: 'ms-MY', name: 'Malay' },
  { code: 'no-NO', name: 'Norwegian' },
  { code: 'pl-PL', name: 'Polish' },
  { code: 'pt-BR', name: 'Portuguese (Brazilian)' },
  { code: 'pt-PT', name: 'Portuguese (European)' },
  { code: 'ru-RU', name: 'Russian' },
  { code: 'es-ES', name: 'Spanish (European)' },
  { code: 'es-US', name: 'Spanish (US)' },
  { code: 'sv-SE', name: 'Swedish' },
  { code: 'ta-IN', name: 'Tamil' },
  { code: 'te-IN', name: 'Telugu' },
  { code: 'th-TH', name: 'Thai' },
  { code: 'tr-TR', name: 'Turkish' },
  { code: 'uk-UA', name: 'Ukrainian' },
  { code: 'vi-VN', name: 'Vietnamese' },
];

export type TranscribeLanguageMode = 'auto' | 'direct' | 'multi';

export interface TranscribeSettings {
  transcribe_language_mode: TranscribeLanguageMode;
  transcribe_language_code: string;
  transcribe_language_options: string[];
}

interface TranscribeSettingsFormProps {
  settings: TranscribeSettings;
  onChange: (settings: TranscribeSettings) => void;
}

export default function TranscribeSettingsForm({
  settings,
  onChange,
}: TranscribeSettingsFormProps) {
  const { t } = useTranslation();

  const handleLanguageOptionToggle = (code: string) => {
    const current = settings.transcribe_language_options;
    const next = current.includes(code)
      ? current.filter((c) => c !== code)
      : [...current, code];
    onChange({ ...settings, transcribe_language_options: next });
  };

  return (
    <div className="space-y-4">
      {/* Language Mode */}
      <div className="space-y-2">
        <label className="block text-sm font-medium text-[#334155] dark:text-[#cbd5e1]">
          {t('transcribe.languageMode')}
        </label>
        <div className="space-y-1.5">
          {(['auto', 'direct', 'multi'] as const).map((mode) => (
            <label
              key={mode}
              className={`flex items-start gap-2.5 px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
                settings.transcribe_language_mode === mode
                  ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/20'
                  : 'border-black/10 dark:border-[#3b4264] hover:border-black/20 dark:hover:border-[#4f5680]'
              }`}
            >
              <input
                type="radio"
                name="transcribe_language_mode"
                value={mode}
                checked={settings.transcribe_language_mode === mode}
                onChange={() =>
                  onChange({ ...settings, transcribe_language_mode: mode })
                }
                className="mt-0.5 accent-blue-500"
              />
              <div>
                <div
                  className={`text-sm font-medium ${
                    settings.transcribe_language_mode === mode
                      ? 'text-blue-700 dark:text-blue-300'
                      : 'text-[#334155] dark:text-[#cbd5e1]'
                  }`}
                >
                  {t(`transcribe.modes.${mode}.name`)}
                </div>
                <div className="text-xs text-[#64748b] dark:text-[#94a3b8]">
                  {t(`transcribe.modes.${mode}.description`)}
                </div>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* Direct: single language select */}
      {settings.transcribe_language_mode === 'direct' && (
        <div className="space-y-2">
          <label className="block text-sm font-medium text-[#334155] dark:text-[#cbd5e1]">
            {t('transcribe.language')}
          </label>
          <select
            data-modal-input
            value={settings.transcribe_language_code}
            onChange={(e) =>
              onChange({
                ...settings,
                transcribe_language_code: e.target.value,
              })
            }
            className="w-full px-3 py-1.5 text-sm border border-black/10 dark:border-[#3b4264] rounded-lg bg-transparent dark:bg-[#0d1117] text-[#0f172a] dark:text-[#f1f5f9] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          >
            {TRANSCRIBE_LANGUAGES.map((lang) => (
              <option key={lang.code} value={lang.code}>
                {lang.name} ({lang.code})
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Multi: checkbox language selection */}
      {settings.transcribe_language_mode === 'multi' && (
        <div className="space-y-2">
          <label className="block text-sm font-medium text-[#334155] dark:text-[#cbd5e1]">
            {t('transcribe.languages')}
            {settings.transcribe_language_options.length > 0 && (
              <span className="ml-1.5 text-xs font-normal text-[#64748b]">
                ({settings.transcribe_language_options.length})
              </span>
            )}
          </label>
          <div className="max-h-48 overflow-y-auto border border-black/10 dark:border-[#3b4264] rounded-lg p-2 space-y-0.5 bg-transparent dark:bg-[#0d1117]">
            {TRANSCRIBE_LANGUAGES.map((lang) => (
              <label
                key={lang.code}
                className="flex items-center gap-2 px-2 py-1 rounded hover:bg-black/5 dark:hover:bg-white/5 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={settings.transcribe_language_options.includes(
                    lang.code,
                  )}
                  onChange={() => handleLanguageOptionToggle(lang.code)}
                  className="accent-blue-500"
                />
                <span className="text-sm text-[#334155] dark:text-[#cbd5e1]">
                  {lang.name}
                </span>
                <span className="text-xs text-[#94a3b8]">{lang.code}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
