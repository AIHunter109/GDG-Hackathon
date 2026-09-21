"""End-to-end HTTP check of the reviewer workflow using in-memory data."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from call_for_help import RaiseIssueToHuman
from comparator import compare_documents
from dashboard import Handler
from extractor import extract_fields


class MemoryRepository:
    def __init__(self):
        self.email = {
            "email_id": "email_demo",
            "from": "operator@example.com",
            "subject": "Check draft BL",
            "body": "Please compare SI and BL",
            "attachments": ["attachments/si.txt", "attachments/bl.txt"],
        }
        self.files = {
            "attachments/si.txt": b"Shipper: ACME\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 2\nGross Weight: 400 KG",
            "attachments/bl.txt": b"Shipper: ACME\nConsignee: Buyer\nNotify: Buyer\nPOL: Klang\nPOD: Callao\nContainers: 3\nGross Weight: 400 KG",
        }
        si, bl = [
            extract_fields({"path": path, "text": data.decode(), "file_type": ".txt"})
            for path, data in self.files.items()
        ]
        self.case = {
            "email_id": "email_demo",
            "email": self.email,
            "category": "BL_COMPARISON",
            "classification": {"category": "BL_COMPARISON", "confidence": 0.98},
            "status": "MISMATCH",
            "si_fields": si,
            "bl_fields": bl,
            "mismatches": compare_documents(si, bl)["mismatches"],
        }
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
        self.decision[email_id] = {
            "action": action,
            "note": note,
            "corrections": corrections or {},
        }
        return self.decision[email_id]


class WebWorkflowTests(unittest.TestCase):
    def test_assignment_and_feedback_preserve_operational_decision_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            decisions = RaiseIssueToHuman(Path(directory) / "decisions.json")
            decisions.resolve("email_demo", "confirm", note="Mismatch checked")
            decisions.resolve(
                "email_demo",
                "update_assignment",
                corrections={
                    "reviewer": "Alex",
                    "priority": "high",
                    "due": "2026-09-25",
                },
            )
            result = decisions.resolve(
                "email_demo",
                "reviewer_feedback",
                corrections={
                    "outcome": "corrected",
                    "cause": "extraction",
                    "comment": "Weight fixed",
                },
            )
            self.assertEqual(result["action"], "confirm")
            self.assertEqual(result["assignment"]["reviewer"], "Alex")
            self.assertEqual(result["feedback"]["cause"], "extraction")
            self.assertEqual(
                [item["action"] for item in result["activity"]],
                ["confirm", "update_assignment", "reviewer_feedback"],
            )

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
            self.assertEqual(
                cases["features"]["verified_performance"]["expected_reviews"], 20
            )
            self.assertNotIn(
                "overall_accuracy", cases["features"]["verified_performance"]
            )
            with urllib.request.urlopen(base + "/api/email/email_demo") as response:
                self.assertEqual(json.load(response)["subject"], "Check draft BL")
            with urllib.request.urlopen(
                base + "/api/document/email_demo/0"
            ) as response:
                self.assertIn("Shipper: ACME", json.load(response)["text"])
            payload = json.dumps(
                {
                    "action": "correct",
                    "role": "bl",
                    "field": "container_count",
                    "value": "2",
                    "note": "Checked source",
                }
            ).encode()
            request = urllib.request.Request(
                base + "/api/decision/email_demo",
                payload,
                {"Content-Type": "application/json"},
                method="POST",
            )
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
        Handler.repository.case["bl_fields"]["gross_weight_kg"]["confidence"] = 0.7
        Handler.repository.case["status"] = "NEEDS_REVIEW"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = json.dumps(
                {"action": "confirm_value", "role": "bl", "field": "gross_weight_kg"}
            ).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/decision/email_demo",
                payload,
                {"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            self.assertEqual(result["case"]["status"], "MISMATCH")
            self.assertEqual(
                result["case"]["bl_fields"]["gross_weight_kg"]["confidence"], 1
            )
            self.assertEqual(
                Handler.repository.decision["email_demo"]["corrections"]["field"],
                "gross_weight_kg",
            )
        finally:
            server.shutdown()
            server.server_close()
            Handler.repository = previous

    def test_static_assets_are_served(self):
        """Regression test for the bug where dashboard.html referenced
        style.css/dashboard.js as plain relative paths but the router had
        no route for them -- both 404'd and the page rendered unstyled
        with no JavaScript at all."""
        previous = Handler.repository
        Handler.repository = MemoryRepository()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/") as response:
                self.assertEqual(response.status, 200)
                self.assertIn("text/html", response.headers["Content-Type"])
            with urllib.request.urlopen(base + "/style.css") as response:
                self.assertEqual(response.status, 200)
                self.assertIn("text/css", response.headers["Content-Type"])
            with urllib.request.urlopen(base + "/dashboard.js") as response:
                self.assertEqual(response.status, 200)
                self.assertIn(
                    response.headers["Content-Type"],
                    ("application/javascript", "text/javascript"),
                )
        finally:
            server.shutdown()
            server.server_close()
            Handler.repository = previous

    def test_reopen_action_is_accepted(self):
        previous = Handler.repository
        Handler.repository = MemoryRepository()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = json.dumps({"action": "reopen", "note": "double-check"}).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/decision/email_demo",
                payload,
                {"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            self.assertEqual(result["decision"]["action"], "reopen")
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
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/retry/email_demo",
                b"{}",
                {"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            self.assertTrue(Handler.repository.processing_seen)
            self.assertEqual(result["case"]["status"], "MISMATCH")
            self.assertEqual(Handler.repository.submission["status"], "MISMATCH")
        finally:
            server.shutdown()
            server.server_close()
            Handler.repository = previous

    def test_completed_review_can_be_reopened(self):
        previous = Handler.repository
        Handler.repository = MemoryRepository()
        Handler.repository.case["status"] = "NEEDS_REVIEW"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:

            def post(action):
                request = urllib.request.Request(
                    base + "/api/decision/email_demo",
                    json.dumps({"action": action}).encode(),
                    {"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    return json.load(response)

            self.assertEqual(post("resolve")["decision"]["action"], "resolve")
            with urllib.request.urlopen(base + "/api/cases") as response:
                self.assertEqual(json.load(response)["metrics"]["human_review"], 0)
            reopened = post("reopen")
            self.assertEqual(reopened["decision"]["action"], "reopen")
            self.assertEqual(reopened["case"]["status"], "NEEDS_REVIEW")
            with urllib.request.urlopen(base + "/api/cases") as response:
                self.assertEqual(json.load(response)["metrics"]["human_review"], 1)
            with self.assertRaises(urllib.error.HTTPError) as error:
                post("reopen")
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            Handler.repository = previous

    def test_non_comparison_workflow_supports_validated_states(self):
        previous = Handler.repository
        Handler.repository = MemoryRepository()
        Handler.repository.case["category"] = "INVOICE_QUERY"
        Handler.repository.case["status"] = "OK"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:

            def post(action, workflow_state=None):
                body = {"action": action}
                if workflow_state:
                    body["workflow_state"] = workflow_state
                request = urllib.request.Request(
                    base + "/api/decision/email_demo",
                    json.dumps(body).encode(),
                    {"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    return json.load(response)

            routed = post("update_category_workflow", "routed_to_finance")
            self.assertEqual(
                routed["decision"]["corrections"]["workflow_state"], "routed_to_finance"
            )
            waiting = post("update_category_workflow", "waiting_for_finance")
            self.assertEqual(
                waiting["decision"]["corrections"]["workflow_state"],
                "waiting_for_finance",
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                post("update_category_workflow", "forwarded_to_owner")
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            Handler.repository = previous


if __name__ == "__main__":
    unittest.main()
