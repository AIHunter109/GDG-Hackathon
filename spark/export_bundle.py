"""Create a private Firestore seed from the 520 local participant emails.

The output belongs outside Hosting's public directory. No document is sent to AI.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "main"))

from extractor import read_document  # noqa: E402
from inbox import Inbox  # noqa: E402
from main import build_evidence_report, process_email  # noqa: E402


def export(source: Path, destination: Path) -> int:
    os.environ.pop("GEMINI_API_KEY", None)
    inbox = Inbox(source)
    records = {}
    for email in inbox:
        _, case = process_email(email, inbox)
        evidence = build_evidence_report(case)
        document_texts = {}
        for path in email.get("attachments", []):
            try:
                text = read_document(path, inbox)["text"]
                if text:
                    document_texts[path] = text
            except (ValueError, UnicodeError, OSError):
                pass
        evidence["document_texts"] = document_texts
        records[email["email_id"]] = evidence
    if len(records) != 520:
        raise ValueError(f"Expected 520 emails, found {len(records)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "Bundle")
    parser.add_argument("--output", type=Path, default=ROOT / "spark" / "private" / "bundle-cases.json")
    args = parser.parse_args()
    print(f"Prepared {export(args.source, args.output)} private email cases at {args.output}")
