// @vitest-environment node
// The DOM comes from ../../test/jsdom (the stock jsdom environment cannot
// start in this workspace); it must be imported before testing-library.
import '../../test/jsdom';
import { fireEvent, render, screen } from '@testing-library/react';
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../../i18n/locales/en.json';
import { PROJECTS_VIEW_KEY } from '../../lib/projectTree';
import type { Project } from '../ProjectSettingsModal';
import { Route } from '../../routes/index';

const api = vi.hoisted(() => ({ fetchApi: vi.fn() }));

vi.mock('../../hooks/useAwsClient', () => ({
  useAwsClient: () => ({ fetchApi: api.fetchApi }),
}));
vi.mock('react-oidc-context', () => ({
  useAuth: () => ({ user: { profile: { email: 'asha.verma@example.com' } } }),
}));

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: 'en',
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
    showSupportNotice: false,
  });
});

const PROJECTS: Project[] = [
  {
    project_id: 'p-sneha',
    name: 'Sneha Kulkarni – Personal Loan',
    description: 'Salaried personal loan · Case handler: Asha Verma',
    status: 'active',
    created_by: 'asha.verma',
    language: 'en',
    color: 0,
    created_at: '2026-09-20T09:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
  },
];

beforeEach(() => {
  localStorage.clear();
  api.fetchApi.mockReset();
  api.fetchApi.mockImplementation(async (url: string) =>
    url === 'projects' ? PROJECTS : [],
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** The projects page (route '/') in a memory router. */
function renderPage() {
  const Page = Route.options.component;
  if (!Page) throw new Error('the / route has no component');
  const rootRoute = createRootRoute({ component: Outlet });
  const routeTree = rootRoute.addChildren([
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/',
      component: Page,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/projects/$projectId',
      component: () => null,
    }),
  ]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  return render(
    <I18nextProvider i18n={i18n}>
      <RouterProvider router={router} />
    </I18nextProvider>,
  );
}

const isPressed = (name: 'Tree' | 'Cards') =>
  screen.getByRole('button', { name }).getAttribute('aria-pressed');

describe('projects page: Tree | Cards', () => {
  it('opens in the tree view', async () => {
    renderPage();

    expect(
      await screen.findByRole('treegrid', { name: 'Projects by loan product' }),
    ).toBeTruthy();
    expect(isPressed('Tree')).toBe('true');
    expect(isPressed('Cards')).toBe('false');
    expect(
      screen.getByRole('link', { name: 'Sneha Kulkarni' }).getAttribute('href'),
    ).toBe('/projects/p-sneha');
    expect(screen.queryByText('Click to create')).toBeNull();
    expect(api.fetchApi.mock.calls).toEqual([['projects']]);
  });

  it('switches to the cards, and opens there next time', async () => {
    const first = renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Cards' }));

    expect(screen.queryByRole('treegrid')).toBeNull();
    expect(screen.getByText('Click to create')).toBeTruthy();
    expect(
      screen.getByRole('heading', { name: 'Sneha Kulkarni – Personal Loan' }),
    ).toBeTruthy();
    expect(localStorage.getItem(PROJECTS_VIEW_KEY)).toBe('cards');
    first.unmount();

    renderPage();
    expect(await screen.findByText('Click to create')).toBeTruthy();
    expect(isPressed('Cards')).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: 'Tree' }));
    expect(screen.getByRole('treegrid')).toBeTruthy();
    expect(localStorage.getItem(PROJECTS_VIEW_KEY)).toBe('tree');
  });

  it('still switches when the storage refuses to save the choice', async () => {
    const setItem = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new DOMException(
          'The quota has been exceeded.',
          'QuotaExceededError',
        );
      });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Cards' }));

    expect(setItem).toHaveBeenCalledWith(PROJECTS_VIEW_KEY, 'cards');
    expect(screen.getByText('Click to create')).toBeTruthy();
    expect(isPressed('Cards')).toBe('true');
  });

  it('opens in the tree view when the stored choice cannot be read', async () => {
    localStorage.setItem(PROJECTS_VIEW_KEY, 'cards');
    const getItem = Storage.prototype.getItem;
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (key === PROJECTS_VIEW_KEY) {
        throw new DOMException('Access is denied.', 'SecurityError');
      }
      return getItem.call(this, key);
    });
    renderPage();

    expect(await screen.findByRole('treegrid')).toBeTruthy();
    expect(isPressed('Tree')).toBe('true');
  });
});
