"""Gemini understanding fallback. Verification and arithmetic stay in application code."""

import base64
import json
import logging
import os
import urllib.error
import urllib.request

LOG = logging.getLogger(__name__)
CATEGORIES = {"BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"}
FIELDS = {"shipper", "consignee", "notify_party", "port_of_loading",
          "port_of_discharge", "container_count", "gross_weight_kg"}


class AIService:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
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
            parts.append({"inline_data": {"mime_type": mime_type,
                                          "data": base64.b64encode(content).decode("ascii")}})
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            data=json.dumps({"contents": [{"parts": parts}],
                             "generationConfig": {"responseMimeType": "application/json",
                                                  "temperature": 0}}).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            output = "".join(part.get("text", "") for part in
                             payload["candidates"][0]["content"]["parts"])
            return json.loads(output)
        except (OSError, KeyError, IndexError, ValueError) as exc:
            LOG.warning("AI fallback failed: %s", exc)
            return None

    def classify_email(self, email):
        prompt = ("Classify this shipping operations email. Return JSON with category from "
                  "BL_COMPARISON, SI_REQUEST, INVOICE_QUERY, GENERAL, SPAM; confidence 0 to 1; "
                  "and a short evidence-based reason. Only BL_COMPARISON if asking to verify a draft BL "
                  "against an SI. Email: " + json.dumps({key: email.get(key) for key in
                                                 ("subject", "body", "attachments")}))
        result = self._json(prompt)
        if not isinstance(result, dict) or result.get("category") not in CATEGORIES:
            return None
        try:
            confidence = float(result.get("confidence", 0))
        except (TypeError, ValueError):
            return None
        if not 0 <= confidence <= 1:
            return None
        return {"category": result["category"], "confidence": confidence,
                "reason": str(result.get("reason", "AI intent analysis"))[:240], "method": "gemini"}

    def identify_documents(self, email, documents):
        summary = [{"path": d["path"], "text_start": d["text"][:1200]} for d in documents]
        prompt = ("Identify the reference shipping instruction and draft bill of lading. "
                  "A Bill of Lading Instruction is an SI. Return JSON with si_path, bl_path, "
                  "confidence 0 to 1, and reason. Use null if uncertain. "
                  + json.dumps({"email": {"subject": email.get("subject"), "body": email.get("body", "")[:1500]},
                                "documents": summary}))
        result = self._json(prompt)
        paths = {d["path"] for d in documents}
        if not isinstance(result, dict) or result.get("si_path") not in paths or result.get("bl_path") not in paths:
            return None
        if result["si_path"] == result["bl_path"]:
            return None
        try:
            confidence = float(result.get("confidence", 0))
        except (TypeError, ValueError):
            return None
        return {"si": next(d for d in documents if d["path"] == result["si_path"]),
                "bl": next(d for d in documents if d["path"] == result["bl_path"]),
                "confidence": confidence, "reason": str(result.get("reason", ""))[:240]}

    def extract_missing_fields(self, document, missing):
        requested = sorted(FIELDS & set(missing))
        if not requested:
            return {}
        prompt = ("Map unfamiliar labels to ONLY these shipping fields: " + ", ".join(requested) +
                  ". Return JSON object keyed by canonical field. Each value must contain raw_value, "
                  "exact source_text copied from the document, and confidence 0 to 1. "
                  "Omit absent or uncertain fields. Do not infer a missing value. Document:\n" + document["text"][:18000])
        result = self._json(prompt)
        return result if isinstance(result, dict) else {}

    def transcribe_pdf(self, data):
        prompt = ("Transcribe the shipping document faithfully as plain text. Preserve field labels "
                  "and values. Return JSON with text and confidence 0 to 1. Do not infer illegible values.")
        result = self._json(prompt, media=("application/pdf", data))
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            return None
        try:
            confidence = float(result.get("confidence", 0))
        except (TypeError, ValueError):
            return None
        return {"text": result["text"], "confidence": confidence}
