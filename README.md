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

Open `http://127.0.0.1:8765`. The Dashboard is the home page. Its clickable summary cards and quick filters narrow one master case table; opening a row takes you to its Case Detail page. The detail page keeps the seven-field SI/BL comparison, clickable mismatch evidence, human-review actions, and expandable email and attachments together. A reviewer can confirm or correct a selected value, mark two values equivalent, choose the SI and draft BL, retry extraction, confirm a mismatch, or resolve a review. Value changes rerun the comparison and update the case and submission. If Gemini is configured, an on-demand AI explanation can summarize review evidence and PDF cases can be retried with OCR/Vision; the verification rules still decide the result. A correction email draft, optionally worded by AI, is available only after a mismatch is confirmed; the reviewer must send it separately.

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
