// WhatsApp and SMS document follow-up templates, ported from the CallVarta CRM
// telecaller pack (callvarta-telecaller-pack/whatsapp-sms-templates.md,
// 27 Sep 2026). Status there: DRAFT – none is approved by Meta or registered
// on DLT, and the Hindi and Marathi still need a native speaker's review.
//
// Only the templates for a NOT READY file are ported: T1 document request,
// T2 reminder, T3 final reminder, T5 mismatch clarification and T6 one more
// document (T4 received, T7 file ready and T8 consultation do not apply).
// The source's numbered variables ({{1}}, {{2}}, …) are named here. The
// reminder builder (lib/reminder.ts) fills {{first_name}}, {{documents}},
// {{document}} and, when the checklist names it, {{product}} from the
// verdict; every other placeholder stays visible for the user to fill.
//
// Rules the texts follow (from the source): only the customer's first name
// and document names – never PAN, Aadhaar, account numbers or amounts; no
// approval wording; English SMS use plain ASCII (GSM-7).

export type ReminderLanguage = 'en' | 'hi' | 'mr';
export type ReminderChannel = 'whatsapp' | 'sms';
export type ReminderTemplateId = 'T1' | 'T2' | 'T3' | 'T5' | 'T6';

export interface ReminderLanguageOption {
  code: ReminderLanguage;
  /** Shown in the picker, in the language itself. */
  label: string;
  /** Meta template language code (the source's supported-languages list). */
  whatsappCode: string;
}

export const REMINDER_LANGUAGES: ReminderLanguageOption[] = [
  { code: 'en', label: 'English', whatsappCode: 'en_IN' },
  { code: 'hi', label: 'हिन्दी', whatsappCode: 'hi' },
  { code: 'mr', label: 'मराठी', whatsappCode: 'mr' },
];

export interface ReminderTemplate {
  id: ReminderTemplateId;
  /** documents: lists the missing items; mismatch: names the document type. */
  purpose: 'documents' | 'mismatch';
  /** When the source sends it. */
  trigger: string;
  /** WhatsApp body (utility category; approval: to confirm). */
  whatsapp: Record<ReminderLanguage, string>;
  /** Draft opt-out footer (confirm with compliance); T3 only. */
  whatsappFooter?: Record<ReminderLanguage, string>;
  /** SMS text (DLT registration: to confirm). {{dsa_name}} is the brand prefix. */
  sms: Record<ReminderLanguage, string>;
  /** The source's count of characters without variables, per SMS. */
  smsFixedLength: Record<ReminderLanguage, number>;
}

export const REMINDER_TEMPLATES: Record<ReminderTemplateId, ReminderTemplate> =
  {
    T1: {
      id: 'T1',
      purpose: 'documents',
      trigger:
        'Document request after a call (send_upload_link from the voice agent or a telecaller)',
      whatsapp: {
        en: 'Hi {{first_name}}, this is {{dsa_name}} about your {{product}} application (Ref {{ref}}). Pending documents: {{documents}}. Upload securely: {{upload_link}} (valid 7 days). Reply HELP for a call back.',
        hi: 'नमस्ते {{first_name}}, {{dsa_name}} की ओर से आपके {{product}} आवेदन (Ref {{ref}}) के बारे में। बाकी documents: {{documents}}। सुरक्षित upload करें: {{upload_link}} (7 दिन वैध)। Call back के लिए HELP लिखें।',
        mr: 'नमस्कार {{first_name}}, {{dsa_name}} कडून तुमच्या {{product}} अर्जाबद्दल (Ref {{ref}}). बाकी कागदपत्रे: {{documents}}. सुरक्षितपणे अपलोड करा: {{upload_link}} (7 दिवस वैध). कॉल बॅकसाठी HELP लिहा.',
      },
      sms: {
        en: '{{dsa_name}}: Hi {{first_name}}, docs pending for Ref {{ref}}: {{documents}}. Upload: {{upload_link}} (valid 7 days).',
        hi: '{{dsa_name}}: नमस्ते {{first_name}}, Ref {{ref}} के बाकी documents: {{documents}}। Upload: {{upload_link}} (7 दिन वैध)।',
        mr: '{{dsa_name}}: नमस्कार {{first_name}}, Ref {{ref}} ची बाकी कागदपत्रे: {{documents}}. अपलोड: {{upload_link}} (7 दिवस वैध).',
      },
      smsFixedLength: { en: 56, hi: 58, mr: 59 },
    },
    T2: {
      id: 'T2',
      purpose: 'documents',
      trigger: 'Reminder on day 2, if items are still pending',
      whatsapp: {
        en: 'Hi {{first_name}}, a gentle reminder from {{dsa_name}}. Your {{product}} application (Ref {{ref}}) is still waiting for: {{documents}}. You can upload here: {{upload_link}} (link valid till {{link_expiry}}). Reply HELP for a call back.',
        hi: 'नमस्ते {{first_name}}, {{dsa_name}} की ओर से एक याद दिलाना। आपके {{product}} आवेदन (Ref {{ref}}) के लिए ये documents अभी बाकी हैं: {{documents}}। यहाँ upload करें: {{upload_link}} (link {{link_expiry}} तक वैध)। Call back के लिए HELP लिखें।',
        mr: 'नमस्कार {{first_name}}, {{dsa_name}} कडून एक आठवण. तुमच्या {{product}} अर्जासाठी (Ref {{ref}}) ही कागदपत्रे अजून बाकी आहेत: {{documents}}. इथे अपलोड करा: {{upload_link}} (लिंक {{link_expiry}} पर्यंत वैध). कॉल बॅकसाठी HELP लिहा.',
      },
      sms: {
        en: '{{dsa_name}}: Reminder {{first_name}}: Ref {{ref}} still needs {{documents}}. Upload: {{upload_link}} (till {{link_expiry}}).',
        hi: '{{dsa_name}}: याद दिलाना {{first_name}}: Ref {{ref}} के लिए अभी बाकी: {{documents}}। Upload: {{upload_link}} ({{link_expiry}} तक)।',
        mr: '{{dsa_name}}: आठवण {{first_name}}: Ref {{ref}} साठी अजून बाकी: {{documents}}. अपलोड: {{upload_link}} ({{link_expiry}} पर्यंत).',
      },
      smsFixedLength: { en: 49, hi: 54, mr: 50 },
    },
    T3: {
      id: 'T3',
      purpose: 'documents',
      trigger:
        'Final reminder on day 5 with a new 7-day link; after it a telecaller takes over',
      whatsapp: {
        en: 'Hi {{first_name}}, this is a last reminder from {{dsa_name}} about your {{product}} application (Ref {{ref}}). Still pending: {{documents}}. Here is a new secure link: {{upload_link}} (valid 7 days). Reply HELP if you would like a call back.',
        hi: 'नमस्ते {{first_name}}, {{dsa_name}} की ओर से आपके {{product}} आवेदन (Ref {{ref}}) के लिए आखिरी याद। अभी बाकी: {{documents}}। नया सुरक्षित link: {{upload_link}} (7 दिन वैध)। Call back चाहिए तो HELP लिखें।',
        mr: 'नमस्कार {{first_name}}, {{dsa_name}} कडून तुमच्या {{product}} अर्जासाठी (Ref {{ref}}) शेवटची आठवण. अजून बाकी: {{documents}}. नवी सुरक्षित लिंक: {{upload_link}} (7 दिवस वैध). कॉल बॅक हवा असल्यास HELP लिहा.',
      },
      whatsappFooter: {
        en: 'Reply STOP to stop these reminders',
        hi: 'ये reminders बंद करने के लिए STOP लिखें',
        mr: 'ही आठवण बंद करण्यासाठी STOP लिहा',
      },
      sms: {
        en: '{{dsa_name}}: Final reminder {{first_name}}: Ref {{ref}} still needs {{documents}}. New link: {{upload_link}} (7 days).',
        hi: '{{dsa_name}}: आखिरी याद {{first_name}}: Ref {{ref}} के लिए बाकी: {{documents}}। नया link: {{upload_link}} (7 दिन वैध)।',
        mr: '{{dsa_name}}: शेवटची आठवण {{first_name}}: Ref {{ref}} साठी बाकी: {{documents}}. नवी लिंक: {{upload_link}} (7 दिवस वैध).',
      },
      smsFixedLength: { en: 58, hi: 57, mr: 58 },
    },
    T5: {
      id: 'T5',
      purpose: 'mismatch',
      trigger:
        'The file check reports a MISMATCH; names the document type, never the value',
      whatsapp: {
        en: "Hi {{first_name}}, a detail on your application doesn't match one document ({{document}}). Please call {{dsa_phone}} or reply CALL.",
        hi: 'नमस्ते {{first_name}}, आपके आवेदन की एक जानकारी एक document ({{document}}) से मेल नहीं खा रही है। कृपया {{dsa_phone}} पर call करें या CALL लिखकर reply करें।',
        mr: 'नमस्कार {{first_name}}, तुमच्या अर्जातील एक माहिती एका कागदपत्राशी ({{document}}) जुळत नाही. कृपया {{dsa_phone}} वर कॉल करा किंवा CALL असे उत्तर द्या.',
      },
      sms: {
        en: '{{dsa_name}}: Hi {{first_name}}, a detail on your application (Ref {{ref}}) does not match one document ({{document}}). Please call {{dsa_phone}}.',
        hi: '{{dsa_name}}: नमस्ते {{first_name}}, आवेदन (Ref {{ref}}) की एक जानकारी एक document ({{document}}) से मेल नहीं खाती। कृपया {{dsa_phone}} पर call करें।',
        mr: '{{dsa_name}}: नमस्कार {{first_name}}, अर्जातील (Ref {{ref}}) एक माहिती एका कागदपत्राशी ({{document}}) जुळत नाही. कृपया {{dsa_phone}} वर कॉल करा.',
      },
      smsFixedLength: { en: 88, hi: 91, mr: 86 },
    },
    T6: {
      id: 'T6',
      purpose: 'documents',
      trigger:
        'Only one month-based item is left (a salary slip month or a statement range)',
      whatsapp: {
        en: 'Hi {{first_name}}, your {{product}} application (Ref {{ref}}) needs one more document: {{documents}}. Please upload it here: {{upload_link}} (valid 7 days). Reply HELP for a call back.',
        hi: 'नमस्ते {{first_name}}, आपके {{product}} आवेदन (Ref {{ref}}) के लिए एक और document चाहिए: {{documents}}। कृपया यहाँ upload करें: {{upload_link}} (7 दिन वैध)। Call back के लिए HELP लिखें।',
        mr: 'नमस्कार {{first_name}}, तुमच्या {{product}} अर्जासाठी (Ref {{ref}}) अजून एक कागदपत्र हवे आहे: {{documents}}. कृपया इथे अपलोड करा: {{upload_link}} (7 दिवस वैध). कॉल बॅकसाठी HELP लिहा.',
      },
      sms: {
        en: '{{dsa_name}}: Hi {{first_name}}, Ref {{ref}} needs one more item: {{documents}}. Upload: {{upload_link}} (valid 7 days).',
        hi: '{{dsa_name}}: नमस्ते {{first_name}}, Ref {{ref}} के लिए एक चीज़ बाकी है: {{documents}}। Upload: {{upload_link}} (7 दिन वैध)।',
        mr: '{{dsa_name}}: नमस्कार {{first_name}}, Ref {{ref}} साठी एक गोष्ट बाकी आहे: {{documents}}. अपलोड: {{upload_link}} (7 दिवस वैध).',
      },
      smsFixedLength: { en: 59, hi: 63, mr: 64 },
    },
  };

/** The order the message picker lists them in. */
export const REMINDER_TEMPLATE_ORDER: ReminderTemplateId[] = [
  'T2',
  'T1',
  'T3',
  'T6',
  'T5',
];

// ------------------------------------------------------------------ glossary
// Section 3 of the source: document names for {{documents}}, in the
// template's language, and the English SMS short forms.

export type ReminderDocCode =
  | 'SALARY_SLIP'
  | 'BANK_STATEMENT'
  | 'FORM16_ITR'
  | 'APPLICATION_FORM'
  | 'PAN_COPY'
  | 'AADHAAR_MASKED'
  | 'ADDRESS_PROOF'
  | 'EMPLOYMENT_PROOF'
  | 'GST_RETURNS';

export interface ReminderDocName {
  whatsapp: Record<ReminderLanguage, string>;
  /** SMS short form (the source gives English ones; they are used in all three). */
  sms: string;
}

/** Names without a month. SALARY_SLIP / BANK_STATEMENT with months: see below. */
export const REMINDER_DOC_NAMES: Record<ReminderDocCode, ReminderDocName> = {
  SALARY_SLIP: {
    whatsapp: { en: 'salary slip', hi: 'salary slip', mr: 'salary slip' },
    sms: 'salary slip',
  },
  BANK_STATEMENT: {
    whatsapp: {
      en: 'bank statement',
      hi: 'bank statement',
      mr: 'bank statement',
    },
    sms: 'bank statement',
  },
  FORM16_ITR: {
    whatsapp: {
      en: 'Form-16 or ITR',
      hi: 'Form-16 या ITR',
      mr: 'Form-16 किंवा ITR',
    },
    sms: 'Form-16',
  },
  APPLICATION_FORM: {
    whatsapp: {
      en: 'signed application form',
      hi: 'sign किया हुआ application form',
      mr: 'सही केलेला अर्ज',
    },
    sms: 'App form',
  },
  PAN_COPY: {
    whatsapp: {
      en: 'PAN card copy',
      hi: 'PAN card की copy',
      mr: 'PAN कार्डची प्रत',
    },
    sms: 'PAN copy',
  },
  AADHAAR_MASKED: {
    whatsapp: {
      en: 'masked Aadhaar copy',
      hi: 'masked Aadhaar की copy',
      mr: 'masked आधारची प्रत',
    },
    sms: 'Aadhaar copy',
  },
  ADDRESS_PROOF: {
    whatsapp: {
      en: 'address proof',
      hi: 'पते का प्रमाण (address proof)',
      mr: 'पत्त्याचा पुरावा',
    },
    sms: 'Addr proof',
  },
  EMPLOYMENT_PROOF: {
    whatsapp: {
      en: 'company ID card or appointment letter',
      hi: 'company ID card या appointment letter',
      mr: 'कंपनी ओळखपत्र किंवा appointment letter',
    },
    sms: 'Emp proof',
  },
  GST_RETURNS: {
    whatsapp: {
      en: 'GST returns for the last 12 months',
      hi: 'पिछले 12 महीनों के GST returns',
      mr: 'मागील 12 महिन्यांचे GST returns',
    },
    sms: 'GST returns',
  },
};

/** Month names of the source (English WhatsApp uses the full English name). */
export const REMINDER_MONTHS: Record<ReminderLanguage, string[]> = {
  en: [
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
  ],
  hi: [
    'जनवरी',
    'फ़रवरी',
    'मार्च',
    'अप्रैल',
    'मई',
    'जून',
    'जुलाई',
    'अगस्त',
    'सितंबर',
    'अक्टूबर',
    'नवंबर',
    'दिसंबर',
  ],
  mr: [
    'जानेवारी',
    'फेब्रुवारी',
    'मार्च',
    'एप्रिल',
    'मे',
    'जून',
    'जुलै',
    'ऑगस्ट',
    'सप्टेंबर',
    'ऑक्टोबर',
    'नोव्हेंबर',
    'डिसेंबर',
  ],
};

/** English SMS short month names ("Jun slip", "Mar-May bank stmt"). */
export const REMINDER_SMS_MONTHS_EN = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/**
 * How the glossary words month-based items, per language:
 * - WhatsApp: "June 2026 salary slip" / "जून 2026 की salary slip" /
 *   "जून 2026 ची salary slip"; "bank statement for March to May 2026" /
 *   "मार्च से मई 2026 तक का bank statement" / "मार्च ते मे 2026 चे bank statement".
 * - SMS lists (T1–T3): "Jun slip", "Mar-May bank stmt" / "जून slip",
 *   "मार्च-मई bank statement" (no year, to keep English SMS in one segment).
 * - SMS T6 (one item, with the year): "bank stmt for Mar-May 2026" /
 *   "मार्च-मई 2026 का bank statement" / "मार्च-मे 2026 चे bank statement".
 */
export const REMINDER_MONTH_WORDS: Record<
  ReminderLanguage,
  {
    /** "March to May" / "मार्च से मई" / "मार्च ते मे" */
    rangeJoin: string;
    /** Appended to a WhatsApp range: "मार्च से मई 2026 तक" */
    rangeSuffix: string;
    /** Last joiner of a month list: " and " / " और " / " आणि " */
    listAnd: string;
  }
> = {
  en: { rangeJoin: ' to ', rangeSuffix: '', listAnd: ' and ' },
  hi: { rangeJoin: ' से ', rangeSuffix: ' तक', listAnd: ' और ' },
  mr: { rangeJoin: ' ते ', rangeSuffix: '', listAnd: ' आणि ' },
};
