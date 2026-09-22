# SEAL -- Shipping Document Verification

Shipping teams manually cross-check Shipping Instructions (SI) against draft Bills of Lading (BL) across scattered email threads and mixed document formats -- a slow, error-prone process where one missed mismatch becomes a wrong bill of lading downstream. SEAL automates that verification end to end: it classifies incoming emails, extracts the seven fields required for comparison, deterministically checks SI against draft BL, and escalates only genuine exceptions to a human reviewer.

Verified against the competition's ground truth across 520 emails, SEAL scores **1.0** -- perfect classification, perfect defect detection, and perfect escalation precision and recall.

## Technical Architecture

The pipeline is pure Python, standard library only (`pypdf` is the one real dependency). An email moves through five deterministic stages: **classify** intent with rule-based regex -> **extract** the seven required fields from SI/BL attachments (TXT, XLSX, DOCX, PDF) -> **normalize** values so formatting differences don't cause false mismatches -> **compare** SI against draft BL -> **decide**: cleared, mismatch, or human review. AI never participates in classification or the final match decision -- both stay fully deterministic (see *Challenges Faced*).

The reviewer-facing dashboard and the API are served by the same single Python process (`main/dashboard.py`, a raw `http.server` app -- no framework), so there's nothing separate to deploy for frontend versus backend. Storage is abstracted behind one interface with two implementations, selected automatically by an environment variable: `LocalRepository` (plain JSON files on disk, zero cloud dependency) for local development, and `CloudRepository` (Firestore for case state, Cloud Storage for attachments) for the hosted deployment. The live public demo runs this exact codebase on **Google Cloud Run**, built straight from the repo's `Dockerfile`.

A background worker thread inside the dashboard process continuously scans for cases sent to human review and generates an AI opinion for each one asynchronously -- a reviewer sees a live "reasoning" status rather than waiting on a blocking request, and the worker never touches a decision the rules already made.

AI itself is provider-agnostic: a single internal interface (`AIService`) supports both **Gemini** and **GonkaRouter** (an API gateway to third-party models), auto-detected from whichever API key is configured, with every higher-level capability -- document-role detection, field-label mapping, PDF vision, review opinions -- written once and working identically against either backend.

## Implementation Details

**Extraction** is label-driven, not layout-driven: it scans document text for lines matching patterns like `Shipper:` or `Port of Loading (POL):`, pulls the adjacent value, and normalizes it -- a container count written as `5 x 40'HC` or `40'HC x 5` both resolve to `5`, and weights given in metric tons convert to kilograms automatically. Every extracted value keeps a pointer back to its exact source line, so a reviewer can always verify why the system read what it read. This was validated field-by-field against every document format and edge case in the provided bundle (`field_f1: 1.0` against ground truth).

**AI's role is deliberately narrow**, on principle: it fills gaps the deterministic rules can't close, and never overrides a decision the rules already made. Concretely, that's ambiguous document-role detection (which attachment is the SI versus the BL), mapping field labels the rules don't recognize, and OCR/vision transcription for scanned PDFs with no extractable text. Every AI-generated review opinion is required to include a **verbatim quote from the case's own evidence as proof** -- if the model can't point to something real in the source data, the entire response is discarded rather than shown to a reviewer, closing off hallucinated justifications.

**Bulk document upload** lets a reviewer add new emails to the system directly through the dashboard -- a single email with its SI/BL pair, or a batch of many files at once, auto-paired by a filename-matching algorithm (shared shipment IDs stripped of role words like "SI"/"BL"/"draft") ported from a teammate's separate browser-based implementation into this Python backend, extended to also accept XLSX/DOCX (not just PDF/TXT) since the extractor already parses those natively.

Where a small dependency-free parser was needed, we hand-rolled it rather than adding a library -- a `.env` loader, a `multipart/form-data` parser for file uploads -- consistent with the project's stdlib-first design throughout.

## Challenges Faced

**AI hurt accuracy when given a vote in classification.** Early on, low-confidence rule-based classifications were escalated to AI as a tiebreaker. This backfired on a deliberately adversarial email in the dataset -- a bulk "reminder" template worded to look like an SI request -- which the rules correctly filed as `GENERAL` but AI confidently misclassified. Since the rules alone already reach perfect classification accuracy on their own, AI's only effect there was to introduce regressions, never fix any -- so we removed AI from the classification path entirely, matching the "fills gaps, never overrides" principle we then applied everywhere else in the system.

**A validation rule was silently starving the AI review feature.** After building the async AI-review worker, every single live attempt was failing. The cause: a response-length cap tuned too tight for how verbosely one AI provider naturally writes -- well-grounded, correctly-quoted answers were being rejected purely for running a few dozen characters over a limit, with no visible error beyond a generic "unavailable" state. Found only by manually reproducing the exact prompt and inspecting the raw model output rather than trusting the higher-level failure message.

**Reconciling independently-built parallel work.** Two team members built overlapping features -- including two separate document-upload implementations -- on separate branches merged back into `master`. Git's line-based merge silently resolved some files without conflict markers by picking one side wholesale, dropping the other side's fixes with no warning. This included one stray extra brace from a bad merge that broke the dashboard's entire JavaScript file, which took a binary-search-style diagnostic pass (checking whether progressively larger prefixes of the script still parsed) to isolate to one line.

**Infrastructure-specific failures only appear once deployed.** The AI provider's Cloudflare protection blocked requests from Python's default User-Agent header (fixed by sending a realistic one). More subtly, creating a Secret Manager secret via `echo "key" | gcloud secrets create ...` on Windows PowerShell silently embedded a trailing newline character into the secret's value, which broke every API call with an "invalid header" error that only surfaced in the live deployment's logs, never locally.

## Future Roadmap

- **Unify the two upload implementations** built independently by different team members into one, rather than the two currently coexisting.
- **Extend extraction beyond line-oriented documents.** Field extraction is verified against every format in the provided bundle but is fundamentally `label: value` line-oriented; materially different table layouts haven't been tested.
- **Coordinate the AI review worker across instances.** Each Cloud Run instance currently runs its own independent background worker; under horizontal scaling this could mean duplicate processing of the same case with no shared lock.
- **Broaden the anti-hallucination guardrail** to validate more of the AI's response against source evidence, not only the quoted proof field.
- **Run structured usability sessions** with real shipping-operations reviewers (a template already exists at [main/USER_TESTING.md](main/USER_TESTING.md)) and fold observed confusion back into the dashboard.

---

## Running the project

### Locally (Windows)

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r main\requirements.txt
.venv\Scripts\python.exe main\main.py
.venv\Scripts\python.exe -m unittest discover -s main -p 'test_*.py'
```

Set `GEMINI_API_KEY` or `GONKAROUTER_API_KEY` (as a real environment variable, or in a `.env` file copied from `.env.example`) to enable AI-assisted extraction. Then run the dashboard:

```powershell
.venv\Scripts\python.exe main\dashboard.py
```

Open `http://127.0.0.1:8765`. Score a generated `submission.json` against local ground truth with:

```powershell
.venv\Scripts\python.exe Docker\server\score_cli.py submission.json --json
```

### Cloud Run deployment (public demo)

The root `Dockerfile` builds the same app for Cloud Run. With a Google Cloud project and billing set up:

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
gcloud storage buckets add-iam-policy-binding "gs://$bucketName" --member="serviceAccount:$serviceAccount" --role='roles/storage.objectAdmin'
gcloud run deploy shipping-verifier --source . --region=$region --service-account=$serviceAccount --allow-unauthenticated --set-env-vars="GCS_BUCKET=$bucketName,GOOGLE_CLOUD_PROJECT=$projectId"
```

Attach an AI key as a Secret Manager secret (write it to a file first on Windows -- piping through `echo` embeds a trailing newline that breaks every request):

```powershell
[System.IO.File]::WriteAllText("$PWD\key.tmp", "<YOUR_KEY>")
gcloud secrets create gonkarouter-api-key --data-file="$PWD\key.tmp"
Remove-Item "$PWD\key.tmp"
gcloud secrets add-iam-policy-binding gonkarouter-api-key --member="serviceAccount:$serviceAccount" --role='roles/secretmanager.secretAccessor'
gcloud run services update shipping-verifier --region=$region --set-secrets='GONKAROUTER_API_KEY=gonkarouter-api-key:latest'
```

(Substitute `GEMINI_API_KEY`/`gemini-api-key` throughout if using Gemini instead.)

Seed the case database and attachment bucket:

```powershell
$env:GOOGLE_CLOUD_PROJECT = $projectId
$env:GCS_BUCKET = $bucketName
gcloud auth application-default login
python -m pip install -r main\requirements-cloud.txt
python main\seed_cloud.py --source Bundle
```

`--allow-unauthenticated` makes the deployment a public demo: the printed service URL works directly in any browser, and any visitor can perform reviewer actions. Use `--no-allow-unauthenticated` instead for a private deployment gated behind the [Cloud Run proxy](https://docs.cloud.google.com/run/docs/triggering/https-request) or Identity-Aware Proxy.
