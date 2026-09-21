import test from "node:test";
import assert from "node:assert/strict";
import { applyReview, compare, fieldRecord, metricsFor, normalize, partitionLinkedAnalyses, syntheticCases } from "./core.js";

test("shipping values normalize before comparison", () => {
  assert.equal(normalize("gross_weight_kg", "12 MT"), "12000");
  assert.equal(normalize("gross_weight_kg", "12,000 kg"), "12000");
  assert.equal(normalize("container_count", "02 containers"), 2);
  assert.equal(normalize("shipper", "ACME Exporters, Ltd."), "acme exporters ltd");
  const same = compare(
    { gross_weight_kg: fieldRecord("gross_weight_kg", "12 MT", "SI") },
    { gross_weight_kg: fieldRecord("gross_weight_kg", "12,000 kg", "BL") },
  );
  assert.equal(same.mismatches.length, 0);
});

test("synthetic cases cover match, discrepancy, and human review", () => {
  const cases = syntheticCases();
  assert.equal(cases.demo_match.case.status, "OK");
  assert.deepEqual(cases.demo_mismatch.case.mismatches.map((m) => m.field), ["port_of_discharge"]);
  assert.equal(cases.demo_mismatch.case.status, "MISMATCH");
  assert.equal(cases.demo_review.case.status, "NEEDS_REVIEW");
  assert.deepEqual(cases.demo_review.case.missing_fields, ["notify_party"]);
});

test("reviewer correction recomputes the decision", () => {
  const original = syntheticCases().demo_review.case;
  const result = applyReview(original, "correct", {
    role: "bl", field: "notify_party", value: "Northstar Logistics",
  });
  assert.equal(result.caseRecord.status, "OK");
  assert.equal(result.caseRecord.missing_fields.length, 0);
  assert.equal(original.status, "NEEDS_REVIEW");
});

test("reviewer changes cannot clear failed document validation", () => {
  const original = syntheticCases().demo_review.case;
  original.validation.pairing = { valid: false, conflicts: ["booking"] };
  const result = applyReview(original, "correct", {
    role: "bl", field: "notify_party", value: "Northstar Logistics",
  });
  assert.equal(result.caseRecord.status, "NEEDS_REVIEW");
  assert.equal(result.caseRecord.internal_reason, "POSSIBLE_WRONG_DOCUMENT_PAIR");
});

test("reopening a completed review restores the pending review count", () => {
  const review = syntheticCases().demo_review;
  const resolved = { case: review.case, decision: { action: "resolve" } };
  assert.equal(metricsFor({ demo_review: resolved }).human_review, 0);
  const reopened = applyReview(review.case, "reopen");
  assert.equal(reopened.caseRecord.status, "NEEDS_REVIEW");
  assert.equal(metricsFor({ demo_review: { case: reopened.caseRecord,
    decision: { action: "reopen" } } }).human_review, 1);
});

test("dashboard document-check totals exclude non-comparison email", () => {
  const records = syntheticCases();
  records.general = { case: { email_id: "general", category: "GENERAL", status: "OK" }, decision: null };
  const metrics = metricsFor(records);
  assert.equal(metrics.total, 4);
  assert.equal(metrics.comparison_requests, 3);
  assert.equal(metrics.automatically_cleared, 1);
  assert.equal(metrics.mismatches, 1);
});

test("non-comparison categories support realistic workflow states", () => {
  const general = { email_id: "general", category: "GENERAL", status: "OK" };
  const forwarded = applyReview(general, "update_category_workflow", {
    workflow_state: "forwarded_to_owner",
  });
  assert.deepEqual(forwarded.caseRecord, general);
  assert.equal(forwarded.corrections.workflow_state, "forwarded_to_owner");
  assert.throws(
    () => applyReview(general, "update_category_workflow", { workflow_state: "routed_to_finance" }),
    /valid workflow status/,
  );
  assert.throws(
    () => applyReview(syntheticCases().demo_match.case, "complete_category"),
    /verification review actions/,
  );
});

test("analysis of bundle documents stays linked without increasing email count", () => {
  const originals = { email_013: { case: { email_id: "email_013", category: "BL_COMPARISON", status: "MISMATCH" } } };
  const uploaded = { case: { email_id: "upload_1789944385211", category: "BL_COMPARISON", status: "NEEDS_REVIEW",
    email: { attachments: ["SI-email_013_BL.txt", "BL-email_013_SI.txt"] } } };
  const records = { ...originals, upload_1789944385211: uploaded };
  const { linkedAnalyses, caseRecords } = partitionLinkedAnalyses(records, originals);
  assert.deepEqual(linkedAnalyses, { upload_1789944385211: "email_013" });
  assert.equal(metricsFor(caseRecords).total, 1);
  assert.equal(Object.keys(records).length, 2);
});
