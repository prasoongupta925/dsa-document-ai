// @vitest-environment node
import {
  REMINDER_TEMPLATES,
  type ReminderLanguage,
  type ReminderTemplateId,
} from '../data/reminderTemplates';
import {
  buildReminder,
  reminderChoice,
  reminderFirstName,
  reminderItems,
  reminderPlaceholders,
  smsInfo,
  smsMonthPhrase,
  whatsappMonthPhrase,
} from './reminder';
import {
  AMIT_PAN_MISMATCH_RESULT,
  NOT_READY_RESULT,
  SNEHA_NOT_READY_RESULT,
} from '../components/FileCheckPanel/fixtures';
import type { FileCheckApplicant } from '../types/fileCheck';

const SNEHA = SNEHA_NOT_READY_RESULT.applicants[0];
const AMIT = AMIT_PAN_MISMATCH_RESULT.applicants[0];
const CHECKLIST = SNEHA_NOT_READY_RESULT.checklist.id;
const LANGUAGES: ReminderLanguage[] = ['en', 'hi', 'mr'];

/** Sneha with only the bank statement months left (T6). */
const SNEHA_STATEMENT_ONLY: FileCheckApplicant = {
  ...SNEHA,
  checklist: SNEHA.checklist.map((row) =>
    row.item_id === 'bank_statement'
      ? row
      : { ...row, status: 'PRESENT', ok: true, missing_months: [] },
  ),
  missing_items: ['Bank statement: Mar 2026, Apr 2026, May 2026'],
  consistency: [],
  mismatches: [],
};

/** Every value of a draft that must never reach a customer message. */
function expectNoIdsOrAmounts(text: string) {
  expect(text).not.toMatch(/[A-Z]{5}\d{4}[A-Z]/); // PAN
  expect(text).not.toContain('₹');
  expect(text).not.toMatch(/\d{2},\d{3}/); // an amount like 58,000
  expect(text).not.toMatch(/\.pdf/); // file names
}

describe('reminder items', () => {
  it('lists exactly the verdict’s missing items and months', () => {
    const items = reminderItems(SNEHA, CHECKLIST);
    expect(items.map((i) => [i.itemId, i.noun, i.months, i.docs])).toEqual([
      ['salary_slips', 'salary_slip', ['2026-06'], []],
      [
        'bank_statement',
        'bank_statement',
        ['2026-03', '2026-04', '2026-05'],
        [],
      ],
      ['form16_itr', null, [], ['FORM16_ITR']],
    ]);
    // Present, REVIEW and optional items are never asked for.
    const amitRows = reminderItems(NOT_READY_RESULT.applicants[0], CHECKLIST);
    expect(amitRows.map((i) => i.itemId)).toEqual(['salary_slips']);
    expect(amitRows[0].months).toEqual(['2026-06', '2026-07']);
  });

  it('falls back to the missing_items text when there are no rows', () => {
    const items = reminderItems(
      {
        ...SNEHA,
        checklist: [],
        missing_items: ['Salary slip: May 2026, Jun 2026', 'Address proof'],
      },
      'vlp_pl_sal',
    );
    expect(items.map((i) => [i.label, i.noun, i.months])).toEqual([
      ['Salary slip', 'salary_slip', ['2026-05', '2026-06']],
      ['Address proof', null, []],
    ]);
  });

  it('picks the message that fits the verdict', () => {
    expect(reminderChoice(SNEHA, CHECKLIST)).toMatchObject({
      available: ['T2', 'T1', 'T3', 'T5'],
      preferred: 'T2',
      mismatchDocs: ['SALARY_SLIP', 'BANK_STATEMENT'],
    });
    expect(reminderChoice(SNEHA_STATEMENT_ONLY, CHECKLIST)).toMatchObject({
      available: ['T2', 'T1', 'T3', 'T6'],
      preferred: 'T6',
    });
    expect(reminderChoice(AMIT, CHECKLIST)).toMatchObject({
      available: ['T5'],
      preferred: 'T5',
      mismatchDocs: ['PAN_COPY'],
    });
    // NOT READY only because of items a person must review: nothing to ask.
    const reviewOnly: FileCheckApplicant = {
      ...AMIT,
      consistency: [],
      mismatches: [],
    };
    expect(reminderChoice(reviewOnly, CHECKLIST)).toMatchObject({
      available: [],
      preferred: null,
    });
    // FOIR is a policy figure, not a customer's document.
    expect(
      reminderChoice(
        {
          ...reviewOnly,
          consistency: [
            {
              check_id: 'foir',
              check: 'FOIR (indicative)',
              status: 'MISMATCH',
              detail: 'existing EMIs exceed the limit',
              documents: [],
            },
          ],
        },
        CHECKLIST,
      ).preferred,
    ).toBeNull();
  });
});

describe('buildReminder', () => {
  it('fills the WhatsApp reminder in English, Hindi and Marathi', () => {
    const text = (language: ReminderLanguage) =>
      buildReminder(SNEHA, {
        template: 'T2',
        language,
        channel: 'whatsapp',
        checklistId: CHECKLIST,
        product: 'personal_loan',
      }).text;

    expect(text('en')).toBe(
      'Hi Sneha, a gentle reminder from {{dsa_name}}. Your personal loan application (Ref {{ref}}) is still waiting for: June 2026 salary slip, bank statement for March to May 2026, Form-16 or ITR (latest FY). You can upload here: {{upload_link}} (link valid till {{link_expiry}}). Reply HELP for a call back.',
    );
    expect(text('hi')).toBe(
      'नमस्ते Sneha, {{dsa_name}} की ओर से एक याद दिलाना। आपके personal loan आवेदन (Ref {{ref}}) के लिए ये documents अभी बाकी हैं: जून 2026 की salary slip, मार्च से मई 2026 तक का bank statement, Form-16 या ITR। यहाँ upload करें: {{upload_link}} (link {{link_expiry}} तक वैध)। Call back के लिए HELP लिखें।',
    );
    expect(text('mr')).toBe(
      'नमस्कार Sneha, {{dsa_name}} कडून एक आठवण. तुमच्या personal loan अर्जासाठी (Ref {{ref}}) ही कागदपत्रे अजून बाकी आहेत: जून 2026 ची salary slip, मार्च ते मे 2026 चे bank statement, Form-16 किंवा ITR. इथे अपलोड करा: {{upload_link}} (लिंक {{link_expiry}} पर्यंत वैध). कॉल बॅकसाठी HELP लिहा.',
    );
    for (const language of LANGUAGES) expectNoIdsOrAmounts(text(language));
  });

  it('uses the glossary SMS short forms in all three languages', () => {
    const docs = (language: ReminderLanguage) =>
      buildReminder(SNEHA, {
        template: 'T1',
        language,
        channel: 'sms',
        checklistId: CHECKLIST,
      }).documents.join(', ');
    // The source's own sample lists.
    expect(docs('en')).toBe('Jun slip, Mar-May bank stmt, Form-16');
    expect(docs('hi')).toBe('जून slip, मार्च-मई bank statement, Form-16');
    expect(docs('mr')).toBe('जून slip, मार्च-मे bank statement, Form-16');
  });

  it('leaves what the verdict does not know as visible placeholders', () => {
    const draft = buildReminder(SNEHA, {
      template: 'T1',
      language: 'en',
      channel: 'whatsapp',
      checklistId: CHECKLIST,
    });
    // No product without the checklist's product id.
    expect(draft.placeholders).toEqual([
      'dsa_name',
      'product',
      'ref',
      'upload_link',
    ]);
    expect(
      buildReminder(
        { ...SNEHA, applicant: 'Unknown' },
        { template: 'T2', language: 'hi', channel: 'sms' },
      ).placeholders,
    ).toContain('first_name');
  });

  it('adds the draft opt-out footer to the final WhatsApp reminder', () => {
    const draft = buildReminder(SNEHA, {
      template: 'T3',
      language: 'mr',
      channel: 'whatsapp',
      checklistId: CHECKLIST,
    });
    expect(draft.text.endsWith('\n\nही आठवण बंद करण्यासाठी STOP लिहा')).toBe(
      true,
    );
    expect(
      buildReminder(SNEHA, {
        template: 'T3',
        language: 'mr',
        channel: 'sms',
      }).text,
    ).not.toContain('STOP');
  });

  it('names only the document type of a mismatch (T5)', () => {
    for (const language of LANGUAGES) {
      const whatsapp = buildReminder(AMIT, {
        template: 'T5',
        language,
        channel: 'whatsapp',
      });
      expectNoIdsOrAmounts(whatsapp.text);
      expect(whatsapp.text).not.toContain('DMVPP');
    }
    expect(
      buildReminder(AMIT, {
        template: 'T5',
        language: 'en',
        channel: 'whatsapp',
      }).text,
    ).toBe(
      "Hi Amit, a detail on your application doesn't match one document (PAN card copy). Please call {{dsa_phone}} or reply CALL.",
    );
    expect(
      buildReminder(AMIT, {
        template: 'T5',
        language: 'hi',
        channel: 'whatsapp',
      }).documents,
    ).toEqual(['PAN card की copy']);
    // Sneha's two salary mismatches name the slip and the statement.
    expect(
      buildReminder(SNEHA, {
        template: 'T5',
        language: 'en',
        channel: 'whatsapp',
      }).documents,
    ).toEqual(['salary slip', 'bank statement']);
  });

  it('reproduces the source’s SMS counts when its sample values are filled in', () => {
    // whatsapp-sms-templates.md §5: brand "VarunikaLoans", a 30-character
    // link, Ref DA-2026-0002 (DA-2026-0003 for T5), expiry "4 Oct".
    const samples = (language: ReminderLanguage): Record<string, string> => ({
      dsa_name: 'VarunikaLoans',
      upload_link: 'https://x.example.com/u/Ab3xY9',
      dsa_phone: '+91 90000 00000',
      link_expiry: { en: '4 Oct', hi: '4 अक्टूबर', mr: '4 ऑक्टोबर' }[language],
    });
    const expected: [ReminderTemplateId, ReminderLanguage, number, number][] = [
      ['T1', 'en', 152, 1],
      ['T1', 'hi', 160, 3],
      ['T1', 'mr', 161, 3],
      ['T2', 'en', 150, 1],
      ['T2', 'hi', 165, 3],
      ['T2', 'mr', 161, 3],
      ['T3', 'en', 154, 1],
      ['T3', 'hi', 159, 3],
      ['T3', 'mr', 160, 3],
      ['T5', 'en', 145, 1],
      ['T5', 'hi', 148, 3],
      ['T5', 'mr', 143, 3],
      ['T6', 'en', 145, 1],
      ['T6', 'hi', 154, 3],
      ['T6', 'mr', 155, 3],
    ];
    for (const [template, language, length, segments] of expected) {
      const applicant =
        template === 'T5'
          ? AMIT
          : template === 'T6'
            ? SNEHA_STATEMENT_ONLY
            : SNEHA;
      const ref = template === 'T5' ? 'DA-2026-0003' : 'DA-2026-0002';
      const values = { ...samples(language), ref };
      const draft = buildReminder(applicant, {
        template,
        language,
        channel: 'sms',
        checklistId: CHECKLIST,
      });
      const filled = draft.text.replace(
        /\{\{(\w+)\}\}/g,
        (_, key: string) => values[key as keyof typeof values] ?? `{{${key}}}`,
      );
      expect(reminderPlaceholders(filled)).toEqual([]);
      const info = smsInfo(filled);
      expect([template, language, info.length, info.segments]).toEqual([
        template,
        language,
        length,
        segments,
      ]);
      expect(info.encoding).toBe(language === 'en' ? 'GSM-7' : 'UCS-2');
    }
  });
});

describe('ported templates', () => {
  it('match the source character for character (its "fixed" counts)', () => {
    for (const tpl of Object.values(REMINDER_TEMPLATES)) {
      for (const language of LANGUAGES) {
        const fixed = tpl.sms[language].replace(/\{\{\w+\}\}/g, '');
        expect([tpl.id, language, fixed.length]).toEqual([
          tpl.id,
          language,
          tpl.smsFixedLength[language],
        ]);
        // Meta: a template cannot start or end with a parameter.
        expect(tpl.whatsapp[language]).not.toMatch(/^\{\{|\}\}$/);
      }
      // English SMS stay plain ASCII (GSM-7).
      expect(tpl.sms.en).toMatch(/^[\x20-\x7e]+$/);
    }
  });
});

describe('months and names', () => {
  it('words months like the glossary', () => {
    expect(whatsappMonthPhrase(['2026-06'], 'en')).toBe('June 2026');
    expect(whatsappMonthPhrase(['2026-05', '2026-03', '2026-04'], 'hi')).toBe(
      'मार्च से मई 2026 तक',
    );
    expect(whatsappMonthPhrase(['2026-06', '2026-07'], 'mr')).toBe(
      'जून आणि जुलै 2026',
    );
    expect(whatsappMonthPhrase(['2025-12', '2026-01', '2026-02'], 'en')).toBe(
      'December 2025 to February 2026',
    );
    expect(whatsappMonthPhrase(['2026-03', '2026-05'], 'en')).toBe(
      'March and May 2026',
    );
    expect(smsMonthPhrase(['2026-03', '2026-04', '2026-05'], 'en', false)).toBe(
      'Mar-May',
    );
    expect(smsMonthPhrase(['2026-03', '2026-05'], 'hi', true)).toBe(
      'मार्च/मई 2026',
    );
    expect(smsMonthPhrase(['2025-12', '2026-01'], 'en', true)).toBe(
      'Dec 2025-Jan 2026',
    );
  });

  it('greets by first name only, never with an id', () => {
    expect(reminderFirstName('Sneha Anil Kulkarni')).toBe('Sneha');
    expect(reminderFirstName('Mr. Amit S. Patil')).toBe('Amit');
    expect(reminderFirstName('Unknown')).toBeNull();
    expect(reminderFirstName('CKRPK7314M')).toBeNull();
    expect(reminderFirstName('')).toBeNull();
  });

  it('writes several missing slip months exactly', () => {
    const draft = buildReminder(NOT_READY_RESULT.applicants[0], {
      template: 'T2',
      language: 'en',
      channel: 'whatsapp',
      checklistId: CHECKLIST,
    });
    expect(draft.documents).toEqual(['salary slips for June and July 2026']);
    expect(
      buildReminder(NOT_READY_RESULT.applicants[0], {
        template: 'T2',
        language: 'mr',
        channel: 'whatsapp',
        checklistId: CHECKLIST,
      }).documents,
    ).toEqual(['जून आणि जुलै 2026 च्या salary slips']);
  });
});

describe('smsInfo', () => {
  it('counts GSM-7, its extension characters and UCS-2', () => {
    expect(smsInfo('a'.repeat(160))).toMatchObject({
      encoding: 'GSM-7',
      length: 160,
      segments: 1,
    });
    expect(smsInfo('a'.repeat(161)).segments).toBe(2);
    // Braces are GSM extension characters: 2 septets each.
    expect(smsInfo('{x}').length).toBe(5);
    // One en dash or ₹ makes it UCS-2 (70 / 67).
    expect(smsInfo('Fee – ok')).toMatchObject({
      encoding: 'UCS-2',
      singleLimit: 70,
      partLimit: 67,
    });
    expect(smsInfo('न'.repeat(70)).segments).toBe(1);
    expect(smsInfo('न'.repeat(71)).segments).toBe(2);
  });
});
