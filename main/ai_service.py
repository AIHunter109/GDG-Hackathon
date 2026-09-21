"""Gemini understanding fallback. Verification and arithmetic stay in application code."""

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request

LOG = logging.getLogger(__name__)
CATEGORIES = {"BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"}
FIELDS = {
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
}


class AIService:
    def __init__(self, api_key=None, model=None):
        self.api_key = (
            api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        )
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    @property
    def enabled(self):
        return bool(self.api_key)

    def _json(self, instruction, *, media=None):
        if not self.enabled:
            return None
        parts = [{"text": instruction}]
        if media:
            mime_type, content = media
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(content).decode("ascii"),
                    }
                }
            )
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            data=json.dumps(
                {
                    "contents": [{"parts": parts}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0,
                    },
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            output = "".join(
                part.get("text", "")
                for part in payload["candidates"][0]["content"]["parts"]
            )
            return json.loads(output)
        except (OSError, KeyError, IndexError, ValueError) as exc:
            LOG.warning("AI fallback failed: %s", exc)
            return None

    def classify_email(self, email):
        prompt = (
            "Classify this shipping operations email. Return JSON with category from "
            "BL_COMPARISON, SI_REQUEST, INVOICE_QUERY, GENERAL, SPAM; confidence 0 to 1; "
            "and a short evidence-based reason. Only BL_COMPARISON if asking to verify a draft BL "
            "against an SI. Email: "
            + json.dumps(
                {key: email.get(key) for key in ("subject", "body", "attachments")}
            )
        )
        result = self._json(prompt)
        if not isinstance(result, dict) or result.get("category") not in CATEGORIES:
            return None
        try:
            confidence = float(result.get("confidence", 0))
        except (TypeError, ValueError):
            return None
        if not 0 <= confidence <= 1:
            return None
        return {
            "category": result["category"],
            "confidence": confidence,
            "reason": str(result.get("reason", "AI intent analysis"))[:240],
            "method": "gemini",
        }

    def identify_documents(self, email, documents):
        summary = [
            {"path": d["path"], "text_start": d["text"][:1200]} for d in documents
        ]
        prompt = (
            "Identify the reference shipping instruction and draft bill of lading. "
            "A Bill of Lading Instruction is an SI. Return JSON with si_path, bl_path, "
            "confidence 0 to 1, and reason. Use null if uncertain. "
            + json.dumps(
                {
                    "email": {
                        "subject": email.get("subject"),
                        "body": email.get("body", "")[:1500],
                    },
                    "documents": summary,
                }
            )
        )
        result = self._json(prompt)
        paths = {d["path"] for d in documents}
        if (
            not isinstance(result, dict)
            or result.get("si_path") not in paths
            or result.get("bl_path") not in paths
        ):
            return None
        if result["si_path"] == result["bl_path"]:
            return None
        try:
            confidence = float(result.get("confidence", 0))
        except (TypeError, ValueError):
            return None
        return {
            "si": next(d for d in documents if d["path"] == result["si_path"]),
            "bl": next(d for d in documents if d["path"] == result["bl_path"]),
            "confidence": confidence,
            "reason": str(result.get("reason", ""))[:240],
        }

    def extract_missing_fields(self, document, missing):
        requested = sorted(FIELDS & set(missing))
        if not requested:
            return {}
        prompt = (
            "Map unfamiliar labels to ONLY these shipping fields: "
            + ", ".join(requested)
            + ". Return JSON object keyed by canonical field. Each value must contain raw_value, "
            "exact source_text copied from the document, and confidence 0 to 1. "
            "Omit absent or uncertain fields. Do not infer a missing value. Document:\n"
            + document["text"][:18000]
        )
        result = self._json(prompt)
        return result if isinstance(result, dict) else {}

    def transcribe_pdf(self, data):
        prompt = (
            "Transcribe the shipping document faithfully as plain text. Preserve field labels "
            "and values. Return JSON with text and confidence 0 to 1. Do not infer illegible values."
        )
        result = self._json(prompt, media=("application/pdf", data))
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            return None
        try:
            confidence = float(result.get("confidence", 0))
        except (TypeError, ValueError):
            return None
        return {"text": result["text"], "confidence": confidence}

    def explain_review(self, case):
        """Summarize existing review evidence without changing the verification decision."""
        if not self.enabled or case.get("status") != "NEEDS_REVIEW":
            return None
        evidence = {
            key: case.get(key)
            for key in (
                "internal_reason",
                "missing_fields",
                "uncertain_fields",
                "validation",
                "error",
            )
        }
        affected = set(case.get("missing_fields", [])) | set(
            case.get("uncertain_fields", [])
        )
        evidence["fields"] = {
            field: {
                role: case.get(role + "_fields", {}).get(field) for role in ("si", "bl")
            }
            for field in affected & FIELDS
        }
        result = self._json(
            "Explain this shipping document review in plain business language. "
            "Use only the supplied evidence. Return JSON with a brief explanation "
            "and one recommended action. Do not infer missing values or decide whether "
            "the SI and BL match. Evidence: " + json.dumps(evidence, default=str)
        )
        if not isinstance(result, dict):
            return None
        explanation, action = result.get("explanation"), result.get("action")
        if not all(
            isinstance(value, str) and 5 <= len(value.strip()) <= 240
            for value in (explanation, action)
        ):
            return None
        return {"explanation": explanation.strip(), "action": action.strip()}

    def draft_correction_email(self, case):
        """Let AI word the request while application code inserts confirmed facts."""
        if (
            not self.enabled
            or case.get("status") != "MISMATCH"
            or not case.get("mismatches")
        ):
            return None
        facts = [
            {
                "field": item["field"],
                "si_value": str(item["si"]["raw_value"]),
                "bl_value": str(item["bl"]["raw_value"]),
            }
            for item in case["mismatches"]
        ]
        result = self._json(
            "Return JSON with opening and closing sentences for a short, courteous "
            "email requesting a draft Bill of Lading correction. Use generic wording only. "
            "Do not include shipment facts, names, numbers, dates, deadlines, or recipients."
        )
        if not isinstance(result, dict):
            return None
        opening, closing = result.get("opening"), result.get("closing")
        if not all(
            isinstance(value, str)
            and 10 <= len(value.strip()) <= 180
            and not re.search(r"\d|@|https?://|\n", value, re.I)
            for value in (opening, closing)
        ):
            return None
        lines = ["Dear Team,", "", opening.strip(), ""]
        for fact in facts:
            lines.append(
                f"- {fact['field']}: SI says {fact['si_value']}; draft BL says {fact['bl_value']}."
            )
        lines.extend(["", closing.strip(), "", "Best regards,"])
        return "\n".join(lines)
