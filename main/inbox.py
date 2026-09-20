"""Bundle.loader.Inbox compatible import for local and Cloud Run builds."""

try:
    from Bundle.loader import Inbox
except ModuleNotFoundError:
    import json
    import urllib.request
    from pathlib import Path

    class Inbox:
        def __init__(self, source):
            self.source = str(source).rstrip("/")
            self.is_http = self.source.startswith(("http://", "https://"))

        def emails(self):
            if self.is_http:
                with urllib.request.urlopen(self.source + "/emails") as response:
                    return json.load(response)
            return [json.loads(path.read_text(encoding="utf-8")) for path in
                    sorted((Path(self.source) / "inbox").glob("email_*.json"))]

        def __iter__(self):
            return iter(self.emails())

        def get(self, email_id):
            if self.is_http:
                with urllib.request.urlopen(self.source + "/emails/" + email_id) as response:
                    return json.load(response)
            return json.loads((Path(self.source) / "inbox" / (email_id + ".json")).read_text(encoding="utf-8"))

        def read_bytes(self, path):
            if self.is_http:
                with urllib.request.urlopen(self.source + "/" + path.lstrip("/")) as response:
                    return response.read()
            return (Path(self.source) / path).read_bytes()

        def sample_submission(self):
            if self.is_http:
                with urllib.request.urlopen(self.source + "/sample_submission") as response:
                    return json.load(response)
            return json.loads((Path(self.source) / "sample_submission.json").read_text(encoding="utf-8"))
