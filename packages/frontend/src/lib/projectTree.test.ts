// @vitest-environment node
import type { Project } from '../components/ProjectSettingsModal';
import {
  PROJECTS_VIEW_KEY,
  countByProduct,
  customerName,
  detectProduct,
  documentProgress,
  filterTreeProjects,
  groupTreeProjects,
  pageCount,
  pageWindow,
  readProjectsView,
  toTreeProjects,
  writeProjectsView,
  type ProductId,
} from './projectTree';

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

const LABELS: Record<ProductId, string> = {
  personalLoan: 'Personal Loan',
  homeLoan: 'Home Loan',
  loanAgainstProperty: 'Loan Against Property',
  businessLoan: 'Business Loan',
  carLoan: 'Car Loan',
  educationLoan: 'Education Loan',
  balanceTransfer: 'Balance Transfer',
  creditCard: 'Credit Card',
  telecallerQa: 'Telecaller QA',
  other: 'Other',
};
const label = (product: ProductId) => LABELS[product];

describe('detectProduct', () => {
  it.each([
    ['Sneha Kulkarni – Personal Loan', '', 'personalLoan'],
    ['Amit Patil – Home Loan', '', 'homeLoan'],
    ['Telecaller QA – Sample calls', '', 'telecallerQa'],
    [
      'Rahul Deshmukh',
      'Salaried personal loan · Case handler: Asha Verma',
      'personalLoan',
    ],
    ['PL: Rahul Deshmukh', '', 'personalLoan'],
    ['Amit Patil LAP', '', 'loanAgainstProperty'],
    ['Amit Patil – mortgage loan', '', 'loanAgainstProperty'],
    ['Sneha Kulkarni – Car loan', '', 'carLoan'],
    ['Branch visit notes', 'Notes from the branch visit', 'other'],
    // Abbreviations count in capitals only: "pl" here is not a product.
    ['Plan for pl review', '', 'other'],
  ])('%s / %s -> %s', (name, description, product) => {
    expect(detectProduct({ name, description })).toBe(product);
  });

  it('prefers the name to the description', () => {
    expect(
      detectProduct({
        name: 'Amit Patil – Home Loan',
        description: 'Came in as a personal loan enquiry',
      }),
    ).toBe('homeLoan');
  });

  it('takes the product named first', () => {
    expect(
      detectProduct({
        name: 'Home loan with a personal loan top-up',
        description: '',
      }),
    ).toBe('homeLoan');
  });
});

describe('customerName', () => {
  it('drops the part of the name that only names the product', () => {
    expect(customerName('Sneha Kulkarni – Personal Loan', 'personalLoan')).toBe(
      'Sneha Kulkarni',
    );
    expect(customerName('Home Loan | Amit Patil', 'homeLoan')).toBe(
      'Amit Patil',
    );
    expect(customerName('Telecaller QA – Sample calls', 'telecallerQa')).toBe(
      'Sample calls',
    );
  });

  it('keeps any other name whole', () => {
    expect(customerName('Personal loan for Sneha', 'personalLoan')).toBe(
      'Personal loan for Sneha',
    );
    expect(
      customerName('Sneha Kulkarni – Personal Loan top-up', 'personalLoan'),
    ).toBe('Sneha Kulkarni – Personal Loan top-up');
    expect(customerName('Branch visit notes', 'other')).toBe(
      'Branch visit notes',
    );
    // Only the product: nothing would be left.
    expect(customerName('Personal Loan', 'personalLoan')).toBe('Personal Loan');
  });
});

describe('toTreeProjects and groupTreeProjects', () => {
  const rows = toTreeProjects([
    project('p-notes', 'Branch visit notes'),
    project('p-rahul', 'Rahul Deshmukh – Personal Loan', {
      updated_at: '2026-09-30T10:00:00Z',
    }),
    project('p-calls', 'Telecaller QA – Sample calls'),
    project('p-amit', 'Amit Patil – Home Loan'),
    project('p-sneha', 'Sneha Kulkarni – Personal Loan', {
      updated_at: null,
      created_at: '2026-10-01T08:00:00Z',
    }),
  ]);

  it('orders by product, then the latest update first (created when never updated)', () => {
    expect(rows.map((r) => r.project.project_id)).toEqual([
      'p-sneha',
      'p-rahul',
      'p-amit',
      'p-calls',
      'p-notes',
    ]);
    expect(rows[0].updated).toBe('2026-10-01T08:00:00Z');
    expect(rows.map((r) => r.customer)).toEqual([
      'Sneha Kulkarni',
      'Rahul Deshmukh',
      'Amit Patil',
      'Sample calls',
      'Branch visit notes',
    ]);
  });

  it('groups in product order, Other last', () => {
    const groups = groupTreeProjects(rows);
    expect(
      groups.map((g) => [g.product, g.rows.map((r) => r.project.project_id)]),
    ).toEqual([
      ['personalLoan', ['p-sneha', 'p-rahul']],
      ['homeLoan', ['p-amit']],
      ['telecallerQa', ['p-calls']],
      ['other', ['p-notes']],
    ]);
    expect(Object.fromEntries(countByProduct(rows))).toEqual({
      personalLoan: 2,
      homeLoan: 1,
      telecallerQa: 1,
      other: 1,
    });
  });
});

describe('filterTreeProjects', () => {
  const rows = toTreeProjects([
    project('p-sneha', 'Sneha Kulkarni – Personal Loan'),
    project('p-rahul', 'Rahul Deshmukh – Personal Loan', {
      created_by: 'rohan.iyer',
    }),
    project('p-amit', 'Amit Patil – Home Loan'),
    project('p-calls', 'Telecaller QA – Sample calls', {
      created_by: 'rohan.iyer',
    }),
  ]);
  const ids = (query: string) =>
    filterTreeProjects(rows, query, label).map((r) => r.project.project_id);

  it('finds the name, the product and the case handler, ignoring case', () => {
    expect(ids('SNEHA')).toEqual(['p-sneha']);
    expect(ids('home loan')).toEqual(['p-amit']);
    expect(ids('rohan')).toEqual(['p-rahul', 'p-calls']);
  });

  it('needs every word somewhere in the row', () => {
    expect(ids('personal rohan')).toEqual(['p-rahul']);
    expect(ids('home rohan')).toEqual([]);
  });

  it('keeps every row for an empty query', () => {
    expect(ids('   ')).toHaveLength(4);
  });
});

describe('paging', () => {
  it('counts pages, at least one', () => {
    expect(pageCount(0, 10)).toBe(1);
    expect(pageCount(10, 10)).toBe(1);
    expect(pageCount(11, 10)).toBe(2);
    expect(pageCount(51, 25)).toBe(3);
  });

  it('shows every page up to 7, else the ends and the pages around', () => {
    expect(pageWindow(1, 3)).toEqual([1, 2, 3]);
    expect(pageWindow(1, 10)).toEqual([1, 2, 3, 4, 5, null, 10]);
    expect(pageWindow(5, 10)).toEqual([1, null, 4, 5, 6, null, 10]);
    expect(pageWindow(10, 10)).toEqual([1, null, 6, 7, 8, 9, 10]);
  });
});

describe('documentProgress', () => {
  it('counts the documents and the completed ones', () => {
    expect(
      documentProgress([
        { status: 'completed' },
        { status: 'in_progress' },
        { status: 'completed' },
        { status: 'failed' },
      ]),
    ).toEqual({ total: 4, completed: 2 });
    expect(documentProgress([])).toEqual({ total: 0, completed: 0 });
  });
});

describe('readProjectsView and writeProjectsView', () => {
  const memoryStorage = () => {
    const items = new Map<string, string>();
    return {
      getItem: (key: string) => items.get(key) ?? null,
      setItem: (key: string, value: string) => void items.set(key, value),
    };
  };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('starts on the tree and remembers the choice', () => {
    const storage = memoryStorage();
    vi.stubGlobal('localStorage', storage);

    expect(readProjectsView()).toBe('tree');
    writeProjectsView('cards');
    expect(storage.getItem(PROJECTS_VIEW_KEY)).toBe('cards');
    expect(readProjectsView()).toBe('cards');
    writeProjectsView('tree');
    expect(readProjectsView()).toBe('tree');
  });

  it('reads an unknown stored value as the tree', () => {
    const storage = memoryStorage();
    storage.setItem(PROJECTS_VIEW_KEY, 'grid');
    vi.stubGlobal('localStorage', storage);

    expect(readProjectsView()).toBe('tree');
  });

  it('copes without storage or with storage that throws', () => {
    vi.stubGlobal('localStorage', undefined);
    expect(readProjectsView()).toBe('tree');
    expect(() => writeProjectsView('cards')).not.toThrow();

    const blocked = () => {
      throw new Error('SecurityError: storage is blocked');
    };
    vi.stubGlobal('localStorage', { getItem: blocked, setItem: blocked });
    expect(readProjectsView()).toBe('tree');
    expect(() => writeProjectsView('cards')).not.toThrow();
  });
});
