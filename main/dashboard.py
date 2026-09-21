"""Shipping operations web app. Run: python main/dashboard.py"""

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
                return self._send(200, {"cases": cases, "decisions": decisions,
                                        "metrics": metrics_for(cases, decisions),
                                        "features": {"ai_enabled": AIService().enabled,
                                                     "cloud_mode": bool(os.getenv("GCS_BUCKET"))}})
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
            if length > 100_000:
                return self._send(413, {"error": "Request too large"})
            payload = json.loads(self.rfile.read(length) or b"{}")
            action, email_id = parts[1], parts[2]
            store = self.store()
            case = store.get_case(email_id)
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
