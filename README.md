# Shipping document verification

The pipeline reads `Bundle.loader.Inbox`, classifies each email, compares the seven official SI and draft BL fields, and writes the competition schema to `submission.json`. It also writes detailed extraction and review evidence to `evidence.json`.

## Run on Windows

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r main\requirements.txt
.venv\Scripts\python.exe main\main.py
.venv\Scripts\python.exe -m unittest discover -s main -p 'test_*.py'
```

`python main/main.py --source <bundle-or-http-url> --output <path> --evidence <path>` accepts another bundle or the organizer's inbox endpoint.

TXT, XLSX, and DOCX extraction use the Python standard library. Selectable PDF text uses pypdf. Set `GEMINI_API_KEY` to enable AI fallback for uncertain email intent, document roles, unfamiliar labels, and image-only PDF transcription. AI output is checked against source evidence and low-confidence extraction is sent to human review. The model never performs the final SI/BL comparison. Decisions use `NEEDS_REVIEW` plus the bundle's allowed review reasons in the submission. More specific internal reasons and source text remain in `evidence.json`.

## Reviewer dashboard

```powershell
.venv\Scripts\python.exe main\dashboard.py
```

Open `http://127.0.0.1:8765`. The Dashboard is the home page. Summary cards show totals, while search, category, and status filters narrow the email table; opening a row takes you to its Case Detail page. The detail page keeps the seven-field SI/BL comparison, clickable mismatch evidence, human-review actions, and expandable email and attachments together. A reviewer can confirm or correct a selected value, mark two values equivalent, choose the SI and draft BL, retry extraction, confirm a mismatch, resolve a review, or undo review completion. Value changes rerun the comparison and update the case and submission. If Gemini is configured, an on-demand AI explanation can summarize review evidence and PDF cases can be retried with OCR/Vision; the verification rules still decide the result. A correction email draft, optionally worded by AI, is available only after a mismatch is confirmed; the reviewer must send it separately.

The hosted browser edition has one **Add emails and documents** function with modes for one email, mixed PDF/TXT files or folder, and separate SI and BL folders. Batch modes preview filename-based pairs before processing, keep unclear or incomplete pairs out of verification, support manual pairing, process up to 10 pairs per run, and skip previously saved file signatures. Complete TXT documents are extracted locally; PDFs are sent to Gemini only after the reviewer starts processing.

Retries show a `Processing` state, then the updated result. Technical failures appear as `Processing failed` with the failed step and Retry action. The competition export maps these to the schema's `NEEDS_REVIEW` status and `unreadable` reason. A scanned document that cannot be transcribed remains `Human review required` in the app. Use [main/USER_TESTING.md](main/USER_TESTING.md) to run and record a human usability session.

The included `Docker/server/score_cli.py` can evaluate a local bundle when ground truth is available:

```powershell
.venv\Scripts\python.exe Docker\server\score_cli.py submission.json --json
```

The API also reports field extraction coverage, human-review rate, and processing-failure rate for the current cases. Coverage measures whether fields were found; measuring field extraction **accuracy** requires independently labeled field values. The scorer reports classification and mismatch results against its local ground truth.

## No-billing cloud demo

For the hackathon's AI and cloud criteria without enabling billing, use the [deployed Firebase Spark browser edition](https://seal-509214.web.app) and its [setup notes](spark/README.md). It uses Hosting, owner-only Firestore access to all 520 processed bundle emails, and Firebase AI Logic with Gemini's free tier. Sign in with the project owner Google account to see the dataset. The site's public files do not include the original emails or attachment binaries. Keep the project's billing disabled.

## Cloud Run deployment (billing required)

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
