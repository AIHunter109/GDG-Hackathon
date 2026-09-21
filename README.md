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

`python main/main.py --source <bundle-or-http-url> --output <path> --evidence <path>` accepts another bundle or the organizer's inbox endpoint. Add `--submit-url <server>` (e.g. `http://localhost:8080`, or omit it when `--source` is itself an http(s) URL) to POST the generated submission straight to the server's `/submit` endpoint and print the scoreboard in one command, instead of a separate manual request. Pass `--quiet` to suppress the batch-progress log lines; each run stamps every case with a `run_id` (visible in `evidence.json`) so a specific run's cases can be told apart.

TXT, XLSX, and DOCX extraction use the Python standard library. Selectable PDF text uses pypdf. Set an API key (see below) to enable AI fallback for uncertain email intent, document roles, unfamiliar labels, and image-only PDF transcription.

**Two AI providers are supported**, chosen automatically by `AIService` from whichever key is set (`AI_PROVIDER=gemini` or `AI_PROVIDER=gonkarouter` forces one explicitly if you ever have both configured):
- **Gemini** -- set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`, default `gemini-2.5-flash`, a verified-working non-preview model).
- **GonkaRouter** -- an API gateway routing to third-party models (MiniMax, Kimi, Zhipu, DeepSeek); set `GONKAROUTER_API_KEY` (and optionally `GONKAROUTER_MODEL`, default `MiniMaxAI/MiniMax-M2.7`). If this key is present, it's used instead of Gemini unless `AI_PROVIDER=gemini` is set. `ai_service.py`'s only provider-specific code is in `_json_gemini`/`_json_gonkarouter` -- every higher-level method (classification, document-role detection, field mapping, PDF vision, review explanation, correction drafting) is written once and works against either provider identically, including the anti-hallucination and confidence-threshold guardrails.

**Configuring either key**: set it as a real environment variable (`$env:GEMINI_API_KEY = "..."` for the current terminal, or `setx GEMINI_API_KEY "..."` to persist across new terminals/processes), or copy `.env.example` to `.env` at the repo root and fill it in there. `.env` is gitignored and never committed. A real environment variable always takes priority over `.env` if both are set -- `.env` only fills in whatever isn't already set. Every entry point (`main.py`, `dashboard.py`, `check_gemini_live.py`) loads `.env` automatically at startup via `main/dotenv_loader.py`, a small dependency-free parser (no `python-dotenv` package needed, consistent with this project's existing habit of hand-rolling small parsers instead of adding a dependency for something simple). Run `python main/check_gemini_live.py` any time to verify whichever provider/key you've configured actually works live, without touching your real data. AI output is checked against source evidence and low-confidence extraction is sent to human review. The model never performs the final SI/BL comparison. Decisions use `NEEDS_REVIEW` plus the bundle's allowed review reasons in the submission. More specific internal reasons and source text remain in `evidence.json`.

**Known limitations, stated plainly:** without a Gemini key, image-only/scanned PDFs have no local OCR fallback and correctly escalate to `unreadable` rather than being read at all. Field extraction is fundamentally line-oriented (`label: value`); it's been verified against every document format and edge case in the provided bundle (`field_f1: 1.0` against ground truth) but hasn't been tested against materially different table layouts. Each case also carries a `classification_confidence` and `classification_fallback` flag (true when nothing more specific than the generic fallback matched) purely for a reviewer to spot-check -- it never changes `submission.json`'s schema or forces escalation on its own.

## Reviewer dashboard

```powershell
.venv\Scripts\python.exe main\dashboard.py
```

Open `http://127.0.0.1:8765`. The Dashboard is the home page (served from `main/dashboard/dashboard.html`, `dashboard.js`, and `style.css`). Its clickable summary cards and quick filters narrow one master case table; opening a row takes you to its Case Detail page. The detail page keeps the seven-field SI/BL comparison, clickable mismatch evidence, human-review actions, and expandable email and attachments together. A reviewer can confirm or correct a selected value, mark two values equivalent, choose the SI and draft BL, retry extraction, confirm a mismatch, resolve a review, or **reopen** a resolved case for another look. Every reviewer decision keeps a `history` list of what was decided before it, instead of silently overwriting the prior decision -- that full audit trail lives in `review_decisions.json`. Each retry also increments the case's `retry_count`. Value changes rerun the comparison and update the case and submission. If an AI provider is configured, a background worker inside `dashboard.py` automatically gives every unresolved `NEEDS_REVIEW` case a short opinion -- an assessment, a verbatim quote of evidence as proof, and a suggested next step -- so a reviewer can open the dashboard at any time and see "AI reviewing..." while it's still working or the finished report once it's ready, with no button to click and no request to wait on. The worker only fills a gap the rules already gave up on; it never revisits a decision the rules or a reviewer already made, and its proof is rejected if it isn't a literal quote from the case's own evidence. PDF cases can still be retried with OCR/Vision. A correction email draft, optionally worded by AI, is available only after a mismatch is confirmed; the reviewer must send it separately.

Retries show a `Processing` state, then the updated result. Technical failures appear as `Processing failed` with the failed step and Retry action. The competition export maps these to the schema's `NEEDS_REVIEW` status and `unreadable` reason. A scanned document that cannot be transcribed remains `Human review required` in the app. Use [main/USER_TESTING.md](main/USER_TESTING.md) to run and record a human usability session.

The included `Docker/server/score_cli.py` can evaluate a local bundle when ground truth is available:

```powershell
.venv\Scripts\python.exe Docker\server\score_cli.py submission.json --json
```

The API also reports field extraction coverage, human-review rate, and processing-failure rate for the current cases. Coverage measures whether fields were found; measuring field extraction **accuracy** requires independently labeled field values. The scorer reports classification and mismatch results against its local ground truth.

## Cloud Run deployment

The root `Dockerfile` builds the same web app for Cloud Run. Set up a Google Cloud project with billing, a Firestore Native database, and a Cloud Storage bucket. Install the [Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install-sdk) and run `gcloud init` to sign in and select the project. The deployer needs [Cloud Run source deployment permissions](https://docs.cloud.google.com/run/docs/deploying-source-code), including access to use the runtime service account; the build service account may also need `roles/run.builder`. Use an account with Firestore and bucket write access for seeding. Skip the database, bucket, or service account creation commands below if those resources already exist.

```powershell
$projectId = '<PROJECT_ID>'
$region = 'asia-southeast1'
$bucketName = '<GLOBALLY_UNIQUE_BUCKET_NAME>'
$serviceAccount = "shipping-verifier@$projectId.iam.gserviceaccount.com"
gcloud config set project $projectId
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com storage.googleapis.com secretmanager.googleapis.com
gcloud firestore databases create --database='(default)' --location=$region --edition=standard --type=firestore-native
gcloud storage buckets create "gs://$bucketName" --location=$region --uniform-bucket-level-access --public-access-prevention
gcloud iam service-accounts create shipping-verifier
gcloud projects add-iam-policy-binding $projectId --member="serviceAccount:$serviceAccount" --role='roles/datastore.user'
gcloud storage buckets add-iam-policy-binding "gs://$bucketName" --member="serviceAccount:$serviceAccount" --role='roles/storage.objectViewer'
gcloud run deploy shipping-verifier --source . --region=$region --service-account=$serviceAccount --no-allow-unauthenticated --set-env-vars="GCS_BUCKET=$bucketName,GOOGLE_CLOUD_PROJECT=$projectId"
```

For Gemini, create a new auth [Gemini API key in Google AI Studio](https://ai.google.dev/gemini-api/docs/api-key). Create a Secret Manager secret named `gemini-api-key` with that value using the [Cloud Console](https://docs.cloud.google.com/secret-manager/docs/create-secret-quickstart), then grant the runtime service account access and attach version 1 to Cloud Run:

```powershell
gcloud secrets add-iam-policy-binding gemini-api-key --member="serviceAccount:$serviceAccount" --role='roles/secretmanager.secretAccessor'
gcloud run services update shipping-verifier --region=$region --set-secrets='GEMINI_API_KEY=gemini-api-key:1'
```

Do not put the key in source control or chat. The AI service uses the [Gemini generateContent API](https://ai.google.dev/api/generate-content) with JSON output. Verify it with a synthetic email and a scanned PDF retry; a configured key alone does not prove the live calls work.

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
