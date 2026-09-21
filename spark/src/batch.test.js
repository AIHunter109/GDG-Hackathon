import test from "node:test";
import assert from "node:assert/strict";
import { batchCaseId, extractTextFields, hasAllFields, pairDocuments,
  validateDocumentConsistency, validatePairIdentifiers } from "./batch.js";

const file = (name, path = name, size = 100) => ({ name, webkitRelativePath: path, size, lastModified: 42 });

test("mixed SI and BL files pair by shared shipment ID", () => {
  const plan = pairDocuments([
    { file: file("email_041_BL.pdf") },
    { file: file("email_041_SI.pdf") },
  ]);
  assert.equal(plan.pairs.length, 1);
  assert.equal(plan.pairs[0].key, "email_041");
  assert.equal(plan.pairs[0].si.file.name, "email_041_SI.pdf");
  assert.equal(plan.pairs[0].bl.file.name, "email_041_BL.pdf");
  assert.equal(plan.unmatched.length, 0);
});

test("separate SI and BL folders pair same-named files", () => {
  const plan = pairDocuments([
    { file: file("shipment-42.txt", "SI/shipment-42.txt"), source: "si" },
    { file: file("shipment-42.txt", "BL/shipment-42.txt"), source: "bl" },
  ]);
  assert.equal(plan.pairs.length, 1);
  assert.equal(plan.pairs[0].key, "shipment_42");
  assert.equal(batchCaseId(plan.pairs[0]), batchCaseId(pairDocuments([
    { file: file("shipment-42.txt", "new/SI/shipment-42.txt"), source: "si" },
    { file: file("shipment-42.txt", "new/BL/shipment-42.txt"), source: "bl" },
  ]).pairs[0]));
});

test("ambiguous, incomplete, existing, and unsupported files are not processed", () => {
  const plan = pairDocuments([
    { file: file("email_013_SI.txt") },
    { file: file("email_013_BL.txt") },
    { file: file("shipment_7_SI.txt") },
    { file: file("shipment_8_SI_BL.txt") },
    { file: file("shipment_9_SI.xlsx") },
  ], ["email_013"]);
  assert.equal(plan.pairs[0].status, "original");
  assert.equal(plan.unmatched.length, 2);
  assert.equal(plan.rejected.length, 1);
});

test("plain TXT fields can be read locally without Gemini", () => {
  const text = ["Shipper: ACME", "Consignee: Buyer", "Notify Party: Agent",
    "POL: Port Klang", "POD: Callao", "Containers: 2", "Gross Weight: 400 KG"].join("\n");
  const fields = extractTextFields(text, "sample_SI.txt");
  assert.equal(hasAllFields(fields), true);
  assert.equal(fields.gross_weight_kg.normalized_value, "400");
  assert.equal(fields.port_of_loading.source_text, "POL: Port Klang");
});

test("conflicting shipment identifiers require review despite matching filenames", () => {
  const result = validatePairIdentifiers("Booking Ref: BK123\nShipper: ACME", "Booking Ref: BK999\nShipper: ACME");
  assert.equal(result.valid, false);
  assert.deepEqual(result.conflicts, ["booking"]);
});

test("container rows and weights are checked against declared totals", () => {
  const fields = {
    container_count: { normalized_value: 2 },
    gross_weight_kg: { normalized_value: "400" },
  };
  const valid = "ABCD1234567\n40HC\n200\nEFGH1234567\n40HC\n200";
  assert.equal(validateDocumentConsistency(valid, fields).valid, true);
  assert.equal(validateDocumentConsistency(valid, {
    ...fields, container_count: { normalized_value: 3 },
  }).valid, false);
  assert.equal(validateDocumentConsistency(valid, {
    ...fields, gross_weight_kg: { normalized_value: "300" },
  }).valid, false);
});
