#!/usr/bin/env python3
"""Batch-push many files to mayhemink/mayhemink.github.io in ONE commit.

Why: pushing files one-by-one via the Contents API makes one commit PER FILE.
GitHub Pages soft-limits ~10 builds/hour, so a 40-photo publish starves the
whole site (timecards board included) and builds start erroring. This script
uses the Git Data API (blobs -> tree -> commit -> ref) so ANY number of files
lands as one commit = one Pages build.

Usage:
  GH_TOKEN=... python3 gh_batch_push.py "Commit message" local1:repo/path1 [local2:repo/path2 ...]

Each arg is LOCALPATH:REPOPATH. Text or binary both fine. Exits nonzero on any
failure; nothing is committed unless every blob uploaded.
"""
import base64, json, os, sys, time, urllib.request, urllib.error

REPO = "mayhemink/mayhemink.github.io"
API = f"https://api.github.com/repos/{REPO}"
TOKEN = os.environ.get("GH_TOKEN")

def gh(path, method="GET", body=None):
    req = urllib.request.Request(API + path, method=method, headers={
        "Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json"})
    if body is not None:
        req.data = json.dumps(body).encode()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < 2:
                time.sleep(3); continue
            sys.exit(f"FAIL {method} {path}: HTTP {e.code} {e.read().decode()[:200]}")

def main():
    if not TOKEN:
        sys.exit("Set GH_TOKEN env var")
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    message, pairs = sys.argv[1], sys.argv[2:]

    ref = gh("/git/ref/heads/main")
    base_commit = ref["object"]["sha"]
    base_tree = gh(f"/git/commits/{base_commit}")["tree"]["sha"]

    entries = []
    for p in pairs:
        local, repo_path = p.split(":", 1)
        data = open(local, "rb").read()
        blob = gh("/git/blobs", "POST",
                  {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        entries.append({"path": repo_path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  blob {blob['sha'][:8]}  {repo_path}  ({len(data)} bytes)")

    tree = gh("/git/trees", "POST", {"base_tree": base_tree, "tree": entries})
    commit = gh("/git/commits", "POST",
                {"message": message, "tree": tree["sha"], "parents": [base_commit]})
    gh("/git/refs/heads/main", "PATCH", {"sha": commit["sha"]})
    print(f"DONE: {len(entries)} file(s) in ONE commit {commit['sha'][:8]}")

if __name__ == "__main__":
    main()
