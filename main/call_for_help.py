"""Persist reviewer decisions separately from automated predictions."""

import json
from datetime import datetime, timezone
from pathlib import Path


def draft_correction_email(case, *, confirmed=False):
    """Draft a correction request only after a reviewer confirms a mismatch."""
    if not confirmed or case.get("status") != "MISMATCH" or not case.get("mismatches"):
        raise ValueError("A genuine mismatch must be confirmed before drafting")
    lines = ["Dear Team,", "", "During verification of the draft Bill of Lading against the Shipping Instruction, we found:"]
    for mismatch in case["mismatches"]:
        lines.append(f"- {mismatch['field']}: SI says {mismatch['si']['raw_value']}; draft BL says {mismatch['bl']['raw_value']}.")
    lines.extend(["", "Please review and amend the draft accordingly.", "", "Best regards,"])
    return "\n".join(lines)


class RaiseIssueToHuman:
    def __init__(self, path="review_decisions.json"):
        self.path = Path(path)

    def _read(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def showIssue(self, case=None):
        if case is None:
            return self._read()
        return {"email_id": case["email_id"], "reason": case.get("internal_reason") or case.get("review_reason"),
                "evidence": case.get("mismatches") or case.get("missing_fields") or case.get("error")}

    def resolve(self, email_id, action, *, note="", corrections=None):
        allowed = {"confirm", "confirm_value", "correct", "mark_equivalent", "resolve", "reopen",
                   "select_document", "complete_category", "reopen_category", "update_category_workflow",
                   "update_assignment", "reviewer_feedback"}
        if action not in allowed:
            raise ValueError(f"Unknown reviewer action: {action}")
        if action == "correct" and not corrections:
            raise ValueError("Corrections are required for the correct action")
        decisions = self._read()
        existing = decisions.get(email_id, {})
        updated_at = datetime.now(timezone.utc).isoformat()
        event = {"action": action, "note": note, "details": corrections or {}, "at": updated_at}
        decision = {**existing, "updated_at": updated_at,
                    "activity": [*existing.get("activity", []), event][-100:]}
        if action == "update_assignment":
            decision["assignment"] = corrections or {}
        elif action == "reviewer_feedback":
            decision["feedback"] = corrections or {}
        else:
            decision.update({"action": action, "note": note, "corrections": corrections or {}})
        decisions[email_id] = decision
        self.path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
        return decisions[email_id]
