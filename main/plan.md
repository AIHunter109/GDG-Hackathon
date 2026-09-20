# Shipping Document Verification: Implementation Status

## Working prototype

The application has three layers:

1. **AI understanding:** `ai_service.py` uses Gemini, when `GEMINI_API_KEY` is configured, for uncertain email intent, ambiguous SI/BL roles, unfamiliar field labels, and image-only PDF transcription. It validates categories, attachment paths, and quoted field evidence before accepting model output. Native extraction remains the first path.
2. **Deterministic verification:** `extractor.py`, `comparator.py`, and `main.py` read TXT/XLSX/DOCX/PDF, normalize the seven required fields, check document consistency and shipment identifiers, compare SI against BL, and produce the exact competition submission schema. Low-confidence extraction goes to review.
3. **Web and cloud application:** `dashboard.py` serves the inbox, case detail, attachment preview, comparison, human review, and analytics views. `repository.py` supports local files and Google Cloud Firestore/Cloud Storage. The root `Dockerfile` packages the app for Cloud Run. `seed_cloud.py` uploads a participant bundle and processed cases.

## User workflow

The inbox shows sender, subject, classification and confidence, attachment count, and result. Selecting a case opens the email body, document roles and previews, all seven raw and normalized values, match status, evidence, and reviewer actions. Reviewers can correct a field, mark two values equivalent, select SI and BL documents, retry extraction, confirm a mismatch, resolve a case, and draft a correction email after confirming a mismatch. Drafts are never sent automatically.

The analytics view shows processing totals, category and status distributions, review volume, and automation rate. No unsupported savings claims are shown.

## Deployment still requiring project access

The local app is running. Deployment to Cloud Run and seeding Firestore/Cloud Storage require a Google Cloud project, billing and IAM access, an existing bucket and Firestore database, and optional Gemini API access. Deployment instructions are in the root `README.md`. The cloud service should remain authenticated until access for judges is configured.

## Verification and current limits

- Local tests cover classification, file formats, role identification, extraction, normalization, internal validation, comparison, AI response validation, and submission shape.
- The local scorer evaluates the complete bundle. Its main score measures the competition output and does not test Gemini or cloud connectivity.
- Gemini behavior needs a configured API key and a live integration check. Without a key the system remains deterministic.
- AI-transcribed PDFs are sent to review because image-derived text has lower trust. Damaged PDFs with no usable transcription are marked unreadable.
- Firestore and Cloud Storage integration is implemented but cannot be exercised until project credentials are available.
