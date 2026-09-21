# Shipping Document Verification: Implementation Status

## Working prototype

The application has three layers:

1. **AI understanding:** `ai_service.py` uses Gemini, when `GEMINI_API_KEY` is configured, for uncertain email intent, ambiguous SI/BL roles, unfamiliar field labels, image-only PDF transcription, and on-demand review explanations. It validates categories, attachment paths, and quoted field evidence before accepting model output. Native extraction remains the first path.
2. **Deterministic verification:** `extractor.py`, `comparator.py`, and `main.py` read TXT/XLSX/DOCX/PDF, normalize the seven required fields, check document consistency and shipment identifiers, compare SI against BL, and produce the exact competition submission schema. Low-confidence extraction goes to review.
3. **Web and cloud application:** `dashboard.py` serves the Dashboard, Case Detail, attachment preview, comparison, and human-review actions. `repository.py` supports local files and Google Cloud Firestore/Cloud Storage. The root `Dockerfile` packages the app for Cloud Run. `seed_cloud.py` uploads a participant bundle and processed cases.

## User workflow

The Dashboard is the home page and has summary totals, one search/category/status filter row, and an email table with status, attachment count, and update time. Selecting an email opens one Case Detail page with a result summary, the seven SI/BL fields, clickable discrepancy rows, reviewer actions, and expandable email and attachments. Normalized values appear when they clarify a match. Reviewers can confirm or correct a selected field, mark two values equivalent, select SI and BL documents, retry extraction, confirm a mismatch, resolve a case, and draft a correction email after confirming a mismatch. A retry shows Processing; PDF cases can use OCR/Vision when Gemini is configured. Value actions rerun comparison. Drafts are never sent automatically.

The Dashboard summary shows processing totals, document checks, matching and mismatching cases, and unresolved human reviews. No unsupported savings claims are shown.

## No-billing cloud demo

The browser edition is deployed at [seal-509214.web.app](https://seal-509214.web.app) on Firebase Hosting. The project remains on Spark with billing disabled. Google sign-in and Firestore rules limit the original email dataset to the project owner's verified Google account. Firestore holds 520 processed cases, including the original email metadata and body, seven-field evidence, and extractable attachment text. Original attachment binaries are not uploaded. The Dashboard labels source file counts as Attachments. The original bundle contains 394 emails with zero attachments, 2 with one, and 124 with two. The email table has category and status filters together; New email history and Add a new email appear below the main table. New submissions capture sender, recipient, subject, body, and two SI/BL files; reviewer changes and submissions are saved separately under the signed-in user. A reviewer upload of email_013 documents was previously counted as a new case; it is hidden from the dashboard while its saved record remains in the private workspace. Reanalysis of an original email is started from its Case Detail and updates that email instead of adding another entry. The public site contains only the app code and Firebase web configuration, so visitors must sign in to see the emails. The free hosted edition uses Firebase AI Logic with the Gemini Developer API for explicit PDF/TXT analysis and review explanations; deterministic comparison remains the final decision path. The Python server, Cloud Storage integration, and Cloud Run deployment remain separate, optional paths that require billing. See `spark/README.md`.

## Verification and current limits

- Local tests cover classification, file formats, role identification, extraction, normalization, internal validation, comparison, AI response validation, reviewer field confirmation, processing failure handling, and submission shape.
- The local scorer evaluates the complete bundle. Its main score measures the competition output and does not test Gemini or cloud connectivity.
- The project has an existing Gemini API key. A live synthetic email classification and image-only PDF OCR check passed with `gemini-3.6-flash`. Two `gemini-3.8-flash` calls returned HTTP 503, so `3.6` is the default. Without the key in the app environment the system remains deterministic.
- AI-transcribed PDFs are sent to review because image-derived text has lower trust. Damaged PDFs with no usable transcription are marked unreadable.
- The hosted page and configuration return HTTP 200. Firestore contains 520 distinct case IDs from `email_001` through `email_520`, and an unauthenticated read of a case returns HTTP 403. A synthetic Firebase AI Logic call succeeded. Google sign-in is enabled and the `web.app` domain is authorized. The Python server's Cloud Storage integration remains unexercised.
- The first owner browser sign-in exposed a Firestore permission error when the app listed reviewer cases. The owner account matched the rules, but a document-ID condition on the collection read prevented the query. The revised rule is deployed; rule tests pass for owner bundle and reviewer-workspace reads, and deny other users and unauthenticated access. Browser confirmation after the fix is pending.
- The hosted case details show extractable attachment text. Original PDFs are not stored in the cloud; reviewers can select their local SI and BL files to retry OCR with Gemini. Original bundle documents are never sent to Gemini automatically.
- Human usability tasks and an observation template are in `USER_TESTING.md`. A human session has not yet been conducted.
