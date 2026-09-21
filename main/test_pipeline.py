"""Focused regression tests for document decisions and submission shape."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_service import AIService
from call_for_help import RaiseIssueToHuman
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
from review_diagnostics import build_review_diagnostics

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

        documents = [
            doc("Shipping Instruction", "si.txt"),
            doc("Draft Bill of Lading", "bl.txt"),
        ]
        ai._json = lambda prompt, **kwargs: {
            "si_path": "si.txt",
            "bl_path": "bl.txt",
            "confidence": 2,
        }
        self.assertIsNone(ai.identify_documents({}, documents))
        ai._json = lambda prompt, **kwargs: {"text": "Shipper: ACME", "confidence": -1}
        self.assertIsNone(ai.transcribe_pdf(b"synthetic"))

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

    def test_ai_review_case_requires_verbatim_proof(self):
        ai = AIService(api_key="test")
        case = {
            "status": "NEEDS_REVIEW",
            "internal_reason": "LOW_EXTRACTION_CONFIDENCE",
            "uncertain_fields": ["gross_weight_kg"],
            "bl_fields": {
                "gross_weight_kg": {
                    "raw_value": "400 KG",
                    "source_text": "Gross Weight: 400 KG",
                    "confidence": 0.6,
                }
            },
        }
        ai._json = lambda prompt, **kwargs: {
            "assessment": "The BL's gross weight was extracted with low confidence.",
            "proof": "Gross Weight: 400 KG",
            "recommended_action": "Confirm the weight against the source document.",
        }
        result = ai.review_case(case)
        self.assertEqual(result["proof"], "Gross Weight: 400 KG")
        self.assertEqual(case["status"], "NEEDS_REVIEW")
        ai._json = lambda prompt, **kwargs: {
            "assessment": "The BL's gross weight was extracted with low confidence.",
            "proof": "The document clearly states four hundred kilograms.",
            "recommended_action": "Confirm the weight against the source document.",
        }
        self.assertIsNone(ai.review_case(case))

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

    def test_draft_request_without_documents_does_not_create_review(self):
        email = {
            "email_id": "draft_request",
            "subject": "Draft BL for checking",
            "body": "Please send the draft BL for booking ABC for checking.",
            "attachments": [],
        }
        record, case = process_email(email, None)
        self.assertEqual(record["category"], "BL_COMPARISON")
        self.assertEqual(record["status"], "OK")
        self.assertEqual(case["status"], "NOT_APPLICABLE")
        self.assertEqual(case["internal_reason"], "AWAITING_COMPARISON_DOCUMENTS")

    def test_actionable_comparison_without_documents_requires_review(self):
        email = {
            "email_id": "missing_pair",
            "subject": "Compare SI and draft BL",
            "body": "Please compare the SI and draft BL; the attachments were dropped.",
            "attachments": [],
        }
        record, case = process_email(email, None)
        self.assertEqual(record["status"], "NEEDS_REVIEW")
        self.assertEqual(record["review_reason"], "missing_attachment")
        self.assertEqual(case["internal_reason"], "MISSING_SI_AND_BL")

    def test_review_diagnostics_reports_false_reviews(self):
        submission = {
            "expected": {"status": "NEEDS_REVIEW", "review_reason": "unreadable"},
            "extra": {"status": "NEEDS_REVIEW", "review_reason": "missing_attachment"},
            "ok": {"status": "OK", "review_reason": None},
        }
        evidence = {
            "expected": {
                "internal_reason": "UNREADABLE_DOCUMENT",
                "email": {"attachments": ["scan.pdf"]},
            },
            "extra": {
                "internal_reason": "MISSING_SI_AND_BL",
                "email": {"attachments": []},
            },
        }
        truth = {
            "expected": {"status": "NEEDS_REVIEW"},
            "extra": {"status": "OK"},
            "ok": {"status": "OK"},
        }
        report = build_review_diagnostics(submission, evidence, truth)
        self.assertEqual(report["false_reviews"], 1)
        self.assertEqual(report["missed_reviews"], 0)
        self.assertEqual(report["by_internal_reason"], {"MISSING_SI_AND_BL": 1})

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

    def test_wrong_doc_type_and_corrupted_pdf_edge_cases_if_present(self):
        """Two real edge cases seeded in the bundle: a "_BL.txt" that is
        actually a Commercial Invoice, and a "_BL.pdf" with a genuinely
        broken stream (pypdf raises PdfStreamError on it -- note
        email_499_BL.pdf looks similarly "corrupted" under pdfplumber, but
        pypdf actually recovers real content from it, so it is correctly
        NOT part of this bundle's unreadable set; email_511/515 are)."""
        root = Path(__file__).resolve().parent.parent
        if not (root / "Bundle" / "attachments").exists():
            self.skipTest("Local competition bundle unavailable")
        from Bundle.loader import Inbox

        inbox = Inbox(str(root / "Bundle"))
        si = read_document("attachments/email_501_SI.txt", inbox)
        wrong_bl = read_document("attachments/email_501_BL.txt", inbox)
        self.assertEqual(
            identify_documents({}, [si, wrong_bl])["reason"], "wrong_doc_type"
        )

        with self.assertRaises(Exception):
            read_document("attachments/email_511_BL.pdf", inbox)

        email = inbox.get("email_511")
        record, case = process_email(email, inbox)
        self.assertEqual(record["status"], "NEEDS_REVIEW")
        self.assertEqual(record["review_reason"], "unreadable")

    def test_zero_attachment_forward_request_resolves_ok_not_review(self):
        """Regression test for the over-escalation bug found and fixed:
        a plain "please prepare the draft BL later" ask with nothing
        attached yet must resolve OK, not sit in the review queue."""
        email = {
            "email_id": "forward_request",
            "subject": "TO CONFIRM DOCS",
            "body": "Dear Team,\n\nPlease assist to send the draft BL for X for checking asap.\n\nThank you.",
            "attachments": [],
        }
        record, case = process_email(email, inbox=None)
        self.assertEqual(record["category"], "BL_COMPARISON")
        self.assertEqual(record["status"], "OK")
        self.assertIsNone(record["review_reason"])

        anomaly_email = {
            **email,
            "email_id": "dropped_attachment",
            "body": "Please compare the SI and draft BL for X and confirm (attachments appear to have been dropped).",
        }
        record, case = process_email(anomaly_email, inbox=None)
        self.assertEqual(record["status"], "NEEDS_REVIEW")
        self.assertEqual(record["review_reason"], "missing_attachment")

    def test_decision_history_and_reopen(self):
        """review_decisions.json must keep every prior decision as an audit
        trail instead of overwriting it, and a resolved case can be
        reopened."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review_decisions.json"
            raiser = RaiseIssueToHuman(path)
            first = raiser.resolve("email_x", "confirm", note="looks fine")
            self.assertEqual(first["history"], [])
            second = raiser.resolve("email_x", "resolve", note="closing it out")
            self.assertEqual(len(second["history"]), 1)
            self.assertEqual(second["history"][0]["action"], "confirm")
            reopened = raiser.resolve("email_x", "reopen", note="need another look")
            self.assertEqual(reopened["action"], "reopen")
            self.assertEqual(len(reopened["history"]), 2)
            self.assertEqual(reopened["history"][-1]["action"], "resolve")

    def test_concurrent_decision_writes_do_not_lose_updates(self):
        """The threading.Lock around review_decisions.json must prevent a
        classic read-modify-write race: N threads resolving N different
        cases concurrently must all survive, none silently dropped."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review_decisions.json"
            raiser = RaiseIssueToHuman(path)
            email_ids = [f"email_{i:03d}" for i in range(30)]

            def resolve_one(email_id):
                raiser.resolve(email_id, "resolve", note=f"resolved {email_id}")

            threads = [
                threading.Thread(target=resolve_one, args=(eid,)) for eid in email_ids
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            saved = raiser.showIssue()
            self.assertEqual(set(saved), set(email_ids))

    def test_review_diagnostics_reports_false_reviews(self):
        submission = {
            "expected": {"status": "NEEDS_REVIEW", "review_reason": "unreadable"},
            "extra": {"status": "NEEDS_REVIEW", "review_reason": "missing_attachment"},
            "ok": {"status": "OK", "review_reason": None},
        }
        evidence = {
            "expected": {
                "internal_reason": "UNREADABLE_DOCUMENT",
                "email": {"attachments": ["scan.pdf"]},
            },
            "extra": {
                "internal_reason": "MISSING_SI_AND_BL",
                "email": {"attachments": []},
            },
        }
        truth = {
            "expected": {"status": "NEEDS_REVIEW"},
            "extra": {"status": "OK"},
            "ok": {"status": "OK"},
        }
        report = build_review_diagnostics(submission, evidence, truth)
        self.assertEqual(report["false_reviews"], 1)
        self.assertEqual(report["missed_reviews"], 0)
        self.assertEqual(report["by_internal_reason"], {"MISSING_SI_AND_BL": 1})


if __name__ == "__main__":
    unittest.main()
