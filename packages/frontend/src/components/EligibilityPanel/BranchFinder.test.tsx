// @vitest-environment node
// (Rendered to static markup: the workspace's jsdom install cannot start.
// Effects do not run, so the requests are tested through the functions the
// component calls, with a fetchApi stub.)
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import i18next, { type TFunction } from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../../i18n/locales/en.json';
import { ApiError } from '../../lib/apiError';
import BranchFinder, {
  BranchResults,
  MAX_LENDERS,
  ReferenceListsPanel,
  branchesPath,
  csvTemplate,
  describeUploadError,
  distanceText,
  fetchBranches,
  lenderQuery,
  nearestText,
  parseBranches,
  parseReferenceLists,
  removeReferenceList,
  uploadProblems,
  uploadReferenceList,
  type BranchesState,
  type ReferenceListsState,
} from './BranchFinder';

vi.mock('../../hooks/useAwsClient', () => ({
  useAwsClient: () => ({ fetchApi: async () => undefined }),
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

const t: TFunction = ((key: string, options?: Record<string, unknown>) =>
  i18n.t(key, options)) as TFunction;

function render(node: ReactNode): string {
  return renderToStaticMarkup(
    <I18nextProvider i18n={i18n}>{node}</I18nextProvider>,
  );
}

/** Text of an HTML fragment, tags stripped and entities decoded. */
function plain(html: string): string {
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

/** The <li> of one lender, by data-lender. */
function lenderItem(html: string, lender: string): string {
  const start = html.indexOf(`data-lender="${lender}"`);
  if (start < 0) throw new Error(`${lender} not rendered`);
  const open = html.lastIndexOf('<li', start);
  return html.slice(open, html.indexOf('</li>', start) + 5);
}

/** The <li> of one uploaded-list kind, by data-kind. */
function listItem(html: string, kind: string): string {
  const start = html.indexOf(`data-kind="${kind}"`);
  if (start < 0) throw new Error(`${kind} not rendered`);
  const open = html.lastIndexOf('<li', start);
  const end = html.indexOf('data-kind=', start + 10);
  return html.slice(open, end < 0 ? undefined : end);
}

// What GET .../eligibility/branches?pincode=400606 answers (shape of
// packages/backend/app/branches.py; branch rows as in the public data).
const ANSWER = {
  pincode: '400606',
  place: {
    office: 'Jekegram',
    district: 'Thane',
    state: 'Maharashtra',
    lat: 19.206,
    lon: 72.9669,
    approximate: false,
  },
  lenders: [
    {
      lender: 'HDFC Bank',
      lender_id: 'hdfc_bank',
      serviceable: true,
      source: 'public_data',
      serviceable_source: 'sample',
      branches_source: 'public_data',
      branches: [
        {
          name: 'Thane West',
          address: 'Shop 1, Station Road, Thane West 400606',
          city: 'Thane',
          district: 'Thane',
          state: 'Maharashtra',
          pincode: '400606',
          ifsc: 'HDFC0003959',
          distance_km: 0,
        },
        {
          name: 'Thane West Naupada',
          address: null,
          city: 'Thane',
          district: 'Thane',
          state: 'Maharashtra',
          pincode: '400608',
          ifsc: 'HDFC0008922',
          distance_km: 3.8,
        },
        {
          name: 'Kasarvadavali',
          address: null,
          city: 'Kasarvadavali',
          district: 'Thane',
          state: 'Maharashtra',
          pincode: '400615',
          ifsc: 'HDFC0010006',
          distance_km: 8.6,
        },
      ],
      label: null,
      notes: [],
    },
    {
      lender: 'Bajaj Finance',
      lender_id: 'bajaj_finance',
      serviceable: true,
      source: 'sample',
      serviceable_source: 'sample',
      branches_source: 'sample',
      branches: [
        {
          name: 'Ghodbunder Road (sample)',
          address: 'SAMPLE address for the demo, not a real branch',
          city: 'Kasarvadavali',
          district: 'Thane',
          state: 'Maharashtra',
          pincode: '400615',
          ifsc: null,
          distance_km: 8.6,
        },
      ],
      label: 'SAMPLE branch, made up for the demo: not a real address',
      notes: [],
    },
    {
      lender: 'Tata Capital',
      lender_id: 'tata_capital',
      serviceable: false,
      source: 'dsa_list',
      serviceable_source: 'dsa_list',
      branches_source: 'dsa_list',
      branches: [
        {
          name: 'Thane Station Road',
          address: null,
          city: null,
          district: 'Thane',
          state: 'Maharashtra',
          pincode: '400611',
          ifsc: null,
          distance_km: 0.6,
        },
      ],
      label: null,
      notes: [
        'Pincode 400606 is not on your serviceability list for Tata Capital',
      ],
    },
    {
      lender: 'Example Credit Co-op',
      lender_id: null,
      serviceable: null,
      source: 'public_data',
      serviceable_source: null,
      branches_source: null,
      branches: [],
      label: null,
      notes: [
        'No branch data for Example Credit Co-op: the public data covers HDFC Bank, ICICI Bank and Axis Bank; upload your branch list',
      ],
    },
  ],
  sources: [
    {
      name: 'Department of Posts, All India Pincode Directory with Latitude and Longitude (data.gov.in)',
      licence: 'Government Open Data License - India (GODL-India)',
      url: 'https://data.gov.in/files/ogdpv2dms/s3fs-public/dataurl03122020/pincode.csv',
    },
    {
      name: 'SAMPLE branches for Bajaj Finance and Tata Capital, made up for the demo (no open list exists)',
      licence: 'Synthetic sample data, not real branches',
      url: null,
    },
  ],
  notes: [],
};

const LISTS = {
  lists: [
    {
      kind: 'pincode_serviceability',
      label: 'Pincode serviceability',
      description: 'Where each lender lends',
      columns: ['lender', 'pincode'],
      optional_columns: ['serviceable'],
      uploaded: false,
      filename: null,
      rows: 0,
      lenders: [],
      uploaded_at: null,
      expires_at: null,
      duplicates: null,
      notes: [],
    },
    {
      kind: 'lender_branches',
      label: 'Lender branches',
      description: "Your lenders' branches",
      columns: ['lender', 'branch', 'pincode'],
      optional_columns: ['address', 'city', 'district', 'state', 'ifsc'],
      uploaded: true,
      filename: 'branches.csv',
      rows: 120,
      lenders: ['Bajaj Finance', 'Tata Capital'],
      uploaded_at: '2026-10-02T10:00:00+00:00',
      expires_at: '2026-10-09T10:00:00+00:00',
      duplicates: null,
      notes: [],
    },
    {
      kind: 'company_categories',
      columns: ['lender', 'company', 'category'],
      optional_columns: [],
      uploaded: false,
    },
  ],
  retention_days: 7,
  max_upload_bytes: 4194304,
  max_rows: 100000,
};

const PARSED = parseBranches(ANSWER);
const LOADED: BranchesState = { loading: false, error: null, answer: PARSED };
const noop = () => undefined;

function results(state: BranchesState = LOADED) {
  return render(
    <BranchResults waitingFor={null} state={state} onRefresh={noop} />,
  );
}

function listsPanel(
  state: Partial<ReferenceListsState> = {},
  open = true,
): string {
  return render(
    <ReferenceListsPanel
      open={open}
      onToggle={noop}
      state={{
        data: parseReferenceLists(LISTS),
        loading: false,
        loadError: null,
        busy: null,
        outcome: null,
        ...state,
      }}
      onRetry={noop}
      onUpload={noop}
      onRemove={noop}
    />,
  );
}

interface Call {
  url: string;
  init?: RequestInit;
}

/** fetchApi stub: records each call and answers `reply` (an Error is thrown). */
function fakeApi(reply: unknown) {
  const calls: Call[] = [];
  const fetchApi = async <T,>(url: string, init?: RequestInit): Promise<T> => {
    calls.push({ url, init });
    if (reply instanceof Error) throw reply;
    return reply as T;
  };
  return { calls, fetchApi };
}

describe('parseBranches', () => {
  it('keeps every lender with its badges, branches and notes', () => {
    expect(PARSED.pincode).toBe('400606');
    expect(PARSED.place).toEqual({
      office: 'Jekegram',
      district: 'Thane',
      state: 'Maharashtra',
      approximate: false,
    });
    expect(PARSED.lenders.map((l) => [l.lender, l.source])).toEqual([
      ['HDFC Bank', 'public_data'],
      ['Bajaj Finance', 'sample'],
      ['Tata Capital', 'dsa_list'],
      ['Example Credit Co-op', 'public_data'],
    ]);
    expect(PARSED.lenders[0].branches[0]).toEqual({
      name: 'Thane West',
      address: 'Shop 1, Station Road, Thane West 400606',
      city: 'Thane',
      district: 'Thane',
      state: 'Maharashtra',
      pincode: '400606',
      ifsc: 'HDFC0003959',
      distance_km: 0,
      approximate: false,
    });
    expect(PARSED.lenders[3].serviceable).toBeNull();
    expect(PARSED.sources).toHaveLength(2);
  });

  it('drops what it cannot use and refuses an answer without lenders', () => {
    const parsed = parseBranches({
      pincode: '400606',
      place: null,
      lenders: [
        { lender: '' },
        {
          lender: 'Axis Bank',
          serviceable: 'yes',
          source: 'elsewhere',
          branches: [{ name: 'No pincode' }, { name: 'Ok', pincode: '400608' }],
        },
      ],
      sources: [{ licence: 'no name' }],
    });
    expect(parsed.place).toBeNull();
    expect(parsed.lenders).toHaveLength(1);
    expect(parsed.lenders[0]).toMatchObject({
      lender: 'Axis Bank',
      serviceable: null,
      source: 'public_data',
      serviceable_source: null,
      notes: [],
    });
    expect(parsed.lenders[0].branches.map((b) => b.name)).toEqual(['Ok']);
    expect(parsed.lenders[0].branches[0].distance_km).toBeNull();
    expect(parsed.sources).toEqual([]);
    expect(() => parseBranches({ detail: 'nope' })).toThrow();
    expect(() => parseBranches(null)).toThrow();
  });
});

describe('requests', () => {
  it('asks once the pincode has 6 digits and a lender is given', () => {
    expect(
      branchesPath('proj_demo', '400606', ['HDFC Bank', 'Bajaj Finance']),
    ).toBe(
      'projects/proj_demo/eligibility/branches?pincode=400606&lenders=HDFC%20Bank%2CBajaj%20Finance',
    );
    expect(branchesPath('proj_demo', ' 400 606 ', ['HDFC Bank'])).toContain(
      'pincode=400606&',
    );
    for (const pincode of [undefined, '', '40060', '012345', '40060a']) {
      expect(branchesPath('proj_demo', pincode, ['HDFC Bank'])).toBeNull();
    }
    expect(branchesPath('proj_demo', '400606', [])).toBeNull();
    expect(branchesPath('proj_demo', '400606', ['  ', ','])).toBeNull();
  });

  it('sends each lender once, without the comma that separates them', () => {
    expect(
      lenderQuery([
        'HDFC Bank',
        'hdfc  bank',
        'Tata Capital, Ltd',
        'ICICI Bank',
      ]),
    ).toEqual(['HDFC Bank', 'Tata Capital Ltd', 'ICICI Bank']);
    const many = Array.from({ length: 25 }, (_, n) => `Lender ${n}`);
    expect(lenderQuery(many)).toHaveLength(MAX_LENDERS);
  });

  it('fetches and parses the answer', async () => {
    const { calls, fetchApi } = fakeApi(ANSWER);
    const path = branchesPath('proj_demo', '400606', ['HDFC Bank']) as string;
    const answer = await fetchBranches(fetchApi, path);
    expect(calls).toEqual([{ url: path, init: undefined }]);
    expect(answer.lenders[0].lender).toBe('HDFC Bank');
  });

  it('uploads the file bytes as they are, with its kind and name', async () => {
    const saved = { ...LISTS.lists[1], duplicates: 2, notes: ['check this'] };
    const { calls, fetchApi } = fakeApi(saved);
    const csv =
      'lender,branch,pincode\r\nTata Capital,Thane Station Road,400611\r\n';
    const file = new File([csv], 'my branches.csv', { type: 'text/csv' });

    const list = await uploadReferenceList(
      fetchApi,
      'proj_demo',
      'lender_branches',
      file,
    );

    expect(calls[0].url).toBe(
      'projects/proj_demo/eligibility/reference-data?kind=lender_branches&filename=my%20branches.csv',
    );
    expect(calls[0].init?.method).toBe('POST');
    expect(calls[0].init?.headers).toEqual({
      'Content-Type': 'application/octet-stream',
    });
    const body = calls[0].init?.body as Uint8Array;
    expect(body).toBeInstanceOf(Uint8Array);
    expect(new TextDecoder().decode(body)).toBe(csv);
    expect(list).toMatchObject({
      kind: 'lender_branches',
      rows: 120,
      duplicates: 2,
      notes: ['check this'],
    });
  });

  it('removes a list with DELETE', async () => {
    const { calls, fetchApi } = fakeApi({
      kind: 'lender_branches',
      deleted: true,
    });
    expect(
      await removeReferenceList(fetchApi, 'proj_demo', 'lender_branches'),
    ).toBe(true);
    expect(calls[0]).toEqual({
      url: 'projects/proj_demo/eligibility/reference-data/lender_branches',
      init: { method: 'DELETE' },
    });
  });

  it('offers a template whose header is the columns the API reads', () => {
    const header = (kind: Parameters<typeof csvTemplate>[0]) =>
      csvTemplate(kind).split('\r\n')[0].split(',');
    expect(header('pincode_serviceability')).toEqual([
      'lender',
      'pincode',
      'serviceable',
    ]);
    expect(header('lender_branches')).toEqual([
      'lender',
      'branch',
      'pincode',
      'address',
      'city',
      'district',
      'state',
      'ifsc',
    ]);
    expect(header('company_categories')).toEqual([
      'lender',
      'company',
      'category',
    ]);
    expect(csvTemplate('company_categories')).toMatch(/\r\n$/);
  });

  it('explains a refused upload with its row problems', () => {
    const refused = new ApiError(400, {
      message: 'The file has 2 problems',
      errors: [
        "Row 2: the pincode '4006' is not 6 digits",
        "Row 3: serviceable is 'perhaps': use yes or no",
      ],
    });
    expect(describeUploadError(t, refused)).toBe('The file has 2 problems');
    expect(uploadProblems(refused)).toHaveLength(2);
    expect(
      describeUploadError(t, new ApiError(413, 'The file is over 4 MB')),
    ).toBe('The file is over 4 MB');
    expect(describeUploadError(t, new ApiError(502))).toBe(
      'The eligibility service is unavailable (HTTP 502). Try again.',
    );
    expect(describeUploadError(t, new Error('offline'))).toBe('offline');
    expect(uploadProblems(new Error('offline'))).toEqual([]);
  });
});

describe('distances', () => {
  const branch = PARSED.lenders[0].branches[0];

  it('says same pincode, under 1 km or about N km', () => {
    expect(distanceText(t, branch, '400606')).toBe('same pincode');
    expect(
      distanceText(
        t,
        { ...branch, pincode: '400611', distance_km: 0.6 },
        '400606',
      ),
    ).toBe('under 1 km');
    expect(
      distanceText(
        t,
        { ...branch, pincode: '400608', distance_km: 3.8 },
        '400606',
      ),
    ).toBe('about 4 km');
  });

  it('says roughly when a pincode has no exact location', () => {
    const rough = { ...branch, pincode: '411051', approximate: true };
    expect(distanceText(t, { ...rough, distance_km: 7.4 }, '411038')).toBe(
      'roughly 7 km',
    );
    expect(distanceText(t, { ...rough, distance_km: 0 }, '411033')).toBe(
      'close by, roughly',
    );
    // Its own pincode is never rough.
    expect(distanceText(t, { ...rough, distance_km: 0 }, '411051')).toBe(
      'same pincode',
    );
  });

  it('reads "Nearest branch: <name>, <city> (about N km)"', () => {
    expect(
      nearestText(
        t,
        { ...branch, pincode: '400608', distance_km: 3.8 },
        '400606',
      ),
    ).toBe('Nearest branch: Thane West, Thane (about 4 km)');
    // No city: the district; no distance: none shown.
    expect(
      nearestText(
        t,
        { ...branch, city: null, pincode: '400608', distance_km: null },
        '400606',
      ),
    ).toBe('Nearest branch: Thane West, Thane');
  });
});

describe('BranchResults', () => {
  it('shows each lender’s nearest branch, serviceability and source', () => {
    const html = results();
    expect(plain(html)).toContain(
      'Nearest branches 400606 · Jekegram, Thane, Maharashtra',
    );

    const hdfc = lenderItem(html, 'HDFC Bank');
    expect(plain(hdfc)).toContain(
      'Nearest branch: Thane West, Thane (same pincode) · IFSC HDFC0003959',
    );
    expect(plain(hdfc)).toContain('Shop 1, Station Road, Thane West 400606');
    expect(plain(hdfc)).toContain(
      'Also: Thane West Naupada (about 4 km) · Kasarvadavali (about 9 km)',
    );
    // Serviceable per the SAMPLE pincode list; branches from the public data.
    expect(hdfc).toContain('data-serviceable="true"');
    expect(plain(hdfc)).toContain('Serviceable · sample list');
    expect(hdfc).toContain('data-source="public_data"');
    expect(plain(hdfc)).toContain('Public data');
  });

  it('labels the SAMPLE rows and the DSA’s own list', () => {
    const html = results();
    const bajaj = lenderItem(html, 'Bajaj Finance');
    expect(plain(bajaj)).toContain(
      'Nearest branch: Ghodbunder Road (sample), Kasarvadavali (about 9 km)',
    );
    expect(bajaj).toContain('data-source="sample"');
    expect(bajaj).toContain(
      'title="SAMPLE branch, made up for the demo: not a real address"',
    );
    expect(bajaj).not.toContain('IFSC');

    const tata = lenderItem(html, 'Tata Capital');
    expect(tata).toContain('data-serviceable="false"');
    expect(plain(tata)).toContain('Not serviceable Your list');
    expect(plain(tata)).not.toContain('sample list');
    expect(plain(tata)).toContain(
      'Nearest branch: Thane Station Road, Thane (under 1 km)',
    );
    expect(plain(tata)).toContain(
      'Pincode 400606 is not on your serviceability list for Tata Capital',
    );
  });

  it('says why a lender has no branch', () => {
    const other = lenderItem(results(), 'Example Credit Co-op');
    expect(other).toContain('data-serviceable="unknown"');
    expect(plain(other)).toContain('Serviceability not known');
    expect(plain(other)).toContain(
      'No branch data for Example Credit Co-op: the public data covers HDFC Bank, ICICI Bank and Axis Bank; upload your branch list',
    );
    expect(plain(other)).not.toContain('Nearest branch');
    expect(plain(other)).not.toContain('No branch found nearby.');
  });

  it('names the data sources with their licences', () => {
    const html = results();
    expect(plain(html)).toContain(
      'Distances are pincode centre to pincode centre, so they are approximate.',
    );
    expect(plain(html)).not.toContain('Roughly:');
    expect(plain(html)).toContain('Data sources and licences (2)');
    const sources = plain(
      html.slice(html.indexOf('data-testid="branch-sources"')),
    );
    expect(sources).toContain(
      'Department of Posts, All India Pincode Directory with Latitude and Longitude (data.gov.in) · Government Open Data License - India (GODL-India)',
    );
    expect(sources).toContain('Synthetic sample data, not real branches');
    expect(html).toContain(
      'href="https://data.gov.in/files/ogdpv2dms/s3fs-public/dataurl03122020/pincode.csv"',
    );
  });

  it('shows an approximate place and the answer’s notes', () => {
    const html = results({
      ...LOADED,
      answer: {
        ...PARSED,
        place: {
          office: 'Jekegram',
          district: 'Thane',
          state: 'Maharashtra',
          approximate: true,
        },
        notes: [
          'Your uploaded lists could not be read just now: this answer uses the public and sample data',
        ],
      },
    });
    expect(plain(html)).toContain('Maharashtra · location approximate');
    expect(plain(html)).toContain(
      'Your uploaded lists could not be read just now',
    );
  });

  it('marks a rough distance and says why', () => {
    const answer = parseBranches({
      ...ANSWER,
      pincode: '411033',
      lenders: [
        {
          ...ANSWER.lenders[0],
          branches: [
            { ...ANSWER.lenders[0].branches[0], pincode: '411033' },
            {
              ...ANSWER.lenders[0].branches[1],
              pincode: '411032',
              distance_km: 0,
              approximate: true,
            },
          ],
        },
      ],
    });
    const html = results({ loading: false, error: null, answer });
    const hdfc = plain(lenderItem(html, 'HDFC Bank'));
    expect(hdfc).toContain('Nearest branch: Thane West, Thane (same pincode)');
    expect(hdfc).toContain('Also: Thane West Naupada (close by, roughly)');
    expect(plain(html)).toContain(
      'so they are approximate. Roughly: the directory has no exact location for one of the two pincodes.',
    );
  });

  it('waits for a pincode or a lender', () => {
    const pincode = render(
      <BranchResults waitingFor="pincode" state={LOADED} onRefresh={noop} />,
    );
    expect(plain(pincode)).toContain(
      "Enter the applicant's 6-digit pincode to see each lender's nearest branch.",
    );
    expect(pincode).not.toContain('Look up the branches again');
    const lenders = render(
      <BranchResults waitingFor="lenders" state={LOADED} onRefresh={noop} />,
    );
    expect(plain(lenders)).toContain('No eligible lender to look up.');
  });

  it('shows the lookup and its failure', () => {
    const loading = results({ loading: true, error: null, answer: null });
    expect(loading).toContain('aria-busy="true"');
    expect(plain(loading)).toContain('Finding the nearest branches…');

    const failed = results({
      loading: false,
      error: new ApiError(502),
      answer: null,
    });
    expect(failed).toContain('role="alert"');
    expect(plain(failed)).toContain(
      'Could not find the branches: The eligibility service is unavailable (HTTP 502). Try again.',
    );
    expect(plain(failed)).toContain('Try again');
  });
});

describe('ReferenceListsPanel', () => {
  it('stays closed until opened, with how many lists are uploaded', () => {
    const html = listsPanel({}, false);
    expect(html).toContain('aria-expanded="false"');
    expect(plain(html)).toContain('Your lists (CSV) · 1 of 3 uploaded');
    expect(html).not.toContain('data-kind=');
  });

  it('shows each kind: its columns, its upload and what to do', () => {
    const html = listsPanel();
    expect(plain(html)).toContain(
      'A new file replaces the previous list of its kind; lists are deleted after 7 days.',
    );
    const branches = listItem(html, 'lender_branches');
    expect(branches).toContain('data-uploaded="true"');
    const until = new Date('2026-10-09T10:00:00+00:00').toLocaleDateString([], {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
    expect(plain(branches)).toContain(
      `branches.csv · 120 rows · deleted on ${until}`,
    );
    expect(plain(branches)).toContain(
      'Columns: lender, branch, pincode (optional: address, city, district, state, ifsc)',
    );
    expect(plain(branches)).toContain('Replace');
    expect(plain(branches)).toContain('Remove');

    const serviceability = listItem(html, 'pincode_serviceability');
    expect(serviceability).toContain('data-uploaded="false"');
    expect(plain(serviceability)).toContain(
      'Not uploaded: the public and sample data are used.',
    );
    expect(plain(serviceability)).toContain('Upload CSV');
    expect(plain(serviceability)).not.toContain('Remove');
    expect(plain(serviceability)).toContain('Template');
    expect(plain(listItem(html, 'company_categories'))).toContain(
      'Columns: lender, company, category',
    );
  });

  it('reports a saved upload with its notes', () => {
    const list = parseReferenceLists(LISTS).lists[1];
    const html = listsPanel({
      outcome: {
        kind: 'lender_branches',
        ok: true,
        list: {
          ...list,
          rows: 1,
          duplicates: 1,
          notes: ['1 pincode is not in the India Post directory (999999)'],
        },
      },
    });
    const branches = plain(listItem(html, 'lender_branches'));
    expect(branches).toContain('Saved 1 row. · 1 identical row dropped');
    expect(branches).toContain(
      '1 pincode is not in the India Post directory (999999)',
    );
    expect(plain(listItem(html, 'pincode_serviceability'))).not.toContain(
      'Saved',
    );
  });

  it('lists the row problems of a refused file', () => {
    const html = listsPanel({
      outcome: {
        kind: 'pincode_serviceability',
        ok: false,
        error: 'The file has 2 problems',
        problems: [
          "Row 2: the pincode '4006' is not 6 digits",
          "Row 3: serviceable is 'perhaps': use yes or no",
        ],
      },
    });
    const item = listItem(html, 'pincode_serviceability');
    expect(item).toContain('role="alert"');
    expect(plain(item)).toContain(
      "The file has 2 problems Row 2: the pincode '4006' is not 6 digits Row 3: serviceable is 'perhaps': use yes or no",
    );
  });

  it('says when the lists cannot be loaded', () => {
    const html = listsPanel({ data: null, loadError: new ApiError(504) });
    expect(plain(html)).toContain(
      'Could not load your lists: The eligibility service is unavailable (HTTP 504). Try again.',
    );
    const loading = listsPanel({ data: null, loading: true });
    expect(plain(loading)).toContain('Loading your lists…');
  });
});

describe('BranchFinder', () => {
  it('looks the branches up once the pincode has 6 digits', () => {
    const html = render(
      <BranchFinder
        projectId="proj_demo"
        pincode="400606"
        lenders={['HDFC Bank', 'Bajaj Finance']}
      />,
    );
    expect(html).toContain('data-testid="branch-finder"');
    expect(plain(html)).toContain('Finding the nearest branches…');
    expect(plain(html)).toContain('Your lists (CSV)');
  });

  it('asks for the pincode, then for a lender', () => {
    const noPincode = render(
      <BranchFinder
        projectId="proj_demo"
        pincode="4006"
        lenders={['HDFC Bank']}
      />,
    );
    expect(plain(noPincode)).toContain("Enter the applicant's 6-digit pincode");
    const noLender = render(
      <BranchFinder projectId="proj_demo" pincode="400606" lenders={[]} />,
    );
    expect(plain(noLender)).toContain('No eligible lender to look up.');
  });
});
