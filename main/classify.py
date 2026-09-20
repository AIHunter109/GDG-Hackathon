"""Email intent classification using message and attachment evidence."""

import re


def classify_email(email):
    subject = email.get("subject", "").lower()
    body = email.get("body", "").lower()
    names = " ".join(email.get("attachments", [])).lower()
    message = subject + " " + body
    docs = bool(
        re.search(
            r"draft\s*(?:b/?l|bill of lading)|shipping instruction|\bsi\b.*\bbl\b|\bbl\b.*\bsi\b",
            message,
        )
    )
    pair = bool(re.search(r"(?:_si|_bl|shipping.?instruction|draft.?bl)", names))
    verify = bool(
        re.search(
            r"check|confirm|verify|compare|amend|review|approval|matches", message
        )
    )
    if (docs and (verify or pair)) or (pair and verify):
        return {
            "category": "BL_COMPARISON",
            "confidence": 0.98 if pair else 0.85,
            "reason": "SI and draft BL verification context",
        }
    if re.search(
        r"unsubscribe|lottery|winner|free money|click here|limited time offer|crypto investment|bitcoin investment|guaranteed \d+% returns|exclusive offer|\d+% off|undelivered messages|unpaid customs fee|track-parcel|verify your account|mailbox has exceeded|bank details|urgent business proposal|weird trick",
        message,
    ):
        return {
            "category": "SPAM",
            "confidence": 0.95,
            "reason": "Promotional or unsolicited content",
        }
    if re.search(
        r"\bsi\s*(?:needed|request|required)\b|\brequest\s+si\b|please find shipping instruction|shipping instruction for|send (?:the )?draft bl",
        message,
    ):
        return {
            "category": "SI_REQUEST",
            "confidence": 0.94,
            "reason": "Shipping instruction or draft request",
        }
    if re.search(
        r"invoice|payment advice|remittance|outstanding balance|credit note|detention charges|local charges|\bthc\b|release payment",
        body,
    ) and not re.search(r"(?:original invoice|billing process.*completed)", body):
        return {
            "category": "INVOICE_QUERY",
            "confidence": 0.92,
            "reason": "Invoice or payment discussion",
        }
    if re.search(
        r"(?:request|send|prepare|issue|provide|need|submit).{0,50}(?:shipping instruction|\bsi\b)|(?:shipping instruction|\bsi\b).{0,50}(?:request|template|form)",
        body,
    ) and not re.search(r"submit si\s*&\s*aed|outstanding list", body):
        return {
            "category": "SI_REQUEST",
            "confidence": 0.88,
            "reason": "Request for shipping instructions",
        }
    return {
        "category": "GENERAL",
        "confidence": 0.7,
        "reason": "No comparison or specific request detected",
    }


class Classify:
    def __init__(self, message):
        self.message = message
        self.type = None

    def parser(self, message=None):
        result = classify_email(message or self.message)
        self.type = result["category"]
        return result
