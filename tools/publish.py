#!/usr/bin/env python3
"""Mayhem publish helper — one commit, many files, via the GitHub Trees API.
Usage: python3 publish.py "commit message" repo_path1=local_path1 [repo_path2=local_path2 ...]
Token: reads GH_TOKEN env or ~/mayhem-keys/github_token.txt (Cowork: Downloads/mayhem-keys/)."""
import json,os,sys,urllib.request
REPO='mayhemink/mayhemink.github.io'
def tok():
    t=os.environ.get('GH_TOKEN')
    if t:return t.strip()
    for p in [os.path.expanduser('~/mayhem-keys/github_token.txt'),
              '/sessions/'+os.environ.get('USER','')+'/mnt/Downloads/mayhem-keys/github_token.txt']:
        if os.path.exists(p):return open(p).read().strip()
    raise SystemExit('no token found')
def api(path,data=None,method=None):
    req=urllib.request.Request('https://api.github.com'+path,
        data=json.dumps(data).encode() if data is not None else None,method=method)
    req.add_header('Authorization','Bearer '+tok())
    req.add_header('Accept','application/vnd.github+json')
    return json.load(urllib.request.urlopen(req))
def main():
    msg=sys.argv[1];pairs=[a.split('=',1) for a in sys.argv[2:]]
    head=api('/repos/%s/git/ref/heads/main'%REPO)['object']['sha']
    base_tree=api('/repos/%s/git/commits/%s'%(REPO,head))['tree']['sha']
    import base64
    entries=[]
    for rp,lp in pairs:
        b=open(lp,'rb').read()
        blob=api('/repos/%s/git/blobs'%REPO,{'content':base64.b64encode(b).decode(),'encoding':'base64'})
        entries.append({'path':rp,'mode':'100644','type':'blob','sha':blob['sha']})
        print('blob',rp,len(b),'bytes')
    tree=api('/repos/%s/git/trees'%REPO,{'base_tree':base_tree,'tree':entries})
    commit=api('/repos/%s/git/commits'%REPO,{'message':msg,'tree':tree['sha'],'parents':[head]})
    api('/repos/%s/git/refs/heads/main'%REPO,{'sha':commit['sha']},method='PATCH')
    print('COMMITTED',commit['sha'][:8],msg[:60])
if __name__=='__main__':main()
