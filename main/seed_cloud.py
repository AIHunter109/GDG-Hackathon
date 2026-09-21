"""Upload a participant bundle and processed cases to Firestore and Cloud Storage."""

import argparse
import logging
import os
from pathlib import Path

from inbox import Inbox
from repository import CloudRepository

from main import build_evidence_report, process_email

LOG = logging.getLogger(__name__)


def seed(source, *, limit=None):
    if not os.getenv("GCS_BUCKET"):
        raise ValueError("Set GCS_BUCKET to an existing Cloud Storage bucket")
    inbox = Inbox(source)
    cloud = CloudRepository()
    count = 0
    uploaded = set()
    for email in inbox:
        for path in email.get("attachments", []):
            if path in uploaded:
                continue
            cloud.bucket.blob(path).upload_from_string(inbox.read_bytes(path))
            uploaded.add(path)
        submission, case = process_email(email, inbox)
        cloud.collection.document(email["email_id"]).set(
            {
                "email": email,
                "case": build_evidence_report(case),
                "submission": submission,
            }
        )
        count += 1
        if count % 25 == 0:
            LOG.info("Uploaded %s cases", count)
        if limit and count >= limit:
            break
    LOG.info("Seeded %s cases and %s attachments", count, len(uploaded))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", default=str(Path(__file__).resolve().parent.parent / "Bundle")
    )
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    seed(args.source, limit=args.limit)
