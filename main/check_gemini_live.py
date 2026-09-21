"""Verify the configured AI provider actually works before relying on it
anywhere real. Works for either provider (Gemini or GonkaRouter) -- whichever
one AIService() auto-detects from your configured key.

Makes one real API call with a synthetic email -- no bundle data, no key
ever printed. Run after setting up your key (via .env or a real environment
variable):

    python main/check_gemini_live.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv_loader import load_dotenv

load_dotenv()

from ai_service import AIService


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
    print("Calling the live API once...")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
