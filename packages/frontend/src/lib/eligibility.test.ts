// @vitest-environment node
import {
  SOURCED_FIELDS,
  addOtherIncome,
  addOtherIncomeRow,
  addTradeline,
  addTradelineRow,
  calculateRequestBody,
  documentRowsMissing,
  eligibilityApplicant,
  emptyInputs,
  enquiriesOutOfOrder,
  fillFromDocuments,
  fillableFields,
  formatFoir,
  formatIndianNumber,
  formatRoi,
  formatRupees,
  inputsKey,
  inputsRequestBody,
  isFutureDate,
  isMaskedPan,
  lenderTone,
  localToday,
  mobileLooksValid,
  normalizeInputs,
  otherDocumentValue,
  parseAmount,
  parseCalculateResponse,
  parseCount,
  parseInputsResponse,
  parseLenders,
  parseLoginResponse,
  removeTradeline,
  roiPercent,
  rowFromDocument,
  setCibil,
  setEnquiries,
  setFieldValue,
  setLoan,
  setProfile,
  setTradelineAction,
  sourceOf,
  stillNeeded,
  updateOtherIncome,
  updateTradeline,
  webhookOutcome,
} from './eligibility';
import {
  FULL_DRAFT_RESPONSE,
  LENDERS_RESPONSE,
  LOGIN_NOTIFIED,
  LOGIN_NO_WEBHOOK,
  LOGIN_WEBHOOK_FAILED,
  NOT_READY_RESULT_RESPONSE,
  PREFILLED_INPUTS_RESPONSE,
  SAVED_INPUTS_RESPONSE,
  WORKED_EXAMPLE_RESPONSE,
} from '../components/EligibilityPanel/fixtures';

const SNEHA = 'Sneha Anil Kulkarni';

describe('Indian number formatting', () => {
  it('groups rupees in lakhs and crores', () => {
    expect(formatRupees(2058000)).toBe('₹20,58,000');
    expect(formatRupees(1500000)).toBe('₹15,00,000');
    expect(formatRupees(98000)).toBe('₹98,000');
    expect(formatRupees(12345678)).toBe('₹1,23,45,678');
    expect(formatRupees(0)).toBe('₹0');
    expect(formatRupees(null)).toBe('–');
    expect(formatRupees(Number.NaN)).toBe('–');
  });

  it('shows paise only when there are any, rounded to 2 places', () => {
    expect(formatRupees(39172.13458345223)).toBe('₹39,172.13');
    expect(formatRupees(33366.67152735264)).toBe('₹33,366.67');
    expect(formatRupees(2174.242307264313)).toBe('₹2,174.24');
    expect(formatRupees(2465226.6134698153)).toBe('₹24,65,226.61');
    expect(formatIndianNumber(2465226.6134698153)).toBe('24,65,226.61');
    expect(formatIndianNumber(2058000)).toBe('20,58,000');
    expect(formatIndianNumber(null)).toBe('');
  });

  it('always shows both paise digits of an amount with paise', () => {
    expect(formatRupees(39172.1)).toBe('₹39,172.10');
    expect(formatRupees(2465226.6)).toBe('₹24,65,226.60');
    expect(formatRupees(1775331.11)).toBe('₹17,75,331.11');
    expect(formatRupees(0.5)).toBe('₹0.50');
    // Paise that round away are not shown.
    expect(formatRupees(39172.004)).toBe('₹39,172');
    expect(formatIndianNumber(98000.5)).toBe('98,000.50');
    expect(formatIndianNumber(98000)).toBe('98,000');
  });

  it('reads typed amounts with or without grouping and the rupee sign', () => {
    expect(parseAmount('20,58,000')).toBe(2058000);
    expect(parseAmount('₹ 98,000')).toBe(98000);
    expect(parseAmount('Rs. 15000')).toBe(15000);
    expect(parseAmount('1,00,000.50')).toBe(100000.5);
    expect(parseAmount('  ')).toBeNull();
    expect(parseAmount('12abc')).toBeNaN();
    expect(parseAmount('-500')).toBeNaN();
    expect(parseCount('60')).toBe(60);
    expect(parseCount('6.5')).toBeNaN();
    expect(parseCount('')).toBeNull();
  });

  it('shows the ROI as a percent whether sent as 11 or 0.11', () => {
    expect(formatRoi(11)).toBe('11%');
    expect(formatRoi(0.11)).toBe('11%');
    expect(formatRoi(10.75)).toBe('10.75%');
    expect(formatRoi(null)).toBe('–');
    expect(roiPercent(0.12)).toBe(12);
  });

  it('shows a FOIR (a fraction in the API) as a percent, 100% included', () => {
    expect(formatFoir(0.7)).toBe('70%');
    expect(formatFoir(0.655)).toBe('65.5%');
    expect(formatFoir(1)).toBe('100%');
    expect(formatFoir(null)).toBe('–');
  });
});

describe('inputs', () => {
  it('reads a draft: the fields the documents filled, a masked PAN', () => {
    const parsed = parseInputsResponse(PREFILLED_INPUTS_RESPONSE, SNEHA);
    expect(parsed.saved).toBe(false);
    expect(parsed.fromDocuments).toEqual([
      'name',
      'pan',
      'company',
      'employment_type',
      'net_income',
    ]);
    expect(parsed.prefill).toMatchObject({
      available: true,
      income_source: 'verified: salary slips, median net pay',
    });
    expect(parsed.inputs.profile).toMatchObject({
      name: SNEHA,
      pan: 'XXXXXX314M',
      company: 'Konkan Softworks Pvt Ltd',
      employment_type: 'private_limited',
      net_income: 98000,
    });
    expect(isMaskedPan(parsed.inputs.profile.pan)).toBe(true);
    expect(isMaskedPan('ABCDE1234F')).toBe(false);
  });

  it('reads saved inputs and when they are deleted', () => {
    const parsed = parseInputsResponse(SAVED_INPUTS_RESPONSE, SNEHA);
    expect(parsed.saved).toBe(true);
    expect(parsed.applicant).toBe('CKRPK7314M');
    expect(parsed.expiresAt).toBe('2026-10-07T10:00:00.000000+00:00');
    expect(parsed.inputs.cibil.tradelines.map((r) => r.action)).toEqual([
      'obligate',
      'close',
    ]);
    expect(parsed.inputs.profile.other_income).toMatchObject([
      { type: 'bonus', frequency: 'yearly', agreement: null, amount: 60000 },
      {
        type: 'rented',
        agreement: 'registered',
        frequency: null,
        amount: 12000,
      },
    ]);
  });

  it('rejects a response that is not the contract', () => {
    expect(() => parseInputsResponse({ detail: 'x' }, SNEHA)).toThrow();
    expect(() => parseInputsResponse(null, SNEHA)).toThrow();
  });

  it('builds the PUT body: trimmed text, no row keys, NaN typing as null', () => {
    let inputs = normalizeInputs(SAVED_INPUTS_RESPONSE.inputs);
    inputs = updateTradeline(inputs, 0, { lender: '  Axis Bank ', emi: NaN });
    const body = inputsRequestBody('CKRPK7314M', inputs);
    expect(Object.keys(body)).toEqual([
      'applicant',
      'profile',
      'cibil',
      'loan',
    ]);
    expect(body.applicant).toBe('CKRPK7314M');
    expect(body.cibil.tradelines[0]).toEqual({
      loan_type: 'personal',
      lender: 'Axis Bank',
      sanction_amount: 600000,
      outstanding: 410000,
      emi: null,
      status: 'active',
      action: 'obligate',
      account_number: 'PL00001234',
      emis_paid: 14,
      emis_pending: 34,
      open_date: '2025-07-05',
      last_payment_date: '2026-09-05',
    });
    // Optional details that are empty are left out.
    expect(body.cibil.tradelines[1]).toEqual({
      loan_type: 'credit_card',
      lender: 'Kotak Mahindra Bank',
      sanction_amount: 150000,
      outstanding: 18000,
      emi: 0,
      status: 'active',
      action: 'close',
    });
    expect(JSON.stringify(body)).not.toContain('"key"');
    expect(body.profile.pan).toBe('XXXXXX314M');
  });

  it('keeps the source of a bureau pull or a bank-statement suggestion', () => {
    const inputs = normalizeInputs({
      cibil: {
        source: 'bureau',
        report_date: '2026-09-29',
        tradelines: [
          {
            loan_type: 'car',
            emi: 8200,
            action: 'obligate',
            source: 'bank_statement',
          },
        ],
      },
    });
    const body = inputsRequestBody(SNEHA, inputs);
    expect(body.cibil.source).toBe('bureau');
    expect(body.cibil.report_date).toBe('2026-09-29');
    expect(body.cibil.tradelines[0].source).toBe('bank_statement');
    // Manual is the API's default and is not sent.
    const manual = inputsRequestBody(SNEHA, addTradeline(emptyInputs()));
    expect(manual.cibil).not.toHaveProperty('source');
    expect(manual.cibil.tradelines[0]).not.toHaveProperty('source');
  });

  it('asks the API by PAN when the verdict has one, else by name', () => {
    expect(
      eligibilityApplicant({ applicant: SNEHA, pan: ' ckrpk7314m ' }),
    ).toEqual({ id: 'CKRPK7314M', name: SNEHA, pan: 'CKRPK7314M' });
    expect(eligibilityApplicant({ applicant: SNEHA, pan: null })).toEqual({
      id: SNEHA,
      name: SNEHA,
      pan: null,
    });
  });

  it('dates are checked against the local day', () => {
    const now = new Date(2026, 8, 30, 23, 30);
    expect(localToday(now)).toBe('2026-09-30');
    expect(isFutureDate('2026-09-30', now)).toBe(false);
    expect(isFutureDate('2026-10-01', now)).toBe(true);
    expect(isFutureDate(null, now)).toBe(false);
  });

  it('checks the formats the API checks', () => {
    expect(mobileLooksValid('+91 98200 12345')).toBe(true);
    expect(mobileLooksValid('09820012345')).toBe(true);
    expect(mobileLooksValid('12345')).toBe(false);
    expect(enquiriesOutOfOrder({ d30: 1, d60: 2, d90: 3, d120: 4 })).toBeNull();
    expect(enquiriesOutOfOrder({ d30: 3, d60: null, d90: 2, d120: 4 })).toEqual(
      ['d30', 'd90'],
    );
  });
});

describe('BT / Obligate / Close', () => {
  const inputs = normalizeInputs(SAVED_INPUTS_RESPONSE.inputs);
  const actions = (body: ReturnType<typeof calculateRequestBody>) =>
    body.inputs?.cibil.tradelines.map((r) => r.action);

  it('a toggle changes only its own loan in the calculate request', () => {
    expect(actions(calculateRequestBody(SNEHA, inputs))).toEqual([
      'obligate',
      'close',
    ]);
    const bt = setTradelineAction(inputs, 0, 'bt');
    expect(actions(calculateRequestBody(SNEHA, bt))).toEqual(['bt', 'close']);
    const both = setTradelineAction(bt, 1, 'obligate');
    expect(actions(calculateRequestBody(SNEHA, both))).toEqual([
      'bt',
      'obligate',
    ]);
    // The original inputs are not changed.
    expect(actions(calculateRequestBody(SNEHA, inputs))).toEqual([
      'obligate',
      'close',
    ]);
  });

  it('a toggle makes the result stale (the inputs key changes)', () => {
    const key = inputsKey(inputs);
    expect(inputsKey(setTradelineAction(inputs, 0, 'close'))).not.toBe(key);
    expect(inputsKey(setTradelineAction(inputs, 0, 'obligate'))).toBe(key);
  });

  it('adds loans as Obligate and removes by position', () => {
    const added = addTradeline(emptyInputs());
    expect(added.cibil.tradelines).toHaveLength(1);
    expect(added.cibil.tradelines[0]).toMatchObject({
      action: 'obligate',
      status: 'active',
      loan_type: 'personal',
    });
    const removed = removeTradeline(inputs, 0);
    expect(removed.cibil.tradelines.map((r) => r.lender)).toEqual([
      'Kotak Mahindra Bank',
    ]);
  });

  it('other income rows keep only their type’s own fields', () => {
    let i = addOtherIncome(emptyInputs(), 'rented');
    expect(i.profile.other_income[0]).toMatchObject({
      type: 'rented',
      agreement: 'registered',
      frequency: null,
    });
    i = updateOtherIncome(i, 0, { amount: 20000 });
    i = updateOtherIncome(i, 0, { type: 'incentive' });
    expect(i.profile.other_income[0]).toMatchObject({
      type: 'incentive',
      frequency: 'yearly',
      agreement: null,
      amount: 20000,
    });
    expect(inputsRequestBody(SNEHA, i).profile.other_income).toEqual([
      {
        type: 'incentive',
        amount: 20000,
        frequency: 'yearly',
        agreement: null,
      },
    ]);
  });
});

describe('results', () => {
  it('reads the worked example as sent', () => {
    const result = parseCalculateResponse(WORKED_EXAMPLE_RESPONSE, SNEHA);
    expect(result.best_lender).toBe('ICICI Bank');
    expect(result.income_considered).toBe(98000);
    expect(result.obligations).toBe(15000);
    expect(result.sample).toBe(true);
    const icici = result.per_lender[0];
    expect(icici).toMatchObject({
      lender: 'ICICI Bank',
      lender_id: 'icici_bank',
      status: 'eligible',
      eligible_amount: 2058000,
      tenure_months: 72,
      calculation_tenure_months: 60,
      roi: 11,
      foir: 0.7,
      multiplier: 21,
      multiplier_eligibility: 2058000,
    });
    expect(formatRupees(icici.per_lakh_emi)).toBe('₹2,174.24');
    expect(formatRupees(icici.foir_eligibility)).toBe('₹24,65,226.61');
    expect(formatRupees(icici.emi)).toBe('₹39,172.13');
    expect(result.per_lender.map((r) => lenderTone(r.status))).toEqual([
      'eligible',
      'eligible',
      'notServiceable',
      'notEligible',
    ]);
    expect(result.counted_obligations).toEqual([
      {
        index: 1,
        lender: 'Axis Bank',
        loan_type: 'personal',
        emi: 15000,
        source: 'tradeline',
        flag: null,
      },
    ]);
  });

  it('drops a best lender that is not in the list', () => {
    const result = parseCalculateResponse(
      { ...WORKED_EXAMPLE_RESPONSE, best_lender: 'Some Bank' },
      SNEHA,
    );
    expect(result.best_lender).toBeNull();
    expect(() => parseCalculateResponse({ error: 'x' }, SNEHA)).toThrow();
  });

  it('tells a notified CRM webhook from a failed one and from none', () => {
    const notified = parseLoginResponse(LOGIN_NOTIFIED);
    expect(notified.status).toBe('recorded');
    expect(webhookOutcome(notified)).toBe('notified');
    expect(notified.delivery?.http_status).toBe(200);
    expect(webhookOutcome(parseLoginResponse(LOGIN_NO_WEBHOOK))).toBe(
      'notSent',
    );
    expect(webhookOutcome(parseLoginResponse(LOGIN_WEBHOOK_FAILED))).toBe(
      'failed',
    );
    expect(() => parseLoginResponse({})).toThrow();
  });

  it('reads the lender policies', () => {
    const parsed = parseLenders(LENDERS_RESPONSE);
    expect(parsed.sample).toBe(true);
    expect(
      parsed.lenders.map((l) => [l.id, l.name, l.roi, l.max_tenure_months]),
    ).toEqual([
      ['hdfc_bank', 'HDFC Bank', 12, 60],
      ['icici_bank', 'ICICI Bank', 11, 72],
    ]);
    expect(parsed.lenders[1].unlisted_company).toEqual({
      accepted: true,
      foir: 0.5,
      multiplier: 10,
    });
    expect(() => parseLenders({})).toThrow();
  });
});

describe('document values (the auto-fill)', () => {
  const draft = parseInputsResponse(FULL_DRAFT_RESPONSE, 'BQXPD4821K');
  const { inputs, sources } = draft;

  it('reads every field a document filled, with its file and page', () => {
    expect(draft.fromDocuments).toEqual(FULL_DRAFT_RESPONSE.from_documents);
    expect(Object.keys(sources)).toEqual(
      expect.arrayContaining([
        ...SOURCED_FIELDS.filter((f) => f !== 'loan_amount'),
        'report',
      ]),
    );
    // Rows are not fields: they come as rows, each with its document.
    expect(sources).not.toHaveProperty('other_income');
    expect(sources).not.toHaveProperty('tradelines');
    expect(sources.mobile).toEqual({
      source: 'document',
      value: '9000000101',
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: 1,
          doc_type: 'loan_application',
        },
      ],
      detail: null,
      unverified: false,
    });
    expect(
      inputs.profile.other_income.map((r) => [
        r.type,
        r.document?.documents[0].file,
      ]),
    ).toEqual([
      ['rented', '08_rent_agreement_flat_12.pdf'],
      ['bonus', '05_salary_slip_2026-08.pdf'],
      ['incentive', '03_salary_slip_2026-06.pdf'],
    ]);
    expect(
      inputs.cibil.tradelines.map((r) => [
        r.action,
        r.source,
        r.document?.documents[0].page,
      ]),
    ).toEqual([
      ['obligate', 'credit_report', 2],
      ['obligate', 'credit_report', 2],
      ['close', 'credit_report', 3],
    ]);
    // The CIBIL block carries its own sources (the CIBIL tab gets only it).
    expect(inputs.cibil.sources?.score?.value).toBe(771);
    expect(inputs.cibil.sources?.report?.detail).toBe('CIBIL report');
    expect(inputs.cibil.sources?.tradelines).toHaveLength(3);
    expect(draft.documentRows.other_income).toHaveLength(3);
    expect(draft.stillNeeded).toEqual([
      {
        field: 'loan_amount',
        required: false,
        fromDocuments: false,
        tab: 'profile',
      },
      {
        field: 'tradelines.2.emi',
        required: true,
        fromDocuments: false,
        tab: 'cibil',
        loan: { number: 2, lender: null, loanType: null },
      },
    ]);
  });

  it('never sends the sources back; rows keep their credit-report source', () => {
    const body = inputsRequestBody('BQXPD4821K', inputs);
    const json = JSON.stringify(body);
    expect(json).not.toContain('"document"');
    expect(json).not.toContain('"sources"');
    expect(json).not.toContain('"documents"');
    expect(body.cibil).toMatchObject({
      source: 'credit_report',
      report_date: '2026-09-20',
      score: 771,
    });
    expect(body.cibil.tradelines.map((r) => r.source)).toEqual([
      'credit_report',
      'credit_report',
      'credit_report',
    ]);
    // Only the last 4 of an account number ever reach the page.
    expect(body.cibil.tradelines[0].account_number).toBe('XXXX4410');
  });

  it('computes the same still-needed list as the backend', () => {
    const fields = (items: { field: string; required: boolean }[]) =>
      items.map((i) => [i.field, i.required]);
    expect(fields(stillNeeded(inputs, sources))).toEqual(
      fields(FULL_DRAFT_RESPONSE.still_needed),
    );
    // Without documents: every field, those every lender needs marked.
    const empty = stillNeeded(emptyInputs());
    expect(empty.filter((i) => i.required).map((i) => i.field)).toEqual([
      'pincode',
      'company',
      'employment_type',
      'net_income',
      'score',
      'enquiries.d90',
    ]);
    expect(empty).toHaveLength(18);
    // Typing a value takes it off the list; the CIBIL tab sees its own part.
    const typed = setLoan(inputs, { amount: 800000 });
    expect(stillNeeded(typed, sources).map((i) => i.field)).toEqual([
      'tradelines.2.emi',
    ]);
    expect(stillNeeded(typed, sources, 'profile')).toEqual([]);
    expect(
      stillNeeded(updateTradeline(typed, 1, { emi: 900 }), sources),
    ).toEqual([]);
    // A loan marked BT needs its outstanding instead.
    const bt = updateTradeline(setTradelineAction(typed, 0, 'bt'), 0, {
      outstanding: null,
    });
    expect(stillNeeded(bt, sources, 'cibil')[0]).toMatchObject({
      field: 'tradelines.1.outstanding',
      fromDocuments: true,
      loan: { number: 1, lender: 'Mulshi Auto Finance Ltd (sample)' },
    });
  });

  it('shows a source only while the field holds the documents value', () => {
    expect(sourceOf(inputs, sources, 'mobile')).toBe(sources.mobile);
    const typed = setProfile(inputs, { mobile: '9820012345' });
    expect(sourceOf(typed, sources, 'mobile')).toBeNull();
    // The documents' value stays next to the typed one, never put in by itself.
    expect(otherDocumentValue(typed, sources, 'mobile')).toBe(sources.mobile);
    expect(typed.profile.mobile).toBe('9820012345');
    // Same value, other spelling: still the documents' value.
    expect(
      sourceOf(
        setProfile(inputs, {
          mobile: '+91 90000 00101',
          name: 'RAHUL  VIJAY DESHMUKH',
          pan: 'BQXPD4821K',
        }),
        sources,
        'name',
      ),
    ).toBe(sources.name);
    const full = setProfile(inputs, { pan: 'BQXPD4821K' });
    expect(sourceOf(full, sources, 'pan')).toBe(sources.pan);
    expect(
      sourceOf(setProfile(inputs, { pan: 'BQXPD4822K' }), sources, 'pan'),
    ).toBeNull();
    expect(sourceOf(inputs, sources, 'enquiries')).toBe(sources.enquiries);
    expect(
      sourceOf(setEnquiries(inputs, { d90: 2 }), sources, 'enquiries'),
    ).toBeNull();
  });

  it('fills only the empty fields from the documents', () => {
    // Saved inputs typed by hand: a mobile, an empty pincode and CIBIL block.
    let saved = normalizeInputs(SAVED_INPUTS_RESPONSE.inputs);
    saved = setProfile(saved, { mobile: '9820012345', pincode: null });
    saved = setCibil(saved, {
      score: null,
      enquiries: { d30: null, d60: null, d90: null, d120: null },
    });
    // The typed mobile and tenure are not empty: not filled.
    expect(fillableFields(saved, sources)).toEqual([
      'dob',
      'pincode',
      'current_address',
      'permanent_address',
      'score',
      'enquiries',
    ]);
    const filled = fillFromDocuments(saved, sources);
    // Typed values win.
    expect(filled.profile).toMatchObject({
      mobile: '9820012345',
      name: 'Sneha Anil Kulkarni',
      net_income: 98000,
      pincode: '400606',
      dob: '1992-02-14',
    });
    expect(filled.loan).toEqual(saved.loan);
    expect(filled.cibil).toMatchObject({
      score: 771,
      enquiries: { d30: 0, d60: 1, d90: 1, d120: 2 },
      source: 'credit_report',
      report_date: '2026-09-20',
    });
    // Rows are offered, not added: the typed loans stay as they are.
    expect(filled.cibil.tradelines).toEqual(saved.cibil.tradelines);
    // Enquiries are taken whole: one typed window keeps all four as typed.
    const one = setEnquiries(saved, { d90: 4 });
    expect(fillFromDocuments(one, sources).cibil.enquiries).toEqual({
      d30: null,
      d60: null,
      d90: 4,
      d120: null,
    });
    expect(fillFromDocuments(filled, sources)).toEqual(filled);
  });

  it('tells a document row from an edited one and offers the missing ones', () => {
    const [car] = inputs.cibil.tradelines;
    expect(rowFromDocument(car)).toBe(true);
    // BT / Obligate / Close is the user's choice: the data is still the report's.
    expect(rowFromDocument({ ...car, action: 'bt' })).toBe(true);
    expect(rowFromDocument({ ...car, emi: 8000 })).toBe(false);
    expect(rowFromDocument(inputs.profile.other_income[1])).toBe(true);
    expect(
      rowFromDocument({
        ...inputs.profile.other_income[1],
        frequency: 'monthly',
      }),
    ).toBe(false);
    // A draft holds every document row.
    expect(
      documentRowsMissing(
        inputs.cibil.tradelines,
        draft.documentRows.tradelines,
      ),
    ).toEqual([]);
    // Typed loans: the report's three are offered; an added one is no more.
    const typed = normalizeInputs(SAVED_INPUTS_RESPONSE.inputs);
    const missing = documentRowsMissing(
      typed.cibil.tradelines,
      draft.documentRows.tradelines,
    );
    expect(missing.map((r) => r.lender)).toEqual([
      'Mulshi Auto Finance Ltd (sample)',
      'Sample Bank Card (sample)',
      'Deccan Consumer Finance (sample)',
    ]);
    const added = addTradelineRow(typed, missing[0]);
    expect(added.cibil.tradelines).toHaveLength(3);
    expect(added.cibil.tradelines[2].key).not.toBe(missing[0].key);
    expect(
      documentRowsMissing(
        added.cibil.tradelines,
        draft.documentRows.tradelines,
      ),
    ).toHaveLength(2);
    const income = addOtherIncomeRow(
      emptyInputs(),
      draft.documentRows.other_income[0],
    );
    expect(inputsRequestBody('x', income).profile.other_income).toEqual([
      {
        type: 'rented',
        amount: 12000,
        frequency: null,
        agreement: 'registered',
      },
    ]);
  });

  it('puts the documents value in when asked (Use)', () => {
    const typed = setProfile(inputs, { mobile: '9820012345' });
    expect(
      setFieldValue(typed, 'mobile', sources.mobile?.value).profile.mobile,
    ).toBe('9000000101');
    expect(
      setFieldValue(emptyInputs(), 'tenure_months', 48).loan.tenure_months,
    ).toBe(48);
    expect(
      setFieldValue(emptyInputs(), 'enquiries', sources.enquiries?.value).cibil
        .enquiries,
    ).toEqual({ d30: 0, d60: 1, d90: 1, d120: 2 });
  });

  it('reads the file check of a calculation', () => {
    const result = parseCalculateResponse(NOT_READY_RESULT_RESPONSE, SNEHA);
    expect(result.file_check).toMatchObject({
      used: true,
      verdict: 'NOT READY',
      ready: false,
    });
    expect(result.file_check?.issues).toHaveLength(5);
    expect(
      parseLoginResponse({
        ...LOGIN_NOTIFIED,
        file_ready: false,
        open_issues: 5,
      }),
    ).toMatchObject({
      file_ready: false,
      open_issues: 5,
    });
  });
});
