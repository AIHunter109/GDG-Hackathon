"""Seed owner-only bundle cases into Firestore without using Gemini or billing.

Requires the existing Google Cloud CLI login and deployed owner-only rules.
"""

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = "seal-509214"
ROOT = Path(__file__).resolve().parent
GCLOUD = Path.home() / "AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
BASE = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"


def google_token():
    process = subprocess.run([str(GCLOUD), "auth", "print-access-token"], capture_output=True, text=True, check=True)
    return process.stdout.strip()


def value(item):
    if item is None:
        return {"nullValue": None}
    if isinstance(item, bool):
        return {"booleanValue": item}
    if isinstance(item, int):
        return {"integerValue": str(item)}
    if isinstance(item, float):
        return {"doubleValue": item}
    if isinstance(item, str):
        return {"stringValue": item}
    if isinstance(item, list):
        return {"arrayValue": {"values": [value(entry) for entry in item]}}
    if isinstance(item, dict):
        return {"mapValue": {"fields": {key: value(entry) for key, entry in item.items()}}}
    raise TypeError(type(item))


def request(url, token, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": PROJECT,
        "Content-Type": "application/json",
    }, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Firebase API returned HTTP {exc.code}") from exc


def main():
    records = json.loads((ROOT / "private/bundle-cases.json").read_text(encoding="utf-8"))
    if len(records) != 520:
        raise ValueError("Expected 520 original emails")
    token = google_token()
    release = request(f"https://firebaserules.googleapis.com/v1/projects/{PROJECT}/releases/cloud.firestore", token)
    if not release.get("rulesetName"):
        raise RuntimeError("Owner-only Firestore rules are not deployed")
    ruleset = request(f"https://firebaserules.googleapis.com/v1/{release['rulesetName']}", token)
    source = "\n".join(file.get("content", "") for file in ruleset.get("source", {}).get("files", []))
    if "allow read: if isOwner();" not in source or "allow write: if false;" not in source:
        raise RuntimeError("Private bundle rules need review before seeding")
    items = sorted(records.items())
    for start in range(0, len(items), 100):
        batch = items[start:start + 100]
        writes = [{"update": {
            "name": f"projects/{PROJECT}/databases/(default)/documents/bundleCases/{email_id}",
            "fields": {key: value(entry) for key, entry in case.items()},
        }} for email_id, case in batch]
        request(f"{BASE}:commit", token, {"writes": writes})
        print(f"Seeded {min(start + len(batch), len(items))}/520 cases")
    print("Private Firestore seed complete")


if __name__ == "__main__":
    main()
