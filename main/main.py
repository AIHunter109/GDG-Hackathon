import json
import sys
from pathlib import Path

from classify import Classify
from extractor import Extractor

from Bundle.loader import Inbox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def run():

    inbox = Inbox("Bundle")                     # this folder  (or a server URL)
    submission = {}
    for email in inbox:
        eid = email["email_id"]
        # ... your classify + extract + compare pipeline ...
        submission[eid] = {
            "category": "BL_COMPARISON",
            "status": "MISMATCH",
            "review_reason": None,
            "has_defect": True,
            "defect_fields": ["consignee"],
        }


    with open("submission.json", "w") as file:
        json.dump(submission, file, indent=2)

if __name__ == "__main__":
    run()