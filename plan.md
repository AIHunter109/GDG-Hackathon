# Shipping Document Verification Gap Plan

## Problem and proposed approach

The repository already implements most of the core workflow: rule-based and optional AI classification, seven-field extraction, SI/BL comparison, PDF/DOCX/XLSX reading, review decisions, retries, a dashboard, and regression tests. The remaining work is primarily reliability, evaluation, and advanced-input coverage rather than creating the basic pipeline from scratch.

Prioritize the gaps that affect correctness against the use case:

1. Make incomplete and uncertain cases consistently enter human review instead of treating some missing-document requests as automatically OK.
2. Improve extraction for realistic table layouts, label variants, multiline values, and scanned/image-only documents.
3. Add a complete evaluation loop that can submit generated output to the local evaluator and retain score/error diagnostics.
4. Harden reviewer workflows, persistence, and retry behavior so decisions survive refreshes and failures without silently changing automated results.
5. Add end-to-end and adversarial tests, then document the supported run modes and limitations.

## Missing or incomplete capabilities identified

- **Evaluator submission workflow:** `main.py` generates local JSON/evidence but does not provide a clear CLI command to POST the result through `Inbox.submit()` and display the scoreboard.
- **Missing-document policy:** some comparison messages with no attachments are treated as `OK` unless the body explicitly says an attachment was lost. The use case says missing required documents should be escalated, so this policy needs an explicit, configurable distinction between “request for a future document” and “verification request missing its source documents.”
- **Low-confidence classification review:** uncertain classification currently defaults to `GENERAL`; there is no explicit review queue for ambiguous intent when AI is unavailable or rule confidence is low.
- **Extraction coverage:** parsing is strongest for `Label: value` lines. It remains vulnerable to multiline/table layouts, duplicated labels, values separated from labels by columns, unusual units, and labels not covered by the alias table.
- **Scanned-document support:** image-only PDFs depend on the optional Gemini vision path; there is no local OCR fallback, page-level evidence, or clear behavior for image attachments.
- **Pairing and document-role UX:** reviewers can select SI/BL files, but the system does not expose a broad attachment-role workflow for all ambiguous formats or preserve a detailed audit trail of why a role was changed.
- **Review lifecycle:** reviewer decisions are stored in a local JSON file without versioning, case history, conflict handling, or an explicit “reopen” action. Corrected values are not represented as a separate immutable revision.
- **Operational observability:** processing errors are surfaced in the UI, but there is no structured run ID, retry count/backoff, batch progress, or export of review history and evidence.
- **Test coverage:** current tests cover important pipeline functions, but not browser behavior, asset serving in all modes, evaluator submission, concurrent review writes, all supported document samples, or adversarial classification/extraction cases.
- **Documentation and packaging:** run instructions for the dashboard, static bundle, optional AI configuration, dependencies, evaluator submission, and generated artifacts should be consolidated and kept aligned with the current directory layout.

## Implementation todos

- Auditing current behavior against all use-case requirements
- Correcting missing-document and uncertain-classification review policy
- Expanding resilient document extraction and evidence capture
- Adding local OCR or an explicit scanned-document fallback
- Completing evaluator submission and diagnostics
- Hardening reviewer decisions, audit history, and retries
- Adding end-to-end and adversarial regression tests
- Updating runbook and deployment documentation

## Notes and considerations

- Preserve the existing seven canonical fields: shipper, consignee, notify party, port of loading, port of discharge, container count, and gross weight in kilograms.
- Keep SI as the reference document and never overwrite automated evidence when a reviewer corrects a value.
- Do not classify a genuine mismatch as a review-only result merely because values use different harmless formatting; reserve review for missing, unreadable, contradictory, or low-confidence evidence.
- Keep AI optional. A no-key local run must remain deterministic and must fail visibly when it cannot read a document.
- The evaluator is useful for classification and mismatch accuracy, but it does not replace tests for human-review correctness and operational behavior.
