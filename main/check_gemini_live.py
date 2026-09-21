"""Verify the configured AI provider actually works before relying on it
anywhere real. Works for either provider (Gemini or GonkaRouter) -- whichever
one AIService() auto-detects from your configured key.

Checks two paths with only synthetic content -- no bundle data, no key ever
printed: (1) text classification, (2) image-only PDF vision/OCR, using a
synthetic scanned PDF with no selectable text (rendered from a tiny bitmap
font below, so this needs no real scanned document to test against). Run
after setting up your key (via .env or a real environment variable):

    python main/check_gemini_live.py
"""
import io
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv_loader import load_dotenv

load_dotenv()

from ai_service import AIService

GLYPHS = {
    " ": ("00000",) * 7,
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
}


def synthetic_scanned_pdf():
    """Draw SHIPPER ACME into a PDF image, with no selectable text."""
    label = "SHIPPER ACME"
    scale = 12
    margin = 12
    width = len(label) * 6 * scale + margin * 2
    height = 7 * scale + margin * 2
    pixels = bytearray(b"\xff" * (width * height))
    for char_index, char in enumerate(label):
        for row_index, row in enumerate(GLYPHS[char]):
            for col_index, bit in enumerate(row):
                if bit == "1":
                    for y in range(scale):
                        start = (
                            (margin + row_index * scale + y) * width
                            + margin
                            + (char_index * 6 + col_index) * scale
                        )
                        pixels[start : start + scale] = b"\x00" * scale

    image = zlib.compress(pixels)
    content = b"q\n590 0 0 80 15 25 cm\n/Im0 Do\nQ\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 620 130] "
            b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
        ),
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
            f"/Length {len(image)} >>\nstream\n"
        ).encode()
        + image
        + b"\nendstream",
        f"<< /Length {len(content)} >>\nstream\n".encode()
        + content
        + b"endstream",
    ]
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return output.getvalue()


def main():
    ai = AIService()
    if not ai.enabled:
        print(
            "FAIL: no API key found. Set GEMINI_API_KEY or GONKAROUTER_API_KEY "
            "as a real environment variable, or fill one in in .env."
        )
        return 1

    print(f"Provider: {ai.provider}")
    print(f"Found a key ({len(ai.api_key)} characters, not shown). Model: {ai.model}")

    print("\n--- Check 1: text classification ---")
    synthetic_email = {
        "subject": "RE_ TO CONFIRM DOCS",
        "body": "Please check the attached SI against the draft BL and confirm.",
        "attachments": ["attachments/x_SI.txt", "attachments/x_BL.txt"],
    }
    result = ai.classify_email(synthetic_email)
    if result is None:
        print(
            "FAIL: the API call did not return a usable result. Common causes: "
            "wrong model name for this key/account, invalid key, no network "
            "access, or a temporary outage/rate limit on the provider's side. "
            "Check the provider's dashboard for available model names and set "
            "GEMINI_MODEL or GONKAROUTER_MODEL if needed."
        )
        return 1
    print("PASS: live call succeeded.")
    print(f"  category:   {result['category']}")
    print(f"  confidence: {result['confidence']}")
    print(f"  reason:     {result['reason']}")
    print(f"  method:     {result['method']}")

    print("\n--- Check 2: image-only PDF vision/OCR ---")
    pdf = synthetic_scanned_pdf()
    from pypdf import PdfReader

    assert not PdfReader(io.BytesIO(pdf)).pages[0].extract_text().strip(), (
        "synthetic PDF unexpectedly has selectable text -- test is broken"
    )
    transcription = ai.transcribe_pdf(pdf)
    text = transcription.get("text", "").upper() if transcription else ""
    vision_ok = "SHIPPER" in text and "ACME" in text
    print(f"PASS: OCR read back the label correctly." if vision_ok else "FAIL: vision path did not return the expected text (or isn't supported by this provider/model).")

    if result.get("category") != "BL_COMPARISON":
        return 1
    return 0 if vision_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
