---
name: file-check
description: "Deterministic loan-file readiness check. Use for ANY question about whether a loan file is ready, complete or consistent, and what is missing. Triggers: 'is the file ready', 'is the file complete', 'what is missing', 'READY / NOT READY', PAN / name / employer mismatch, declared vs actual salary, pending documents, 'kya missing hai', 'file ready hai kya', and any letter, email or checklist of pending documents for an applicant. Use this skill INSTEAD of search for these questions."
---

# File Check Skill

**AI reads, rules decide, you explain.** The verdict comes from a deterministic rules engine over the facts extracted from each analysed document. You only report and explain it.

## Tools

**`filecheck___list_checklists`**
Lists the loan-product checklists that the file check can apply (id, name, product, applicant type, required documents and rules).

**`filecheck___run_file_check`**
Runs the deterministic readiness check for the current project and returns `overall_verdict`, one entry per applicant (verdict, reasons, checklist, consistency, missing_items, mismatches) and lists of pending / failed / unsupported / unassigned documents.

Never pass `project_id` or `user_id`; the system injects them.

## Workflow

1. **Checklist.** If the user named a loan product or a lender / DSA brand (e.g. Varunika Loan Partners `vlp_*` or Varunika Loans `vl_*` rule sets), pick its `checklist_id`. Call `filecheck___list_checklists` only when you are unsure which checklist applies AND more than one exists; if it is still ambiguous, ask with `ask_user`. Otherwise omit `checklist_id` (the default is the salaried personal loan).
2. **Run the check.** Call `filecheck___run_file_check`. Pass `applicant` (name or PAN) only when the user asks about one person.
3. **Answer** in this order:
   - `overall_verdict` and each applicant's `verdict` EXACTLY as returned: `READY` or `NOT READY`.
   - The applicant's `reasons`, verbatim.
   - A checklist table: `| Item | Status | Detail |` from `checklist` (item, status, detail).
   - A consistency table: `| Check | Status | Detail |` from `consistency` (check, status, detail).
   - Cite document names exactly as returned in `documents` / `document_name`.
   - Reasons that start with `REVIEW –` (and the `manual_review` list) are items the rules cannot verify, such as address proof, property papers or eligibility criteria. Say that a person must check them; a required REVIEW item keeps the verdict NOT READY.
4. **Never change the findings.** Do NOT change, soften, recompute, reorder or add findings, and NEVER decide READY / NOT READY yourself. If `pending_documents` is non-empty, say the verdict is provisional until those documents finish analysis. Mention `failed_documents`, `unsupported_documents` and `unassigned_documents` when present.
5. **Letters, emails, .docx or any other artifact** (e.g. a pending-documents letter): list exactly the applicant's `missing_items`, verbatim and in the same order, and list `mismatches` as items "to correct", verbatim and in the same order. Add nothing else to these lists.
6. **Search is secondary.** The search skill may be used only to quote supporting details from documents. It must never contradict the tool's verdict or findings.
7. **Errors.** If the tool returns `error` or fails, report the error to the user and do NOT guess a verdict.
