#!/usr/bin/env python3
"""Stage job art for the Mockup Lab's agent mode — no chunk-pasting.

Publishes art files to mockup-lab/tmp-art/<name> in ONE commit (same Trees-API route as
publish.py) so labAPI.importArt(side, 'tmp-art/<name>') can fetch them same-origin.
Run it where the GitHub token lives (Mac/PC: Downloads/mayhem-keys/publish-tmp/):

  GH_TOKEN=$(cat ../github_token.txt) python3 stage_art.py add 6569-front.svg [more files…]
  GH_TOKEN=$(cat ../github_token.txt) python3 stage_art.py clean            # delete ALL of tmp-art/

Prints the in-Lab path for each file. GitHub Pages serves it ~1–2 min after the commit;
the script polls until each URL answers 200. Clean up when the job is done (customer art
should not sit in the public repo longer than needed).
"""
import base64, json, os, sys, time, urllib.request

REPO = 'mayhemink/mayhemink.github.io'
PAGES = 'https://mayhemink.github.io/mockup-lab/'

def tok():
    t = os.environ.get('GH_TOKEN')
    if not t: raise SystemExit('set GH_TOKEN (cat ../github_token.txt) — never paste the token itself')
    return t.strip()

def api(path, data=None, method=None):
    req = urllib.request.Request('https://api.github.com' + path,
        data=json.dumps(data).encode() if data is not None else None, method=method)
    req.add_header('Authorization', 'Bearer ' + tok())
    req.add_header('Accept', 'application/vnd.github+json')
    return json.load(urllib.request.urlopen(req))

def commit(entries, msg):
    head = api('/repos/%s/git/ref/heads/main' % REPO)['object']['sha']
    base_tree = api('/repos/%s/git/commits/%s' % (REPO, head))['tree']['sha']
    tree = api('/repos/%s/git/trees' % REPO, {'base_tree': base_tree, 'tree': entries})
    c = api('/repos/%s/git/commits' % REPO, {'message': msg, 'tree': tree['sha'], 'parents': [head]})
    api('/repos/%s/git/refs/heads/main' % REPO, {'sha': c['sha']}, method='PATCH')
    return c['sha'][:8]

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('add', 'clean'):
        raise SystemExit(__doc__)
    if sys.argv[1] == 'add':
        files = sys.argv[2:]
        if not files: raise SystemExit('add which files?')
        entries = []
        for f in files:
            b = open(f, 'rb').read()
            blob = api('/repos/%s/git/blobs' % REPO, {'content': base64.b64encode(b).decode(), 'encoding': 'base64'})
            entries.append({'path': 'mockup-lab/tmp-art/' + os.path.basename(f), 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
        print('COMMITTED', commit(entries, 'tmp-art: stage ' + ', '.join(os.path.basename(f) for f in files)))
        for f in files:
            url = PAGES + 'tmp-art/' + os.path.basename(f)
            for _ in range(60):
                try:
                    if urllib.request.urlopen(url + '?x=%d' % time.time()).status == 200: break
                except Exception: pass
                time.sleep(5)
            print('LIVE  labAPI.importArt(side, %r)' % ('tmp-art/' + os.path.basename(f)))
    else:
        try:
            items = api('/repos/%s/contents/mockup-lab/tmp-art' % REPO)
        except Exception:
            print('tmp-art/ is already empty'); return
        entries = [{'path': it['path'], 'mode': '100644', 'type': 'blob', 'sha': None} for it in items if it['type'] == 'file']
        if not entries: print('tmp-art/ is already empty'); return
        print('COMMITTED', commit(entries, 'tmp-art: clean %d file(s)' % len(entries)))

if __name__ == '__main__':
    main()
