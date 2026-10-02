import { useTranslation } from 'react-i18next';
import { Info, AlertTriangle } from 'lucide-react';

export const OCR_MODELS = [
  {
    value: 'pp-ocrv5',
    hasLangOption: true,
    hasOptions: true,
  },
  {
    value: 'paddleocr-vl',
    hasLangOption: false,
    hasOptions: false,
  },
];

export const OCR_LANGUAGES = [
  { code: '', name: 'Default (Not specified)' },
  { code: 'ch', name: 'Chinese & English' },
  { code: 'en', name: 'English' },
  { code: 'korean', name: 'Korean' },
  { code: 'japan', name: 'Japanese' },
  { code: 'chinese_cht', name: 'Chinese Traditional' },
  { code: 'french', name: 'French' },
  { code: 'german', name: 'German' },
  { code: 'it', name: 'Italian' },
  { code: 'es', name: 'Spanish' },
  { code: 'pt', name: 'Portuguese' },
  { code: 'ru', name: 'Russian' },
  { code: 'ar', name: 'Arabic' },
  { code: 'hi', name: 'Hindi' },
  { code: 'vi', name: 'Vietnamese' },
  { code: 'th', name: 'Thai' },
  { code: 'ms', name: 'Malay' },
  { code: 'id', name: 'Indonesian' },
  { code: 'tr', name: 'Turkish' },
  { code: 'pl', name: 'Polish' },
  { code: 'nl', name: 'Dutch' },
  { code: 'latin', name: 'Latin (Multi-language)' },
  { code: 'arabic', name: 'Arabic Script (Multi-language)' },
  { code: 'cyrillic', name: 'Cyrillic Script (Multi-language)' },
  { code: 'devanagari', name: 'Devanagari Script (Multi-language)' },
  { code: 'ta', name: 'Tamil' },
  { code: 'te', name: 'Telugu' },
  { code: 'ml', name: 'Malayalam' },
  { code: 'mr', name: 'Marathi' },
  { code: 'ne', name: 'Nepali' },
  { code: 'bn', name: 'Bengali' },
  { code: 'gu', name: 'Gujarati' },
  { code: 'pa', name: 'Punjabi' },
  { code: 'fa', name: 'Persian' },
  { code: 'ur', name: 'Urdu' },
  { code: 'he', name: 'Hebrew' },
  { code: 'az', name: 'Azerbaijani' },
  { code: 'uz', name: 'Uzbek' },
  { code: 'uk', name: 'Ukrainian' },
  { code: 'bg', name: 'Bulgarian' },
  { code: 'sr', name: 'Serbian' },
  { code: 'hr', name: 'Croatian' },
  { code: 'cs', name: 'Czech' },
  { code: 'hu', name: 'Hungarian' },
  { code: 'ro', name: 'Romanian' },
  { code: 'fi', name: 'Finnish' },
  { code: 'sv', name: 'Swedish' },
  { code: 'no', name: 'Norwegian' },
  { code: 'da', name: 'Danish' },
  { code: 'tl', name: 'Tagalog' },
  { code: 'mn', name: 'Mongolian' },
  { code: 'sw', name: 'Swahili' },
];

export interface OcrSettings {
  ocr_model: string;
  ocr_lang: string;
  use_doc_orientation_classify: boolean;
  use_doc_unwarping: boolean;
  use_textline_orientation: boolean;
}

interface OcrSettingsFormProps {
  settings: OcrSettings;
  onChange: (settings: OcrSettings) => void;
  variant?: 'bento' | 'compact';
}

export default function OcrSettingsForm({
  settings,
  onChange,
  variant = 'bento',
}: OcrSettingsFormProps) {
  const { t } = useTranslation();

  const isBento = variant === 'bento';

  const selectedModel = OCR_MODELS.find((m) => m.value === settings.ocr_model);

  return (
    <div className={isBento ? undefined : 'space-y-4'}>
      {/* OCR Model */}
      <div className={isBento ? 'bento-form-group' : 'space-y-2'}>
        <div
          className={
            isBento
              ? 'bento-form-label'
              : 'flex items-center gap-1.5 text-sm font-medium text-[#334155] dark:text-[#cbd5e1]'
          }
        >
          {t('ocr.model')}
          {!isBento && (
            <span className="relative group/model">
              <Info className="h-3.5 w-3.5 text-[#94a3b8] cursor-help" />
              <span className="absolute top-full left-0 mt-1 hidden group-hover/model:block w-52 p-2 text-xs font-normal text-[#475569] dark:text-[#cbd5e1] bg-white dark:bg-[#1e2235] border border-black/10 dark:border-[#3b4264] rounded-lg shadow-lg z-10">
                {t('ocr.settingsTooltip')}
              </span>
            </span>
          )}
        </div>
        {isBento ? (
          <div className="bento-radio-group">
            {OCR_MODELS.map((model) => (
              <label
                key={model.value}
                className={`bento-radio-option ${settings.ocr_model === model.value ? 'active' : ''}`}
              >
                <input
                  type="radio"
                  name="ocr_model"
                  value={model.value}
                  checked={settings.ocr_model === model.value}
                  onChange={(e) =>
                    onChange({
                      ...settings,
                      ocr_model: e.target.value,
                      ...(e.target.value === 'paddleocr-vl'
                        ? {
                            use_doc_orientation_classify: false,
                            use_doc_unwarping: false,
                            use_textline_orientation: false,
                          }
                        : {}),
                    })
                  }
                />
                <div>
                  <div className="bento-radio-label">
                    {t(`ocr.models.${model.value}.name`)}
                  </div>
                  <div className="bento-radio-desc">
                    {t(`ocr.models.${model.value}.description`)}
                  </div>
                </div>
              </label>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-1.5">
            {OCR_MODELS.map((model) => (
              <label
                key={model.value}
                title={t(`ocr.models.${model.value}.description`)}
                className={`relative flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg border cursor-pointer transition-colors text-center ${
                  settings.ocr_model === model.value
                    ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/20'
                    : 'border-black/10 dark:border-[#3b4264] hover:border-black/20 dark:hover:border-[#4f5680]'
                }`}
              >
                <input
                  type="radio"
                  name="ocr_model"
                  value={model.value}
                  checked={settings.ocr_model === model.value}
                  onChange={(e) =>
                    onChange({
                      ...settings,
                      ocr_model: e.target.value,
                      ...(e.target.value === 'paddleocr-vl'
                        ? {
                            use_doc_orientation_classify: false,
                            use_doc_unwarping: false,
                            use_textline_orientation: false,
                          }
                        : {}),
                    })
                  }
                  className="sr-only"
                />
                <span
                  className={`text-xs font-medium ${
                    settings.ocr_model === model.value
                      ? 'text-blue-700 dark:text-blue-300'
                      : 'text-[#475569] dark:text-[#94a3b8]'
                  }`}
                >
                  {t(`ocr.models.${model.value}.name`)}
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      {/* PaddleOCR-VL cold start warning */}
      {settings.ocr_model === 'paddleocr-vl' && (
        <div
          className={`flex items-start gap-2 ${isBento ? 'mt-2 p-3' : 'p-2.5'} bg-amber-50 dark:bg-amber-500/[0.07] border border-amber-200 dark:border-amber-400/20 rounded-lg`}
        >
          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
          <p
            className={`${isBento ? 'text-xs' : 'text-[11px]'} text-amber-700 dark:text-amber-300`}
          >
            {t('ocr.vlColdStartWarning')}
          </p>
        </div>
      )}

      {/* Language */}
      {selectedModel?.hasLangOption && (
        <div className={isBento ? 'bento-form-group' : 'space-y-2'}>
          <div
            className={
              isBento
                ? 'bento-form-label'
                : 'flex items-center gap-1.5 text-sm font-medium text-[#334155] dark:text-[#cbd5e1]'
            }
          >
            {t('ocr.language')}
            {!isBento && (
              <span className="relative group/lang">
                <Info className="h-3.5 w-3.5 text-[#94a3b8] cursor-help" />
                <span className="absolute top-full left-0 mt-1 hidden group-hover/lang:block w-48 p-2 text-xs font-normal text-[#475569] dark:text-[#cbd5e1] bg-white dark:bg-[#1e2235] border border-black/10 dark:border-[#3b4264] rounded-lg shadow-lg z-10">
                  {t('ocr.languageSettingsTooltip')}
                </span>
              </span>
            )}
          </div>
          <select
            data-modal-input={!isBento || undefined}
            value={settings.ocr_lang}
            onChange={(e) =>
              onChange({ ...settings, ocr_lang: e.target.value })
            }
            className={
              isBento
                ? 'bento-form-select'
                : 'w-full px-3 py-1.5 text-sm border border-black/10 dark:border-[#3b4264] rounded-lg bg-transparent dark:bg-[#0d1117] text-[#0f172a] dark:text-[#f1f5f9] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }
          >
            {OCR_LANGUAGES.map((lang) => (
              <option key={lang.code} value={lang.code}>
                {t(`ocr.languages.${lang.code || 'default'}`)}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Processing Options */}
      {selectedModel?.hasOptions && (
        <div className={isBento ? 'bento-form-group' : 'space-y-2'}>
          <label
            className={
              isBento
                ? 'bento-form-label'
                : 'block text-sm font-medium text-[#334155] dark:text-[#cbd5e1]'
            }
          >
            {t('ocr.processingOptions')}
          </label>
          <div className={isBento ? 'bento-checkbox-group' : 'space-y-1.5'}>
            <label
              className={
                isBento
                  ? 'bento-checkbox-option'
                  : 'flex items-start gap-2 cursor-pointer'
              }
            >
              <input
                type="checkbox"
                checked={settings.use_doc_orientation_classify}
                onChange={(e) =>
                  onChange({
                    ...settings,
                    use_doc_orientation_classify: e.target.checked,
                  })
                }
                className={
                  isBento
                    ? undefined
                    : 'mt-0.5 accent-blue-400 appearance-auto bg-transparent'
                }
              />
              <div>
                <div
                  className={
                    isBento
                      ? 'bento-checkbox-label'
                      : 'text-sm text-[#334155] dark:text-[#cbd5e1]'
                  }
                >
                  {t('ocr.documentOrientation')}
                </div>
                <div
                  className={
                    isBento
                      ? 'bento-checkbox-desc'
                      : 'text-xs text-[#64748b] dark:text-[#94a3b8]'
                  }
                >
                  {t('ocr.documentOrientationDesc')}
                </div>
              </div>
            </label>
          </div>
        </div>
      )}
    </div>
  );
}
