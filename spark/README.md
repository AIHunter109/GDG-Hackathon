# Firebase Spark deployment

Live site: [https://seal-509214.web.app](https://seal-509214.web.app). Sign in with the project owner Google account to load the 520 original bundle emails. The Dashboard shows those emails in an **Emails** table with search, category, and status filters together. **New email history** and **Add a new email** appear below that table. The source email's attachment count is shown as **Attachments**; the bundle has 394 emails with zero files, 2 with one file, and 124 with two files. The public site shell includes summary totals, Case Detail, seven SI/BL fields, mismatch evidence, email and attachment text, reviewer actions, retry, and correction drafts. The 520 processed case records are in the private `bundleCases` Firestore collection. Reviewer edits and new email submissions are saved under `users/{uid}/cases`. Uploads recognized as documents from an original email are excluded from the dashboard. Open the original Case Detail and choose **Reanalyze with Gemini** to update that case instead of creating another entry.

The project uses Firebase Hosting, Google Authentication, one default Firestore Standard database, and Firebase AI Logic with the Gemini Developer API. Billing is disabled. [Firebase's Spark plan](https://firebase.google.com/docs/projects/billing/firebase-pricing-plans) does not require payment information and service usage stops at its no-cost limits. The Firestore database reports `freeTier: true`. [Firebase AI Logic pricing](https://firebase.google.com/docs/ai-logic/pricing) describes the Gemini Developer API free tier. Cloud Run, Cloud Functions, Cloud Storage, App Hosting, and paid Gemini tiers are not used by this site.

## Data access and AI

Firestore rules allow only the project owner's verified Google account to read the original emails. Unauthenticated reads return HTTP 403. The public Hosting build contains only HTML, JavaScript, and the Firebase web configuration; it does not contain the email dataset or private Gemini key. Original attachment binaries are not uploaded. Extractable document text is kept with each private case so it can be previewed; scanned PDFs without text show an OCR retry prompt. The owner can select local SI and BL files to retry OCR. Those files go to Gemini only after the owner presses **Analyze with Gemini**. Uploaded files stay in the browser session; extracted text and reviewer decisions go to private Firestore.

The browser's final seven-field comparison is deterministic. Gemini helps extract selected PDF/TXT documents and explain review evidence. AI-derived values require review. The Python pipeline remains the competition scorer and supports TXT/XLSX/DOCX/PDF, uncertain intent, document-role and label fallback, and image-only PDF transcription with `GEMINI_API_KEY` configured. The hosted upload form currently accepts PDF/TXT; the original bundle's XLSX/DOCX cases are displayed from the private records processed by Python.

Gemini's [free-tier data-use terms](https://ai.google.dev/gemini-api/docs/pricing) may allow submitted content to improve Google products. Do not select sensitive original files for Gemini unless their use is authorized. The 520 original bundle documents were processed locally for the initial seed and were not sent to Gemini automatically.

## Rebuild and verify

From the repository root on this workstation:

```powershell
& 'C:\Program Files\nodejs\npm.cmd' test --prefix spark
& 'C:\Program Files\nodejs\npm.cmd' run build --prefix spark
.venv\Scripts\python.exe spark\deploy_hosting.py
```

`spark/export_bundle.py` regenerates the ignored `spark/private/bundle-cases.json` from the local `Bundle` without calling Gemini. `spark/seed_private.py` writes those 520 processed records to Firestore using the existing Google Cloud CLI login. Neither script puts the original emails in Hosting or Git. Review `spark/firestore.rules` before changing owner access. `spark/check_ai_live.mjs` sends only a synthetic prompt to test Firebase AI Logic.

Verified on 21 September 2026: the live site and config returned HTTP 200, Firestore had 520 distinct IDs from `email_001` to `email_520`, unauthenticated case access returned HTTP 403, Google sign-in was enabled for the Hosting domain, a synthetic Firebase AI Logic request succeeded, and local Node and Python tests passed. An owner sign-in exposed a collection-read rule issue; the corrected rule is deployed and rule tests now pass for owner reads while denying other users. A browser reload after the fix and a human usability session remain to be observed. See [main/USER_TESTING.md](../main/USER_TESTING.md).
