// @vitest-environment node
// The DOM comes from ../../test/jsdom (the stock jsdom environment cannot
// start in this workspace); it must be imported before testing-library.
import '../../test/jsdom';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useParams,
} from '@tanstack/react-router';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../../i18n/locales/en.json';
import ProjectTreeView, { type ProjectTreeViewProps } from '.';
import type { Project } from '../ProjectSettingsModal';

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: 'en',
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
    showSupportNotice: false,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

const project = (
  project_id: string,
  name: string,
  more: Partial<Project> = {},
): Project => ({
  project_id,
  name,
  description: '',
  status: 'active',
  created_by: 'asha.verma',
  language: 'en',
  color: null,
  created_at: '2026-09-20T09:00:00Z',
  updated_at: '2026-09-28T09:00:00Z',
  ...more,
});

// Made-up sample customers and the demo personas asha.verma / rohan.iyer.
const PROJECTS = [
  project('p-rahul', 'Rahul Deshmukh – Personal Loan', {
    created_by: 'rohan.iyer',
    updated_at: '2026-09-30T10:00:00Z',
  }),
  project('p-sneha', 'Sneha Kulkarni – Personal Loan', {
    description: 'Salaried personal loan · Case handler: Asha Verma',
    updated_at: '2026-10-01T10:00:00Z',
  }),
  project('p-calls', 'Telecaller QA – Sample calls', {
    description: 'Sample recorded telecaller calls for the Call QA Reviewer',
    created_by: 'rohan.iyer',
  }),
  project('p-amit', 'Amit Patil – Home Loan'),
  project('p-notes', 'Branch visit notes', { created_by: null }),
];

const SNEHA_DOCS = [
  {
    document_id: 'd-pan',
    name: 'pan-card.pdf',
    status: 'completed',
    file_type: 'application/pdf',
    file_size: 120_000,
  },
  {
    document_id: 'd-slip',
    name: 'salary-slip-aug.pdf',
    status: 'completed',
    file_type: 'application/pdf',
    file_size: 80_000,
  },
  {
    document_id: 'd-bank',
    name: 'bank-statement.pdf',
    status: 'in_progress',
    file_type: 'application/pdf',
    file_size: 2_400_000,
  },
];

/** fetchApi stub: answers each URL from `replies` (an Error is thrown). */
function fakeApi(replies: Record<string, unknown> = {}) {
  const calls: string[] = [];
  const fetchApi = (async (url: string) => {
    calls.push(url);
    const reply = replies[url];
    if (reply instanceof Error) throw reply;
    return reply;
  }) as ProjectTreeViewProps['fetchApi'];
  return { calls, fetchApi };
}

/** A promise the test settles itself. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function ProjectPage() {
  const { projectId } = useParams({ strict: false });
  return <p>Project page {projectId}</p>;
}

/** Renders the tree on '/' of a memory router that also has the project page. */
async function renderTree(props: Partial<ProjectTreeViewProps> = {}) {
  const callbacks = {
    onCreate: vi.fn(),
    onEdit: vi.fn(),
    onDelete: vi.fn(),
  };
  const api = fakeApi({ 'projects/p-sneha/documents': SNEHA_DOCS });
  const rootRoute = createRootRoute({ component: Outlet });
  const routeTree = rootRoute.addChildren([
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/',
      component: () => (
        <ProjectTreeView
          projects={PROJECTS}
          fetchApi={api.fetchApi}
          {...callbacks}
          {...props}
        />
      ),
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/projects/$projectId',
      component: ProjectPage,
    }),
  ]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  render(
    <I18nextProvider i18n={i18n}>
      <RouterProvider router={router} />
    </I18nextProvider>,
  );
  await screen.findByRole('treegrid', { name: 'Projects by loan product' });
  return { ...callbacks, ...api, router };
}

const text = (element: Element) =>
  (element.textContent ?? '').replace(/\s+/g, ' ').trim();

/** The tree rows (header excluded) at an aria-level: 1 group, 2 project, 3 document. */
const rowsAt = (level: 1 | 2 | 3) =>
  screen
    .getAllByRole('row')
    .filter((row) => row.getAttribute('aria-level') === String(level));

const projectRow = (customer: string) => {
  const row = screen.getByRole('link', { name: customer }).closest('tr');
  if (!row) throw new Error(`no row for ${customer}`);
  return row;
};

/** Clicks a project row on its Documents cell (not on the link or buttons). */
const clickRow = (row: HTMLElement) =>
  fireEvent.click(row.querySelectorAll('td')[3]);

const customers = () =>
  rowsAt(2).map((row) => text(within(row).getByRole('link')));

describe('ProjectTreeView', () => {
  it('groups the projects by loan product, with a case count', async () => {
    await renderTree();

    expect(rowsAt(1).map(text)).toEqual([
      'Personal Loan2 cases',
      'Home Loan1 case',
      'Telecaller QA1 case',
      'Other1 case',
    ]);
    // Latest update first in a group; the product part of a name is dropped.
    expect(customers()).toEqual([
      'Sneha Kulkarni',
      'Rahul Deshmukh',
      'Amit Patil',
      'Sample calls',
      'Branch visit notes',
    ]);
    const rahul = projectRow('Rahul Deshmukh');
    expect(text(rahul)).toContain('Personal Loan');
    expect(text(rahul)).toContain('rohan.iyer');
    expect(within(rahul).getByText('30 Sept 2026')).toBeTruthy();
    expect(rahul.getAttribute('aria-expanded')).toBe('false');
  });

  it('links each customer to its project page', async () => {
    const { router } = await renderTree();

    const link = screen.getByRole('link', { name: 'Sneha Kulkarni' });
    expect(link.getAttribute('href')).toBe('/projects/p-sneha');
    expect(link.getAttribute('title')).toBe('Sneha Kulkarni – Personal Loan');
    expect(
      screen.getByRole('link', { name: 'Sample calls' }).getAttribute('href'),
    ).toBe('/projects/p-calls');

    fireEvent.click(link);

    expect(await screen.findByText('Project page p-sneha')).toBeTruthy();
    expect(router.state.location.pathname).toBe('/projects/p-sneha');
  });

  it('searches the name, the product and the case handler', async () => {
    await renderTree();
    const search = screen.getByRole('searchbox', { name: 'Search:' });

    fireEvent.change(search, { target: { value: 'rohan' } });
    expect(customers()).toEqual(['Rahul Deshmukh', 'Sample calls']);
    expect(
      screen.getByText(
        'Showing 1 to 2 of 2 entries (filtered from 5 total entries)',
      ),
    ).toBeTruthy();

    fireEvent.change(search, { target: { value: 'home loan' } });
    expect(customers()).toEqual(['Amit Patil']);
    expect(rowsAt(1).map(text)).toEqual(['Home Loan1 case']);

    fireEvent.change(search, { target: { value: 'KULKARNI' } });
    expect(customers()).toEqual(['Sneha Kulkarni']);

    fireEvent.change(search, { target: { value: 'nobody' } });
    expect(rowsAt(2)).toEqual([]);
    expect(screen.getByText('No matching entries')).toBeTruthy();
    expect(screen.getByText(/^Showing 0 to 0 of 0 entries/)).toBeTruthy();
  });

  it('shows 10 entries, pages through the rest, and shows 25 on request', async () => {
    const many = Array.from({ length: 12 }, (_, i) => {
      const n = String(i + 1).padStart(2, '0');
      return project(`p-${n}`, `Customer ${n} – Personal Loan`, {
        updated_at: `2026-09-${n}T10:00:00Z`,
      });
    });
    await renderTree({ projects: many });

    expect(rowsAt(2)).toHaveLength(10);
    expect(customers()[0]).toBe('Customer 12');
    expect(screen.getByText('Showing 1 to 10 of 12 entries')).toBeTruthy();
    expect(text(rowsAt(1)[0])).toBe('Personal Loan12 cases');

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(customers()).toEqual(['Customer 02', 'Customer 01']);
    expect(screen.getByText('Showing 11 to 12 of 12 entries')).toBeTruthy();
    expect(
      screen
        .getByRole('button', { name: 'Page 2' })
        .getAttribute('aria-current'),
    ).toBe('page');
    expect(
      (screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    fireEvent.change(
      screen.getByRole('combobox', { name: 'Entries per page' }),
      {
        target: { value: '25' },
      },
    );
    expect(rowsAt(2)).toHaveLength(12);
    expect(screen.getByText('Showing 1 to 12 of 12 entries')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Page 2' })).toBeNull();
  });

  it("loads a project's documents when it is first opened, and only then", async () => {
    const reply = deferred<unknown>();
    const calls: string[] = [];
    const fetchApi = ((url: string) => {
      calls.push(url);
      return reply.promise;
    }) as ProjectTreeViewProps['fetchApi'];
    await renderTree({ fetchApi });
    expect(calls).toEqual([]);

    const sneha = projectRow('Sneha Kulkarni');
    clickRow(sneha);

    expect(sneha.getAttribute('aria-expanded')).toBe('true');
    expect(sneha.getAttribute('aria-busy')).toBe('true');
    expect(calls).toEqual(['projects/p-sneha/documents']);
    expect(rowsAt(3).map(text)).toEqual(['Loading documents…']);

    reply.resolve(SNEHA_DOCS);

    const docs = await waitFor(() => {
      const rows = rowsAt(3);
      expect(rows).toHaveLength(3);
      return rows;
    });
    expect(docs.map(text)).toEqual([
      'pan-card.pdfCompleted117.2 KB',
      'salary-slip-aug.pdfCompleted78.1 KB',
      'bank-statement.pdfAnalyzing2.3 MB',
    ]);
    expect(within(sneha).getByText('2 of 3 done')).toBeTruthy();

    // Closed: the documents go, the count stays.
    clickRow(sneha);
    expect(sneha.getAttribute('aria-expanded')).toBe('false');
    expect(rowsAt(3)).toEqual([]);
    expect(within(sneha).getByText('2 of 3 done')).toBeTruthy();

    // Opened again: shown at once, not fetched again.
    clickRow(sneha);
    expect(rowsAt(3)).toHaveLength(3);
    expect(calls).toEqual(['projects/p-sneha/documents']);

    // Other projects were never fetched.
    expect(
      within(projectRow('Rahul Deshmukh')).getByText(
        'Open the row to load its documents',
      ),
    ).toBeTruthy();
  });

  it('says when a project has no documents', async () => {
    await renderTree({
      fetchApi: fakeApi({ 'projects/p-amit/documents': [] }).fetchApi,
    });

    clickRow(projectRow('Amit Patil'));

    expect(await screen.findByText('No documents yet')).toBeTruthy();
    expect(
      within(projectRow('Amit Patil')).getByText('0 of 0 done'),
    ).toBeTruthy();
  });

  it('offers Retry when the documents could not load', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const replies: Record<string, unknown> = {
      'projects/p-sneha/documents': new Error('HTTP 502'),
    };
    const api = fakeApi(replies);
    await renderTree({ fetchApi: api.fetchApi });

    clickRow(projectRow('Sneha Kulkarni'));
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Could not load the documents.',
    );

    replies['projects/p-sneha/documents'] = SNEHA_DOCS;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(rowsAt(3)).toHaveLength(3));
    expect(api.calls).toEqual([
      'projects/p-sneha/documents',
      'projects/p-sneha/documents',
    ]);
  });

  it('opens and closes rows from the keyboard', async () => {
    const { calls } = await renderTree();
    const group = rowsAt(1)[0];
    const sneha = projectRow('Sneha Kulkarni');
    expect(group.tabIndex).toBe(0);
    expect(sneha.tabIndex).toBe(0);

    // Enter and Space toggle the focused row.
    group.focus();
    fireEvent.keyDown(group, { key: 'Enter' });
    expect(group.getAttribute('aria-expanded')).toBe('false');
    expect(customers()).not.toContain('Sneha Kulkarni');
    fireEvent.keyDown(group, { key: ' ' });
    expect(group.getAttribute('aria-expanded')).toBe('true');

    // Down moves to the first project; Space opens it and loads its documents.
    fireEvent.keyDown(group, { key: 'ArrowDown' });
    const first = projectRow('Sneha Kulkarni');
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: ' ' });
    expect(first.getAttribute('aria-expanded')).toBe('true');
    expect(calls).toEqual(['projects/p-sneha/documents']);
    await waitFor(() => expect(rowsAt(3)).toHaveLength(3));

    // Right moves into the open row, Left back to it, Left again closes it.
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(text(document.activeElement as Element)).toContain('pan-card.pdf');
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: 'ArrowLeft' });
    expect(first.getAttribute('aria-expanded')).toBe('false');

    // Left on a closed project goes to its group; Enter on the link is the link's.
    fireEvent.keyDown(first, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(group);
    fireEvent.keyDown(screen.getByRole('link', { name: 'Rahul Deshmukh' }), {
      key: 'Enter',
    });
    expect(projectRow('Rahul Deshmukh').getAttribute('aria-expanded')).toBe(
      'false',
    );
  });

  it('closes a product group on click', async () => {
    await renderTree();

    fireEvent.click(rowsAt(1)[2]);

    expect(rowsAt(1)[2].getAttribute('aria-expanded')).toBe('false');
    expect(customers()).not.toContain('Sample calls');
    expect(customers()).toContain('Amit Patil');
  });

  it('edits, deletes and creates through the callbacks, without opening rows', async () => {
    const { onCreate, onEdit, onDelete, calls } = await renderTree();
    const sneha = projectRow('Sneha Kulkarni');

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Edit Sneha Kulkarni – Personal Loan',
      }),
    );
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Delete Sneha Kulkarni – Personal Loan',
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'New Project' }));

    expect(onEdit).toHaveBeenCalledWith(PROJECTS[1]);
    expect(onDelete).toHaveBeenCalledWith(PROJECTS[1]);
    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(sneha.getAttribute('aria-expanded')).toBe('false');
    expect(calls).toEqual([]);
  });
});
