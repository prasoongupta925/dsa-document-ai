// @vitest-environment node
import {
  CSV_HEADERS,
  applicantUsageTotal,
  buildFileCheckCsv,
  consistencyTone,
  csvCell,
  csvUsd,
  documentUsage,
  eraseConfirmMatches,
  eraseMayBeIncomplete,
  eraseRequestBody,
  maskPan,
  parseEraseResponse,
  fileCheckCsvFileName,
  fileCheckFindings,
  itemTone,
  loanEmiRows,
  monthRangeLabel,
  normalizeChecklists,
  obligationFindings,
  ordinal,
  pickDefaultChecklistId,
  safeHttpUrl,
  verdictTone,
} from './fileCheck';
import {
  NOT_READY_RESULT,
  READY_OBLIGATIONS_RESULT,
  READY_WITH_REVIEW_RESULT,
  USAGE_RESULT,
} from '../components/FileCheckPanel/fixtures';
import { ApiError } from './apiError';
import type { FileCheckApplicant } from '../types/fileCheck';

describe('normalizeChecklists', () => {
  it('accepts the engine list_checklists object', () => {
    const { checklists, defaultId } = normalizeChecklists({
      default_checklist: 'vlp_pl_sal',
      checklists: [
        { id: 'salaried_personal_loan', name: 'Personal Loan - Salaried' },
        { id: 'vlp_pl_sal', name: 'Varunika Loan Partners - PL - Salaried' },
      ],
    });
    expect(checklists.map((c) => c.id)).toEqual([
      'salaried_personal_loan',
      'vlp_pl_sal',
    ]);
    expect(defaultId).toBe('vlp_pl_sal');
  });

  it('keeps the checklist FOIR policy', () => {
    const foir = {
      value: 0.5,
      source: "Varunika Loans: 'Calculated at 50% FOIR'",
    };
    const { checklists } = normalizeChecklists([
      { id: 'vl_pl_sal', name: 'LS PL', foir },
      { id: 'plain', name: 'Plain', foir: null },
    ]);
    expect(checklists[0].foir).toEqual(foir);
    expect(checklists[1]).not.toHaveProperty('foir');
  });

  it('accepts a bare list with label instead of name and drops bad rows', () => {
    const { checklists, defaultId } = normalizeChecklists([
      { id: 'a', label: 'A list' },
      { id: 'a', name: 'duplicate' },
      { name: 'no id' },
      null,
    ]);
    expect(checklists).toEqual([
      {
        id: 'a',
        name: 'A list',
        product: null,
        applicant_type: null,
        description: null,
      },
    ]);
    expect(defaultId).toBeNull();
  });

  it('ignores a default that is not in the list', () => {
    expect(
      normalizeChecklists({ default_checklist: 'x', items: [{ id: 'a' }] })
        .defaultId,
    ).toBeNull();
  });
});

describe('pickDefaultChecklistId', () => {
  const list = [
    { id: 'vlp_hl_sal', name: 'HL', product: 'home_loan' },
    {
      id: 'vl_pl_sal',
      name: 'PL',
      product: 'personal_loan',
      applicant_type: 'salaried',
    },
    { id: 'salaried_personal_loan', name: 'Personal Loan - Salaried' },
  ];

  it('prefers the API default', () => {
    expect(pickDefaultChecklistId(list, 'vlp_hl_sal')).toBe('vlp_hl_sal');
  });

  it('falls back to the salaried personal-loan checklist', () => {
    expect(pickDefaultChecklistId(list, null)).toBe('salaried_personal_loan');
    expect(pickDefaultChecklistId(list.slice(0, 2))).toBe('vl_pl_sal');
    expect(pickDefaultChecklistId(list.slice(0, 1))).toBe('vlp_hl_sal');
    expect(pickDefaultChecklistId([])).toBe('');
  });
});

describe('tones', () => {
  it('maps verdicts without deciding them', () => {
    expect(verdictTone('READY')).toBe('ready');
    expect(verdictTone('NOT READY')).toBe('notReady');
    expect(verdictTone('not_ready')).toBe('notReady');
    expect(verdictTone('NEEDS REVIEW')).toBe('review');
    expect(verdictTone('NEEDS_REVIEW')).toBe('review');
    expect(verdictTone('SOMETHING ELSE')).toBe('unknown');
  });

  it('maps item and consistency statuses', () => {
    expect(itemTone('PRESENT')).toBe('ok');
    expect(itemTone('MISSING')).toBe('bad');
    expect(itemTone('MISSING', false)).toBe('muted');
    expect(itemTone('REVIEW')).toBe('review');
    expect(consistencyTone('MISMATCH')).toBe('bad');
    expect(consistencyTone('REVIEW')).toBe('review');
    expect(consistencyTone('N/A')).toBe('muted');
    expect(consistencyTone('INFO')).toBe('info');
  });
});

describe('CSV', () => {
  it('quotes commas, quotes and new lines and neutralises formulas', () => {
    expect(csvCell('plain')).toBe('plain');
    expect(csvCell('a, b')).toBe('"a, b"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell('line1\nline2')).toBe('"line1\nline2"');
    expect(csvCell('=HYPERLINK("x")')).toBe(`"'=HYPERLINK(""x"")"`);
    expect(csvCell('@sum')).toBe("'@sum");
    expect(csvCell(null)).toBe('');
  });

  it('has one row per finding', () => {
    const findings = fileCheckFindings(NOT_READY_RESULT);
    // 4 checklist items + 2 consistency checks + 1 unsupported document
    expect(findings).toHaveLength(7);
    expect(findings.map((f) => f.section)).toEqual([
      'Checklist',
      'Checklist',
      'Checklist',
      'Checklist',
      'Consistency',
      'Consistency',
      'Not checked',
    ]);
    expect(findings[3].item).toBe('Form-16 / ITR (optional)');
    expect(findings[6]).toMatchObject({
      applicant: '',
      item: 'statement.xlsx',
      status: 'UNSUPPORTED',
      documents: ['statement.xlsx'],
    });
  });

  it('is Excel-friendly UTF-8 with a BOM and CRLF rows', () => {
    const csv = buildFileCheckCsv(NOT_READY_RESULT);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    const lines = csv.slice(1).split('\r\n');
    expect(lines[0]).toBe(CSV_HEADERS.join(','));
    // header + 7 findings + 2 documents read + trailing empty string after
    // the final CRLF
    expect(lines).toHaveLength(11);
    expect(lines[10]).toBe('');
    expect(lines[2]).toBe(
      [
        'Amit Suresh Patil',
        'NOT READY',
        'Personal Loan - Salaried',
        'Checklist',
        'Salary slips (last 3 months)',
        'MISSING',
        '"missing Jun 2026, Jul 2026 slip(s); found: Aug 2026 (slip_aug.pdf)"',
        'slip_aug.pdf',
        // no usage on a finding row
        '',
        '',
        '',
        '',
      ].join(','),
    );
    // The applicant's documents follow its findings; the backend recorded
    // no usage for them, so the usage cells stay empty.
    expect(lines[8]).toBe(
      'Amit Suresh Patil,NOT READY,Personal Loan - Salaried,Documents,slip_aug.pdf,READ,salary slip; unverified: net_salary; usage not recorded,slip_aug.pdf,,,,',
    );
    expect(lines[9]).toMatch(/^,,Personal Loan - Salaried,Not checked,/);
    expect(csv).toContain('₹72,000');
    expect(csv).toContain('application.pdf; slip_aug.pdf');
  });

  it('keeps ₹ and Devanagari as UTF-8 bytes after the BOM', async () => {
    const csv = buildFileCheckCsv({
      ...NOT_READY_RESULT,
      applicants: [
        { ...NOT_READY_RESULT.applicants[0], applicant: 'अमित सुरेश पाटील' },
      ],
    });
    const bytes = new Uint8Array(
      await new Blob([csv], { type: 'text/csv;charset=utf-8' }).arrayBuffer(),
    );
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    const text = new TextDecoder('utf-8').decode(bytes.slice(3));
    expect(text).toContain('अमित सुरेश पाटील,NOT READY,');
    expect(text).toContain('₹72,000');
  });

  it('builds a safe file name', () => {
    expect(fileCheckCsvFileName(NOT_READY_RESULT)).toBe(
      'file-check_salaried-personal-loan_2026-09-28.csv',
    );
    expect(fileCheckCsvFileName(NOT_READY_RESULT, 'Amit S. Patil')).toBe(
      'file-check_salaried-personal-loan_amit-s-patil_2026-09-28.csv',
    );
  });
});

describe('obligations helpers', () => {
  it('formats days, month ranges and links', () => {
    expect([1, 2, 3, 4, 5, 11, 12, 13, 21, 22, 23, 31].map(ordinal)).toEqual([
      '1st',
      '2nd',
      '3rd',
      '4th',
      '5th',
      '11th',
      '12th',
      '13th',
      '21st',
      '22nd',
      '23rd',
      '31st',
    ]);
    expect(monthRangeLabel(['2026-08', '2026-03', '2026-05'])).toBe(
      'Mar 2026 – Aug 2026',
    );
    expect(monthRangeLabel(['2026-05'])).toBe('May 2026');
    expect(monthRangeLabel([])).toBe('');
    expect(
      safeHttpUrl('https://www.varunikaloanpartners.example/calculator'),
    ).toBe('https://www.varunikaloanpartners.example/calculator');
    expect(safeHttpUrl(['javascript', 'alert(1)'].join(':'))).toBeNull();
    expect(safeHttpUrl(42)).toBeNull();
  });

  it('pairs each bank EMI with the declared EMI it matched', () => {
    const rows = loanEmiRows(
      READY_WITH_REVIEW_RESULT.applicants[0].obligations ?? {},
    );
    expect(rows.map((r) => [r.debit.payee, r.status])).toEqual([
      ['MULSHI AUTO FINANCE / CAR LOAN EMI', 'matched'],
      ['KESARI FINSERV / PERSONAL LOAN EMI', 'not_declared'],
    ]);
    expect(rows[0].declared?.lender).toBe('Mulshi Auto Finance Ltd (sample)');
    // Declared EMIs not extracted: an undeclared-looking EMI is "not checked".
    expect(
      loanEmiRows({
        declared_available: false,
        fixed_loan_emis: [{ payee: 'X', amount: 1 }],
      })[0].status,
    ).toBe('not_checked');
  });
});

describe('CSV obligations rows', () => {
  it('adds loan EMIs, fixed debits, totals and FOIR after the checks', () => {
    const a = READY_OBLIGATIONS_RESULT.applicants[0];
    const rows = obligationFindings(a, 'Personal Loan - Salaried');
    expect(rows.map((r) => [r.item, r.status])).toEqual([
      ['Loan EMI – MULSHI AUTO FINANCE / CAR LOAN EMI', 'MATCHED'],
      ['Fixed debit – RENT / VASANT JOSHI', 'FIXED'],
      ['Fixed debit – SIP / SAMPLE ASSET MGMT MF', 'FIXED'],
      ['Fixed debit – MOBILE & BROADBAND', 'FIXED'],
      ['Monthly obligations (totals)', 'INFO'],
      ['FOIR (indicative)', 'OK'],
    ]);
    expect(rows.every((r) => r.section === 'Obligations')).toBe(true);
    expect(rows[0].detail).toBe(
      '₹8,200, on the 5th, 6 of 6 months (Mar 2026 – Aug 2026), ACH; declared ₹8,200 Mulshi Auto Finance Ltd (sample) (Car loan) [01_loan_application_form.pdf]',
    );
    expect(rows[0].documents).toEqual([
      '06_bank_statement_2026-03_to_2026-08.pdf',
    ]);
    expect(rows[1].detail).toBe(
      'rent: ₹18,000, on the 3rd, 6 of 6 months (Mar 2026 – Aug 2026)',
    );
    expect(rows[4].detail).toBe(
      'loan EMIs ₹8,200; other fixed ₹24,299; fixed monthly ₹32,499; variable monthly average ₹13,265.5',
    );
    expect(rows[5].detail).toBe(
      "FOIR 9.9% vs limit 70%; max new EMI ₹49,550; limit source: Varunika Loan Partners calculator, eligibility tab: 'FOIR 70%' (Max EMI = 0.7 × net monthly income − existing EMIs); the Varunika Loans calculator also shows 'FOIR 70%'; indicative — the lender's policy decides",
    );
  });

  it('marks an undeclared bank EMI and exports the new consistency rows', () => {
    const findings = fileCheckFindings(READY_WITH_REVIEW_RESULT);
    const kesari = findings.find(
      (f) => f.item === 'Loan EMI – KESARI FINSERV / PERSONAL LOAN EMI',
    );
    expect(kesari).toMatchObject({
      section: 'Obligations',
      status: 'NOT DECLARED',
    });
    expect(kesari?.detail).toContain('not on the loan application');
    const consistency = findings.filter((f) => f.section === 'Consistency');
    expect(consistency.map((f) => [f.item, f.status])).toContainEqual([
      'Declared EMIs vs bank debits',
      'REVIEW',
    ]);
    expect(consistency.map((f) => [f.item, f.status])).toContainEqual([
      'FOIR (indicative)',
      'OK',
    ]);
    // Obligations rows follow the applicant's consistency rows.
    const sections = findings.map((f) => f.section);
    expect(sections.lastIndexOf('Consistency')).toBeLessThan(
      sections.indexOf('Obligations'),
    );
    const csv = buildFileCheckCsv(READY_WITH_REVIEW_RESULT);
    expect(csv).toContain(
      'Rahul Vijay Deshmukh,READY,Personal Loan - Salaried,Obligations,Loan EMI – KESARI FINSERV / PERSONAL LOAN EMI,NOT DECLARED,',
    );
    expect(csv).toContain('FOIR 17.8% vs limit 70%; max new EMI ₹43,050');
  });
});

describe('CSV usage columns', () => {
  it('adds model, tokens and cost per document read and a total row', () => {
    // Only the facts extraction step is recorded: the headers say so.
    expect(CSV_HEADERS.slice(-4)).toEqual([
      'Facts extraction model',
      'Facts extraction input tokens',
      'Facts extraction output tokens',
      'Facts extraction cost (USD)',
    ]);
    const lines = buildFileCheckCsv(USAGE_RESULT).slice(1).split('\r\n');
    expect(lines[0].split(',')).toHaveLength(12);
    const docs = lines.filter((l) => l.split(',')[3] === 'Documents');
    expect(docs).toEqual([
      'Amit Suresh Patil,NOT READY,Personal Loan - Salaried,Documents,application.pdf,READ,loan application,application.pdf,global.amazon.nova-2-lite-v1:0,12345,678,0.006321',
      'Amit Suresh Patil,NOT READY,Personal Loan - Salaried,Documents,slip_aug.pdf,READ,salary slip; unverified: net_salary; usage not recorded,slip_aug.pdf,,,,',
      'Amit Suresh Patil,NOT READY,Personal Loan - Salaried,Documents,Facts extraction cost (total),INFO,"file-check facts step only, other analysis steps not included; 1 of 2 documents with recorded usage",,,12345,678,0.006321',
    ]);
    // Documents come after the applicant's findings, before "Not checked".
    const sections = lines.map((l) => l.split(',')[3]);
    expect(sections.indexOf('Documents')).toBeGreaterThan(
      sections.lastIndexOf('Consistency'),
    );
    expect(sections.lastIndexOf('Documents')).toBeLessThan(
      sections.indexOf('Not checked'),
    );
  });

  it('writes no $0 total when no document has recorded usage', () => {
    // Documents analysed before costs were recorded (every document of 591d5a7).
    const csv = buildFileCheckCsv({
      ...USAGE_RESULT,
      applicants: [
        {
          ...USAGE_RESULT.applicants[0],
          documents: USAGE_RESULT.applicants[0].documents.map((d) => ({
            ...d,
            usage: null,
          })),
          usage_total: {
            input_tokens: 0,
            output_tokens: 0,
            cost_usd: 0,
            documents_with_usage: 0,
            documents_total: 2,
          },
        },
      ],
    });
    const total = csv
      .split('\r\n')
      .find((l) => l.includes('Facts extraction cost (total)'));
    expect(total).toBe(
      'Amit Suresh Patil,NOT READY,Personal Loan - Salaried,Documents,Facts extraction cost (total),INFO,not recorded (analysed before costs were recorded); 0 of 2 documents with recorded usage,,,,,',
    );
  });

  it('ignores malformed usage and writes plain numbers', () => {
    expect(
      documentUsage({
        usage: {
          model_id: 'm',
          input_tokens: Number.NaN,
          output_tokens: 1,
          cost_usd: 0,
        },
      }),
    ).toBeNull();
    expect(documentUsage({ usage: null })).toBeNull();
    expect(applicantUsageTotal({})).toBeNull();
    expect(csvUsd(0.1 + 0.2)).toBe('0.3');
    expect(csvUsd(0.00000049)).toBe('0');
    expect(csvUsd(0.0123456789)).toBe('0.012346');
  });
});

describe('erase helpers', () => {
  it('requires the name exactly as shown', () => {
    expect(eraseConfirmMatches('Amit Suresh Patil', 'Amit Suresh Patil')).toBe(
      true,
    );
    // Only spacing the page does not show is forgiven.
    expect(
      eraseConfirmMatches('  Amit  Suresh Patil ', 'Amit Suresh Patil'),
    ).toBe(true);
    expect(eraseConfirmMatches('amit suresh patil', 'Amit Suresh Patil')).toBe(
      false,
    );
    expect(eraseConfirmMatches('Amit Patil', 'Amit Suresh Patil')).toBe(false);
    expect(eraseConfirmMatches('', '')).toBe(false);
  });

  it('validates the erase response', () => {
    expect(
      parseEraseResponse(
        {
          applicant: 'Amit Suresh Patil',
          documents_deleted: [{ document_id: 'd1', name: 'application.pdf' }],
          failed: [{ document_id: 'd2', name: 'slip_aug.pdf', error: 'x' }],
          delivery_log_redacted: 2,
          not_erased: ['Verdicts the CRM webhook already delivered', 7],
          erased_at: '2026-09-30T10:00:00+00:00',
        },
        'Amit',
      ),
    ).toEqual({
      applicant: 'Amit Suresh Patil',
      documents_deleted: [{ document_id: 'd1', name: 'application.pdf' }],
      failed: [{ document_id: 'd2', name: 'slip_aug.pdf', error: 'x' }],
      delivery_log_redacted: 2,
      not_erased: ['Verdicts the CRM webhook already delivered'],
      erased_at: '2026-09-30T10:00:00+00:00',
    });
    // null: the name could not be removed from the delivery log.
    expect(
      parseEraseResponse(
        { documents_deleted: [], failed: [], delivery_log_redacted: null },
        'Amit',
      ).delivery_log_redacted,
    ).toBeNull();
    expect(() => parseEraseResponse({ detail: 'x' }, 'Amit')).toThrow();
    expect(maskPan('ckrpk7314m')).toBe('XXXXXX314M');
    expect(maskPan(null)).toBeNull();
  });

  it('builds the erase request from the verdict’s applicant', () => {
    const applicant = {
      applicant: 'Amit Suresh Patil',
      pan: ' ABCPP1234K ',
      documents: [
        { document_id: 'd1', document_name: 'application.pdf' },
        { document_id: 'd2', document_name: 'slip_aug.pdf' },
        { document_id: 'd1', document_name: 'application.pdf' },
        { document_id: null, document_name: 'old-engine.pdf' },
      ],
    } as FileCheckApplicant;
    expect(eraseRequestBody(applicant, 'Amit Suresh Patil')).toEqual({
      applicant: 'ABCPP1234K',
      confirm: 'Amit Suresh Patil',
      document_ids: ['d1', 'd2'],
    });
    expect(
      eraseRequestBody({ ...applicant, pan: null }, 'Amit Suresh Patil')
        .applicant,
    ).toBe('Amit Suresh Patil');
  });

  it('tells a refused erase from one that may have run', () => {
    // Nothing deleted: every 4xx, and the API's own errors before deleting.
    for (const error of [
      new ApiError(400),
      new ApiError(404, 'Applicant not found in this project'),
      new ApiError(409, "The applicant's documents changed since the check"),
      new ApiError(422, [{ msg: 'List should have at least 1 item' }]),
      new ApiError(503, 'File check is not configured'),
      new ApiError(502, 'Applicant lookup failed: Unknown tool'),
      new ApiError(502, 'Applicant lookup returned an unexpected response'),
    ]) {
      expect([error.status, eraseMayBeIncomplete(error)]).toEqual([
        error.status,
        false,
      ]);
    }
    // No answer, or a failure that may come after deleting started.
    for (const error of [
      new TypeError('Failed to fetch'),
      new Error('unexpected response from the erase service'),
      new ApiError(500, 'Internal Server Error'),
      new ApiError(503, { message: 'Service Unavailable' }),
      new ApiError(504),
    ]) {
      expect(eraseMayBeIncomplete(error)).toBe(true);
    }
  });
});
