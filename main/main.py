"""Run document verification over a Bundle.loader.Inbox."""

import argparse
import json
import logging
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_service import AIService
from classify import classify_email
from comparator import compare_documents
from extractor import (
    extract_fields,
    identify_documents,
    read_document,
    validate_document_consistency,
    validate_document_pair,
)
from inbox import Inbox

LOG = logging.getLogger(__name__)


def is_actionable_comparison_request(email):
    """Return true when the message explicitly asks to compare both SI and BL.

    Thread messages that ask someone to send a draft BL are correctly classified
    as BL-related, but they do not yet contain a document pair to verify. They
    should not create a human verification task until both documents are expected.
    """
    message = " ".join((email.get("subject", ""), email.get("body", ""))).lower()
    has_si = bool(re.search(r"shipping instruction|\bsi\b", message))
    has_bl = bool(re.search(r"draft\s*(?:b/?l|bill of lading)|\bb/?l\b", message))
    compare = bool(re.search(r"compare|verify|check\s+(?:the\s+)?si|si\s+and\s+(?:the\s+)?draft|confirm\s+(?:the\s+)?(?:si|documents?)", message))
    return has_si and has_bl and compare


def _submission_record(category, status="OK", fields=(), reason=None):
    return {
        "category": category,
        "status": status,
        "review_reason": reason,
        "has_defect": status == "MISMATCH",
        "defect_fields": list(fields),
    }


def _review_record(category, case, reason):
    LOG.info(
        "Human review required for %s: %s",
        case["email_id"],
        case.get("internal_reason") or reason,
    )
    return _submission_record(category, "NEEDS_REVIEW", reason=reason), case


def build_evidence_report(case):
    """Compact report for a reviewer; independent of the competition schema."""
    return {
        key: case.get(key)
        for key in (
            "email_id",
            "email",
            "category",
            "classification",
            "status",
            "review_reason",
            "internal_reason",
            "documents",
            "other_attachments",
            "document_identification",
            "si_fields",
            "bl_fields",
            "mismatches",
            "missing_fields",
            "uncertain_fields",
            "validation",
            "error",
            "processing_step",
            "failed_attachment",
            "updated_at",
        )
        if key in case
    }


def process_email(email, inbox, role_override=None, *, force_vision=False):
    eid = email["email_id"]
    ai = AIService()
    classification = classify_email(email)
    if ai.enabled and classification["confidence"] < 0.8:
        suggestion = ai.classify_email(email)
        if suggestion and suggestion["confidence"] >= 0.85:
            classification = suggestion
            LOG.info("AI classified email %s", eid)
    category = classification["category"]
    case = {
        "email_id": eid,
        "category": category,
        "classification": classification,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "email": {
            key: email.get(key)
            for key in ("from", "to", "subject", "body", "attachments")
        },
    }
    if category != "BL_COMPARISON":
        case["status"] = "OK"
        return _submission_record(category), case
    paths = email.get("attachments", [])
    if not paths:
<<<<<<< HEAD
        case.update(
            status="NEEDS_REVIEW",
            review_reason="missing_attachment",
            internal_reason="MISSING_SI_AND_BL",
        )
=======
        if not is_actionable_comparison_request(email):
            case.update(status="NOT_APPLICABLE", internal_reason="AWAITING_COMPARISON_DOCUMENTS")
            return _submission_record(category), case
        case.update(status="NEEDS_REVIEW", review_reason="missing_attachment",
                    internal_reason="MISSING_SI_AND_BL")
>>>>>>> 1b18a5bcebf7f3cd550e45c921df91f261a382e6
        return _review_record(category, case, "missing_attachment")
    processing_step = "Reading attachments"
    try:
        documents = []
        for path in paths:
            try:
                documents.append(
                    read_document(path, inbox, ai, force_vision=force_vision)
                )
            except Exception:
                case["failed_attachment"] = path
                raise
        processing_step = "Identifying documents"
        pair = identify_documents(email, documents)
        clearly_other = any(
            re.match(
                r"\s*(?:commercial invoice|packing list|certificate of origin)\b",
                d["text"],
                re.I,
            )
            for d in documents
        )
        if pair["reason"] == "wrong_doc_type" and ai.enabled and not clearly_other:
            suggestion = ai.identify_documents(email, documents)
            if suggestion and suggestion["confidence"] >= 0.9:
                LOG.info("AI identified document roles for %s", eid)
                pair = {
                    "si": suggestion["si"],
                    "bl": suggestion["bl"],
                    "reason": None,
                    "other": [
                        d
                        for d in documents
                        if d not in (suggestion["si"], suggestion["bl"])
                    ],
                    "method": "gemini_role_detection",
                    "confidence": suggestion["confidence"],
                }
        if role_override:
            paths_by_role = {role: role_override.get(role) for role in ("si", "bl")}
            by_path = {d["path"]: d for d in documents}
            for role, path in paths_by_role.items():
                if path is not None:
                    if path not in by_path:
                        raise ValueError(
                            "Selected document is not attached to this email"
                        )
                    pair[role] = by_path[path]
            if pair.get("si") and pair.get("bl") and pair["si"] is not pair["bl"]:
                pair.update(
                    reason=None,
                    method="reviewer_document_selection",
                    confidence=1,
                    other=[
                        d
                        for d in documents
                        if d is not pair["si"] and d is not pair["bl"]
                    ],
                )
        case["documents"] = {
            role: pair[role]["path"] if pair.get(role) else None
            for role in ("si", "bl")
        }
        case["other_attachments"] = [d["path"] for d in pair.get("other", [])]
        case["document_identification"] = {
            "method": pair.get("method", "rules"),
            "confidence": pair.get("confidence", 1 if not pair["reason"] else 0),
        }
        if pair["reason"]:
            case.update(
                status="NEEDS_REVIEW",
                review_reason=pair["reason"],
                internal_reason="AMBIGUOUS_DOCUMENT_ROLE"
                if pair["reason"] == "wrong_doc_type"
                else "MISSING_SI_OR_BL",
            )
            return _review_record(category, case, pair["reason"])
        si, bl = pair["si"], pair["bl"]
        processing_step = "Extracting shipment fields"
        si_fields, bl_fields = extract_fields(si, ai), extract_fields(bl, ai)
        case.update(si_fields=si_fields, bl_fields=bl_fields)
        processing_step = "Validating documents"
        consistency = {
            "si": validate_document_consistency(si, si_fields),
            "bl": validate_document_consistency(bl, bl_fields),
        }
        pairing = validate_document_pair(si, bl)
        case["validation"] = {"consistency": consistency, "pairing": pairing}
        if not pairing["valid"] or not all(c["valid"] for c in consistency.values()):
            case.update(
                status="NEEDS_REVIEW",
                review_reason="wrong_doc_type",
                internal_reason="POSSIBLE_WRONG_DOCUMENT_PAIR"
                if not pairing["valid"]
                else "DOCUMENT_INTERNAL_INCONSISTENCY",
            )
            return _review_record(category, case, "wrong_doc_type")
        processing_step = "Comparing SI and draft BL"
        comparison = compare_documents(si_fields, bl_fields)
        case["mismatches"] = comparison["mismatches"]
        if comparison["missing_fields"]:
            case.update(
                status="NEEDS_REVIEW",
                review_reason="missing_value",
                missing_fields=comparison["missing_fields"],
                internal_reason="MISSING_REQUIRED_FIELD",
            )
            return _review_record(category, case, "missing_value")
        uncertain = [
            field
            for field in si_fields
            if si_fields[field].get("confidence", 0) < 0.9
            or bl_fields[field].get("confidence", 0) < 0.9
        ]
        if uncertain:
            case.update(
                status="NEEDS_REVIEW",
                review_reason="missing_value",
                internal_reason="LOW_EXTRACTION_CONFIDENCE",
                uncertain_fields=uncertain,
            )
            return _review_record(category, case, "missing_value")
        fields = [m["field"] for m in comparison["mismatches"]]
        status = "MISMATCH" if fields else "OK"
        case["status"] = status
        return _submission_record(category, status, fields), case
    except (ValueError, UnicodeError, zipfile.BadZipFile) as exc:
        LOG.warning("Unreadable document in %s: %s", eid, exc)
        case.update(
            status="NEEDS_REVIEW",
            review_reason="unreadable",
            error=str(exc),
            internal_reason="UNREADABLE_DOCUMENT",
        )
        return _review_record(category, case, "unreadable")
    except Exception as exc:
        LOG.exception("Processing failed for %s during %s", eid, processing_step)
        case.update(
            status="PROCESSING_FAILED",
            review_reason="unreadable",
            error=str(exc),
            processing_step=processing_step,
            internal_reason="TECHNICAL_FAILURE",
        )
        return _submission_record(category, "NEEDS_REVIEW", reason="unreadable"), case


def generate_submission(inbox, *, evidence_path=None):
    submission, evidence = {}, {}
    for email in inbox:
        record, case = process_email(email, inbox)
        submission[email["email_id"]] = record
        evidence[email["email_id"]] = build_evidence_report(case)
    if evidence_path:
        Path(evidence_path).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return submission


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(ROOT / "Bundle"))
    parser.add_argument("--output", default=str(ROOT / "submission.json"))
    parser.add_argument("--evidence", default=str(ROOT / "evidence.json"))
    args = parser.parse_args()
    inbox = Inbox(args.source)
    submission = generate_submission(inbox, evidence_path=args.evidence)
    expected = set(inbox.sample_submission())
    if set(submission) != expected:
        raise ValueError(
            f"Submission IDs differ from sample: {len(submission)} vs {len(expected)}"
        )
    Path(args.output).write_text(json.dumps(submission, indent=2), encoding="utf-8")
    from collections import Counter

    print(f"Wrote {len(submission)} records to {args.output}")
    print("Categories:", dict(Counter(r["category"] for r in submission.values())))
    print("Statuses:", dict(Counter(r["status"] for r in submission.values())))


if __name__ == "__main__":
    run()
