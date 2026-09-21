"""Explain which cases enter human review and, when supplied, false reviews."""

import argparse
import json
from collections import Counter
from pathlib import Path


def _document_type(case):
    attachments = case.get("email", {}).get("attachments", [])
    if not attachments:
        return "no_attachment"
    suffixes = {
        Path(path).suffix.lower().lstrip(".") or "unknown" for path in attachments
    }
    return "+".join(sorted(suffixes))


def build_review_diagnostics(submission, evidence, ground_truth=None):
    reviewed = {
        email_id
        for email_id, record in submission.items()
        if record.get("status") == "NEEDS_REVIEW"
    }
    expected = (
        {
            email_id
            for email_id, record in ground_truth.items()
            if record.get("status") == "NEEDS_REVIEW"
        }
        if ground_truth is not None
        else None
    )
    selected = reviewed - expected if expected is not None else reviewed

    by_reason = Counter()
    by_internal_reason = Counter()
    by_document_type = Counter()
    by_field = Counter()
    for email_id in selected:
        record = submission[email_id]
        case = evidence.get(email_id, {})
        by_reason[record.get("review_reason") or "unspecified"] += 1
        by_internal_reason[case.get("internal_reason") or "unspecified"] += 1
        by_document_type[_document_type(case)] += 1
        for field in set(case.get("missing_fields", [])) | set(
            case.get("uncertain_fields", [])
        ):
            by_field[field] += 1

    report = {
        "predicted_reviews": len(reviewed),
        "reviewed_cases_reported": len(selected),
        "report_scope": "false_reviews" if expected is not None else "all_reviews",
        "by_review_reason": dict(by_reason.most_common()),
        "by_internal_reason": dict(by_internal_reason.most_common()),
        "by_document_type": dict(by_document_type.most_common()),
        "by_field": dict(by_field.most_common()),
    }
    if expected is not None:
        report.update(
            expected_reviews=len(expected),
            false_reviews=len(reviewed - expected),
            missed_reviews=len(expected - reviewed),
        )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", type=Path, default=Path("submission.json"))
    parser.add_argument("--evidence", type=Path, default=Path("evidence.json"))
    parser.add_argument("--ground-truth", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    submission = json.loads(args.submission.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    truth = (
        json.loads(args.ground_truth.read_text(encoding="utf-8"))
        if args.ground_truth
        else None
    )
    report = build_review_diagnostics(submission, evidence, truth)
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
