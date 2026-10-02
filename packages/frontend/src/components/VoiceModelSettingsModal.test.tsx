// @vitest-environment node
// (Rendered to static markup: the workspace's jsdom install cannot start.)
import { renderToStaticMarkup } from 'react-dom/server';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../i18n/locales/en.json';
import VoiceModelSettingsModal, {
  getDefaultVoice,
  getStoredVoiceModelConfig,
  saveVoiceModelConfig,
} from './VoiceModelSettingsModal';

const KEY = 'voice_model_config';
const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: 'en',
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
    showSupportNotice: false,
  });
});

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, String(value));
    },
  };
}

beforeEach(() => {
  vi.stubGlobal('localStorage', memoryStorage());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderModal() {
  return renderToStaticMarkup(
    <I18nextProvider i18n={i18n}>
      <VoiceModelSettingsModal
        isOpen
        onClose={() => undefined}
        onSave={() => undefined}
        selectedModel="nova_sonic"
      />
    </I18nextProvider>,
  );
}

describe('VoiceModelSettingsModal', () => {
  it('offers Nova 2 Sonic only, with the Hindi voices and no API key field', () => {
    const html = renderModal();

    expect(html).toContain('Amazon Nova 2 Sonic');
    for (const voice of ['tiffany', 'matthew', 'kiara', 'arjun']) {
      expect(html).toContain(`value="${voice}"`);
    }
    expect(html).toContain('Kiara (Female, Hindi / Indian English)');
    expect(html).toContain('Arjun (Male, Hindi / Indian English)');
    expect(html.match(/<select/g)).toHaveLength(1); // the voice, not the model
    expect(html).not.toContain('<input');
    expect(html).not.toMatch(/gemini|openai|api key/i);
  });

  it('preselects the saved voice', () => {
    localStorage.setItem(
      KEY,
      JSON.stringify({ modelType: 'nova_sonic', voice: 'arjun' }),
    );

    expect(renderModal()).toContain('<option value="arjun" selected="">');
  });
});

describe('voice model config', () => {
  it('defaults to Kiara in India and Tiffany elsewhere', () => {
    expect(getDefaultVoice('Asia/Kolkata')).toBe('kiara');
    expect(getDefaultVoice('Asia/Calcutta')).toBe('kiara');
    expect(getDefaultVoice('Europe/London')).toBe('tiffany');
    expect(getDefaultVoice('America/New_York')).toBe('tiffany');
  });

  it('uses the default voice when nothing is saved', () => {
    expect(getStoredVoiceModelConfig()).toEqual({
      modelType: 'nova_sonic',
      voice: getDefaultVoice(),
    });
  });

  it('keeps a saved Nova Sonic voice', () => {
    localStorage.setItem(
      KEY,
      JSON.stringify({ modelType: 'nova_sonic', voice: 'kiara' }),
    );

    expect(getStoredVoiceModelConfig()).toEqual({
      modelType: 'nova_sonic',
      voice: 'kiara',
    });
  });

  it.each([
    [
      'another model with an API key',
      JSON.stringify({
        modelType: 'gemini',
        voice: 'Kore',
        apiKeys: { gemini: 'QUl6YS10ZXN0' },
      }),
    ],
    ['an unknown voice', JSON.stringify({ voice: 'alloy' })],
    ['broken JSON', '{'],
    ['null', 'null'],
  ])('falls back to Nova Sonic for %s', (_, stored) => {
    localStorage.setItem(KEY, stored);

    expect(getStoredVoiceModelConfig()).toEqual({
      modelType: 'nova_sonic',
      voice: getDefaultVoice(),
    });
  });

  it('saves only the model and voice, dropping old API keys', () => {
    const legacy = {
      modelType: 'nova_sonic' as const,
      voice: 'arjun',
      apiKey: 'sk-test',
      apiKeys: { openai: 'c2stdGVzdA==' },
    };

    saveVoiceModelConfig(legacy);

    expect(JSON.parse(localStorage.getItem(KEY) ?? '')).toEqual({
      modelType: 'nova_sonic',
      voice: 'arjun',
    });
  });
});
