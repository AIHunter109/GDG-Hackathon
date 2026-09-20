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

Open `http://127.0.0.1:8765`. The dashboard shows the email inbox, message details, attachment previews, SI/BL comparison, evidence, human review, and analytics. Reviewer actions are stored in `review_decisions.json`. A corrected value, an equivalence decision, document selection, or retry updates the case and submission. A correction email draft is available only after a mismatch is confirmed; the reviewer must send it separately.

The included `Docker/server/score_cli.py` can evaluate a local bundle when ground truth is available:

```powershell
.venv\Scripts\python.exe Docker\server\score_cli.py submission.json --json
```

## Cloud Run deployment

The root `Dockerfile` builds the same web app for Cloud Run. Set up a Google Cloud project, billing, a Firestore Native database, and a Cloud Storage bucket. Install and authenticate the Google Cloud CLI, then use an account with deployment and data upload permissions. Keep the service authenticated until judge access is configured.

```powershell
$projectId = '<PROJECT_ID>'
$region = 'asia-southeast1'
$bucketName = '<GLOBALLY_UNIQUE_BUCKET_NAME>'
$serviceAccount = "shipping-verifier@$projectId.iam.gserviceaccount.com"
gcloud config set project $projectId
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com storage.googleapis.com secretmanager.googleapis.com
gcloud firestore databases create --database='(default)' --location=$region --type=firestore-native
gcloud storage buckets create "gs://$bucketName" --location=$region --uniform-bucket-level-access --public-access-prevention
gcloud iam service-accounts create shipping-verifier
gcloud projects add-iam-policy-binding $projectId --member="serviceAccount:$serviceAccount" --role='roles/datastore.user'
gcloud storage buckets add-iam-policy-binding "gs://$bucketName" --member="serviceAccount:$serviceAccount" --role='roles/storage.objectViewer'
gcloud run deploy shipping-verifier --source . --region=$region --service-account=$serviceAccount --no-allow-unauthenticated --set-env-vars="GCS_BUCKET=$bucketName,GOOGLE_CLOUD_PROJECT=$projectId"
```

For Gemini, put the API key in Secret Manager as `gemini-api-key`, grant the Cloud Run service account Secret Manager Secret Accessor on that secret, and update the service with `--set-secrets=GEMINI_API_KEY=gemini-api-key:1`. Do not put the key in source control. The AI service uses the [Gemini generateContent API](https://ai.google.dev/api/generate-content) with JSON output.

Seed the case database and attachment bucket using Application Default Credentials on the upload machine:

```powershell
$env:GOOGLE_CLOUD_PROJECT = $projectId
$env:GCS_BUCKET = $bucketName
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv pip install --python .venv\Scripts\python.exe -r main\requirements-cloud.txt
gcloud auth application-default login
.venv\Scripts\python.exe main\seed_cloud.py --source Bundle
```

Cloud Run reads case state from Firestore and original attachments from Cloud Storage. Its service account uses [Application Default Credentials](https://docs.cloud.google.com/run/docs/integrate/using-gcp-services). Grant judges the Cloud Run Invoker role when the deployment is ready for review. The local competition bundle and generated files remain excluded from the container image and Git.
