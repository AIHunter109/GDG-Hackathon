"""Apply explicit reviewer corrections to a case and competition result."""

from copy import deepcopy

from comparator import FIELDS, compare_documents
from extractor import normalize_value


def submission_for_case(case):
    status = case["status"]
    return {"category": case["category"], "status": status,
            "review_reason": case.get("review_reason") if status == "NEEDS_REVIEW" else None,
            "has_defect": status == "MISMATCH",
            "defect_fields": [m["field"] for m in case.get("mismatches", [])] if status == "MISMATCH" else []}


def apply_correction(case, role, field, value):
    if role not in {"si", "bl"} or field not in FIELDS or not isinstance(value, str) or not value.strip():
        raise ValueError("Choose SI or BL, an official field, and a nonempty value")
    normalized = normalize_value(field, value)
    if normalized is None:
        raise ValueError("The corrected value is still missing or ambiguous")
    result = deepcopy(case)
    result.setdefault(role + "_fields", {})[field] = {
        "raw_value": value.strip(), "normalized_value": normalized,
        "source": "reviewer correction", "source_text": value.strip(),
        "confidence": 1, "method": "reviewer"}
    _recompare(result)
    return result, submission_for_case(result)


def mark_equivalent(case, field):
    if field not in FIELDS:
        raise ValueError("Choose an official field")
    result = deepcopy(case)
    si = result.get("si_fields", {}).get(field)
    bl = result.get("bl_fields", {}).get(field)
    if not si or not bl or si.get("normalized_value") is None or bl.get("normalized_value") is None:
        raise ValueError("Both source values must be present before marking them equivalent")
    bl["normalized_value"] = si["normalized_value"]
    bl["method"] = "reviewer_equivalence"
    bl["confidence"] = 1
    _recompare(result)
    return result, submission_for_case(result)


def _recompare(case):
    comparison = compare_documents(case.get("si_fields", {}), case.get("bl_fields", {}))
    case["mismatches"] = comparison["mismatches"]
    case["missing_fields"] = comparison["missing_fields"]
    checks = case.get("validation", {})
    consistency = checks.get("consistency", {})
    if checks and (not checks.get("pairing", {}).get("valid", True) or
                   any(not item.get("valid", True) for item in consistency.values())):
        case.update(status="NEEDS_REVIEW", review_reason="wrong_doc_type")
        return
    if comparison["missing_fields"]:
        case.update(status="NEEDS_REVIEW", review_reason="missing_value", internal_reason="MISSING_REQUIRED_FIELD")
    else:
        case.update(status="MISMATCH" if comparison["mismatches"] else "OK", review_reason=None,
                    internal_reason=None)
