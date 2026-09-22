"""Case and attachment storage for local use or Google Cloud Run."""

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from call_for_help import RaiseIssueToHuman
from inbox import Inbox

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_ATTACHMENT_EXTENSIONS = {".pdf", ".txt", ".xlsx", ".docx"}


def _stamp(case):
    case["updated_at"] = datetime.now(timezone.utc).isoformat()
    return case


def _read_json(path, default=None):
    return (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else (default if default is not None else {})
    )


class LocalRepository:
    def __init__(self, source=None):
        self.inbox = Inbox(source or os.getenv("INBOX_SOURCE", str(ROOT / "Bundle")))
        self.evidence_path = ROOT / "evidence.json"
        self.submission_path = ROOT / "submission.json"
        self.decisions = RaiseIssueToHuman(ROOT / "review_decisions.json")
        self.upload_root = ROOT / ".local_uploads"
        self.upload_manifest = self.upload_root / "manifest.json"
        self._lock = threading.Lock()

    def list_cases(self):
        return _read_json(self.evidence_path)

    def get_case(self, email_id):
        return self.list_cases()[email_id]

    def get_email(self, email_id):
        uploaded = _read_json(self.upload_manifest)
        if email_id in uploaded:
            return uploaded[email_id]
        return self.inbox.get(email_id)

    def read_bytes(self, path):
        relative = Path(path)
        if relative.parts and relative.parts[0] == ".local_uploads":
            resolved = (ROOT / relative).resolve()
            if self.upload_root.resolve() not in resolved.parents:
                raise ValueError("Invalid local upload path")
            return resolved.read_bytes()
        return self.inbox.read_bytes(path)

    def list_new_case_ids(self):
        return list(_read_json(self.upload_manifest))

    def save_new_email(self, metadata, attachments):
        """Persist a reviewer-submitted local email and its two attachments."""
        self.upload_root.mkdir(exist_ok=True)
        attachment_root = self.upload_root / "attachments"
        attachment_root.mkdir(exist_ok=True)
        email_id = "local_" + uuid.uuid4().hex[:12]
        paths = []
        for role, filename, content in attachments:
            suffix = Path(filename).suffix.lower()
            if suffix not in SUPPORTED_ATTACHMENT_EXTENSIONS:
                raise ValueError("Only PDF, TXT, XLSX, and DOCX documents are supported")
            safe_role = re.sub(r"[^a-z0-9_-]", "", role.lower()) or "document"
            relative = Path(".local_uploads") / "attachments" / f"{email_id}_{safe_role}{suffix}"
            (ROOT / relative).write_bytes(content)
            paths.append(relative.as_posix())
        email = {
            "email_id": email_id,
            "from": str(metadata.get("from", ""))[:320],
            "to": str(metadata.get("to", ""))[:320],
            "subject": str(metadata.get("subject", ""))[:500],
            "body": str(metadata.get("body", ""))[:20_000],
            "attachments": paths,
        }
        uploaded = _read_json(self.upload_manifest)
        uploaded[email_id] = email
        self.upload_manifest.write_text(json.dumps(uploaded, indent=2), encoding="utf-8")
        return email

    def replace_documents(self, email_id, attachments):
        """Store reviewer-selected replacement files while keeping the case identity."""
        email = dict(self.get_email(email_id))
        self.upload_root.mkdir(exist_ok=True)
        attachment_root = self.upload_root / "attachments"
        attachment_root.mkdir(exist_ok=True)
        paths = []
        for role, filename, content in attachments:
            suffix = Path(filename).suffix.lower()
            if suffix not in SUPPORTED_ATTACHMENT_EXTENSIONS:
                raise ValueError("Only PDF, TXT, XLSX, and DOCX documents are supported")
            relative = Path(".local_uploads") / "attachments" / f"{email_id}_{role}{suffix}"
            (ROOT / relative).write_bytes(content)
            paths.append(relative.as_posix())
        email["attachments"] = paths
        uploaded = _read_json(self.upload_manifest)
        uploaded[email_id] = email
        self.upload_manifest.write_text(json.dumps(uploaded, indent=2), encoding="utf-8")
        return email

    def list_decisions(self):
        return self.decisions.showIssue()

    def save_decision(self, email_id, action, note="", corrections=None):
        return self.decisions.resolve(
            email_id, action, note=note, corrections=corrections
        )

    def save_case(self, email_id, case, submission):
        with self._lock:
            cases = self.list_cases()
            records = _read_json(self.submission_path)
            cases[email_id] = _stamp(case)
            records[email_id] = submission
            self.evidence_path.write_text(json.dumps(cases, indent=2), encoding="utf-8")
            self.submission_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def save_processing(self, email_id, case, step="Retrying document verification"):
        with self._lock:
            cases = self.list_cases()
            cases[email_id] = _stamp({**case, "status": "PROCESSING", "processing_step": step})
            self.evidence_path.write_text(json.dumps(cases, indent=2), encoding="utf-8")

    def update_ai_review(self, email_id, ai_review):
        """Merge the background AI review worker's result into a case without
        touching the case's own `updated_at` -- that field drives
        reviewer-facing "last updated" sorting and shouldn't move just
        because a background job looked at the case."""
        with self._lock:
            cases = self.list_cases()
            if email_id not in cases:
                return
            cases[email_id] = {**cases[email_id], "ai_review": ai_review}
            self.evidence_path.write_text(json.dumps(cases, indent=2), encoding="utf-8")


class CloudRepository:
    """Firestore holds case state; GCS holds original attachments."""

    def __init__(self):
        from google.cloud import firestore, storage

        self.db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT") or None)
        self.collection = self.db.collection(
            os.getenv("FIRESTORE_COLLECTION", "shipping_cases")
        )
        self.bucket = storage.Client(
            project=os.getenv("GOOGLE_CLOUD_PROJECT") or None
        ).bucket(os.environ["GCS_BUCKET"])
        self.inbox = self

    def list_cases(self):
        return {
            snapshot.id: snapshot.to_dict()["case"]
            for snapshot in self.collection.stream()
            if snapshot.to_dict() and "case" in snapshot.to_dict()
        }

    def get_case(self, email_id):
        record = self.collection.document(email_id).get().to_dict()
        if not record:
            raise KeyError(email_id)
        return record["case"]

    def get_email(self, email_id):
        record = self.collection.document(email_id).get().to_dict()
        if not record:
            raise KeyError(email_id)
        return record["email"]

    def get(self, email_id):
        return self.get_email(email_id)

    def emails(self):
        return [
            snapshot.to_dict()["email"]
            for snapshot in self.collection.stream()
            if snapshot.to_dict() and "email" in snapshot.to_dict()
        ]

    def __iter__(self):
        return iter(self.emails())

    def read_bytes(self, path):
        if not path.startswith("attachments/") or ".." in Path(path).parts:
            raise ValueError("Invalid attachment path")
        return self.bucket.blob(path).download_as_bytes()

    def list_new_case_ids(self):
        return [
            snapshot.id
            for snapshot in self.collection.stream()
            if snapshot.to_dict() and snapshot.to_dict().get("uploaded")
        ]

    def _upload_attachments(self, email_id, attachments):
        paths = []
        for role, filename, content in attachments:
            suffix = Path(filename).suffix.lower()
            if suffix not in SUPPORTED_ATTACHMENT_EXTENSIONS:
                raise ValueError("Only PDF, TXT, XLSX, and DOCX documents are supported")
            safe_role = re.sub(r"[^a-z0-9_-]", "", role.lower()) or "document"
            path = f"attachments/{email_id}_{safe_role}{suffix}"
            self.bucket.blob(path).upload_from_string(content)
            paths.append(path)
        return paths

    def save_new_email(self, metadata, attachments):
        """Persist a reviewer-submitted email and its attachments -- the
        Cloud Run equivalent of LocalRepository's save_new_email, using GCS
        for bytes and Firestore for the email record instead of the local
        .local_uploads/ folder."""
        email_id = "local_" + uuid.uuid4().hex[:12]
        paths = self._upload_attachments(email_id, attachments)
        email = {
            "email_id": email_id,
            "from": str(metadata.get("from", ""))[:320],
            "to": str(metadata.get("to", ""))[:320],
            "subject": str(metadata.get("subject", ""))[:500],
            "body": str(metadata.get("body", ""))[:20_000],
            "attachments": paths,
        }
        self.collection.document(email_id).set(
            {"email": email, "uploaded": True}, merge=True
        )
        return email

    def replace_documents(self, email_id, attachments):
        """Store reviewer-selected replacement files while keeping the case identity."""
        email = dict(self.get_email(email_id))
        email["attachments"] = self._upload_attachments(email_id, attachments)
        self.collection.document(email_id).set({"email": email}, merge=True)
        return email

    def list_decisions(self):
        return {
            snapshot.id: snapshot.to_dict()["decision"]
            for snapshot in self.collection.stream()
            if snapshot.to_dict() and "decision" in snapshot.to_dict()
        }

    def save_decision(self, email_id, action, note="", corrections=None):
        from datetime import datetime, timezone

        if action not in {
            "confirm",
            "confirm_value",
            "correct",
            "mark_equivalent",
            "resolve",
            "reopen",
            "select_document",
            "complete_category",
            "reopen_category",
            "update_category_workflow",
            "update_assignment",
            "reviewer_feedback",
        }:
            raise ValueError("Unknown reviewer action")
        ref = self.collection.document(email_id)
        snapshot = ref.get()
        existing = (
            (snapshot.to_dict() or {}).get("decision", {}) if snapshot.exists else {}
        )
        updated_at = datetime.now(timezone.utc).isoformat()
        event = {
            "action": action,
            "note": note,
            "details": corrections or {},
            "at": updated_at,
        }
        decision = {
            **existing,
            "updated_at": updated_at,
            "activity": [*existing.get("activity", []), event][-100:],
        }
        if action == "update_assignment":
            decision["assignment"] = corrections or {}
        elif action == "reviewer_feedback":
            decision["feedback"] = corrections or {}
        else:
            decision.update(
                {"action": action, "note": note, "corrections": corrections or {}}
            )
        ref.set({"decision": decision}, merge=True)
        return decision

    def save_case(self, email_id, case, submission):
        self.collection.document(email_id).set(
            {"case": _stamp(case), "submission": submission}, merge=True
        )

    def save_processing(self, email_id, case, step="Retrying document verification"):
        processing = _stamp({**case, "status": "PROCESSING", "processing_step": step})
        self.collection.document(email_id).set({"case": processing}, merge=True)

    def update_ai_review(self, email_id, ai_review):
        self.collection.document(email_id).set({"case": {"ai_review": ai_review}}, merge=True)


def get_repository():
    return CloudRepository() if os.getenv("GCS_BUCKET") else LocalRepository()
