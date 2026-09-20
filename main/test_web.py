"""End-to-end HTTP check of the reviewer workflow using in-memory data."""

import json
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comparator import compare_documents
from dashboard import Handler
from extractor import extract_fields


class MemoryRepository:
    def __init__(self):
        self.email = {"email_id": "email_demo", "from": "operator@example.com", "subject": "Check draft BL",
                      "body": "Please compare SI and BL", "attachments": ["attachments/si.txt", "attachments/bl.txt"]}
        self.files = {"attachments/si.txt": b"Shipper: ACME\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 2\nGross Weight: 400 KG",
                      "attachments/bl.txt": b"Shipper: ACME\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 3\nGross Weight: 400 KG"}
        si, bl = [extract_fields({"path": path, "text": data.decode(), "file_type": ".txt"})
                  for path, data in self.files.items()]
        self.case = {"email_id": "email_demo", "email": self.email, "category": "BL_COMPARISON",
                     "classification": {"category": "BL_COMPARISON", "confidence": .98},
                     "status": "MISMATCH", "si_fields": si, "bl_fields": bl,
                     "mismatches": compare_documents(si, bl)["mismatches"]}
        self.decision = {}

    def list_cases(self):
        return {"email_demo": self.case}

    def get_case(self, email_id):
        return self.case

    def get_email(self, email_id):
        return self.email

    def read_bytes(self, path):
        return self.files[path]

    def list_decisions(self):
        return self.decision

    def save_case(self, email_id, case, submission):
        self.case = case
        self.submission = submission

    def save_processing(self, email_id, case, step="Retrying document verification"):
        self.processing_seen = True
        self.case = {**case, "status": "PROCESSING", "processing_step": step}

    def save_decision(self, email_id, action, note="", corrections=None):
        self.decision[email_id] = {"action": action, "note": note, "corrections": corrections or {}}
        return self.decision[email_id]


class WebWorkflowTests(unittest.TestCase):
    def test_inbox_preview_and_reviewer_correction(self):
        previous = Handler.repository
        Handler.repository = MemoryRepository()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/cases") as response:
                cases = json.load(response)
            self.assertEqual(cases["metrics"]["mismatches"], 1)
            with urllib.request.urlopen(base + "/api/email/email_demo") as response:
                self.assertEqual(json.load(response)["subject"], "Check draft BL")
            with urllib.request.urlopen(base + "/api/document/email_demo/0") as response:
                self.assertIn("Shipper: ACME", json.load(response)["text"])
            payload = json.dumps({"action": "correct", "role": "bl", "field": "container_count",
                                  "value": "2", "note": "Checked source"}).encode()
            request = urllib.request.Request(base + "/api/decision/email_demo", payload,
                                             {"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            self.assertEqual(result["case"]["status"], "OK")
            self.assertEqual(Handler.repository.submission["defect_fields"], [])
        finally:
            server.shutdown()
            server.server_close()
            Handler.repository = previous

    def test_confirm_value_endpoint_recompares(self):
        previous = Handler.repository
        Handler.repository = MemoryRepository()
        Handler.repository.case["bl_fields"]["gross_weight_kg"]["confidence"] = .7
        Handler.repository.case["status"] = "NEEDS_REVIEW"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = json.dumps({"action": "confirm_value", "role": "bl",
                                  "field": "gross_weight_kg"}).encode()
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/decision/email_demo",
                                             payload, {"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            self.assertEqual(result["case"]["status"], "MISMATCH")
            self.assertEqual(result["case"]["bl_fields"]["gross_weight_kg"]["confidence"], 1)
            self.assertEqual(Handler.repository.decision["email_demo"]["corrections"]["field"], "gross_weight_kg")
        finally:
            server.shutdown()
            server.server_close()
            Handler.repository = previous

    def test_retry_shows_processing_before_final_result(self):
        previous = Handler.repository
        Handler.repository = MemoryRepository()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/retry/email_demo",
                                             b"{}", {"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            self.assertTrue(Handler.repository.processing_seen)
            self.assertEqual(result["case"]["status"], "MISMATCH")
            self.assertEqual(Handler.repository.submission["status"], "MISMATCH")
        finally:
            server.shutdown()
            server.server_close()
            Handler.repository = previous


if __name__ == "__main__":
    unittest.main()
