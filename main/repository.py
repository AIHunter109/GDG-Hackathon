"""Case and attachment storage for local use or Google Cloud Run."""

import json
import os
from pathlib import Path

from inbox import Inbox
from call_for_help import RaiseIssueToHuman

ROOT = Path(__file__).resolve().parent.parent


def _read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else (default if default is not None else {})


class LocalRepository:
    def __init__(self, source=None):
        self.inbox = Inbox(source or os.getenv("INBOX_SOURCE", str(ROOT / "Bundle")))
        self.evidence_path = ROOT / "evidence.json"
        self.submission_path = ROOT / "submission.json"
        self.decisions = RaiseIssueToHuman(ROOT / "review_decisions.json")

    def list_cases(self):
        return _read_json(self.evidence_path)

    def get_case(self, email_id):
        return self.list_cases()[email_id]

    def get_email(self, email_id):
        return self.inbox.get(email_id)

    def read_bytes(self, path):
        return self.inbox.read_bytes(path)

    def list_decisions(self):
        return self.decisions.showIssue()

    def save_decision(self, email_id, action, note="", corrections=None):
        return self.decisions.resolve(email_id, action, note=note, corrections=corrections)

    def save_case(self, email_id, case, submission):
        cases = self.list_cases()
        records = _read_json(self.submission_path)
        cases[email_id] = case
        records[email_id] = submission
        self.evidence_path.write_text(json.dumps(cases, indent=2), encoding="utf-8")
        self.submission_path.write_text(json.dumps(records, indent=2), encoding="utf-8")


class CloudRepository:
    """Firestore holds case state; GCS holds original attachments."""

    def __init__(self):
        from google.cloud import firestore, storage
        self.db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT") or None)
        self.collection = self.db.collection(os.getenv("FIRESTORE_COLLECTION", "shipping_cases"))
        self.bucket = storage.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT") or None).bucket(os.environ["GCS_BUCKET"])
        self.inbox = self

    def list_cases(self):
        return {snapshot.id: snapshot.to_dict()["case"] for snapshot in self.collection.stream()
                if snapshot.to_dict() and "case" in snapshot.to_dict()}

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
        return [snapshot.to_dict()["email"] for snapshot in self.collection.stream()
                if snapshot.to_dict() and "email" in snapshot.to_dict()]

    def __iter__(self):
        return iter(self.emails())

    def read_bytes(self, path):
        if not path.startswith("attachments/") or ".." in Path(path).parts:
            raise ValueError("Invalid attachment path")
        return self.bucket.blob(path).download_as_bytes()

    def list_decisions(self):
        return {snapshot.id: snapshot.to_dict()["decision"] for snapshot in self.collection.stream()
                if snapshot.to_dict() and "decision" in snapshot.to_dict()}

    def save_decision(self, email_id, action, note="", corrections=None):
        from datetime import datetime, timezone
        if action not in {"confirm", "confirm_value", "correct", "mark_equivalent", "resolve", "select_document"}:
            raise ValueError("Unknown reviewer action")
        decision = {"action": action, "note": note, "corrections": corrections or {},
                    "updated_at": datetime.now(timezone.utc).isoformat()}
        self.collection.document(email_id).set({"decision": decision}, merge=True)
        return decision

    def save_case(self, email_id, case, submission):
        self.collection.document(email_id).set({"case": case, "submission": submission}, merge=True)


def get_repository():
    return CloudRepository() if os.getenv("GCS_BUCKET") else LocalRepository()
