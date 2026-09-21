export const FIELDS = [
  "shipper", "consignee", "notify_party", "port_of_loading",
  "port_of_discharge", "container_count", "gross_weight_kg",
];

const LABELS = {
  shipper: "Shipper", consignee: "Consignee", notify_party: "Notify party",
  port_of_loading: "Port of loading", port_of_discharge: "Port of discharge",
  container_count: "Container count", gross_weight_kg: "Gross weight",
};

export function normalize(field, raw) {
  if (typeof raw !== "string" || !raw.trim() || /^(tba|n\/?a|none|unknown)$/i.test(raw.trim())) return null;
  if (field === "container_count") {
    const number = raw.replaceAll(",", "").match(/\d+/);
    return number ? Number(number[0]) : null;
  }
  if (field === "gross_weight_kg") {
    const number = raw.replaceAll(",", "").match(/\d+(?:\.\d+)?/);
    if (!number) return null;
    const amount = Number(number[0]) * (/\b(mt|metric tons?|tonnes?)\b/i.test(raw) ? 1000 : 1);
    return String(Number(amount.toFixed(3)));
  }
  let value = raw;
  if (["shipper", "consignee", "notify_party"].includes(field)) {
    value = value.split("|")[0].split(/ON BEHALF OF|P\.?O\.? BOX/i)[0];
  }
  return value.normalize("NFKC").toLocaleLowerCase("en")
    .replace(/[^\p{L}\p{N}\s]/gu, " ").trim().replace(/\s+/g, " ") || null;
}

export function fieldRecord(field, raw, source, confidence = 1, method = "rules") {
  if (typeof raw !== "string" || !raw.trim()) return null;
  return {
    raw_value: raw.trim(), normalized_value: normalize(field, raw),
    source, source_text: `${LABELS[field]}: ${raw.trim()}`,
    confidence, method,
  };
}

export function compare(siFields = {}, blFields = {}) {
  const mismatches = [], missing_fields = [];
  for (const field of FIELDS) {
    const si = siFields[field], bl = blFields[field];
    if (si?.normalized_value == null || bl?.normalized_value == null) {
      missing_fields.push(field);
    } else if (si.normalized_value !== bl.normalized_value) {
      mismatches.push({
        field, si, bl,
        reason: `Normalized values differ: ${si.normalized_value} vs ${bl.normalized_value}`,
      });
    }
  }
  return { mismatches, missing_fields };
}

export function recompare(caseRecord) {
  const result = structuredClone(caseRecord);
  const { mismatches, missing_fields } = compare(result.si_fields, result.bl_fields);
  result.mismatches = mismatches;
  result.missing_fields = missing_fields;
  result.uncertain_fields = FIELDS.filter((field) =>
    ["si", "bl"].some((role) => (result[`${role}_fields`]?.[field]?.confidence || 0) < 0.9));
  const pairingInvalid = result.validation?.pairing?.valid === false;
  const consistencyInvalid = Object.values(result.validation?.consistency || {})
    .some((check) => check?.valid === false);
  if (pairingInvalid || consistencyInvalid) {
    Object.assign(result, { status: "NEEDS_REVIEW", review_reason: "wrong_doc_type",
      internal_reason: pairingInvalid ? "POSSIBLE_WRONG_DOCUMENT_PAIR" : "DOCUMENT_INTERNAL_INCONSISTENCY" });
  } else if (missing_fields.length) {
    Object.assign(result, { status: "NEEDS_REVIEW", review_reason: "missing_value", internal_reason: "MISSING_REQUIRED_FIELD" });
  } else if (result.uncertain_fields.length) {
    Object.assign(result, { status: "NEEDS_REVIEW", review_reason: "missing_value", internal_reason: "LOW_EXTRACTION_CONFIDENCE" });
  } else {
    Object.assign(result, { status: mismatches.length ? "MISMATCH" : "OK", review_reason: null, internal_reason: null });
  }
  result.updated_at = new Date().toISOString();
  return result;
}

export function applyReview(caseRecord, action, payload = {}) {
  const result = structuredClone(caseRecord);
  const corrections = {};
  if (["correct", "confirm_value"].includes(action)) {
    const { role, field } = payload;
    if (!["si", "bl"].includes(role) || !FIELDS.includes(field)) throw new Error("Select an SI or BL field");
    const group = result[`${role}_fields`] ||= {};
    if (action === "correct") {
      if (typeof payload.value !== "string" || normalize(field, payload.value) == null) throw new Error("Enter a valid correction");
      group[field] = fieldRecord(field, payload.value, "reviewer correction", 1, "reviewer");
      corrections.value = payload.value;
    } else {
      if (group[field]?.normalized_value == null) throw new Error("No extracted value to confirm");
      group[field].confidence = 1;
      group[field].method = "reviewer_confirmed";
    }
    Object.assign(corrections, { role, field });
    return { caseRecord: recompare(result), corrections };
  }
  if (action === "mark_equivalent") {
    const { field } = payload;
    const si = result.si_fields?.[field], bl = result.bl_fields?.[field];
    if (!FIELDS.includes(field) || si?.normalized_value == null || bl?.normalized_value == null) throw new Error("Both values are required");
    bl.normalized_value = si.normalized_value;
    bl.confidence = si.confidence = 1;
    bl.method = "reviewer_equivalence";
    corrections.field = field;
    return { caseRecord: recompare(result), corrections };
  }
  if (["complete_category", "reopen_category"].includes(action)) {
    if (result.category === "BL_COMPARISON") throw new Error("Document comparisons use verification review actions");
    return { caseRecord: result, corrections };
  }
  if (!["confirm", "resolve", "reopen"].includes(action)) throw new Error("Unknown reviewer action");
  return { caseRecord: result, corrections };
}

export function metricsFor(records) {
  const entries = Object.values(records);
  const cases = entries.map((entry) => entry.case);
  const statuses = {}, categories = {};
  for (const c of cases) {
    statuses[c.status] = (statuses[c.status] || 0) + 1;
    categories[c.category] = (categories[c.category] || 0) + 1;
  }
  const comparisons = cases.filter((c) => c.category === "BL_COMPARISON");
  const reviews = entries.filter((entry) => entry.case.category === "BL_COMPARISON" && entry.case.status === "NEEDS_REVIEW" && entry.decision?.action !== "resolve").length;
  const paired = comparisons.filter((c) => c.si_fields && c.bl_fields);
  const extracted = paired.reduce((count, c) => count + ["si", "bl"].reduce((roleCount, role) =>
    roleCount + FIELDS.filter((field) => c[`${role}_fields`]?.[field]?.normalized_value != null).length, 0), 0);
  return {
    total: cases.length, statuses, categories, comparison_requests: comparisons.length,
    automatically_cleared: comparisons.filter((c) => c.status === "OK").length,
    mismatches: comparisons.filter((c) => c.status === "MISMATCH").length,
    human_review: reviews, processing: statuses.PROCESSING || 0,
    processing_failures: statuses.PROCESSING_FAILED || 0,
    field_extraction_coverage: paired.length ? extracted / (paired.length * 14) : null,
    human_review_rate: comparisons.length ? reviews / comparisons.length : 0,
    processing_failure_rate: cases.length ? (statuses.PROCESSING_FAILED || 0) / cases.length : 0,
  };
}

export function linkedOriginalId(record, originalRecords) {
  const explicit = record?.case?.source_email_id;
  if (explicit && Object.hasOwn(originalRecords, explicit)) return explicit;
  const id = record?.case?.email_id || "";
  if (!id.startsWith("upload_")) return null;
  const attachments = record.case.email?.attachments || [];
  if (attachments.length < 2) return null;
  const ids = attachments.map((path) => path.match(/email_\d{3}/i)?.[0]?.toLowerCase());
  return ids.every((candidate) => candidate && candidate === ids[0]) && Object.hasOwn(originalRecords, ids[0])
    ? ids[0]
    : null;
}

export function partitionLinkedAnalyses(records, originalRecords) {
  const linkedAnalyses = Object.fromEntries(Object.entries(records).flatMap(([id, record]) => {
    if (Object.hasOwn(originalRecords, id)) return [];
    const originalId = linkedOriginalId(record, originalRecords);
    return originalId ? [[id, originalId]] : [];
  }));
  const caseRecords = Object.fromEntries(Object.entries(records).filter(([id]) => !Object.hasOwn(linkedAnalyses, id)));
  return { linkedAnalyses, caseRecords };
}

function demoCase(id, subject, siValues, blValues) {
  const paths = [`${id}-SI.txt`, `${id}-BL.txt`];
  const fields = (values, path) => Object.fromEntries(FIELDS.flatMap((field) =>
    values[field] == null ? [] : [[field, fieldRecord(field, values[field], path)]]));
  const documentTexts = Object.fromEntries([siValues, blValues].map((values, index) => [
    paths[index],
    [`${index ? "Draft Bill of Lading" : "Shipping Instruction"} — ${id}`,
      ...FIELDS.filter((field) => values[field] != null).map((field) => `${LABELS[field]}: ${values[field]}`)].join("\n"),
  ]));
  const record = {
    email_id: id, category: "BL_COMPARISON", status: "OK",
    classification: { method: "rules", confidence: 1 },
    email: { email_id: id, from: "demo@shipping.example", to: "reviewer@example.test", subject,
      body: "Please compare the attached shipping instruction and draft bill of lading.", attachments: paths },
    documents: { si: paths[0], bl: paths[1] }, document_texts: documentTexts,
    si_fields: fields(siValues, paths[0]), bl_fields: fields(blValues, paths[1]),
    validation: { pairing: { valid: true }, consistency: { si: { valid: true }, bl: { valid: true } } },
    updated_at: new Date().toISOString(),
  };
  return recompare(record);
}

export function syntheticCases() {
  const common = {
    shipper: "Acme Exporters Ltd", consignee: "Northstar Imports",
    notify_party: "Northstar Logistics", port_of_loading: "Port Klang",
    port_of_discharge: "Singapore", container_count: "2",
    gross_weight_kg: "12,000 kg",
  };
  const match = demoCase("demo_match", "Draft BL check — matching values", common, common);
  const mismatch = demoCase("demo_mismatch", "Draft BL check — port discrepancy", common,
    { ...common, port_of_discharge: "Jakarta" });
  const review = demoCase("demo_review", "Draft BL check — missing notify party", common,
    { ...common, notify_party: null });
  return Object.fromEntries([match, mismatch, review].map((c) => [c.email_id, { case: c, decision: null }]));
}
