"""Check live Gemini text and PDF paths using only synthetic content."""

import io
import os
import sys
import zlib

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
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
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
    if not os.getenv("GEMINI_API_KEY"):
        raise SystemExit("Set GEMINI_API_KEY in the process environment first")
    ai = AIService()
    email = {
        "subject": "Please verify draft BL against SI",
        "body": "Check the draft bill of lading against the shipping instruction.",
        "attachments": [],
    }
    classification = ai.classify_email(email)
    print(
        "Gemini classification:",
        classification.get("category") if classification else None,
    )
    pdf = synthetic_scanned_pdf()
    from pypdf import PdfReader

    assert not PdfReader(io.BytesIO(pdf)).pages[0].extract_text().strip()
    transcription = ai.transcribe_pdf(pdf)
    text = transcription.get("text", "").upper() if transcription else ""
    print("Gemini image-only PDF OCR:", "SHIPPER" in text and "ACME" in text)
    if not classification or classification.get("category") != "BL_COMPARISON":
        return 1
    if "SHIPPER" not in text or "ACME" not in text:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
