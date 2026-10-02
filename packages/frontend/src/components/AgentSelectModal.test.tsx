// @vitest-environment node
// (Rendered to static markup: the workspace's jsdom install cannot start.)
import { renderToStaticMarkup } from 'react-dom/server';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../i18n/locales/en.json';
import AgentSelectModal from './AgentSelectModal';
import type { Agent } from '../types/project';
import { isBuiltinAgent, sortAgentsBuiltinFirst } from '../lib/agents';

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: 'en',
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
    showSupportNotice: false,
  });
});

// The API lists custom agents first, then the built-in ones.
const AGENTS: Agent[] = [
  {
    agent_id: '0b6c0e8e-1c1d-4a57-9d8e-3f1f5f2d9a10',
    name: 'my-agent',
    created_at: '2026-09-20T10:00:00+00:00',
  },
  {
    agent_id: 'builtin-file-checker',
    name: 'File Checker',
    created_at: '2026-09-29T10:00:00+00:00',
    builtin: true,
    description: 'Runs the deterministic file check and explains the verdict',
  },
  {
    // Recognised by its reserved id even without the flag.
    agent_id: 'builtin-document-reminder',
    name: 'Document Reminder',
    created_at: '',
  },
];

const noop = () => undefined;
const asyncNoop = async () => undefined;

function renderModal(agents: Agent[], selectedAgentId: string | null = null) {
  return renderToStaticMarkup(
    <I18nextProvider i18n={i18n}>
      <AgentSelectModal
        isOpen
        agents={agents}
        selectedAgentId={selectedAgentId}
        onClose={noop}
        onSelect={noop}
        onCreate={asyncNoop}
        onUpdate={asyncNoop}
        onDelete={asyncNoop}
        onLoadDetail={async () => null}
      />
    </I18nextProvider>,
  );
}

/** Opening tag of the list row of agent `id` (its highlight classes). */
function rowTag(html: string, id: string): string {
  const start = html.indexOf(`data-agent-id="${id}"`);
  if (start < 0) throw new Error(`row ${id} not rendered`);
  return html.slice(start, html.indexOf('>', start));
}

const ACTIVE = 'border-blue-500 bg-blue-50';

/** Markup of the list row of agent `id`. */
function rowOf(html: string, id: string): string {
  const start = html.indexOf(`data-agent-id="${id}"`);
  if (start < 0) throw new Error(`row ${id} not rendered`);
  const next = html.indexOf('data-agent-id=', start + 1);
  return html.slice(start, next < 0 ? undefined : next);
}

describe('built-in agents', () => {
  it('are recognised by flag or reserved id and listed first', () => {
    expect(AGENTS.map(isBuiltinAgent)).toEqual([false, true, true]);
    expect(sortAgentsBuiltinFirst(AGENTS).map((a) => a.agent_id)).toEqual([
      'builtin-file-checker',
      'builtin-document-reminder',
      '0b6c0e8e-1c1d-4a57-9d8e-3f1f5f2d9a10',
    ]);
  });

  it('show a Built-in badge and description, without edit or delete', () => {
    const html = renderModal(AGENTS);
    const order = [
      'builtin-file-checker',
      'builtin-document-reminder',
      '0b6c0e8e-1c1d-4a57-9d8e-3f1f5f2d9a10',
    ].map((id) => html.indexOf(`data-agent-id="${id}"`));
    expect(order).toEqual([...order].sort((a, b) => a - b));

    const builtin = rowOf(html, 'builtin-file-checker');
    expect(builtin).toContain('data-builtin="true"');
    expect(builtin).toContain('data-testid="builtin-badge"');
    expect(builtin).toContain('>Built-in</span>');
    expect(builtin).toContain(
      'Runs the deterministic file check and explains the verdict',
    );
    expect(builtin).not.toContain('aria-label="Edit');
    expect(builtin).not.toContain('aria-label="Delete');
    // No description from the API: a generic line instead of a date.
    expect(rowOf(html, 'builtin-document-reminder')).toContain(
      'Ready-made agent that ships with the platform',
    );
    expect(rowOf(html, 'builtin-document-reminder')).not.toContain(
      'aria-label="Delete',
    );

    // A custom agent keeps its edit and delete buttons (real buttons now).
    const custom = rowOf(html, '0b6c0e8e-1c1d-4a57-9d8e-3f1f5f2d9a10');
    expect(custom).not.toContain('data-testid="builtin-badge"');
    expect(custom).toContain('<button type="button" class="p-1.5');
    expect(custom).toContain('aria-label="Edit my-agent"');
    expect(custom).toContain('aria-label="Delete my-agent"');
  });

  it('are selected by id, not by name', () => {
    // A custom agent with the same name as the built-in one.
    const twin: Agent = {
      agent_id: '6f1c2d3e-0000-4000-8000-000000000001',
      name: 'File Checker',
      created_at: '2026-09-21T10:00:00+00:00',
    };
    const html = renderModal([...AGENTS, twin], 'builtin-file-checker');
    expect(rowTag(html, 'builtin-file-checker')).toContain(ACTIVE);
    expect(rowOf(html, 'builtin-file-checker')).toContain('text-blue-700');
    expect(rowTag(html, twin.agent_id)).not.toContain(ACTIVE);
    expect(rowOf(html, twin.agent_id)).toContain(
      'aria-label="Edit File Checker"',
    );
    // And the other way round.
    const custom = renderModal([...AGENTS, twin], twin.agent_id);
    expect(rowTag(custom, twin.agent_id)).toContain(ACTIVE);
    expect(rowTag(custom, 'builtin-file-checker')).not.toContain(ACTIVE);
  });
});
