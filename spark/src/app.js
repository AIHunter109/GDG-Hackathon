import { initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider, signInWithPopup } from "firebase/auth";
import { getFirestore, collection, doc, getDocs, setDoc } from "firebase/firestore";
import { getAI, getGenerativeModel, GoogleAIBackend } from "firebase/ai";
import { FIELDS, applyReview, fieldRecord, linkedOriginalId, metricsFor, partitionLinkedAnalyses, recompare, syntheticCases } from "./core.js";
import { extractTextFields, hasAllFields, validateDocumentConsistency, validatePairIdentifiers } from "./batch.js";
import { mountBatchUploader } from "./batch_ui.js";

const LOCAL_KEY = "shipping-verifier-spark-preview-v1";
let records = syntheticCases();
let baseRecords = syntheticCases();
let database = null;
let userId = null;
let model = null;
let firebaseApp = null;
let cloudError = "";
const uploadFiles = new Map();

function showStatus(message, error = false) {
  const slot = document.getElementById("spark-status");
  if (slot) {
    slot.textContent = message;
    slot.className = `notice${error ? " error" : ""}`;
  }
}

function renderUploadCard() {
  const card = document.createElement("section");
  card.className = "card";
  card.innerHTML = `
    <div class="card-head"><h2>Verify documents</h2><span class="badge">Private workspace</span></div>
    <p class="muted">Add an email with its SI and draft BL, or verify paired documents in a batch. Gemini assists extraction when needed; deterministic rules make the final comparison.</p>
    <label class="filter-field">Upload type
      <select id="upload-mode">
        <option value="email">One email with SI and BL</option>
        <option value="mixed">Mixed SI/BL files or folder</option>
        <option value="folders">Separate SI and BL folders</option>
      </select>
    </label>
    <div id="single-upload-panel">
      <p class="muted">Add one email and its SI and draft BL. For documents from one of the original 520 emails, open that email and choose Reanalyze with Gemini. Files are sent to Gemini only when you select Verify documents.</p>
      <form id="spark-form" class="toolbar">
      <input name="from" type="email" aria-label="Sender email" placeholder="Sender email" required />
      <input name="to" type="email" aria-label="Recipient email" placeholder="Recipient email (optional)" />
      <input name="subject" aria-label="Email subject" placeholder="Email subject" required />
      <textarea name="body" aria-label="Email body" placeholder="Email body" required></textarea>
      <label>Original email ID (if from the 520 emails)
        <input name="source_email_id" aria-label="Original email ID" placeholder="For example, email_013" />
      </label>
      <label>Shipping instruction <input name="si" type="file" accept=".pdf,.txt,application/pdf,text/plain" required /></label>
      <label>Draft BL <input name="bl" type="file" accept=".pdf,.txt,application/pdf,text/plain" required /></label>
      <button class="small-btn primary" type="submit">Verify documents</button>
      </form>
    </div>
    <div id="batch-upload-slot" class="hidden"></div>`;
  document.getElementById("new-email-form-slot").append(card);
  document.getElementById("spark-auth").classList.remove("hidden");
  const signIn = document.createElement("button");
  signIn.id = "spark-sign-in";
  signIn.className = "small-btn";
  signIn.type = "button";
  signIn.hidden = true;
  signIn.textContent = "Sign in with Google to view 520 emails";
  document.getElementById("spark-auth").append(signIn);
  signIn.addEventListener("click", async () => {
    signIn.disabled = true;
    try {
      const provider = new GoogleAuthProvider();
      provider.setCustomParameters({ prompt: "select_account" });
      await signInWithPopup(getAuth(firebaseApp), provider);
      await connectFirebase();
      window.dispatchEvent(new Event("spark-case-added"));
    } catch (error) {
      database = null;
      model = null;
      records = {};
      signIn.hidden = false;
      showStatus(`Private bundle unavailable: ${error.message}`, true);
      window.dispatchEvent(new Event("spark-case-added"));
    } finally {
      signIn.disabled = false;
    }
  });
  card.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!database || !model) {
      showStatus(cloudError || "Firebase setup is required before AI analysis.", true);
      return;
    }
    const form = event.currentTarget;
    const subject = form.elements.subject.value.trim();
    const emailMetadata = {
      from: form.elements.from.value.trim(),
      to: form.elements.to.value.trim(),
      body: form.elements.body.value.trim(),
    };
    const si = form.elements.si.files[0], bl = form.elements.bl.files[0];
    if (!si || !bl) return;
    const requestedSource = form.elements.source_email_id.value.trim().toLowerCase();
    if (requestedSource && !Object.hasOwn(baseRecords, requestedSource)) {
      showStatus("Choose an original email ID from the 520-email bundle.", true);
      return;
    }
    const inferredSource = linkedOriginalId({ case: {
      email_id: "upload_preview",
      email: { attachments: [si.name, bl.name] },
    } }, baseRecords);
    const sourceEmailId = requestedSource || inferredSource;
    if (sourceEmailId) {
      showStatus(`These files belong to ${sourceEmailId}. Open that email and use Reanalyze with Gemini instead of creating another case.`);
      window.location.hash = `case/${encodeURIComponent(sourceEmailId)}`;
      return;
    }
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    showStatus("Reading the SI and draft BL…");
    try {
      const caseRecord = await analyzeFiles(subject, si, bl, null, emailMetadata);
      await saveRecord(caseRecord.email_id, { case: caseRecord, decision: null });
      form.reset();
      showStatus(`New email ${caseRecord.email_id} saved to history. Review its extracted evidence.`);
      window.location.hash = `case/${encodeURIComponent(caseRecord.email_id)}`;
      window.dispatchEvent(new Event("spark-case-added"));
    } catch (error) {
      showStatus(`Analysis failed: ${error.message}`, true);
    } finally {
      button.disabled = false;
    }
  });
}

async function connectFirebase() {
  const config = await fetch("/firebase-config.json", { cache: "no-store" }).then((r) => r.json());
  if (!config.apiKey || !config.appId || !config.authDomain) {
    throw new Error("Firebase web app has not been configured yet. Local preview is available.");
  }
  firebaseApp ||= initializeApp(config);
  const auth = getAuth(firebaseApp);
  await auth.authStateReady();
  const user = auth.currentUser;
  if (!user) {
    records = {};
    baseRecords = {};
    document.getElementById("spark-sign-in").hidden = false;
    showStatus("Sign in with the project owner account to view the private 520-email bundle.");
    return;
  }
  document.getElementById("spark-sign-in").hidden = true;
  userId = user.uid;
  database = getFirestore(firebaseApp);
  const ai = getAI(firebaseApp, { backend: new GoogleAIBackend() });
  model = getGenerativeModel(ai, {
    model: "gemini-3.6-flash",
    generationConfig: { responseMimeType: "application/json", temperature: 0 },
  });
  const bundle = await getDocs(collection(database, "bundleCases"));
  if (bundle.size !== 520) throw new Error(`Private bundle is incomplete (${bundle.size}/520 cases).`);
  baseRecords = Object.fromEntries(bundle.docs.map((item) => [item.id, { case: item.data(), decision: null }]));
  await reloadCloudRecords();
  showStatus("All 520 original emails loaded from private Firestore. Reviewer decisions are saved in your account. Gemini runs only when you start document verification, reanalysis, or request an explanation.");
}

async function reloadCloudRecords() {
  const casesRef = collection(database, "users", userId, "cases");
  const snapshot = await getDocs(casesRef);
  records = { ...baseRecords, ...Object.fromEntries(snapshot.docs.map((item) => [item.id, item.data()])) };
}

function savePreview() {
  try { localStorage.setItem(LOCAL_KEY, JSON.stringify(records)); } catch { /* private mode */ }
}

async function saveRecord(id, record) {
  if (database) {
    await setDoc(doc(database, "users", userId, "cases", id), record);
  }
  records[id] = record;
  if (!database) savePreview();
}

function currentData() {
  const { caseRecords } = partitionLinkedAnalyses(records, baseRecords);
  const newCaseIds = Object.keys(caseRecords).filter((id) => !Object.hasOwn(baseRecords, id));
  const originalRecords = Object.fromEntries(Object.entries(caseRecords).filter(([id]) => Object.hasOwn(baseRecords, id)));
  return {
    cases: Object.fromEntries(Object.entries(caseRecords).map(([id, record]) => [id, record.case])),
    decisions: Object.fromEntries(Object.entries(caseRecords).filter(([, record]) => record.decision).map(([id, record]) => [id, record.decision])),
    metrics: metricsFor(originalRecords),
    features: {
      ai_enabled: Boolean(model && database),
      cloud_mode: Boolean(database),
      original_email_count: Object.keys(baseRecords).length,
      new_case_ids: newCaseIds,
      verified_performance: window.SEAL_VERIFIED_PERFORMANCE || null,
    },
  };
}

function correctionDraft(caseRecord) {
  const mismatchLines = (caseRecord.mismatches || []).map((m) =>
    `- ${m.field.replaceAll("_", " ")}: SI says "${m.si.raw_value}"; draft BL says "${m.bl.raw_value}".`);
  return [
    `Subject: Correction requested for ${caseRecord.email?.subject || caseRecord.email_id}`,
    "", "Hello,", "", "Please correct the following confirmed draft BL differences:",
    ...mismatchLines, "", "Please send an updated draft for review.", "", "Thank you.",
  ].join("\n");
}

async function explainReview(caseRecord) {
  if (!model) throw new Error("Firebase AI Logic is not configured");
  const evidence = {
    reason: caseRecord.internal_reason, missing_fields: caseRecord.missing_fields,
    uncertain_fields: caseRecord.uncertain_fields,
    mismatches: (caseRecord.mismatches || []).map((m) => ({ field: m.field, si: m.si.raw_value, bl: m.bl.raw_value })),
  };
  const result = await model.generateContent(
    `Explain this shipping document review evidence in one sentence and give one specific reviewer action. Return JSON with explanation and action. Do not change the verification result. Evidence: ${JSON.stringify(evidence)}`);
  const parsed = JSON.parse(result.response.text());
  if (!parsed.explanation || !parsed.action) throw new Error("Gemini returned no review explanation");
  return { explanation: String(parsed.explanation), action: String(parsed.action) };
}

function chooseRetryFiles(caseRecord) {
  return new Promise((resolve, reject) => {
    const dialog = document.createElement("dialog");
    const heading = document.createElement("h3");
    heading.textContent = `Reanalyze documents for ${caseRecord.email_id}`;
    const explanation = document.createElement("p");
    explanation.textContent = "Select the original SI and draft BL files. Press Analyze to send those two files to Gemini. They are not saved as cloud files.";
    const form = document.createElement("form");
    const inputs = ["si", "bl"].map((role) => {
      const label = document.createElement("label");
      label.textContent = `${role.toUpperCase()} (${caseRecord.documents?.[role]?.split("/").pop() || "source file"}) `;
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".pdf,.txt,application/pdf,text/plain";
      input.required = true;
      label.append(input);
      form.append(label);
      return input;
    });
    const analyze = document.createElement("button");
    analyze.type = "submit";
    analyze.textContent = "Analyze with Gemini";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    form.append(analyze, cancel);
    dialog.append(heading, explanation, form);
    document.body.append(dialog);
    const close = (value, error) => {
      dialog.close();
      dialog.remove();
      error ? reject(error) : resolve(value);
    };
    cancel.onclick = () => close(null, new Error("OCR retry canceled"));
    dialog.oncancel = (event) => { event.preventDefault(); close(null, new Error("OCR retry canceled")); };
    form.onsubmit = (event) => {
      event.preventDefault();
      close({ si: inputs[0].files[0], bl: inputs[1].files[0] });
    };
    dialog.showModal();
  });
}

async function request(url, body) {
  const [, api, action, encodedId, index] = new URL(url, location.origin).pathname.split("/");
  if (api !== "api") throw new Error("Unknown request");
  if (action === "cases") {
    return currentData();
  }
  const id = decodeURIComponent(encodedId || "");
  const existing = records[id];
  if (!existing) throw new Error("Case not found");
  const caseRecord = existing.case;
  if (action === "email") return { ...caseRecord.email, email_id: id };
  if (action === "document") {
    const path = caseRecord.email.attachments[Number(index)];
    return { path, text: caseRecord.document_texts?.[path] || "Source text is unavailable." };
  }
  if (action === "decision") {
    if (body.action === "reopen" &&
        (caseRecord.status !== "NEEDS_REVIEW" || existing.decision?.action !== "resolve")) {
      throw new Error("Only a completed human review can be reopened");
    }
    if (body.action === "reopen_category" &&
        (caseRecord.category === "BL_COMPARISON" || existing.decision?.action !== "complete_category")) {
      throw new Error("Only a completed category task can be reopened");
    }
    const { caseRecord: revised, corrections } = applyReview(caseRecord, body.action, body);
    const decision = { action: body.action, corrections, note: String(body.note || ""), updated_at: new Date().toISOString() };
    await saveRecord(id, { case: revised, decision });
    return { case: revised, decision };
  }
  if (action === "draft") {
    if (existing.decision?.action !== "confirm" || !caseRecord.mismatches?.length) throw new Error("Confirm a mismatch before drafting");
    return { draft: correctionDraft(caseRecord), source: "Template" };
  }
  if (action === "explain") return explainReview(caseRecord);
  if (action === "retry") {
    let revised;
    let files = uploadFiles.get(id);
    if (body?.vision) {
      files ||= await chooseRetryFiles(caseRecord);
      const analyzed = await analyzeFiles(caseRecord.email.subject, files.si, files.bl, id);
      const siPath = caseRecord.documents?.si || caseRecord.email.attachments[0];
      const blPath = caseRecord.documents?.bl || caseRecord.email.attachments[1];
      const sourcePaths = analyzed.email.attachments;
      revised = recompare({
        ...caseRecord,
        documents: { si: siPath, bl: blPath },
        si_fields: analyzed.si_fields,
        bl_fields: analyzed.bl_fields,
        document_texts: {
          ...caseRecord.document_texts,
          [siPath]: analyzed.document_texts[sourcePaths[0]],
          [blPath]: analyzed.document_texts[sourcePaths[1]],
        },
        document_identification: { method: "reviewer_reselected", confidence: 1 },
        validation: analyzed.validation,
      });
      const urls = uploadFiles.get(id).urls;
      uploadFiles.set(id, {
        ...files,
        urls: caseRecord.email.attachments.map((path) => path === siPath ? urls[0] : path === blPath ? urls[1] : null),
      });
    } else revised = recompare(caseRecord);
    await saveRecord(id, { ...existing, case: revised });
    return { case: revised };
  }
  if (action === "select_document") {
    const { role, path } = body;
    if (!["si", "bl"].includes(role) || !caseRecord.email.attachments.includes(path)) throw new Error("Select an attached SI or BL");
    const revised = structuredClone(caseRecord);
    revised.documents ||= { si: null, bl: null };
    const otherRole = role === "si" ? "bl" : "si";
    if (revised.documents[otherRole] === path) {
      [revised.documents.si, revised.documents.bl] = [revised.documents.bl, revised.documents.si];
      [revised.si_fields, revised.bl_fields] = [revised.bl_fields, revised.si_fields];
    } else {
      revised.documents[role] = path;
      const text = revised.document_texts?.[path];
      revised[`${role}_fields`] = typeof text === "string" ? extractTextFields(text, path) : {};
    }
    const siText = revised.document_texts?.[revised.documents.si] || "";
    const blText = revised.document_texts?.[revised.documents.bl] || "";
    revised.validation = {
      pairing: validatePairIdentifiers(siText, blText),
      consistency: {
        si: validateDocumentConsistency(siText, revised.si_fields),
        bl: validateDocumentConsistency(blText, revised.bl_fields),
      },
    };
    const decision = { action: "select_document", corrections: { [role]: path }, updated_at: new Date().toISOString() };
    const verified = recompare(revised);
    await saveRecord(id, { case: verified, decision });
    return { case: verified, decision };
  }
  throw new Error("Unknown action");
}

function fileToPart(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ inlineData: {
      data: String(reader.result).split(",", 2)[1], mimeType: file.type || "application/pdf",
    } });
    reader.onerror = () => reject(new Error("Could not read the selected file"));
    reader.readAsDataURL(file);
  });
}

async function extractFile(file, role, options = {}) {
  if (file.size > 5_000_000) throw new Error("Use files smaller than 5 MB for the free demo");
  if (!/\.(pdf|txt)$/i.test(file.name)) throw new Error("Choose a PDF or TXT document");
  let nativeText = null;
  let nativeFields = {};
  if (file.name.toLowerCase().endsWith(".txt")) {
    nativeText = (await file.text()).slice(0, 12000);
    nativeFields = extractTextFields(nativeText, file.name);
    if (hasAllFields(nativeFields) || options.allowAIForTxt === false) {
      return { text: nativeText, fields: nativeFields };
    }
  }
  const prompt = `Read this ${role === "si" ? "shipping instruction" : "draft bill of lading"}. Return only JSON with keys text and fields. text must faithfully transcribe the document. fields must be an object with only these seven keys: ${FIELDS.join(", ")}. Each present field must have raw_value, source_text (an exact quote from text containing raw_value), and confidence from 0 to 1. Omit absent or illegible values. Never infer missing values.`;
  const part = nativeText ?? await fileToPart(file);
  const result = await model.generateContent([prompt, part]);
  const parsed = JSON.parse(result.response.text());
  if (typeof parsed.text !== "string" || !parsed.fields || typeof parsed.fields !== "object") throw new Error("Gemini returned incomplete document evidence");
  const fields = {};
  for (const field of FIELDS) {
    const item = parsed.fields[field];
    if (!item || typeof item.raw_value !== "string" || typeof item.source_text !== "string") continue;
    if (!parsed.text.includes(item.source_text) || !item.source_text.includes(item.raw_value)) continue;
    const record = fieldRecord(field, item.raw_value, file.name, Math.min(Number(item.confidence) || 0.6, 0.8), "gemini_document_extraction");
    if (record?.normalized_value != null) fields[field] = { ...record, source_text: item.source_text };
  }
  return { text: nativeText ?? parsed.text.slice(0, 12000), fields: { ...nativeFields, ...fields } };
}

async function analyzeFiles(subject, si, bl, previousId = null, emailMetadata = {}, options = {}) {
  const siData = await extractFile(si, "si", options);
  const blData = await extractFile(bl, "bl", options);
  const pairing = validatePairIdentifiers(siData.text, blData.text);
  const id = previousId || `upload_${Date.now()}`;
  const paths = [`SI-${si.name}`, `BL-${bl.name}`];
  const record = recompare({
    email_id: id, category: "BL_COMPARISON", status: "PROCESSING",
    classification: { method: "document_upload", confidence: 1 },
    email: { email_id: id, from: emailMetadata.from || "Browser upload", to: emailMetadata.to || "Reviewer", subject,
      body: emailMetadata.body || "Documents selected by the reviewer for AI-assisted comparison.", attachments: paths },
    documents: { si: paths[0], bl: paths[1] },
    document_texts: { [paths[0]]: siData.text, [paths[1]]: blData.text },
    si_fields: siData.fields, bl_fields: blData.fields,
    validation: { pairing, consistency: {
      si: validateDocumentConsistency(siData.text, siData.fields),
      bl: validateDocumentConsistency(blData.text, blData.fields),
    } },
  });
  if (!pairing.valid) {
    record.status = "NEEDS_REVIEW";
    record.internal_reason = "POSSIBLE_WRONG_DOCUMENT_PAIR";
    record.review_reason = "wrong_doc_type";
  }
  uploadFiles.set(id, { si, bl, urls: [URL.createObjectURL(si), URL.createObjectURL(bl)] });
  return record;
}

window.sparkRequest = request;
window.sparkAttachmentUrl = (id, index) => uploadFiles.get(id)?.urls[Number(index)] || null;
renderUploadCard();
mountBatchUploader({
  container: document.getElementById("batch-upload-slot"),
  ready: () => Boolean(database && model),
  originalIds: () => Object.keys(baseRecords),
  hasRecord: (id) => Boolean(records[id]),
  processPair: async (pair, id) => {
    const caseRecord = await analyzeFiles(
      `Document batch: ${pair.key}`, pair.si.file, pair.bl.file, id,
      { from: "Document batch", to: "Reviewer",
        body: "SI and draft BL were uploaded as a document batch. No source email was provided." },
      { allowAIForTxt: false },
    );
    caseRecord.batch_import = { pairing_key: pair.key,
      si_file: pair.si.path, bl_file: pair.bl.path };
    await saveRecord(id, { case: caseRecord, decision: null });
  },
  onSaved: () => window.dispatchEvent(new Event("spark-case-added")),
});
const uploadMode = document.getElementById("upload-mode");
function renderUploadMode() {
  const single = uploadMode.value === "email";
  document.getElementById("single-upload-panel").classList.toggle("hidden", !single);
  document.getElementById("batch-upload-slot").classList.toggle("hidden", single);
  window.dispatchEvent(new CustomEvent("seal-upload-mode", { detail: uploadMode.value }));
}
uploadMode.onchange = renderUploadMode;
renderUploadMode();
try {
  await connectFirebase();
} catch (error) {
  cloudError = error.message;
  database = null;
  userId = null;
  model = null;
  if (firebaseApp) {
    records = {};
    document.getElementById("spark-sign-in").hidden = false;
    showStatus(`Private bundle unavailable: ${cloudError}`, true);
  } else {
    try {
      const saved = JSON.parse(localStorage.getItem(LOCAL_KEY) || "null");
      if (saved && typeof saved === "object") records = saved;
    } catch { /* local storage unavailable */ }
    showStatus(`Local preview only: ${cloudError}`, true);
  }
}
window.dispatchEvent(new Event("spark-ready"));
