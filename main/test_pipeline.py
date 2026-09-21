"""Focused regression tests for document decisions and submission shape."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_service import AIService
from classify import classify_email
from comparator import FIELDS, compare_documents
from extractor import (
    extract_fields,
    identify_documents,
    normalize_value,
    read_document,
    validate_document_consistency,
    validate_document_pair,
)
from review_actions import apply_correction, confirm_value, mark_equivalent

from main import generate_submission, process_email


def doc(text, path="sample.txt"):
    return {"path": path, "text": text, "file_type": ".txt"}


class PipelineTests(unittest.TestCase):
    def test_ai_fallback_validates_model_outputs(self):
        ai = AIService(api_key="test")
        ai._json = lambda prompt, **kwargs: {
            "category": "BL_COMPARISON",
            "confidence": 0.91,
            "reason": "Both documents are requested",
        }
        result = ai.classify_email(
            {
                "subject": "Check docs",
                "body": "Compare the attachments",
                "attachments": [],
            }
        )
        self.assertEqual(result["method"], "gemini")
        ai._json = lambda prompt, **kwargs: {"category": "MADE_UP", "confidence": 1}
        self.assertIsNone(
            ai.classify_email({"subject": "x", "body": "x", "attachments": []})
        )

    def test_ai_review_explanation_is_brief_and_cannot_change_status(self):
        ai = AIService(api_key="test")
        ai._json = lambda prompt, **kwargs: {
            "explanation": "The gross weight is unclear in the source.",
            "action": "Check the scan and confirm the value.",
        }
        case = {
            "status": "NEEDS_REVIEW",
            "internal_reason": "LOW_EXTRACTION_CONFIDENCE",
            "uncertain_fields": ["gross_weight_kg"],
        }
        self.assertIn("gross weight", ai.explain_review(case)["explanation"])
        self.assertEqual(case["status"], "NEEDS_REVIEW")
        ai._json = lambda prompt, **kwargs: {"explanation": "x", "action": "Confirm"}
        self.assertIsNone(ai.explain_review(case))

    def test_ai_draft_uses_confirmed_values_only(self):
        ai = AIService(api_key="test")
        case = {
            "status": "MISMATCH",
            "mismatches": [
                {
                    "field": "container_count",
                    "si": {"raw_value": "3"},
                    "bl": {"raw_value": "4"},
                }
            ],
        }
        ai._json = lambda prompt, **kwargs: {
            "opening": "Please review the following discrepancy.",
            "closing": "Please amend the draft accordingly.",
        }
        draft = ai.draft_correction_email(case)
        self.assertIn("SI says 3; draft BL says 4", draft)
        ai._json = lambda prompt, **kwargs: {
            "opening": "Please amend booking 123 by tomorrow.",
            "closing": "Please amend the draft accordingly.",
        }
        self.assertIsNone(ai.draft_correction_email(case))

    def test_semantic_mapping_requires_source_evidence(self):
        ai = AIService(api_key="test")
        ai._json = lambda prompt, **kwargs: {
            "port_of_loading": {
                "raw_value": "Klang",
                "source_text": "Origin Port: Klang",
                "confidence": 0.99,
            },
            "port_of_discharge": {
                "raw_value": "Callao",
                "source_text": "Invented: Callao",
                "confidence": 0.99,
            },
        }
        fields = extract_fields(doc("Origin Port: Klang\nDischarge Port: Callao"), ai)
        self.assertEqual(fields["port_of_loading"]["normalized_value"], "klang")
        self.assertEqual(fields["port_of_loading"]["method"], "gemini_semantic_mapping")
        self.assertEqual(fields["port_of_discharge"]["method"], "native")

    def test_email_intent_uses_body_and_attachments(self):
        comparison = {
            "subject": "RE: old invoice",
            "body": "Please verify the attached SI against the draft BL.",
            "attachments": ["x_SI.txt", "x_BL.txt"],
        }
        self.assertEqual(classify_email(comparison)["category"], "BL_COMPARISON")
        self.assertEqual(
            classify_email(
                {
                    "subject": "request SI",
                    "body": "Please find Shipping instruction for order 10. 3 Original invoice required.",
                    "attachments": [],
                }
            )["category"],
            "SI_REQUEST",
        )
        self.assertEqual(
            classify_email(
                {
                    "subject": "Invoice",
                    "body": "Can you clarify the local charges?",
                    "attachments": [],
                }
            )["category"],
            "INVOICE_QUERY",
        )
        self.assertEqual(
            classify_email(
                {
                    "subject": "News",
                    "body": "Verify your account to claim prize",
                    "attachments": [],
                }
            )["category"],
            "SPAM",
        )

    def test_alias_extraction_and_normalization(self):
        fields = extract_fields(
            doc("""SHIPPING INSTRUCTION
Shipper/Exporter: Acme, Ltd.
Consignee (Non-Negotiable): Buyer Co
Notify: Buyer Co
POL: Port Klang
Discharge Port: Callao, Peru
No. of Containers or Packages: 6 x 40'HC
TOTAL Gross Weight (KG): 21,577 KG""")
        )
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
        self.assertEqual(
            identify_documents({}, [si, wrong])["reason"], "wrong_doc_type"
        )

    def test_comparison_only_official_fields(self):
        si = extract_fields(
            doc(
                "Shipper: ACME LTD\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 2 x 40'HC\nGross Wt: 21,577 KG"
            )
        )
        bl = extract_fields(
            doc(
                "SHIPPER: Acme, Ltd.\nConsignee: Buyer\nNotify: Buyer\nLoad Port: Klang\nDischarge Port: Callao\nContainer Count: 3 x 40'HC\nGross Weight: 21577 kg\nVessel: Different"
            )
        )
        result = compare_documents(si, bl)
        self.assertEqual(
            [m["field"] for m in result["mismatches"]], ["container_count"]
        )
        self.assertEqual(result["missing_fields"], [])

    def test_internal_reconciliation(self):
        text = "No. of Containers: 2\nGross Weight: 400 KG\nABCD1234567\n40HC\n200\nEFGH1234567\n40HC\n200"
        self.assertTrue(validate_document_consistency(doc(text))["valid"])
        self.assertFalse(
            validate_document_consistency(doc(text.replace("400 KG", "300 KG")))[
                "valid"
            ]
        )
        self.assertFalse(
            validate_document_consistency(
                doc(text.replace("Containers: 2", "Containers: 3"))
            )["valid"]
        )

    def test_reviewer_correction_rebuilds_result(self):
        si = extract_fields(
            doc(
                "Shipper: ACME\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 2\nGross Weight: 400 KG"
            )
        )
        bl = extract_fields(
            doc(
                "Shipper: ACME\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 3\nGross Weight: 400 KG"
            )
        )
        case = {
            "category": "BL_COMPARISON",
            "status": "MISMATCH",
            "si_fields": si,
            "bl_fields": bl,
            "mismatches": compare_documents(si, bl)["mismatches"],
        }
        corrected, record = apply_correction(case, "bl", "container_count", "2 x 40'HC")
        self.assertEqual(record["status"], "OK")
        self.assertEqual(case["bl_fields"]["container_count"]["raw_value"], "3")
        equivalent, record = mark_equivalent(case, "container_count")
        self.assertEqual(record["status"], "OK")
        self.assertEqual(equivalent["bl_fields"]["container_count"]["raw_value"], "3")

    def test_confirming_one_value_preserves_other_uncertainty(self):
        text = "Shipper: ACME\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 2\nGross Weight: 400 KG"
        si, bl = extract_fields(doc(text)), extract_fields(doc(text))
        si["gross_weight_kg"]["confidence"] = 0.7
        bl["gross_weight_kg"]["confidence"] = 0.8
        case = {
            "category": "BL_COMPARISON",
            "status": "NEEDS_REVIEW",
            "si_fields": si,
            "bl_fields": bl,
            "uncertain_fields": ["gross_weight_kg"],
        }
        confirmed, record = confirm_value(case, "si", "gross_weight_kg")
        self.assertEqual(record["status"], "NEEDS_REVIEW")
        self.assertEqual(confirmed["uncertain_fields"], ["gross_weight_kg"])
        confirmed, record = confirm_value(confirmed, "bl", "gross_weight_kg")
        self.assertEqual(record["status"], "OK")
        self.assertEqual(confirmed["uncertain_fields"], [])
        self.assertEqual(si["gross_weight_kg"]["confidence"], 0.7)

    def test_scanned_pdf_fallback_and_technical_failure(self):
        class Inbox:
            def read_bytes(self, path):
                return b"not a text PDF"

        class Vision:
            enabled = True

            def transcribe_pdf(self, data):
                return {"text": "Shipper: ACME", "confidence": 0.8}

        document = read_document("scan.pdf", Inbox(), Vision())
        self.assertEqual(document["method"], "gemini_pdf_vision")
        self.assertEqual(document["confidence"], 0.8)

        class BrokenInbox:
            def read_bytes(self, path):
                raise OSError("Storage temporarily unavailable")

        email = {
            "email_id": "broken",
            "subject": "Compare SI and draft BL",
            "body": "Please verify the attached shipping instruction against the draft BL.",
            "attachments": ["x_SI.txt", "x_BL.txt"],
        }
        record, case = process_email(email, BrokenInbox())
        self.assertEqual(case["status"], "PROCESSING_FAILED")
        self.assertEqual(case["processing_step"], "Reading attachments")
        self.assertEqual(case["failed_attachment"], "x_SI.txt")
        self.assertEqual(record["status"], "NEEDS_REVIEW")

    def test_force_vision_retry_uses_pdf_transcription(self):
        class Inbox:
            def read_bytes(self, path):
                return b"pdf content"

        class Page:
            def extract_text(self):
                return "Shipper: Native text"

        class Reader:
            pages = [Page()]

        class Vision:
            enabled = True

            def transcribe_pdf(self, data):
                return {"text": "Shipper: Vision text", "confidence": 0.85}

        with patch("pypdf.PdfReader", return_value=Reader()):
            native = read_document("document.pdf", Inbox(), Vision())
            retry = read_document("document.pdf", Inbox(), Vision(), force_vision=True)
        self.assertEqual(native["text"], "Shipper: Native text")
        self.assertEqual(retry["text"], "Shipper: Vision text")
        self.assertEqual(retry["method"], "gemini_pdf_vision")

    def test_full_bundle_schema_if_present(self):
        root = Path(__file__).resolve().parent.parent
        if not (root / "Bundle" / "sample_submission.json").exists():
            self.skipTest("Local competition bundle unavailable")
        from Bundle.loader import Inbox

        inbox = Inbox(str(root / "Bundle"))
        submission = generate_submission(inbox)
        sample = inbox.sample_submission()
        self.assertEqual(set(submission), set(sample))
        self.assertTrue(
            all(
                set(record) == set(next(iter(sample.values())))
                for record in submission.values()
            )
        )

    def test_multiformat_bundle_examples_if_present(self):
        root = Path(__file__).resolve().parent.parent
        if not (root / "Bundle" / "attachments").exists():
            self.skipTest("Local competition bundle unavailable")
        from Bundle.loader import Inbox

        inbox = Inbox(str(root / "Bundle"))
        for path in (
            "attachments/email_004_SI.txt",
            "attachments/email_005_SI.xlsx",
            "attachments/email_055_BL.docx",
            "attachments/email_059_SI.pdf",
        ):
            with self.subTest(path=path):
                fields = extract_fields(read_document(path, inbox))
                self.assertEqual(set(fields), set(FIELDS))


if __name__ == "__main__":
    unittest.main()
