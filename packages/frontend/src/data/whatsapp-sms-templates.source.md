<!--
Verbatim excerpt of the CallVarta CRM telecaller pack's whatsapp-sms-templates.md
(27 Sep 2026, status: draft): the parts data/reminderTemplates.ts ports.
reminderTemplates.test.ts compares every template, footer, glossary cell and
SMS count below with the port, and the Document Reminder Writer built-in
agent's prompt with both. Change the three together.
-->

# WhatsApp and SMS templates: document follow-up (excerpt)

## 3. Document names

| Code | English (WhatsApp) | Hindi | Marathi | SMS short form (EN) |
|---|---|---|---|---|
| SALARY_SLIP + month | June 2026 salary slip | जून 2026 की salary slip | जून 2026 ची salary slip | Jun slip |
| BANK_STATEMENT + months | bank statement for March to May 2026 | मार्च से मई 2026 तक का bank statement | मार्च ते मे 2026 चे bank statement | Mar-May bank stmt |
| FORM16_ITR | Form-16 or ITR (FY 2025-26) | Form-16 या ITR (FY 2025-26) | Form-16 किंवा ITR (FY 2025-26) | Form-16 |
| APPLICATION_FORM | signed application form | sign किया हुआ application form | सही केलेला अर्ज | App form |
| PAN_COPY | PAN card copy | PAN card की copy | PAN कार्डची प्रत | PAN copy |
| AADHAAR_MASKED | masked Aadhaar copy | masked Aadhaar की copy | masked आधारची प्रत | Aadhaar copy |
| ADDRESS_PROOF | address proof | पते का प्रमाण (address proof) | पत्त्याचा पुरावा | Addr proof |
| EMPLOYMENT_PROOF | company ID card or appointment letter | company ID card या appointment letter | कंपनी ओळखपत्र किंवा appointment letter | Emp proof |
| GST_RETURNS | GST returns for the last 12 months | पिछले 12 महीनों के GST returns | मागील 12 महिन्यांचे GST returns | GST returns |

Month names:
- Hindi: जनवरी, फ़रवरी, मार्च, अप्रैल, मई, जून, जुलाई, अगस्त, सितंबर, अक्टूबर, नवंबर, दिसंबर.
- Marathi: जानेवारी, फेब्रुवारी, मार्च, एप्रिल, मे, जून, जुलै, ऑगस्ट, सप्टेंबर, ऑक्टोबर, नोव्हेंबर, डिसेंबर.

If the English SMS list would be longer than about 36 characters, send "3 documents" instead. The upload page lists them.

## 4. WhatsApp templates

### T1: document request after a call
Variables: `{{1}}` first name · `{{2}}` DSA brand · `{{3}}` product · `{{4}}` Ref · `{{5}}` pending documents · `{{6}}` upload link
- **EN:** Hi {{1}}, this is {{2}} about your {{3}} application (Ref {{4}}). Pending documents: {{5}}. Upload securely: {{6}} (valid 7 days). Reply HELP for a call back.
- **HI:** नमस्ते {{1}}, {{2}} की ओर से आपके {{3}} आवेदन (Ref {{4}}) के बारे में। बाकी documents: {{5}}। सुरक्षित upload करें: {{6}} (7 दिन वैध)। Call back के लिए HELP लिखें।
- **MR:** नमस्कार {{1}}, {{2}} कडून तुमच्या {{3}} अर्जाबद्दल (Ref {{4}}). बाकी कागदपत्रे: {{5}}. सुरक्षितपणे अपलोड करा: {{6}} (7 दिवस वैध). कॉल बॅकसाठी HELP लिहा.

### T2: reminder (day 2)
Variables: `{{1}}` first name · `{{2}}` brand · `{{3}}` product · `{{4}}` Ref · `{{5}}` pending documents · `{{6}}` link · `{{7}}` link expiry date
- **EN:** Hi {{1}}, a gentle reminder from {{2}}. Your {{3}} application (Ref {{4}}) is still waiting for: {{5}}. You can upload here: {{6}} (link valid till {{7}}). Reply HELP for a call back.
- **HI:** नमस्ते {{1}}, {{2}} की ओर से एक याद दिलाना। आपके {{3}} आवेदन (Ref {{4}}) के लिए ये documents अभी बाकी हैं: {{5}}। यहाँ upload करें: {{6}} (link {{7}} तक वैध)। Call back के लिए HELP लिखें।
- **MR:** नमस्कार {{1}}, {{2}} कडून एक आठवण. तुमच्या {{3}} अर्जासाठी (Ref {{4}}) ही कागदपत्रे अजून बाकी आहेत: {{5}}. इथे अपलोड करा: {{6}} (लिंक {{7}} पर्यंत वैध). कॉल बॅकसाठी HELP लिहा.

### T3: final reminder (day 5, new link)
Variables: same as T1.
- **EN:** Hi {{1}}, this is a last reminder from {{2}} about your {{3}} application (Ref {{4}}). Still pending: {{5}}. Here is a new secure link: {{6}} (valid 7 days). Reply HELP if you would like a call back.
- **HI:** नमस्ते {{1}}, {{2}} की ओर से आपके {{3}} आवेदन (Ref {{4}}) के लिए आखिरी याद। अभी बाकी: {{5}}। नया सुरक्षित link: {{6}} (7 दिन वैध)। Call back चाहिए तो HELP लिखें।
- **MR:** नमस्कार {{1}}, {{2}} कडून तुमच्या {{3}} अर्जासाठी (Ref {{4}}) शेवटची आठवण. अजून बाकी: {{5}}. नवी सुरक्षित लिंक: {{6}} (7 दिवस वैध). कॉल बॅक हवा असल्यास HELP लिहा.
- **Footer, draft opt-out (confirm with compliance):**
  - EN: Reply STOP to stop these reminders
  - HI: ये reminders बंद करने के लिए STOP लिखें
  - MR: ही आठवण बंद करण्यासाठी STOP लिहा

### T5: mismatch clarification
Variables: `{{1}}` first name · `{{2}}` document type (never the value) · `{{3}}` DSA phone number
- **EN:** Hi {{1}}, a detail on your application doesn't match one document ({{2}}). Please call {{3}} or reply CALL.
- **HI:** नमस्ते {{1}}, आपके आवेदन की एक जानकारी एक document ({{2}}) से मेल नहीं खा रही है। कृपया {{3}} पर call करें या CALL लिखकर reply करें।
- **MR:** नमस्कार {{1}}, तुमच्या अर्जातील एक माहिती एका कागदपत्राशी ({{2}}) जुळत नाही. कृपया {{3}} वर कॉल करा किंवा CALL असे उत्तर द्या.

### T6: missing-month reminder
Variables: `{{1}}` first name · `{{2}}` product · `{{3}}` Ref · `{{4}}` the one missing item with its month(s) · `{{5}}` link
- **EN:** Hi {{1}}, your {{2}} application (Ref {{3}}) needs one more document: {{4}}. Please upload it here: {{5}} (valid 7 days). Reply HELP for a call back.
- **HI:** नमस्ते {{1}}, आपके {{2}} आवेदन (Ref {{3}}) के लिए एक और document चाहिए: {{4}}। कृपया यहाँ upload करें: {{5}} (7 दिन वैध)। Call back के लिए HELP लिखें।
- **MR:** नमस्कार {{1}}, तुमच्या {{2}} अर्जासाठी (Ref {{3}}) अजून एक कागदपत्र हवे आहे: {{4}}. कृपया इथे अपलोड करा: {{5}} (7 दिवस वैध). कॉल बॅकसाठी HELP लिहा.

## 5. SMS versions

Counts are worked out by script using synthetic sample values (re-checked independently on 27 Sep 2026; all 24 rows reproduce):
- brand "VarunikaLoans" (13 characters)
- first name "Sneha" (T1–T4, T6), "Amit" (T5), "Rahul" (T7) or "Priya" (T8)
- Ref "DA-2026-0002" (T1–T4, T6), "DA-2026-0003" (T5), "DA-2026-0001" (T7)
- document list for T1–T3: EN "Jun slip, Mar-May bank stmt, Form-16"; HI "जून slip, मार्च-मई bank statement, Form-16"; MR "जून slip, मार्च-मे bank statement, Form-16"
- T4 item: "Jun slip" / "जून slip". T5 item: "PAN card copy". T6 item: EN "bank stmt for Mar-May 2026"; HI "मार्च-मई 2026 का bank statement"; MR "मार्च-मे 2026 चे bank statement"
- T2 expiry date: "4 Oct" / "4 अक्टूबर" / "4 ऑक्टोबर". T8 date and window: "29 Sep", "11 AM-12 PM" / "29 सितंबर", "11-12 बजे" / "29 सप्टेंबर", "11-12 वाजता"
- a 30-character link `https://x.example.com/u/Ab3xY9`
- phone "+91 90000 00000" (a placeholder, not a real number)

| ID | SMS text | Encoding | Fixed | With sample values | Segments |
|---|---|---|---|---|---|
| T1-EN | {{1}}: Hi {{2}}, docs pending for Ref {{3}}: {{4}}. Upload: {{5}} (valid 7 days). | GSM-7 | 56 | 152 | 1 |
| T1-HI | {{1}}: नमस्ते {{2}}, Ref {{3}} के बाकी documents: {{4}}। Upload: {{5}} (7 दिन वैध)। | UCS-2 | 58 | 160 | 3 |
| T1-MR | {{1}}: नमस्कार {{2}}, Ref {{3}} ची बाकी कागदपत्रे: {{4}}. अपलोड: {{5}} (7 दिवस वैध). | UCS-2 | 59 | 161 | 3 |
| T2-EN | {{1}}: Reminder {{2}}: Ref {{3}} still needs {{4}}. Upload: {{5}} (till {{6}}). | GSM-7 | 49 | 150 | 1 |
| T2-HI | {{1}}: याद दिलाना {{2}}: Ref {{3}} के लिए अभी बाकी: {{4}}। Upload: {{5}} ({{6}} तक)। | UCS-2 | 54 | 165 | 3 |
| T2-MR | {{1}}: आठवण {{2}}: Ref {{3}} साठी अजून बाकी: {{4}}. अपलोड: {{5}} ({{6}} पर्यंत). | UCS-2 | 50 | 161 | 3 |
| T3-EN | {{1}}: Final reminder {{2}}: Ref {{3}} still needs {{4}}. New link: {{5}} (7 days). | GSM-7 | 58 | 154 | 1 |
| T3-HI | {{1}}: आखिरी याद {{2}}: Ref {{3}} के लिए बाकी: {{4}}। नया link: {{5}} (7 दिन वैध)। | UCS-2 | 57 | 159 | 3 |
| T3-MR | {{1}}: शेवटची आठवण {{2}}: Ref {{3}} साठी बाकी: {{4}}. नवी लिंक: {{5}} (7 दिवस वैध). | UCS-2 | 58 | 160 | 3 |
| T5-EN | {{1}}: Hi {{2}}, a detail on your application (Ref {{3}}) does not match one document ({{4}}). Please call {{5}}. | GSM-7 | 88 | 145 | 1 |
| T5-HI | {{1}}: नमस्ते {{2}}, आवेदन (Ref {{3}}) की एक जानकारी एक document ({{4}}) से मेल नहीं खाती। कृपया {{5}} पर call करें। | UCS-2 | 91 | 148 | 3 |
| T5-MR | {{1}}: नमस्कार {{2}}, अर्जातील (Ref {{3}}) एक माहिती एका कागदपत्राशी ({{4}}) जुळत नाही. कृपया {{5}} वर कॉल करा. | UCS-2 | 86 | 143 | 3 |
| T6-EN | {{1}}: Hi {{2}}, Ref {{3}} needs one more item: {{4}}. Upload: {{5}} (valid 7 days). | GSM-7 | 59 | 145 | 1 |
| T6-HI | {{1}}: नमस्ते {{2}}, Ref {{3}} के लिए एक चीज़ बाकी है: {{4}}। Upload: {{5}} (7 दिन वैध)। | UCS-2 | 63 | 154 | 3 |
| T6-MR | {{1}}: नमस्कार {{2}}, Ref {{3}} साठी एक गोष्ट बाकी आहे: {{4}}. अपलोड: {{5}} (7 दिवस वैध). | UCS-2 | 64 | 155 | 3 |
