"""Deploy only the built public app to Firebase Hosting using the REST API."""

import gzip
import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = "seal-509214"
ROOT = Path(__file__).resolve().parent
GCLOUD = Path.home() / "AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
API = f"https://firebasehosting.googleapis.com/v1beta1/sites/{PROJECT}"


def api(url, token, data=None, method="GET", content_type="application/json"):
    payload = json.dumps(data).encode() if isinstance(data, dict) else data
    request = urllib.request.Request(url, data=payload, method=method, headers={
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": PROJECT,
        "Content-Type": content_type,
    })
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Firebase Hosting returned HTTP {exc.code}: {exc.read().decode()[:500]}") from exc


def main():
    dist = ROOT / "dist"
    files = [path for path in dist.rglob("*") if path.is_file()]
    if not files or len(files) > 20 or any("bundle-cases" in path.name for path in files):
        raise RuntimeError("Unexpected Hosting build contents")
    config = json.loads((dist / "firebase-config.json").read_text(encoding="utf-8"))
    if config.get("projectId") != PROJECT or not config.get("appId"):
        raise RuntimeError("Firebase web app config is missing")
    token = subprocess.run([str(GCLOUD), "auth", "print-access-token"], capture_output=True, text=True, check=True).stdout.strip()
    billing = json.loads(subprocess.run([str(GCLOUD), "billing", "projects", "describe", PROJECT, "--format=json(billingEnabled)"], capture_output=True, text=True, check=True).stdout)
    if billing.get("billingEnabled"):
        raise RuntimeError("Deployment stopped: billing is enabled")
    version = api(f"{API}/versions", token, {"config": {
        "rewrites": [{"glob": "**", "path": "/index.html"}],
        "headers": [{"glob": "**", "headers": {"Cache-Control": "no-cache, no-store"}}],
    }}, "POST")["name"]
    compressed = {}
    hashes = {}
    for path in files:
        content = gzip.compress(path.read_bytes(), mtime=0)
        digest = hashlib.sha256(content).hexdigest()
        compressed[digest] = content
        hashes["/" + path.relative_to(dist).as_posix()] = digest
    pending = api(f"https://firebasehosting.googleapis.com/v1beta1/{version}:populateFiles", token, {"files": hashes}, "POST")
    for digest in pending.get("uploadRequiredHashes", []):
        api(f"{pending['uploadUrl']}/{digest}", token, compressed[digest], "POST", "application/octet-stream")
    api(f"https://firebasehosting.googleapis.com/v1beta1/{version}?update_mask=status", token, {"status": "FINALIZED"}, "PATCH")
    api(f"{API}/releases?versionName={version}", token, b"", "POST")
    print(f"Deployed {len(files)} public app files to https://{PROJECT}.web.app")


if __name__ == "__main__":
    main()
