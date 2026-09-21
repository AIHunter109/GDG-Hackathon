"""Shipping operations web app. Run: python main/dashboard.py"""

import base64
import binascii
import json
import logging
import mimetypes
import os
import sys
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from call_for_help import draft_correction_email
from ai_service import AIService
from comparator import FIELDS
from extractor import read_document
from main import build_evidence_report, process_email
from repository import get_repository
from review_actions import apply_correction, confirm_value, mark_equivalent, submission_for_case

LOG = logging.getLogger(__name__)
VERIFIED_PERFORMANCE = json.loads((Path(__file__).parent / "verified_performance.json").read_text(encoding="utf-8"))
CATEGORY_WORKFLOW_STATES = {
    "SI_REQUEST": {"needs_response", "waiting_for_information", "ready_to_prepare", "response_sent", "completed"},
    "INVOICE_QUERY": {"needs_review", "routed_to_finance", "waiting_for_finance", "response_sent", "completed"},
    "GENERAL": {"needs_review", "forwarded_to_owner", "waiting_for_response", "response_sent", "completed"},
    "SPAM": {"needs_review", "confirmed_spam", "restored_to_inbox", "archived", "completed"},
}


def metrics_for(cases, decisions=None):
    decisions = decisions or {}
    statuses = Counter(c["status"] for c in cases.values())
    categories = Counter(c["category"] for c in cases.values())
    comparison = [c for c in cases.values() if c["category"] == "BL_COMPARISON"]
    paired = [c for c in comparison if c.get("si_fields") is not None and c.get("bl_fields") is not None]
    extracted = sum(c.get(role + "_fields", {}).get(field, {}).get("normalized_value") is not None
                    for c in paired for role in ("si", "bl") for field in FIELDS)
    unresolved_reviews = sum(c["status"] == "NEEDS_REVIEW" and
                             decisions.get(email_id, {}).get("action") != "resolve"
                             for email_id, c in cases.items())
    ai_assisted = sum(c.get("classification", {}).get("method") == "gemini" or
                      c.get("document_identification", {}).get("method") == "gemini_role_detection" or
                      any(field.get("method", "").startswith("gemini") for group in
                          (c.get("si_fields", {}), c.get("bl_fields", {})) for field in group.values())
                      for c in cases.values())
    verification_results = dict(Counter(c["status"] for c in comparison))
    verification_results["NOT_APPLICABLE"] = (verification_results.get("NOT_APPLICABLE", 0) +
                                                len(cases) - len(comparison))
    return {"total": len(cases), "categories": dict(categories), "statuses": dict(statuses),
            "verification_results": verification_results,
            "comparison_requests": len(comparison),
            "automatically_cleared": sum(c["status"] == "OK" for c in comparison),
            "mismatches": statuses["MISMATCH"],
            "human_review": unresolved_reviews,
            "processing_failures": statuses["PROCESSING_FAILED"],
            "processing": statuses["PROCESSING"],
            "field_extraction_coverage": extracted / (2 * len(FIELDS) * len(paired)) if paired else None,
            "human_review_rate": unresolved_reviews / len(comparison) if comparison else 0,
            "processing_failure_rate": statuses["PROCESSING_FAILED"] / len(cases) if cases else 0,
            "ai_assisted_cases": ai_assisted,
            "automation_rate": (sum(c["status"] == "OK" for c in comparison) / len(comparison)
                                if comparison else 0)}


class Handler(BaseHTTPRequestHandler):
    repository = None

    @classmethod
    def store(cls):
        if cls.repository is None:
            cls.repository = get_repository()
        return cls.repository

    def _send(self, status, value, content_type="application/json", *, headers=None):
        data = value if isinstance(value, bytes) else (value.encode("utf-8") if isinstance(value, str)
                                                      else json.dumps(value).encode("utf-8"))
        self.send_response(status)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type == "application/json" else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, item in (headers or {}).items():
            self.send_header(key, item)
        self.end_headers()
        self.wfile.write(data)

    def _attachment(self, email_id, number):
        email = self.store().get_email(email_id)
        paths = email.get("attachments", [])
        index = int(number)
        if not 0 <= index < len(paths):
            raise KeyError("Attachment does not exist")
        return paths[index]

    def do_GET(self):
        parts = [unquote(part) for part in urlsplit(self.path).path.strip("/").split("/")]
        try:
            if parts == [""]:
                return self._send(200, (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8"), "text/html")
            if parts == ["healthz"]:
                return self._send(200, {"ok": True})
            if parts == ["api", "cases"]:
                cases = self.store().list_cases()
                decisions = self.store().list_decisions()
                new_case_ids = (self.store().list_new_case_ids()
                                if hasattr(self.store(), "list_new_case_ids") else [])
                return self._send(200, {"cases": cases, "decisions": decisions,
                                        "metrics": metrics_for(cases, decisions),
                                        "features": {"ai_enabled": AIService().enabled,
                                                     "cloud_mode": bool(os.getenv("GCS_BUCKET")),
                                                     "new_case_ids": new_case_ids,
                                                     "verified_performance": VERIFIED_PERFORMANCE}})
            if len(parts) == 3 and parts[:2] == ["api", "email"]:
                return self._send(200, self.store().get_email(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "attachment"]:
                path = self._attachment(parts[2], parts[3])
                data = self.store().read_bytes(path)
                mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                return self._send(200, data, mime, headers={"Content-Disposition": "inline"})
            if len(parts) == 4 and parts[:2] == ["api", "document"]:
                path = self._attachment(parts[2], parts[3])
                return self._send(200, read_document(path, self.store()))
            self._send(404, {"error": "Not found"})
        except (KeyError, ValueError, OSError, IndexError) as exc:
            self._send(404, {"error": str(exc)})

    def do_POST(self):
        parts = [unquote(part) for part in urlsplit(self.path).path.strip("/").split("/")]
        try:
            if len(parts) != 3 or parts[0] != "api":
                return self._send(404, {"error": "Not found"})
            length = int(self.headers.get("Content-Length", "0"))
            action, email_id = parts[1], parts[2]
            limit = 70_000_000 if action == "upload" else 100_000
            if length > limit:
                return self._send(413, {"error": "Request too large"})
            payload = json.loads(self.rfile.read(length) or b"{}")
            store = self.store()
            if action == "upload" and email_id == "new":
                if not hasattr(store, "save_new_email"):
                    raise ValueError("Local uploads are unavailable in this deployment")
                metadata = payload.get("email") or {}
                files = payload.get("files") or []
                if not metadata.get("subject") or not metadata.get("body"):
                    raise ValueError("Add a short description so the app can classify the submission")
                if not 1 <= len(files) <= 10:
                    raise ValueError("A verification submission must contain between 1 and 10 documents")
                decoded = []
                for index, item in enumerate(files):
                    try:
                        content = base64.b64decode(item.get("content", ""), validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ValueError("An uploaded document could not be read") from exc
                    if not content or len(content) > 5_000_000:
                        raise ValueError("Each document must be between 1 byte and 5 MB")
                    decoded.append((item.get("role") or f"document_{index + 1}",
                                    item.get("name", "document"), content))
                email = store.save_new_email(metadata, decoded)
                record, case = process_email(email, store)
                case = build_evidence_report(case)
                store.save_case(email["email_id"], case, record)
                return self._send(201, {"case": case, "submission": record})
            case = store.get_case(email_id)
            if action == "reanalyze":
                if not hasattr(store, "replace_documents"):
                    raise ValueError("Local document replacement is unavailable in this deployment")
                files = payload.get("files") or []
                if len(files) != 2 or {item.get("role") for item in files} != {"si", "bl"}:
                    raise ValueError("Select one SI and one draft BL")
                decoded = []
                for item in files:
                    try:
                        content = base64.b64decode(item.get("content", ""), validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ValueError("A selected document could not be read") from exc
                    if not content or len(content) > 5_000_000:
                        raise ValueError("Each document must be between 1 byte and 5 MB")
                    decoded.append((item["role"], item.get("name", "document"), content))
                email = store.replace_documents(email_id, decoded)
                record, refreshed = process_email(email, store, force_vision=AIService().enabled)
                refreshed = build_evidence_report(refreshed)
                store.save_case(email_id, refreshed, record)
                return self._send(200, {"case": refreshed, "submission": record})
            if action == "decision":
                choice = payload.get("action")
                corrections = {}
                if choice == "correct":
                    role, field, value = (payload.get(key) for key in ("role", "field", "value"))
                    case, record = apply_correction(case, role, field, value)
                    store.save_case(email_id, case, record)
                    corrections = {"role": role, "field": field, "value": value}
                elif choice == "mark_equivalent":
                    field = payload.get("field")
                    case, record = mark_equivalent(case, field)
                    store.save_case(email_id, case, record)
                    corrections = {"field": field}
                elif choice == "confirm_value":
                    role, field = payload.get("role"), payload.get("field")
                    case, record = confirm_value(case, role, field)
                    store.save_case(email_id, case, record)
                    corrections = {"role": role, "field": field}
                elif choice == "reopen":
                    if case.get("status") != "NEEDS_REVIEW" or store.list_decisions().get(email_id, {}).get("action") != "resolve":
                        raise ValueError("Only a completed human review can be reopened")
                elif choice == "complete_category":
                    if case.get("category") == "BL_COMPARISON":
                        raise ValueError("Document comparisons use verification review actions")
                elif choice == "update_category_workflow":
                    workflow_state = payload.get("workflow_state")
                    if workflow_state not in CATEGORY_WORKFLOW_STATES.get(case.get("category"), set()):
                        raise ValueError("Choose a valid workflow status for this category")
                    corrections = {"workflow_state": workflow_state}
                elif choice == "reopen_category":
                    if (case.get("category") == "BL_COMPARISON" or
                            store.list_decisions().get(email_id, {}).get("action") != "complete_category"):
                        raise ValueError("Only a completed category task can be reopened")
                elif choice == "update_assignment":
                    priority = payload.get("priority", "normal")
                    if priority not in {"low", "normal", "high", "urgent"}:
                        raise ValueError("Choose a valid priority")
                    corrections = {"reviewer": str(payload.get("reviewer", ""))[:120],
                                   "priority": priority, "due": str(payload.get("due", ""))[:10]}
                elif choice == "reviewer_feedback":
                    outcome, cause = payload.get("outcome"), payload.get("cause")
                    if outcome not in {"accepted", "corrected"}:
                        raise ValueError("Choose whether the result was accepted or corrected")
                    if cause not in {"classification", "extraction", "comparison", "other", "none"}:
                        raise ValueError("Choose a valid feedback cause")
                    corrections = {"outcome": outcome, "cause": cause,
                                   "comment": str(payload.get("comment", ""))[:1000]}
                elif choice not in {"confirm", "resolve"}:
                    raise ValueError("Unknown reviewer action")
                decision = store.save_decision(email_id, choice, payload.get("note", ""), corrections)
                LOG.info("Reviewer action %s on %s", choice, email_id)
                return self._send(200, {"decision": decision, "case": case})
            if action == "draft":
                decision = store.list_decisions().get(email_id, {})
                fallback = draft_correction_email(case, confirmed=decision.get("action") == "confirm")
                suggestion = AIService().draft_correction_email(case)
                return self._send(200, {"draft": suggestion or fallback,
                                        "source": "AI" if suggestion else "Template"})
            if action == "explain":
                explanation = AIService().explain_review(case)
                if not explanation:
                    raise ValueError("AI explanation is unavailable; review the source evidence shown in this case")
                return self._send(200, explanation)
            if action == "retry":
                vision = payload.get("vision") is True
                if vision and not AIService().enabled:
                    raise ValueError("AI vision is not configured for this demo")
                LOG.info("Retrying %s%s", email_id, " with AI vision" if vision else "")
                store.save_processing(email_id, case, "Retrying with OCR / Vision" if vision else "Retrying document verification")
                try:
                    record, refreshed = process_email(store.get_email(email_id), store,
                                                      force_vision=vision)
                    case = build_evidence_report(refreshed)
                except Exception as exc:
                    LOG.exception("Retry failed for %s", email_id)
                    case = {**case, "status": "PROCESSING_FAILED", "internal_reason": "TECHNICAL_FAILURE",
                            "review_reason": "unreadable", "processing_step": "Retrying document verification",
                            "error": str(exc)}
                    record = submission_for_case(case)
                store.save_case(email_id, case, record)
                return self._send(200, {"case": case, "submission": record})
            if action == "select_document":
                role, path = payload.get("role"), payload.get("path")
                if role not in {"si", "bl"} or path not in store.get_email(email_id).get("attachments", []):
                    raise ValueError("Select an attached SI or BL document")
                previous = store.list_decisions().get(email_id, {}).get("corrections", {})
                selection = {key: value for key, value in previous.items() if key in {"si", "bl"}}
                selection[role] = path
                if selection.get("si") and selection.get("bl") and selection["si"] != selection["bl"]:
                    record, refreshed = process_email(store.get_email(email_id), store, role_override=selection)
                    case = build_evidence_report(refreshed)
                    store.save_case(email_id, case, record)
                decision = store.save_decision(email_id, "select_document", payload.get("note", ""), selection)
                return self._send(200, {"decision": decision, "case": case})
            self._send(404, {"error": "Unknown action"})
        except (KeyError, ValueError, OSError, TypeError) as exc:
            self._send(400, {"error": str(exc)})


def run():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8765")))
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0" if os.getenv("K_SERVICE") else "127.0.0.1"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    LOG.info("Review dashboard listening on %s:%s", args.host, args.port)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    run()
