#!/usr/bin/env python3
"""Mockup Lab agent-mode smoke test (v8.43+). Run BEFORE publishing any mockup-lab/index.html change.

  cd <repo> && python3 -m http.server 8765 &        # serve the repo
  python3 tools/labapi_smoke.py [http://localhost:8765/mockup-lab/]   # or the live URL

Needs: pip install playwright (Chromium preinstalled in Claude cloud sessions).
Checks contracts, not pixels — screenshots land in ./labapi-smoke-*.png to eyeball.
Exit code 1 on any failure.
"""
import asyncio, json, sys, gzip, base64
from playwright.async_api import async_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8765/mockup-lab/').rstrip('/') + '/'
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200">'
       '<rect x="10" y="10" width="380" height="180" fill="#ffffff"/>'
       '<circle cx="200" cy="100" r="70" fill="#53565a"/>'
       '<rect x="180" y="40" width="40" height="40" fill="#8a9199"/></svg>')
fails = []
def check(name, ok, info=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + str(info)[:200]) if info and not ok else ''))
    if not ok: fails.append(name)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(accept_downloads=True, viewport={'width': 1400, 'height': 1000})
        pg = await ctx.new_page()
        await pg.route('**/fonts.g*/**', lambda r: r.abort())
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        # fake worker so nothing real is written
        async def wk(route):
            if route.request.method == 'OPTIONS':
                return await route.fulfill(status=204, headers={'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Ops-Key', 'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'})
            body = {'ok': True, 'list': 'https://w/handoff/list?t=' + 'a' * 36} if '/handoff' in route.request.url else {'ok': True}
            await route.fulfill(status=200, headers={'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'}, body=json.dumps(body))
        await pg.route('**/mayhem-ops-state.*.workers.dev/**', wk)
        await pg.goto(BASE + '?job')
        # stale resume draft must not stall agent mode (v8.43.1)
        await pg.evaluate("localStorage.setItem('mockuplab.job.v1',JSON.stringify({v:1,when:Date.now()-864e5,customer:'old draft',sets:[{name:'GROUP A',items:[]}]}));localStorage.setItem('mo-key','TESTKEY')")
        await pg.reload(); await pg.wait_for_function('window.labAPI')

        d = base64.b64encode(gzip.compress(SVG.encode())).decode()
        await pg.evaluate('x=>labAPI.stage(x)', d)
        r = await pg.evaluate('''labAPI.loadJob({customer:'Smoke Test Co',quote:'9999',
            garments:[{style:'2000',color:'Black'},{style:'5000',color:'White'}],
            art:{front:'staged'},sizes:{front:11},inks:[{n:2,ink:'SHOP GREY',setup:'both'}]})''')
        s = r['summary']; g = s['groups'][0]
        check('draft backed up', await pg.evaluate("JSON.parse(localStorage.getItem('mockuplab.job.v1.beforeAgent')).customer==='old draft'"))
        check('quote formatted', s['quote'] == '#9999', s['quote'])
        check('alias 2000 -> 5000 photo', g['items'][0]['styleKey'] == '5000' and g['items'][0]['caption'].startswith('Gildan 2000'), g['items'][0])
        check('shown-as warning', any('Gildan 2000' in w for w in g['warnings']), g['warnings'])
        check('width 11', g['locations'][0]['widthIn'] == 11, g['locations'])
        check('3 inks detected', len(g['inks']) == 3, len(g['inks']))
        check('ink by shop name', g['inks'][2]['light']['shop'] == 'SHOP GREY' and g['inks'][2]['dark']['shop'] == 'SHOP GREY', g['inks'][2])
        check('stocked flag', g['inks'][0]['light']['stocked'] is True)
        check('dark shirt has underbase screen', g['items'][0]['dark'] and g['items'][0]['screens']['front'] == 4, g['items'][0])
        h = await pg.evaluate("labAPI.setHeight('front',17)")
        check('setHeight caps at 12.5', h['widthIn'] == 12.5 and h.get('capped'), h)
        await pg.evaluate("labAPI.setWidth('front',11)")
        for js, frag in [("labAPI.addGarment('ZZ99','Black')", 'not in the catalog'), ("labAPI.setInk(9,'WHITE')", 'no detected ink'),
                         ("labAPI.setInk(0,'NOPE')", 'unknown ink')]:
            try:
                await pg.evaluate(js); check('error: ' + js, False, 'no error')
            except Exception as e:
                check('error: ' + js, frag in str(e), str(e))
        pages = await pg.evaluate('labAPI.approvals()')
        check('approval pages', len(pages) == 2, pages)
        await pg.evaluate('labAPI.preview(0)'); await pg.screenshot(path='labapi-smoke-approval.png'); await pg.evaluate('labAPI.closePreview()')
        ho = await pg.evaluate('labAPI.handoff()')
        check('handoff built', ho['files'] >= 3 and ho['locations'][0]['locations'][0]['widthIn'] == 11, ho)
        check('handoff uploaded (fake worker)', isinstance(ho['uploaded'], dict) and ho['uploaded'].get('ok'), ho['uploaded'])
        man = await pg.evaluate('labAPI.__handoff.manifest')
        check('manifest carries shownAs', man['groups'][0]['firstImprint'][0].get('shownAs') == 'Gildan 2000', man['groups'][0]['firstImprint'][0])
        # the hand-clicked 📎 button still downloads + appends the worker link to the prompt
        async with pg.expect_download() as dl:
            await pg.click('#dlPrintavo')
        check('📎 still downloads', (await dl.value).suggested_filename.startswith('printavo-handoff-'))
        await pg.wait_for_function("document.getElementById('pvNote').innerText.includes('handoff')", timeout=20000)
        check('📎 prompt has worker link', '/handoff/list?t=' in await pg.inner_text('#pvNote'))
        check('no page errors', not errs, errs)
        await b.close()
    print('\n%d failure(s)' % len(fails) if fails else '\nALL GREEN')
    sys.exit(1 if fails else 0)

asyncio.run(main())
