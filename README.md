# SEAL — Shipping Document Verification

The pipeline reads `Bundle.loader.Inbox`, classifies each email, compares the seven official SI and draft BL fields, and writes the competition schema to `submission.json`. It also writes detailed extraction and review evidence to `evidence.json`.

The seven verified fields are:

- Shipper
- Consignee
- Notify party
- Port of loading
- Port of discharge
- Container count
- Gross weight in kilograms

## What the system provides

- **Email classification:** separates document comparisons, SI requests, invoice queries, general email, and spam.
- **Document processing:** reads TXT, XLSX, DOCX, and PDF in the Python pipeline. Native extraction runs first; Gemini is used only for uncertain intent, ambiguous document roles, unfamiliar labels, image-only PDFs, requested review explanations, and optional correction-email wording.
- **Deterministic verification:** normalizes the seven fields, checks shipment identifiers and document totals, then reports no mismatch, mismatch, human review, processing, or processing failure. AI does not make the final comparison decision.
- **Reviewer workspace:** centers the main workflow on verifying documents and reviewing exceptions. Four operational totals, concise validation evidence, verification outcomes, human-review reasons, filters, the original email dataset, and new-submission history remain visible without turning the workspace into a generic analytics page.
- **Validation results:** presents a small set of dataset-specific results: emails evaluated, expected reviews identified, missed reviews, and field-extraction coverage. Technical precision and recall remain in the scorer output and documentation instead of dominating the dashboard.
- **Interactive review views:** operational totals, verification outcomes, and human-review reason cards filter the email table so reviewers can move directly from a count to the affected cases.
- **Email filtering:** supports search, category, status, and real-timestamp date filters for Today, Last 7 Days, Last 30 Days, All Time, and a custom range. The interface does not manufacture Daily/Weekly/Monthly trends from the seeded snapshot.
- **Case Detail:** makes the seven-field SI and draft BL comparison and discrepancy count the main result, with source evidence, normalized values, processing methods, validation results, and reviewer actions together.
- **Source evidence:** mismatch details show the SI and draft BL source text so reviewers can inspect why a result was produced.
- **Human review:** reviewers can confirm or correct a field, mark values equivalent, choose which attachment is the SI or draft BL, retry extraction or OCR, confirm a mismatch, resolve a review, and reopen a completed review.
- **Correction drafting:** creates a draft only after a mismatch is confirmed. The application never sends it automatically.
- **Combined upload:** one **Verify documents** function near the top of the hosted workspace supports a single email with SI/BL files, mixed SI/BL files or a folder, and separate SI and BL folders.
- **Batch safety:** previews pairs before processing, leaves unclear files unprocessed for manual pairing, handles 1–10 pairs per run, pauses after the current pair, retries failures, and skips a saved pair with the same pairing key, filenames, sizes, and modified timestamps.
- **Visible AI and cloud integration:** the workspace states where Gemini assists uncertain extraction or OCR, where deterministic rules make the final decision, and where Firebase Authentication and Firestore protect reviewer data.
- **Private cloud demo:** Firebase Authentication and Firestore rules restrict the original dataset to the verified project owner. The public Hosting files contain no original email data or attachment binaries.

### Status meanings

- **No mismatch detected:** All seven required normalized SI and draft BL values are present and agree.
- **Mismatch detected:** At least one verified field differs.
- **Human review required:** A document, value, role, identifier, or AI-derived extraction needs reviewer confirmation.
- **Human review completed:** A reviewer resolved the case. It can be reopened from Case Detail.
- **Processing:** Verification or reanalysis is running.
- **Processing failed:** A technical step failed and can be retried.
- **No SI/BL comparison required:** The email was classified successfully, but it is spam, an SI request, an invoice query, a general email, or a draft-request thread without an actionable SI and draft BL pair. Its category remains visible in the Category column.

### Implementations

#### Local Python app

- **Source dataset:** Local `Bundle` or a compatible URL.
- **Supported documents:** TXT, XLSX, DOCX, and PDF.
- **AI access:** Server-side `GEMINI_API_KEY`.
- **Saved state:** Local JSON files.
- **Attachments:** Read directly from the local bundle.
- **Deployment:** Local server or optional Cloud Run.

#### Hosted Firebase app

- **Source dataset:** 520 private Firestore records.
- **Supported documents:** Existing processed records and new PDF/TXT uploads.
- **AI access:** Firebase AI Logic in the signed-in browser.
- **Saved state:** Private Firestore workspace.
- **Attachments:** Original binaries stay local; extracted text is private in Firestore.
- **Deployment:** Firebase Hosting on the Spark plan.

### Where each function is implemented

- [main/classify.py](main/classify.py) classifies email intent using subject, body, and attachment-name evidence.
- [main/extractor.py](main/extractor.py) reads documents, identifies SI and draft BL roles, extracts and normalizes fields, and validates shipment identifiers and document totals.
- [main/comparator.py](main/comparator.py) compares only the seven official normalized fields.
- [main/main.py](main/main.py) coordinates classification, extraction, validation, fallback AI, status decisions, and competition output.
- [main/ai_service.py](main/ai_service.py) contains the validated Gemini fallbacks for uncertain intent, document roles, unfamiliar labels, PDF transcription, and review explanations.
- [main/review_actions.py](main/review_actions.py) applies reviewer confirmation, correction, and equivalence decisions before rerunning deterministic comparison.
- [main/dashboard.py](main/dashboard.py) and [main/dashboard.html](main/dashboard.html) provide the local API, Dashboard, Case Detail, clickable operational totals, validation results, date filters, verification and review-reason summaries, processing-method explanations, evidence views, retry actions, and review workflow.
- [spark/src/app.js](spark/src/app.js) connects the hosted interface to Google sign-in, private Firestore records, Firebase AI Logic, reanalysis, and new-email history.
- [spark/src/batch.js](spark/src/batch.js) and [spark/src/batch_ui.js](spark/src/batch_ui.js) implement mixed-file and folder pairing, preview, manual pairing, limits, pause, retry, and duplicate skipping.
- [spark/firestore.rules](spark/firestore.rules) restricts the original dataset and reviewer workspace to the verified owner account.
- [main/review_diagnostics.py](main/review_diagnostics.py) reports why cases enter human review and, when ground truth is supplied, counts false and missed reviews.

### Python pipeline decision order

1. Classify the email. A low-confidence rules result can use Gemini, but only a validated high-confidence response replaces it.
2. For document-comparison emails, determine whether the thread is waiting for documents or contains an actionable SI/BL comparison request.
3. Read the attachments, identify the SI and draft BL, and extract the seven fields. Native parsing runs before AI fallback. AI category and role responses are restricted to known values, AI field values require exact quoted source evidence, and AI PDF transcriptions are assigned lower trust.
4. Validate strong shipment identifiers and internal container/weight totals.
5. Require human review when a necessary document or value is missing, a document is unreadable, roles or identifiers conflict, evidence is inconsistent, or extraction remains uncertain.
6. When evidence is complete and dependable, compare normalized SI and draft BL values. Any difference produces **Mismatch detected**; otherwise the result is **No mismatch detected**.
7. Rerun steps 4–6 after a reviewer correction, confirmation, equivalence decision, document-role selection, or retry.

## Run on Windows

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r main\requirements.txt
.venv\Scripts\python.exe main\main.py
.venv\Scripts\python.exe -m unittest discover -s main -p 'test_*.py'
```

`python main/main.py --source <bundle-or-http-url> --output <path> --evidence <path>` accepts another bundle or the organizer's inbox endpoint.

TXT, XLSX, and DOCX extraction use the Python standard library. Selectable PDF text uses pypdf. Set `GEMINI_API_KEY` to enable AI fallback for uncertain email intent, document roles, unfamiliar labels, and image-only PDF transcription. AI responses are schema checked; semantic field mappings must quote text present in the source document, and lower-confidence extraction is sent to human review. Decisions use `NEEDS_REVIEW` plus the bundle's allowed review reasons in the submission. More specific internal reasons and source text remain in `evidence.json`.

## Reviewer dashboard

```powershell
.venv\Scripts\python.exe main\dashboard.py
```

Open `http://127.0.0.1:8765`. The Dashboard is an operational verification workspace. The heading states the product purpose and exposes **Verify documents** and **Review exceptions** as the two primary actions. A compact status strip reports whether the dataset is loaded, Firestore is connected, and Gemini assistance is available.

The first summary row contains four operational counts:

- **Total emails**
- **Document checks**
- **Mismatches detected**
- **Human review required**

Selecting a summary card filters the email table to the related records. `Processing` remains a case status while work is running and is not a permanent headline card. The hosted **Verify documents** workflow appears near the top because SI/BL verification is the core task. Its stages are reading the email and attachments, identifying the SI and draft BL, extracting the seven fields, validating identifiers and totals, comparing normalized values, and returning no mismatch, mismatch, or human review.

The concise **Validation Results** section shows emails evaluated, expected review cases identified, missed reviews, and field-extraction coverage. Classification accuracy and mismatch precision or recall remain available in the scorer output and explanatory note. Results are explicitly labelled as evidence from the supplied validation dataset, not guaranteed performance on unseen production data. Coverage measures whether required values were found and is not described as extraction accuracy.

The **Verification Overview** focuses on document checks: verified with no mismatch, mismatch detected, and human review required. Other emails remain accessible through the category filters. **Human Review Reasons** groups unresolved cases into wrong document, missing attachment, missing value, unreadable document, or other. Selecting an overview or reason card filters the table.

Search, category, status, and date filters narrow the email table. The date choices are **Today**, **Last 7 Days**, **Last 30 Days**, **All Time**, and **Custom range**. Records without meaningful timestamps are excluded instead of assigned invented dates. The seeded validation snapshot does not produce artificial Daily/Weekly/Monthly charts. Each compact table row shows the subject with its sender underneath, category, verification status, attachment count, updated time, and the next action.

Opening a row takes the reviewer to Case Detail. The seven-field SI/BL comparison and discrepancy count are the main result. Mismatch rows open the relevant SI and draft BL source evidence. A processing-method panel explains that classification uses rules with a validated Gemini fallback, native document extraction runs first, Gemini Vision/OCR can assist scanned files when requested, and normalization, validation, comparison, and the final verification decision remain deterministic. Uncertain evidence goes to human review instead of being guessed.

A reviewer can confirm or correct a selected value, mark two values equivalent, choose the SI and draft BL, retry extraction, confirm a mismatch, resolve a review, or undo review completion. Value changes rerun deterministic comparison and update the case and submission. If Gemini is configured, an on-demand explanation can summarize review evidence and PDF cases can be retried with OCR/Vision. A correction email draft, optionally worded by AI, is available only after a mismatch is confirmed; the reviewer must send it separately.

The end-to-end design is:

```text
Email / Documents -> Classification -> Extraction -> Gemini fallback when needed -> Normalization and validation -> Deterministic SI/BL comparison -> Match / Mismatch / Human review
```

Firebase Authentication and Firestore support the private hosted workspace. Shipping staff can classify requests, inspect SI and draft BL evidence, verify the seven fields, investigate exceptions, and prepare corrections from one workspace.

The hosted browser edition has one **Verify documents** function for adding emails and documents in these modes:

1. **One email with SI and BL:** captures sender, recipient, subject, body, and two documents. If the documents belong to one of the original 520 emails, the app opens that email for reanalysis instead of creating a duplicate.
2. **Mixed SI/BL files or folder:** identifies roles from names and folders, pairs documents by a shared shipment key, and presents the proposed pairs before processing.
3. **Separate SI and BL folders:** pairs corresponding files across the two folders and presents incomplete or ambiguous matches for manual action.

The browser uploader accepts PDF and TXT files up to 5 MB each. TXT documents are read locally in the browser. In single-email mode, Gemini can fill missing TXT fields after the reviewer starts analysis; batch TXT processing remains local and routes incomplete extraction to review. PDFs use Gemini only after processing begins. Browser verification checks booking, OC, and BL identifiers plus explicit container rows and weight totals before comparing the seven fields. Batch processing handles 1–10 pairs per run and supports pause, retry, manual pairing, and duplicate skipping. Conflicting identifiers or totals are routed to human review. New emails and completed document batches appear under **New email history**; they do not change the count of 520 original emails.

An email that only asks someone to send a draft BL is treated as awaiting documents and does not create a human verification task. A message that explicitly asks to compare both the SI and draft BL still requires review when either document is missing. This keeps document-request threads out of the exception queue while preserving genuine missing-attachment cases.

Retries show a `Processing` state, then the updated result. Technical failures appear as `Processing failed` with the failed step and Retry action. The competition export maps these to the schema's `NEEDS_REVIEW` status and `unreadable` reason. A scanned document that cannot be transcribed remains `Human review required` in the app. Use [main/USER_TESTING.md](main/USER_TESTING.md) to run and record a human usability session.

The included `Docker/server/score_cli.py` can evaluate a local bundle when ground truth is available:

```powershell
.venv\Scripts\python.exe Docker\server\score_cli.py submission.json --json
```

The API also reports field extraction coverage, human-review rate, and processing-failure rate for the current cases. Coverage measures whether fields were found; measuring field extraction **accuracy** requires independently labeled field values. The scorer reports classification and mismatch results against its local ground truth. These distinct measures feed the Dashboard instead of a vague combined accuracy percentage.

To inspect why cases enter review, generate a diagnostic report. Supplying ground truth additionally reports false and missed reviews:

```powershell
.venv\Scripts\python.exe main\review_diagnostics.py
.venv\Scripts\python.exe main\review_diagnostics.py --ground-truth Docker\data_v2\ground_truth.json --output review_diagnostics.json
```

The current 520-email evaluation produces 20 reviews for 20 expected cases: five wrong-document cases, five missing-attachment cases, five unreadable cases, and five missing-value cases. It reports zero false reviews and zero missed reviews while retaining 100% category accuracy and exact mismatch results against the included ground truth. These figures are specific to the supplied validation dataset and are not presented as guaranteed performance on unseen production data.

### Demo highlights

- Email classification
- SI and draft BL document extraction
- Seven-field verification
- Mismatch detection with source evidence
- Human review for uncertain cases
- Scanned PDF handling with Gemini
- Reviewer correction followed by deterministic re-verification

The README documents prototype capabilities. Presentation timing and speaking notes belong in separate demo material.

## No-billing cloud demo

For the hackathon's AI and cloud criteria without enabling billing, use the [deployed Firebase Spark browser edition](https://seal-509214.web.app) and its [setup notes](spark/README.md). It uses Firebase Hosting, Google sign-in, owner-only Firestore access to all 520 processed bundle emails, and Firebase AI Logic with the Gemini Developer API free tier. Sign in with the verified project owner Google account to see the dataset. Reviewer decisions and new submissions are stored under the signed-in user's private Firestore workspace.

Original attachment binaries are not uploaded to Firebase. Private Firestore records contain email metadata, email bodies, extracted attachment text, field evidence, and verification results. Locally selected documents are sent to Gemini only when the reviewer explicitly starts analysis or OCR. Keep project billing disabled; the deployment script stops if billing is enabled.

### Build, test, and deploy the browser edition

```powershell
& 'C:\Program Files\nodejs\npm.cmd' test --prefix spark
& 'C:\Program Files\nodejs\npm.cmd' run build --prefix spark
.venv\Scripts\python.exe spark\deploy_hosting.py
```

The deployment script publishes Hosting files only after checking that project billing remains disabled. Firestore data seeding and security rule details are documented in [spark/README.md](spark/README.md).

## Optional Cloud Run deployment (billing required)

The root `Dockerfile` builds the Python server for Cloud Run. The target project is `seal-509214`. Its billing is currently disabled, so this optional path is not deployed; linking billing can incur charges. Firestore's default Standard database already exists in `asia-southeast1` for the Spark site. Cloud Run, Secret Manager, and Cloud Storage are not configured. The [Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install-sdk) is installed under `%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin`, though it is not on `PATH`. The deployer needs [Cloud Run source deployment permissions](https://docs.cloud.google.com/run/docs/deploying-source-code), including access to use the runtime service account; the build service account may also need `roles/run.builder`. Use an account with Firestore and bucket write access for seeding. Skip resource creation commands when a resource already exists.

```powershell
$env:PATH = "$(Join-Path $env:LOCALAPPDATA 'Google\Cloud SDK\google-cloud-sdk\bin');$env:PATH"
$projectId = 'seal-509214'
$region = 'asia-southeast1'
$bucketName = '<GLOBALLY_UNIQUE_BUCKET_NAME>'
$serviceAccount = "shipping-verifier@$projectId.iam.gserviceaccount.com"
gcloud config set project $projectId
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com storage.googleapis.com secretmanager.googleapis.com
gcloud storage buckets create "gs://$bucketName" --location=$region --uniform-bucket-level-access --public-access-prevention
gcloud iam service-accounts create shipping-verifier
gcloud projects add-iam-policy-binding $projectId --member="serviceAccount:$serviceAccount" --role='roles/datastore.user'
gcloud storage buckets add-iam-policy-binding "gs://$bucketName" --member="serviceAccount:$serviceAccount" --role='roles/storage.objectViewer'
gcloud run deploy shipping-verifier --source . --region=$region --service-account=$serviceAccount --no-allow-unauthenticated --set-env-vars="GCS_BUCKET=$bucketName,GOOGLE_CLOUD_PROJECT=$projectId"
```

The project already has a Gemini API key restricted to `generativelanguage.googleapis.com`; a new key is unnecessary. Create a Secret Manager secret named `gemini-api-key` with that key using the [Cloud Console](https://docs.cloud.google.com/secret-manager/docs/create-secret-quickstart), then grant the runtime service account access and attach version 1 to Cloud Run:

```powershell
gcloud secrets add-iam-policy-binding gemini-api-key --member="serviceAccount:$serviceAccount" --role='roles/secretmanager.secretAccessor'
gcloud run services update shipping-verifier --region=$region --set-secrets='GEMINI_API_KEY=gemini-api-key:1'
```

Do not put the key in source control or chat. The AI service uses the [Gemini generateContent API](https://ai.google.dev/api/generate-content) with JSON output. `main/check_gemini_live.py` verifies classification and OCR using only synthetic content. It passed with the existing key and `gemini-3.6-flash`; two calls to `gemini-3.8-flash` returned HTTP 503, so `3.6` is the current default. Set `GEMINI_MODEL` to choose a different model after verifying it. A real-document OCR test requires authorization to send that document to Gemini.

Seed the case database and attachment bucket using Application Default Credentials on the upload machine:

```powershell
$env:GOOGLE_CLOUD_PROJECT = $projectId
$env:GCS_BUCKET = $bucketName
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv pip install --python .venv\Scripts\python.exe -r main\requirements-cloud.txt
gcloud auth application-default login
.venv\Scripts\python.exe main\seed_cloud.py --source Bundle
```

Cloud Run reads case state from Firestore and original attachments from Cloud Storage. Its service account uses [Application Default Credentials](https://docs.cloud.google.com/run/docs/integrate/using-gcp-services). The deployment above remains private. To test it in a browser, use the [Cloud Run proxy](https://docs.cloud.google.com/run/docs/triggering/https-request) with an authorized account, or configure Identity-Aware Proxy for testers. Granting Invoker alone does not make a direct browser visit send credentials. For judge access, arrange the approved authenticated path before sharing the URL. The local competition bundle and generated files remain excluded from the container image and Git.

For user testing, ask two or three shipping operations reviewers to work through [main/USER_TESTING.md](main/USER_TESTING.md) without coaching. Record their actual task results and comments, make changes that address observed confusion, then repeat the tasks. The template intentionally contains no invented feedback.
