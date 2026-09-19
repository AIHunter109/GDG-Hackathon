# Shipping Document Verification Implementation Plan

## Problem and approach

The repository has starter modules for email classification, document extraction, comparison, and human escalation, but they are currently placeholders. The use case requires processing every inbox email, classifying it, comparing SI and BL documents for comparison requests, and producing the `sample_submission.json` shape with explicit mismatch details and review cases.

Implement a small, testable pipeline using the existing `Bundle.loader.Inbox` interface:

1. Load each email and its attachments.
2. Classify messages into the dataset's expected categories, using message content and attachment names rather than subject alone.
3. Extract the seven required fields from plain-text documents first, with normalized labels and values.
4. Compare SI as the source of truth against BL, preserving both values for every mismatch.
5. Escalate missing, unreadable, or ambiguous data to human review instead of inventing values.
6. Write a complete submission for every email and validate its schema locally.

The advanced PDF/DOCX/OCR support should be treated as a second stage after the plain-text pipeline is reliable. The current `main.py` also needs its import-path setup moved before local-module imports so direct execution remains reliable.

## Todos

- Inspecting dataset schema and category conventions
- Implementing robust email classification
- Implementing normalized SI and BL field extraction
- Implementing field-by-field comparison and mismatch reporting
- Implementing explicit human-review cases
- Wiring the end-to-end submission pipeline
- Adding focused tests and schema validation
- Evaluating output against the local scoring endpoint
- Adding optional PDF, DOCX, and OCR extraction support

## Notes and considerations

- Required comparison fields: shipper, consignee, notify party, port of loading, port of discharge, container count, and gross weight in kilograms.
- Equivalent labels such as `Load Port` and `Port of Loading` must map to the same canonical field.
- Numeric comparison should normalize commas, units, and harmless formatting differences; text comparison should normalize whitespace and case without hiding meaningful differences.
- Every inbox email must appear in the output, including non-comparison categories.
- A comparison result should use `status: MISMATCH` only when a dependable difference is found; complete matches should use `status: OK` with no defect fields.
- Missing attachments, missing required fields, unreadable files, and low-confidence parsing should produce a review reason and source context.
- Keep generated `submission.json`, local data bundles, and caches out of version control via `.gitignore`.
