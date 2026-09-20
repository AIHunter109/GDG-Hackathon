"""Read shipping documents and extract evidence-backed fields."""

import io
import logging
import re
import unicodedata
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

from comparator import FIELDS

LABELS = {
    "shipper": (r"shipper(?:\s*/\s*exporter)?(?:\s*\(principal or seller\))?",),
    "consignee": (r"consignee(?:\s*\(non-negotiable\))?", r"to the order of"),
    "notify_party": (r"notify(?:\s+party)?(?:\s*/\s*intermediate consignee)?",),
    "port_of_loading": (r"port of loading(?:\s*\(pol\))?", r"pol", r"load port"),
    "port_of_discharge": (
        r"port of discharge(?:\s*\(pod\))?",
        r"pod",
        r"discharge port",
    ),
    "container_count": (
        r"(?:total\s+)?container count",
        r"(?:total\s+)?containers?",
        r"no\.? of containers(?: or packages)?",
    ),
    "gross_weight_kg": (r"(?:total\s+)?gross\s*(?:weight|wt).{0,18}",),
}


def _xlsx_text(data):
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    lines = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(ns + "t")) for si in root]
        for name in sorted(
            n
            for n in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
        ):
            root = ET.fromstring(archive.read(name))
            for row in root.iter(ns + "row"):
                cells = []
                for cell in row.findall(ns + "c"):
                    typ = cell.get("t")
                    if typ == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(ns + "t"))
                    else:
                        value = cell.findtext(ns + "v") or ""
                        if typ == "s" and value:
                            value = shared[int(value)]
                    if value.strip():
                        cells.append(value.strip())
                if cells:
                    lines.append(
                        ": ".join(cells) if len(cells) == 2 else " | ".join(cells)
                    )
    return "\n".join(lines)


def _docx_text(data):
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    lines = []
    for para in root.iter(ns + "p"):
        content = []
        for node in para.iter():
            if node.tag == ns + "t":
                content.append(node.text or "")
            elif node.tag in (ns + "br", ns + "cr"):
                content.append("\n")
            elif node.tag == ns + "tab":
                content.append("\t")
        lines.append("".join(content))
    return "\n".join(lines)


def read_document(document, inbox=None, ai_service=None, *, force_vision=False):
    """Return document text and metadata. `document` is an attachment path or dict."""
    path = document["path"] if isinstance(document, dict) else str(document)
    data = inbox.read_bytes(path) if inbox else Path(path).read_bytes()
    suffix = Path(path).suffix.lower()
    if suffix == ".txt":
        text = data.decode("utf-8-sig", errors="replace")
        if text and text.count(chr(0xFFFD)) / len(text) > 0.05:
            raise ValueError("Text attachment has invalid/corrupted encoding")
    elif suffix == ".xlsx":
        text = _xlsx_text(data)
    elif suffix == ".docx":
        text = _docx_text(data)
    elif suffix == ".pdf":
        try:
            import logging

            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF reader dependency pypdf is unavailable") from exc
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        try:
            text = "\n".join(
                page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages
            )
        except Exception:
            text = ""
        if (
            (force_vision or not text.strip())
            and ai_service
            and ai_service.enabled
            and len(data) <= 15_000_000
        ):
            transcription = ai_service.transcribe_pdf(data)
            if transcription and transcription["text"].strip():
                logging.getLogger(__name__).info("AI vision extracted PDF %s", path)
                return {
                    "path": path,
                    "text": transcription["text"],
                    "file_type": suffix,
                    "method": "gemini_pdf_vision",
                    "confidence": min(transcription["confidence"], 0.8),
                }
    else:
        raise ValueError(f"Unsupported file type: {suffix}")
    if not text.strip():
        raise ValueError("No extractable text; scanned document needs OCR")
    return {"path": path, "text": text, "file_type": suffix}


def _role_score(document, role):
    name = Path(document["path"]).stem.lower()
    head = document["text"][:500].lower()
    score = 0
    if role == "si":
        score += 3 if re.search(r"(?:^|[_ -])si(?:$|[_ -])|instruction", name) else 0
        score += (
            4
            if re.search(
                r"shipping instruction|b/?l instruction|bill of lading instruction",
                head,
            )
            else 0
        )
        score -= 4 if "bill of lading (draft)" in head else 0
    else:
        score += 3 if re.search(r"(?:^|[_ -])bl(?:$|[_ -])|draft", name) else 0
        score += (
            4
            if re.search(r"bill of lading\s*\(draft\)|draft bill of lading", head)
            else 0
        )
        score -= 4 if "shipping instruction" in head else 0
    return score


def identify_documents(email, attachments):
    """Return selected SI/BL or an ambiguity reason. Attachments are read docs."""
    if not attachments:
        return {"si": None, "bl": None, "reason": "missing_attachment"}
    ranked_si = sorted(attachments, key=lambda d: _role_score(d, "si"), reverse=True)
    ranked_bl = sorted(attachments, key=lambda d: _role_score(d, "bl"), reverse=True)
    si = ranked_si[0] if _role_score(ranked_si[0], "si") >= 3 else None
    bl = ranked_bl[0] if _role_score(ranked_bl[0], "bl") >= 3 else None
    if si is None or bl is None:
        return {
            "si": si,
            "bl": bl,
            "reason": "missing_attachment"
            if len(attachments) < 2
            else "wrong_doc_type",
        }
    other_title = r"^\s*(?:commercial invoice|packing list|certificate of origin)\b"
    if re.search(other_title, si["text"], re.I) or re.search(
        other_title, bl["text"], re.I
    ):
        return {"si": si, "bl": bl, "reason": "wrong_doc_type"}
    if (
        si is bl
        or (
            len(ranked_si) > 1
            and _role_score(ranked_si[0], "si") == _role_score(ranked_si[1], "si")
        )
        or (
            len(ranked_bl) > 1
            and _role_score(ranked_bl[0], "bl") == _role_score(ranked_bl[1], "bl")
        )
    ):
        return {"si": None, "bl": None, "reason": "wrong_doc_type"}
    return {
        "si": si,
        "bl": bl,
        "reason": None,
        "other": [d for d in attachments if d is not si and d is not bl],
    }


def normalize_value(field, value):
    value = (
        value.split("|")[0].strip()
        if field in ("shipper", "consignee", "notify_party")
        else value.strip()
    )
    if re.fullmatch(
        r"(?:tba|to be advised|n/?a|none|unknown|[_\s]+)(?:mt|kg|kgs)?", value, re.I
    ):
        return None
    if field in ("shipper", "consignee", "notify_party"):
        value = re.split(
            r"ON BEHALF OF|P\.?O\.? BOX|\d{1,5}\s+[A-Z][A-Z ]+(?:ROAD|STREET|AVENUE)",
            value,
            maxsplit=1,
            flags=re.I,
        )[0].strip()
    if field == "container_count":
        # Prefer the number next to an "x" separator ("5 x 40'HC" or the
        # reversed "40'HC x 5") over blindly taking the first digits in the
        # string, so the container *count* isn't confused with its size.
        cleaned = value.replace(",", "")
        match = (
            re.search(r"(\d+)\s*x\b", cleaned, re.I)
            or re.search(r"\bx\s*(\d+)", cleaned, re.I)
            or re.search(r"\d+", cleaned)
        )
        if not match:
            return None
        return int(match.group(1) if match.groups() else match.group())
    if field == "gross_weight_kg":
        match = re.search(r"\d[\d,]*(?:\.\d+)?", value)
        if not match:
            return None
        try:
            amount = Decimal(match.group().replace(",", ""))
            if re.search(r"\b(?:mt|metric tons?|tonnes?)\b", value, re.I):
                amount *= 1000
            return format(amount.normalize(), "f")
        except InvalidOperation:
            return None
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split()) or None


def extract_fields(document, ai_service=None):
    result = {}
    lines = document["text"].splitlines()
    for number, line in enumerate(lines, 1):
        label, sep, raw = line.partition(":")
        label = re.sub(r"\s*\([^)]*[\u2e80-\u9fff][^)]*\)", "", label.strip())
        raw = raw.strip() if sep else ""
        if not sep:
            label = (
                re.sub(r"\s*\([^)]*\)", "", label).strip()
                if document["file_type"] in (".pdf", ".docx")
                else label
            )
            if number < len(lines):
                raw = lines[number].strip()
        if not raw:
            continue
        for field, patterns in LABELS.items():
            if field in result:
                continue
            if any(re.fullmatch(pattern, label, re.I) for pattern in patterns):
                if field == "gross_weight_kg" and not re.match(r"\s*\d", raw):
                    continue
                result[field] = {
                    "raw_value": raw,
                    "normalized_value": normalize_value(field, raw),
                    "source": f"{document['path']}:line {number}",
                    "source_text": line.strip(),
                    "confidence": document.get("confidence", 0.98),
                    "method": document.get("method", "native"),
                }
                break
    if ai_service and ai_service.enabled:
        missing = set(FIELDS) - set(result)
        if missing:
            suggestions = ai_service.extract_missing_fields(document, missing)
            for field in missing:
                item = suggestions.get(field)
                if not isinstance(item, dict):
                    continue
                raw, source = item.get("raw_value"), item.get("source_text")
                try:
                    confidence = float(item.get("confidence", 0))
                except (TypeError, ValueError):
                    continue
                if (
                    not isinstance(raw, str)
                    or not isinstance(source, str)
                    or not source.strip()
                    or source.casefold() not in document["text"].casefold()
                    or raw.casefold() not in source.casefold()
                    or not 0 <= confidence <= 1
                ):
                    continue
                result[field] = {
                    "raw_value": raw,
                    "normalized_value": normalize_value(field, raw),
                    "source": document["path"],
                    "source_text": source,
                    "confidence": min(confidence * 0.95, document.get("confidence", 1)),
                    "method": "gemini_semantic_mapping",
                }
                logging.getLogger(__name__).info(
                    "AI mapped %s in %s", field, document["path"]
                )
    return result


def _identifiers(document):
    result = {}
    patterns = {
        "booking": r"(?:booking (?:ref(?:erence)?|no\.?|number))\s*:\s*(\S+)",
        "oc": r"oc no\.?\s*:\s*(\S+)",
        "bl": r"(?:bill of lading no\.?|b/?l (?:no\.?|number))\s*:\s*(\S+)",
        "vessel": r"^vessel(?: name)?\s*:\s*([^\r\n]+)",
        "commodity": r"^(?:commodity|description of goods)\s*:\s*([^\r\n]+)",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, document["text"], re.I | re.M)
        if match:
            value = match.group(1).strip().upper()
            result[field] = (
                re.sub(r"[^A-Z0-9]+", "", value)
                if field in {"vessel", "commodity"}
                else value
            )
    return result


def validate_document_pair(si, bl):
    a, b = _identifiers(si), _identifiers(bl)
    conflicts = sorted(
        key
        for key in a.keys() & b.keys()
        if key in {"booking", "oc", "bl"} and a[key] != b[key]
    )
    supporting_differences = sorted(
        key
        for key in a.keys() & b.keys()
        if key in {"vessel", "commodity"} and a[key] != b[key]
    )
    return {
        "valid": not conflicts,
        "conflicts": conflicts,
        "supporting_differences": supporting_differences,
        "si": a,
        "bl": b,
    }


def validate_document_consistency(document, fields=None):
    """Reconcile explicit container rows and weights when they are present."""
    fields = fields or extract_fields(document)
    lines = [line.strip() for line in document["text"].splitlines()]
    row_positions = [
        n for n, line in enumerate(lines) if re.fullmatch(r"[A-Z]{4}\d{7}", line)
    ]
    rows = [lines[n] for n in row_positions]
    declared = fields.get("container_count", {}).get("normalized_value")
    if rows and declared is not None and len(rows) != declared:
        return {
            "valid": False,
            "reason": "DOCUMENT_INTERNAL_INCONSISTENCY",
            "evidence": f"{len(rows)} container rows vs declared {declared}",
        }
    weights = []
    for position in row_positions:
        candidates = lines[position + 1 : position + 4]
        amount = next(
            (
                Decimal(c.replace(",", ""))
                for c in candidates
                if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", c)
            ),
            None,
        )
        if amount is not None:
            weights.append(amount)
    total = fields.get("gross_weight_kg", {}).get("normalized_value")
    if rows and len(weights) == len(rows) and total is not None:
        calculated = sum(weights)
        if calculated != Decimal(total):
            return {
                "valid": False,
                "reason": "DOCUMENT_INTERNAL_INCONSISTENCY",
                "evidence": f"Container weights sum to {calculated} kg vs declared {total} kg",
            }
    return {
        "valid": True,
        "container_rows": len(rows),
        "reconciled_weights": len(weights),
    }


class Extractor:
    def __init__(self, SI, BL, classifier=None):
        self.SI, self.BL, self.classifier = SI, BL, classifier

    def extract_data(self):
        return extract_fields(self.SI), extract_fields(self.BL)
