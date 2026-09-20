"""Focused regression tests for document decisions and submission shape."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from classify import classify_email
from ai_service import AIService
from comparator import compare_documents, FIELDS
from extractor import (extract_fields, identify_documents, normalize_value, read_document,
                       validate_document_consistency, validate_document_pair)
from main import generate_submission
from review_actions import apply_correction, mark_equivalent


def doc(text, path="sample.txt"):
    return {"path": path, "text": text, "file_type": ".txt"}


class PipelineTests(unittest.TestCase):
    def test_ai_fallback_validates_model_outputs(self):
        ai = AIService(api_key="test")
        ai._json = lambda prompt, **kwargs: {"category": "BL_COMPARISON", "confidence": .91,
                                              "reason": "Both documents are requested"}
        result = ai.classify_email({"subject": "Check docs", "body": "Compare the attachments", "attachments": []})
        self.assertEqual(result["method"], "gemini")
        ai._json = lambda prompt, **kwargs: {"category": "MADE_UP", "confidence": 1}
        self.assertIsNone(ai.classify_email({"subject": "x", "body": "x", "attachments": []}))

    def test_semantic_mapping_requires_source_evidence(self):
        ai = AIService(api_key="test")
        ai._json = lambda prompt, **kwargs: {"port_of_loading": {"raw_value": "Klang", "source_text": "Origin Port: Klang", "confidence": .99},
                                              "port_of_discharge": {"raw_value": "Callao", "source_text": "Invented: Callao", "confidence": .99}}
        fields = extract_fields(doc("Origin Port: Klang\nDischarge Port: Callao"), ai)
        self.assertEqual(fields["port_of_loading"]["normalized_value"], "klang")
        self.assertEqual(fields["port_of_loading"]["method"], "gemini_semantic_mapping")
        self.assertEqual(fields["port_of_discharge"]["method"], "native")

    def test_email_intent_uses_body_and_attachments(self):
        comparison = {"subject": "RE: old invoice", "body": "Please verify the attached SI against the draft BL.",
                      "attachments": ["x_SI.txt", "x_BL.txt"]}
        self.assertEqual(classify_email(comparison)["category"], "BL_COMPARISON")
        self.assertEqual(classify_email({"subject": "request SI", "body": "Please find Shipping instruction for order 10. 3 Original invoice required.", "attachments": []})["category"], "SI_REQUEST")
        self.assertEqual(classify_email({"subject": "Invoice", "body": "Can you clarify the local charges?", "attachments": []})["category"], "INVOICE_QUERY")
        self.assertEqual(classify_email({"subject": "News", "body": "Verify your account to claim prize", "attachments": []})["category"], "SPAM")

    def test_alias_extraction_and_normalization(self):
        fields = extract_fields(doc("""SHIPPING INSTRUCTION
Shipper/Exporter: Acme, Ltd.
Consignee (Non-Negotiable): Buyer Co
Notify: Buyer Co
POL: Port Klang
Discharge Port: Callao, Peru
No. of Containers or Packages: 6 x 40'HC
TOTAL Gross Weight (KG): 21,577 KG"""))
        self.assertEqual(set(fields), set(FIELDS))
        self.assertEqual(fields["container_count"]["normalized_value"], 6)
        self.assertEqual(fields["gross_weight_kg"]["normalized_value"], "21577")
        self.assertEqual(normalize_value("gross_weight_kg", "22 MT"), "22000")
        self.assertIsNone(normalize_value("port_of_loading", "TBA"))
        self.assertIn("source_text", fields["shipper"])

    def test_document_roles_and_pairing(self):
        si = doc("BILL OF LADING INSTRUCTION\nBooking Ref: ABC", "x_SI.txt")
        bl = doc("BILL OF LADING (DRAFT)\nBooking Ref: ABC", "x_BL.txt")
        self.assertIsNone(identify_documents({}, [si, bl])["reason"])
        self.assertTrue(validate_document_pair(si, bl)["valid"])
        self.assertFalse(validate_document_pair(si, doc("Booking Ref: XYZ"))["valid"])
        wrong = doc("PACKING LIST\nShipper: ABC", "x_BL.txt")
        self.assertEqual(identify_documents({}, [si, wrong])["reason"], "wrong_doc_type")

    def test_comparison_only_official_fields(self):
        si = extract_fields(doc("Shipper: ACME LTD\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 2 x 40'HC\nGross Wt: 21,577 KG"))
        bl = extract_fields(doc("SHIPPER: Acme, Ltd.\nConsignee: Buyer\nNotify: Buyer\nLoad Port: Klang\nDischarge Port: Callao\nContainer Count: 3 x 40'HC\nGross Weight: 21577 kg\nVessel: Different"))
        result = compare_documents(si, bl)
        self.assertEqual([m["field"] for m in result["mismatches"]], ["container_count"])
        self.assertEqual(result["missing_fields"], [])

    def test_internal_reconciliation(self):
        text = "No. of Containers: 2\nGross Weight: 400 KG\nABCD1234567\n40HC\n200\nEFGH1234567\n40HC\n200"
        self.assertTrue(validate_document_consistency(doc(text))["valid"])
        self.assertFalse(validate_document_consistency(doc(text.replace("400 KG", "300 KG")))["valid"])
        self.assertFalse(validate_document_consistency(doc(text.replace("Containers: 2", "Containers: 3")))["valid"])

    def test_reviewer_correction_rebuilds_result(self):
        si = extract_fields(doc("Shipper: ACME\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 2\nGross Weight: 400 KG"))
        bl = extract_fields(doc("Shipper: ACME\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 3\nGross Weight: 400 KG"))
        case = {"category": "BL_COMPARISON", "status": "MISMATCH", "si_fields": si, "bl_fields": bl,
                "mismatches": compare_documents(si, bl)["mismatches"]}
        corrected, record = apply_correction(case, "bl", "container_count", "2 x 40'HC")
        self.assertEqual(record["status"], "OK")
        self.assertEqual(case["bl_fields"]["container_count"]["raw_value"], "3")
        equivalent, record = mark_equivalent(case, "container_count")
        self.assertEqual(record["status"], "OK")
        self.assertEqual(equivalent["bl_fields"]["container_count"]["raw_value"], "3")

    def test_full_bundle_schema_if_present(self):
        root = Path(__file__).resolve().parent.parent
        if not (root / "Bundle" / "sample_submission.json").exists():
            self.skipTest("Local competition bundle unavailable")
        from Bundle.loader import Inbox
        inbox = Inbox(str(root / "Bundle"))
        submission = generate_submission(inbox)
        sample = inbox.sample_submission()
        self.assertEqual(set(submission), set(sample))
        self.assertTrue(all(set(record) == set(next(iter(sample.values()))) for record in submission.values()))

    def test_multiformat_bundle_examples_if_present(self):
        root = Path(__file__).resolve().parent.parent
        if not (root / "Bundle" / "attachments").exists():
            self.skipTest("Local competition bundle unavailable")
        from Bundle.loader import Inbox
        inbox = Inbox(str(root / "Bundle"))
        for path in ("attachments/email_004_SI.txt", "attachments/email_005_SI.xlsx",
                     "attachments/email_055_BL.docx", "attachments/email_059_SI.pdf"):
            with self.subTest(path=path):
                fields = extract_fields(read_document(path, inbox))
                self.assertEqual(set(fields), set(FIELDS))


if __name__ == "__main__":
    unittest.main()
