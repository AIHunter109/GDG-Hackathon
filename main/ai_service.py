"""AI understanding fallback. Verification and arithmetic stay in application
code -- every method here either returns a validated result or None; the
caller always falls back to deterministic behavior on None.

Two providers are supported, selected by AI_PROVIDER ("gemini" or
"gonkarouter"), or auto-detected from whichever API key is set (GONKAROUTER
_API_KEY takes priority if both are present and AI_PROVIDER isn't set
explicitly). Every method below this point only ever calls self._json(...)
and self.enabled -- neither knows or cares which provider is behind it."""

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

# Ask for bare JSON regardless of provider. Gemini already enforces this via
# generationConfig.responseMimeType, so this is redundant-but-harmless there;
# GonkaRouter's /v1/messages has no equivalent structured-output mode, so
# this instruction is load-bearing for that provider.
_JSON_ONLY_SUFFIX = (
    "\n\nRespond with ONLY a single JSON object and nothing else -- "
    "no markdown code fences, no explanatory text before or after."
)


def _read_error_body(exc, limit=500):
    """Best-effort read of an HTTPError's response body -- the exception's
    own str() is just "HTTP Error 403: Forbidden", but the body usually
    contains the actual reason (invalid key, no credits, wrong model, etc.)."""
    try:
        return exc.read().decode("utf-8", errors="replace")[:limit]
    except Exception:
        return "(could not read response body)"


def _extract_json(text):
    """Parse a JSON object out of a model's plain-text reply, tolerating a
    markdown code fence, a leading <think>...</think> reasoning block (some
    models, e.g. MiniMax via GonkaRouter, emit one before the real answer),
    or stray prose around the object -- GonkaRouter/Anthropic-shaped
    responses have no enforced JSON-only mode, unlike Gemini's
    responseMimeType."""
    text = text.strip()
    # Drop a complete <think>...</think> block if present. If the closing
    # tag never arrives (the model got cut off mid-thought, e.g. it ran out
    # of max_tokens before finishing), there is no usable answer at all --
    # that's a real failure, not something to paper over.
    if text.startswith("<think>"):
        end_think = text.find("</think>")
        if end_think == -1:
            return None
        text = text[end_think + len("</think>") :].strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            pass
    return None


class AIService:
    def __init__(self, api_key=None, model=None, provider=None):
        self.provider = (provider or os.getenv("AI_PROVIDER") or "").strip().lower()
        if not self.provider:
            self.provider = "gonkarouter" if os.getenv("GONKAROUTER_API_KEY") else "gemini"

        if self.provider == "gonkarouter":
            self.api_key = (
                api_key if api_key is not None else os.getenv("GONKAROUTER_API_KEY", "")
            )
            # deepseek-ai/DeepSeek-V4-Flash-0731 is a standard (non-reasoning)
            # model on GonkaRouter's catalog -- MiniMax-M2.7 is a "thinking"
            # model that emits a <think>...</think> block before its answer,
            # which made it slow and prone to truncation/timeouts for the
            # short structured-JSON tasks this app needs. Same price either
            # way; override with GONKAROUTER_MODEL if you want the reasoning
            # model anyway (e.g. for a task that benefits from it).
            self.model = model or os.getenv(
                "GONKAROUTER_MODEL", "deepseek-ai/DeepSeek-V4-Flash-0731"
            )
        else:
            self.api_key = (
                api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
            )
            # gemini-2.5-flash is a non-preview, generally-available model --
            # verified working live. Newer preview-tier models (e.g. the
            # gemini-3.x line) are available on some keys but returned HTTP
            # 503 (temporarily overloaded) during testing; override with
            # GEMINI_MODEL if you specifically want one of those.
            self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    @property
    def enabled(self):
        return bool(self.api_key)

    def _json(self, instruction, *, media=None):
        if not self.enabled:
            return None
        instruction = instruction + _JSON_ONLY_SUFFIX
        if self.provider == "gonkarouter":
            return self._json_gonkarouter(instruction, media=media)
        return self._json_gemini(instruction, media=media)

    def _json_gemini(self, instruction, *, media=None):
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
        except urllib.error.HTTPError as exc:
            LOG.warning("AI fallback failed: %s -- body: %s", exc, _read_error_body(exc))
            return None
        except (OSError, KeyError, IndexError, ValueError) as exc:
            LOG.warning("AI fallback failed: %s", exc)
            return None

    def _json_gonkarouter(self, instruction, *, media=None):
        content = [{"type": "text", "text": instruction}]
        if media:
            mime_type, data = media
            block_type = "image" if mime_type.startswith("image/") else "document"
            content.append(
                {
                    "type": block_type,
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                }
            )
        request = urllib.request.Request(
            "https://api.gonkarouter.io/v1/messages",
            data=json.dumps(
                {
                    "model": self.model,
                    # Generous headroom: MiniMax-M2.7 is a reasoning model
                    # that emits a <think>...</think> block before its
                    # actual answer, and 2048 tokens wasn't enough room for
                    # both the reasoning and the final JSON (confirmed live
                    # -- the response was cut off mid-thought).
                    "max_tokens": 8192,
                    "messages": [{"role": "user", "content": content}],
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                # Python's default User-Agent ("Python-urllib/3.x") is a
                # common trigger for Cloudflare bot-protection blocks
                # (surfaced as "error code: 1010" -- confirmed live against
                # this exact provider). A normal-looking UA avoids that.
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            text = "".join(
                block.get("text", "")
                for block in payload["content"]
                if block.get("type") == "text"
            )
            result = _extract_json(text)
            if result is None:
                raise ValueError(f"could not parse JSON from response: {text[:200]!r}")
            return result
        except urllib.error.HTTPError as exc:
            LOG.warning("AI fallback failed: %s -- body: %s", exc, _read_error_body(exc))
            return None
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
        if not 0 <= confidence <= 1:
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
        if not 0 <= confidence <= 1:
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
            "Use only the supplied evidence. Do not infer missing values or decide "
            "whether the SI and BL match. Return a JSON object with exactly these "
            'two keys and no others: "explanation" (a plain-language summary of '
            'the evidence) and "action" (one recommended next step). '
            "Evidence: " + json.dumps(evidence, default=str)
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

    def review_case(self, case):
            """Give a reviewer a short, evidence-grounded opinion on a NEEDS_REVIEW
            case for the background dashboard worker. Never changes the
            verification decision -- same guardrail as explain_review, plus an
            anti-hallucination check on the model's own cited proof: it must be a
            verbatim quote from the evidence sent, not a paraphrase."""
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
            evidence_text = json.dumps(evidence, default=str)
            result = self._json(
                "Give a shipping operations reviewer a short opinion on why this case "
                "needs human review. Use only the supplied evidence. Do not infer "
                "missing values or decide whether the SI and BL match -- that stays "
                "a human decision. Return a JSON object with exactly these three "
                'keys and no others: "assessment" (plain-language opinion on what '
                'is actually going on), "proof" (an exact, verbatim quote copied '
                "character-for-character from the evidence below -- do not "
                'paraphrase or summarize it), and "recommended_action" (one '
                "concrete next step for the reviewer). Evidence: " + evidence_text
            )
            if not isinstance(result, dict):
                return None
            assessment, proof, action = (
                result.get(key) for key in ("assessment", "proof", "recommended_action")
            )
            if not all(
                isinstance(value, str) and 5 <= len(value.strip()) <= 300
                for value in (assessment, proof, action)
            ):
                return None
            if proof.strip() not in evidence_text:
                return None
            return {
                "assessment": assessment.strip(),
                "proof": proof.strip(),
                "recommended_action": action.strip(),
            }

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
            "Write a short, courteous email requesting a draft Bill of Lading "
            "correction. Use generic wording only -- do not include shipment "
            "facts, names, numbers, dates, deadlines, or recipients. Return a "
            'JSON object with exactly these two keys and no others: "opening" '
            '(one opening sentence) and "closing" (one closing sentence).'
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
