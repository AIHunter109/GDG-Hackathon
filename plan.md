# Shipping Document Verification Gap Plan

## Problem and proposed approach

The repository already implements the full core workflow: rule-based and optional AI classification, seven-field extraction, SI/BL comparison, PDF/DOCX/XLSX reading, review decisions, retries, a dashboard, and regression tests. Verified end-to-end against the local ground truth (`Docker/data_v2/ground_truth.json`): `final_score: 1.0`, `stage1 macro_f1: 1.0`, `stage3 defect_f1: 1.0`, and `reliability.escalation_precision/recall: 1.0` (20/20 gold review cases, zero false escalations). The remaining work is genuinely-open reliability, evaluation-tooling, and observability gaps -- not correctness bugs against the provided data.

Prioritize:

1. Give `main.py` a first-class evaluator submission path instead of a manual `curl`/`Invoke-RestMethod` workaround.
2. Add an audit trail to reviewer decisions (history + reopen), not just a single overwritten record.
3. Add basic operational observability: a run id, batch progress, and retry counts.
4. Add test coverage for gaps found during review: static asset serving, concurrent review writes, and a broader sample of document formats/edge cases.
5. Keep documentation in sync with the current file layout (`main/dashboard/` split, `--submit-url`, env vars).

## Status of previously-identified gaps

- **Evaluator submission workflow:** was open, now implemented. `main.py --submit-url <server>` posts the generated submission and prints the scoreboard; `main/inbox.py`'s dependency-free fallback class also gained a `submit()` method to match `Bundle.loader.Inbox`, for environments (Docker/Cloud Run) where `Bundle/` isn't shipped.
- **~~Missing-document policy~~ -- RESOLVED, do not revisit:** a zero-attachment `BL_COMPARISON` email that's just a forward-looking "please prepare the draft BL for X later" request correctly resolves `OK` (nothing exists yet to compare, nothing has gone wrong). Only a body that explicitly signals an anomaly ("attachments appear to have been dropped", "still missing", etc.) escalates to `NEEDS_REVIEW/missing_attachment`. This exact split was verified against ground truth: 91 emails resolve `OK`, 3 escalate, matching gold exactly. **Do not "fix" this back toward always escalating zero-attachment BL_COMPARISON emails** -- that reintroduces a bug that was already found and eliminated.
- **Low-confidence classification review:** partially addressed. `classify_email`'s only low-confidence branch is the terminal `GENERAL` fallback (confidence 0.7); every other branch already scores >= 0.85. Flagging every `GENERAL` result as "needs review" would just recreate 60 false-positive reviews on data that's already 100% correctly classified, so instead each case now carries an internal `classification_confidence` and a `classification_fallback` flag (true when nothing more specific matched) for a reviewer to *spot-check* -- it does not change `submission.json`'s schema or force escalation.
- **Review lifecycle (versioning/reopen):** implemented. `review_decisions.json` entries now keep a `history` list of every prior decision instead of overwriting, and a `reopen` action is available alongside `resolve`.
- **Operational observability:** partially implemented. `generate_submission()` now stamps a `run_id` on every case and logs batch progress; the dashboard's retry action now increments a per-case `retry_count`. Structured export of review history and a UI display for the new audit trail are still open.
- **Test coverage:** improved, not complete. Added: static-asset-serving coverage (`style.css`/`dashboard.js`), a concurrent-write test proving the `threading.Lock`s added earlier actually prevent lost updates, and an expanded multiformat/edge-case extraction test (wrong-document-type text, a corrupted PDF). Full adversarial-format coverage and a UI/browser-level test are still open.
- **Extraction coverage (tables/multiline/unusual labels):** still a real caveat for *unseen* formats -- verified perfect (`field_f1: 1.0`) against everything in the provided bundle, but the parser is fundamentally label:value-line-oriented and will need hardening if judged against materially different document layouts.
- **Scanned-document support:** still no local OCR; the only path for image-only PDFs is Gemini vision (`ai_service.py::transcribe_pdf`). Without a key, those documents correctly escalate to `unreadable` rather than guessing. Revisit once Gemini is configured -- vision may make a separate local-OCR investment unnecessary.
- **Pairing/document-role UX:** still open. `select_document` lets a reviewer pick SI/BL manually, but there's no audit trail of *why* a role was reassigned beyond the generic decision history added above, and no broader workflow for ambiguous multi-attachment cases.
- **Documentation and packaging:** updated. `README.md` now reflects the `main/dashboard/` folder split, the `--submit-url` flag, and the AI/no-AI limitations honestly.

## Notes and considerations

- Preserve the existing seven canonical fields: shipper, consignee, notify party, port of loading, port of discharge, container count, and gross weight in kilograms.
- Keep SI as the reference document and never overwrite automated evidence when a reviewer corrects a value.
- Do not classify a genuine mismatch as a review-only result merely because values use different harmless formatting; reserve review for missing, unreadable, contradictory, or low-confidence evidence.
- Keep AI optional. A no-key local run must remain deterministic and must fail visibly when it cannot read a document.
- Before changing escalation/classification behavior again, re-run `Docker/server/score_cli.py` (or `--submit-url`) against the local ground truth and diff the `reliability` block -- that's what caught the last regression risk.
