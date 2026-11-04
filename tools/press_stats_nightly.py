#!/usr/bin/env python3
"""
Mayhem Ink — nightly press-stats parser (v4.1, 2026-09-15).

SOURCE OF TRUTH = the sync worker (what both iPads already sync every few seconds),
joined to week.json for row metadata (customer / location / qty / colors / screens).
The old "paste the End-of-Day export into Slack" step is no longer required.

Usage (typical, inside the scheduled session):
    python3 press_stats_nightly.py --target 2026-09-15 --existing existing.json --out report.json

  --target      YYYY-MM-DD (America/Denver). Default: today if it's a weekday and it's after
                04:00; otherwise the most recent weekday.
  --week        Monday of the week to read from the worker. Default: Monday of --target.
  --weekjson    Path or URL of the week.json that matches --week. Default: the live
                https://mayhemink.github.io/data/week.json — the script REFUSES to run if that
                file's "week" differs from --week (use a git-history copy for old weeks).
  --existing    JSON file: [{"date":"9/9/2026","job":"6447","location":"..."}...] — rows already on
                the Daily Data tab (any rows within ±7 days of target are enough). Used for dedupe.
  --export      Optional: a pasted iPad "WEEK TIMING" export (.txt). Only used to recover the
                hand-edit audit log (the worker does not keep it). Never used for timing.
  --out         Where to write the JSON report (default report.json). A human summary + a DM
                draft are printed to stdout.

Math mirrors the board (index.html): setup = setupDone-setupStart-overlapping stops;
print = jobDone-setupDone-overlapping stops; stops folded from patch entries exactly like
pausesOf(). Differences from the board, on purpose (sheet conventions since 8/2026):
  * OPEN stops (never resumed) are NOT subtracted — flagged instead ("print min overstated").
  * Overnight parks (stop > 4h) are subtracted from print but excluded from the Stops column.
  * A run that crosses midnight with NO logged park gets print min / pcs-hr left blank
    ("not separable"); same for a setup that crosses midnight. Spanned runs are dated by
    where most DAYTIME (05:00–21:00) print time fell.
  * Implausible rates (>450 pcs/hr, <8 pcs/hr over an hour, big qty in <5 min) go to
    needs_review instead of the sheet. Setup start/done within a minute = "tapped through".
  * Inferred lunch = the largest 20–90 min idle gap centred in 10:30–14:00; anything else
    stays unlabeled.
Never guesses a time. Anything doubtful is held back or excluded with a reason.
"""
import argparse, json, sys, re, difflib, urllib.request, datetime as dt
from zoneinfo import ZoneInfo

MT = ZoneInfo("America/Denver")
WORKER = "https://mayhem-press-state.autumn-mountain-14c6.workers.dev"
KEY = "wVUsmD1WhcoLlb_wDMuDDlXCZx2QsnT0"          # also in the public page source
LIVE_WEEK = "https://mayhemink.github.io/data/week.json"
OVERNIGHT_MS = 4 * 3600 * 1000
PAID_LUNCH = 30

# ---------------------------------------------------------------- helpers
def fetch(url, headers=None):
    h = {"User-Agent": "mayhem-press-stats/4.1"}      # Cloudflare 403s the default Python UA
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")

def load_json_arg(p):
    if p.startswith("http://") or p.startswith("https://"):
        return json.loads(fetch(p))
    with open(p) as f:
        return json.load(f)

def to_dt(ms):
    return dt.datetime.fromtimestamp(ms / 1000, MT)

def dstr(ms):                      # YYYY-MM-DD in Denver
    return to_dt(ms).strftime("%Y-%m-%d")

def hm(ms):
    if ms is None: return "—"
    t = to_dt(ms)
    return t.strftime("%-I:%M %p")

def hmd(ms):                      # with weekday for spans
    if ms is None: return "—"
    return to_dt(ms).strftime("%a %-I:%M %p")

def mdy(iso):
    y, m, d = iso.split("-")
    return f"{int(m)}/{int(d)}/{y}"

def mins(ms):
    return round(ms / 60000, 1)

def monday_of(iso):
    d = dt.date.fromisoformat(iso)
    return (d - dt.timedelta(days=d.weekday())).isoformat()

def default_target():
    now = dt.datetime.now(MT)
    d = now.date()
    if now.hour < 4:
        d -= dt.timedelta(days=1)
    while d.weekday() > 4:
        d -= dt.timedelta(days=1)
    return d.isoformat()

# ---------------------------------------------------------------- board math (ported)
def is_patch(x):
    return bool(x and x.get("k") is not None and x.get("at"))

def pauses_of(r):
    raw = (r or {}).get("pauses") or []
    out, patches = [], []
    for x in raw:
        if not x: continue
        if is_patch(x): patches.append(x)
        else:
            out.append({"start": x.get("start"), "end": x.get("end"), "reason": x.get("reason") or "",
                        "added": bool(x.get("added")), "edited": bool(x.get("edited")),
                        "_k": x.get("start"), "_del": False})
    patches.sort(key=lambda p: p["at"])
    for p in patches:
        for o in out:
            if o["_k"] != p["k"]: continue
            if p.get("deleted"): o["_del"] = True
            else:
                o["_del"] = False
                if p.get("start") is not None: o["start"] = p["start"]
                if "end" in p: o["end"] = p["end"]
                if p.get("reason") is not None: o["reason"] = p["reason"]
                if p.get("edited"): o["edited"] = True
            break
    out = [o for o in out if not o["_del"] and o["start"] is not None]
    out.sort(key=lambda o: o["start"])
    return out

def closed_paused_ms(pz, a, b, skip_overnight=False):
    """ms of CLOSED stops overlapping [a,b]. Open stops are never subtracted (sheet rule)."""
    t = 0
    for p in pz:
        if not p["end"]: continue
        if skip_overnight and (p["end"] - p["start"]) > OVERNIGHT_MS: continue
        s, e = max(p["start"], a), min(p["end"], b)
        if e > s: t += e - s
    return t

def print_handoffs(r): return [h for h in (r.get("handoffs") or []) if h.get("phase") == "print"]
def setup_handoffs(r): return [h for h in (r.get("handoffs") or []) if h.get("phase") == "setup"]

def crew_str(r):
    names = []
    if r.get("printBy"): names.append(r["printBy"])
    elif r.get("printer"): names.append(r["printer"])
    for h in print_handoffs(r):
        if not names or names[-1] != h.get("to"): names.append(h.get("to"))
    return " → ".join(n for n in names if n) or (r.get("printer") or "?")

def print_segments(r, qty, pz):
    if not r.get("setupDone"): return []
    hs = print_handoffs(r)
    end = r.get("jobDone")
    if not end: return []
    who = r.get("printBy") or r.get("printer") or "?"
    t0, cum0, pts = r["setupDone"], 0, []
    for h in hs:
        pts.append({"printer": who, "start": t0, "end": h["at"], "cumFrom": cum0, "cumTo": h.get("pieces")})
        who, t0, cum0 = h.get("to"), h["at"], h.get("pieces")
    pts.append({"printer": who, "start": t0, "end": end, "cumFrom": cum0, "cumTo": qty})
    out = []
    for p in pts:
        ms = (p["end"] - p["start"]) - closed_paused_ms(pz, p["start"], p["end"])
        ok = p["cumTo"] is not None and p["cumFrom"] is not None and (p["cumTo"] - p["cumFrom"]) > 0
        pieces = (p["cumTo"] - p["cumFrom"]) if ok else None
        out.append({"printer": p["printer"], "start": p["start"], "end": p["end"], "min": mins(ms),
                    "pieces": pieces, "pph": (round(pieces / (ms / 3600000)) if (pieces and ms > 0) else None)})
    return out

def split_by_day(a, b, pz):
    """DAYTIME (05:00–21:00) print ms per Denver date between a and b, minus closed stops — used to date spanned runs."""
    out = {}
    cur = a
    while cur < b:
        d = to_dt(cur)
        nxt = (d + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seg_end = min(b, int(nxt.timestamp() * 1000))
        day0 = d.replace(hour=0, minute=0, second=0, microsecond=0)
        w0, w1 = int((day0 + dt.timedelta(hours=5)).timestamp() * 1000), int((day0 + dt.timedelta(hours=21)).timestamp() * 1000)
        c0, c1 = max(cur, w0), min(seg_end, w1)          # daytime part only
        if c1 > c0:
            out[d.strftime("%Y-%m-%d")] = out.get(d.strftime("%Y-%m-%d"), 0) + (c1 - c0) - closed_paused_ms(pz, c0, c1)
        cur = seg_end
    return out

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target"); ap.add_argument("--week"); ap.add_argument("--weekjson")
    ap.add_argument("--existing"); ap.add_argument("--export"); ap.add_argument("--out", default="report.json")
    a = ap.parse_args()

    target = a.target or default_target()
    week = a.week or monday_of(target)
    tdate = dt.date.fromisoformat(target)
    is_friday = tdate.weekday() == 4

    wj = load_json_arg(a.weekjson or LIVE_WEEK)
    if wj.get("week") != week:
        sys.exit(f"week.json is for week {wj.get('week')} but you asked for {week}. "
                 f"Pass --weekjson with the matching file (git history: data/week.json at the right commit).")
    st = json.loads(fetch(f"{WORKER}/state?week={week}&k={KEY}", {"X-Press-Key": KEY}))
    if not st.get("ok"): sys.exit("worker refused: " + json.dumps(st)[:200])
    st = st["state"]
    rows_state = st.get("rows") or {}
    clocks = st.get("clocks") or {}

    # row metadata from week.json (all tabs incl. Extra cards)
    meta, home_day = {}, {}
    for d in wj.get("days", []):
        for r in d.get("rows", []):
            meta[r["id"]] = r; home_day[r["id"]] = d.get("day")

    # editor day-moves: jobday_<rowId>.notes = {"d": "YYYY-MM-DD"}
    moved = {}
    for rid, r in rows_state.items():
        if rid.startswith("jobday_"):
            try:
                n = json.loads(r.get("notes") or "{}")
                if n.get("d"): moved[rid[7:]] = n["d"]
            except Exception: pass

    # optional export → audit log (hand edits)
    edits = {}
    if a.export:
        txt = open(a.export, encoding="utf-8").read()
        m = re.search(r"--- raw data \(for Claude\) ---\s*(\{.*\})\s*$", txt, re.S)
        if m:
            try:
                raw = json.loads(m.group(1))
                for day in raw.get("days", []):
                    for rid, tr in (day.get("times") or {}).items():
                        if tr.get("edits") or tr.get("audit"):
                            edits[rid] = {"edits": list((tr.get("edits") or {}).keys()), "audit": tr.get("audit") or []}
            except Exception as e:
                print("export raw tail unreadable:", e, file=sys.stderr)

    existing = []
    if a.existing:
        for e in load_json_arg(a.existing):
            existing.append((str(e.get("date", "")).strip(), str(e.get("job", "")).strip(),
                             re.sub(r"\s+", " ", str(e.get("location", ""))).strip().lower()))

    def already_logged(date_mdy, job, location):
        loc = re.sub(r"\s+", " ", location).strip().lower()
        hits = [e for e in existing if e[0] == date_mdy and e[1] == str(job)]
        for e in hits:
            if e[2] == loc or e[2].startswith(loc[:25]) or loc.startswith(e[2][:25]):
                return "exact"
        near = [e for e in hits if difflib.SequenceMatcher(None, e[2], loc).ratio() >= 0.6]
        return f"a similar row for #{job} on {date_mdy} is already on the sheet ('{near[0][2][:40]}…')" if near else None

    daily, held, not_run, press_touch, orphans, skipped, warnings, review = [], [], [], {}, [], [], [], []

    def touch(printer, ms, kind):
        if not printer or ms is None: return
        if dstr(ms) != target: return
        p = press_touch.setdefault(printer, {"taps": [], "lunch_pauses": [], "runs": []})
        p["taps"].append((ms, kind))

    for rid, r in rows_state.items():
        if rid.startswith("jobday_") or rid.startswith("jobnote_"): continue
        ss, sd, jd = r.get("setupStart"), r.get("setupDone"), r.get("jobDone")
        if not (ss or sd or jd): continue
        m = meta.get(rid)
        if not m:
            orphans.append({"id": rid, "why": "taps on the worker but no such row in week.json", "setupStart": hmd(ss)})
            continue
        pz = pauses_of(r)
        job, cust, loc, qty = m.get("job"), m.get("customer", ""), m.get("location", ""), m.get("qty")
        colors = r.get("colors") if r.get("colors") is not None else m.get("colors")
        screens = r.get("screens") if r.get("screens") is not None else m.get("screens")
        setup_by = r.get("setupBy") or r.get("printer")
        crew = crew_str(r)
        print_by = r.get("printBy") or r.get("printer")
        ph = print_handoffs(r)
        finisher = (ph[-1].get("to") if ph else None) or print_by      # whoever tapped JOB DONE
        notes, flags, exclude = [], [], False

        # press-times touches (target day only)
        for who, stamp, kind in ((setup_by, ss, "setupStart"), (setup_by, sd, "setupDone"), (finisher, jd, "jobDone")):
            touch(who, stamp, kind)
        for h in (r.get("handoffs") or []):
            touch(h.get("from"), h.get("at"), "handoff"); touch(h.get("to"), h.get("at"), "handoff")
        for p in pz:
            if p["reason"].lower() == "lunch" and p["end"] and dstr(p["start"]) == target:
                (press_touch.setdefault(print_by or setup_by, {"taps": [], "lunch_pauses": [], "runs": []})
                 ["lunch_pauses"].append((p["start"], p["end"])))

        # unfinished?
        if not jd:
            last = max(x for x in (ss, sd) if x)
            if dstr(last) == target or (ss and dstr(ss) == target):
                open_p = [p for p in pz if not p["end"]]
                held.append({"id": rid, "job": job, "customer": cust, "location": loc, "crew": crew,
                             "setupStart": hmd(ss), "setupDone": hmd(sd),
                             "open_stop": (open_p[-1]["reason"] + " since " + hm(open_p[-1]["start"])) if open_p else None,
                             "abandoned": bool(ss and sd and (sd - ss) < 60000 and not jd)})
            continue

        if not ss or not sd:
            # jobDone without a full setup pair — cannot compute; hold with reason
            held.append({"id": rid, "job": job, "customer": cust, "location": loc, "crew": crew,
                         "setupStart": hmd(ss), "setupDone": hmd(sd), "open_stop": None,
                         "abandoned": False, "why": "JOB DONE tapped but setup stamps incomplete — needs a human look"})
            continue

        # ---- date the run
        ss_d, jd_d = dstr(ss), dstr(jd)
        if ss_d == jd_d:
            run_date = ss_d
        else:
            per = split_by_day(sd, jd, pz) if sd < jd else {}
            run_date = max(per, key=per.get) if per else jd_d
            notes.append(f"SPANNED: setup {hmd(ss)} → JOB DONE {hmd(jd)}; dated by where most print time fell")
        if run_date != target and jd_d != target:
            continue                                  # not tonight's business

        # ---- math
        setup_ms = (sd - ss) - closed_paused_ms(pz, ss, sd)
        print_ms = (jd - sd) - closed_paused_ms(pz, sd, jd)
        setup_min, print_min = mins(setup_ms), mins(print_ms)
        stops_min = mins(closed_paused_ms(pz, ss, jd, skip_overnight=True))
        overnight = [p for p in pz if p["end"] and (p["end"] - p["start"]) > OVERNIGHT_MS]
        open_stops = [p for p in pz if not p["end"]]
        pph = round(qty / (print_ms / 3600000)) if (qty and print_ms > 0) else None
        setup_per_scr = round(setup_min / screens, 1) if (screens and setup_ms > 0) else None

        parks = [p for p in pz if p["end"] and (p["end"] - p["start"]) > OVERNIGHT_MS]
        def crosses_night(a, b):
            return dstr(a) != dstr(b) and not any(p["start"] <= a + 1 and p["end"] >= b - 1 or (dstr(p["start"]) != dstr(p["end"])) for p in parks)
        if crosses_night(sd, jd):
            notes.append(f"spans overnight with no logged park ({hmd(sd)} → {hmd(jd)}) — print min / pcs-hr NOT separable, left blank")
            print_min, pph = None, None
        if crosses_night(ss, sd):
            notes.append(f"setup spans overnight ({hmd(ss)} → {hmd(sd)}) — setup min NOT separable, left blank")
            setup_min, setup_per_scr = None, None
        if (colors != m.get("colors")) or (screens != m.get("screens")):
            notes.append(f"card corrected to {colors}c/{screens}s (order said {m.get('colors')}c/{m.get('screens')}s)")
        for p in overnight:
            notes.append(f"parked {hmd(p['start'])}–{hmd(p['end'])} ({mins(p['end']-p['start'])}m '{p['reason'] or 'unlabeled'}') — subtracted from print, NOT in Stops")
        closed = [p for p in pz if p["end"] and p not in overnight]
        if closed:
            notes.append("stops: " + "; ".join(f"{mins(p['end']-p['start'])}m {p['reason'] or 'unlabeled'}{' *edited' if p['edited'] else ''}{' *added by hand' if p['added'] else ''}" for p in closed))
        if open_stops:
            notes.append(f"{len(open_stops)} stop(s) never resumed ({', '.join(p['reason'] or 'unlabeled' for p in open_stops)}) — not subtracted, print min may be overstated")
        if r.get("notes"): notes.append("printer note: " + str(r["notes"]).strip())
        if setup_handoffs(r):
            notes.append("setup handoff: " + ", ".join(f"{h.get('from') or '?'}→{h.get('to')} {hm(h.get('at'))}" for h in setup_handoffs(r)) + " (setup time shared)")
        segs = print_segments(r, qty, pz) if print_handoffs(r) else []
        if len(segs) > 1:
            if print_min is None and all(s["pieces"] is not None for s in segs):
                notes.append("HANDOFF " + " → ".join(f"{s['printer']} ({s['pieces']} pcs)" for s in segs) + " — per-printer rates not separable across the overnight")
            elif all(s["pieces"] is not None for s in segs):
                notes.append("HANDOFF split: " + "; ".join(f"{s['printer']} {s['min']}m {s['pieces']}pcs {s['pph']}/hr" for s in segs))
            else:
                notes.append("HANDOFF " + " → ".join(s["printer"] for s in segs) + " — pieces not counted at handoff, shop total only (do not credit per printer)")
        if rid in edits:
            notes.append("HAND-EDITED: " + ", ".join(edits[rid]["edits"]) + " (from iPad export audit log)")

        # ---- exclusions (always with a reason)
        if print_ms <= 0 and print_min is not None:
            exclude = True; flags.append("0-minute print — timing not real")
        if (jd - ss) < 120000:
            exclude = True; flags.append(f"all taps within 2 min ({hm(ss)}→{hm(jd)}) — tap-through artifact, not a timing")
        if not qty:
            exclude = True; flags.append("no qty on the row — pcs/hr impossible")
        if setup_ms < 60000 and not exclude:
            flags.append("setup tapped through (start and done within a minute) — setup not real; print kept")
            setup_min, setup_per_scr = None, None
        if r.get("notes") and re.search(r"unreliab|wrong|ignore|forgot|didn.t tap|mistake", str(r["notes"]), re.I):
            exclude = True; flags.append("printer note says the timing is unreliable")

        implausible = None
        if not exclude and pph is not None and print_min is not None:
            if pph > 450: implausible = f"{pph} pcs/hr is not physically plausible"
            elif print_min < 5 and (qty or 0) > 50: implausible = f"{qty} pcs in {print_min}m"
            elif pph < 8 and print_min > 60: implausible = f"{pph} pcs/hr over {print_min}m — looks like a card left open"
        if implausible:
            review.append({"id": rid, "job": job, "customer": cust, "location": loc, "crew": crew, "date": mdy(run_date),
                           "setup_min": setup_min, "print_min": print_min, "pph": pph, "qty": qty,
                           "span": f"{hmd(ss)} → {hmd(jd)}", "why": implausible + (" (setup tapped through: start=done)" if setup_ms < 60000 else "")})
            continue

        date_mdy = mdy(run_date)
        dup = already_logged(date_mdy, job, loc)
        row = {
            "id": rid, "date": date_mdy, "job": job, "customer": cust, "location": loc, "printer": crew,
            "colors": colors, "screens": screens, "qty": qty,
            "setup_min": None if exclude else setup_min, "print_min": None if exclude else print_min,
            "pph": None if exclude else pph, "setup_per_screen": None if exclude else setup_per_scr,
            "stops_min": stops_min, "setup_by": setup_by, "press": r.get("press"),
            "notes": "; ".join(flags + notes), "exclude": "x" if exclude else "",
            "span": f"{hmd(ss)} → {hmd(jd)}",
        }
        if rid in moved: row["notes"] = (row["notes"] + "; " if row["notes"] else "") + f"card moved on the board to {moved[rid]} tab (home day {home_day.get(rid)})"
        if dup == "exact":
            skipped.append({**row, "why": "already on Daily Data (date+job+location)"})
        elif dup:
            row["notes"] = (row["notes"] + "; " if row["notes"] else "") + f"DEDUPE WARNING: {dup} — check before trusting"
            warnings.append(f"#{job} {loc[:40]}: {dup}")
            daily.append(row)
        else:
            daily.append(row)
        if run_date == target:
            # busy intervals per printer (setup credited to setupBy, print to whoever ran each segment)
            press_touch.setdefault(setup_by or "?", {"taps": [], "lunch_pauses": [], "runs": []})["runs"].append((ss, sd))
            for s in (segs if len(segs) > 1 else [{"printer": print_by or setup_by or "?", "start": sd, "end": jd}]):
                press_touch.setdefault(s["printer"], {"taps": [], "lunch_pauses": [], "runs": []})["runs"].append((s["start"], s["end"]))

    # ---- NOT RUN on the target day's tab (honouring editor moves)
    for rid, m in meta.items():
        shown_day = moved.get(rid, home_day.get(rid))
        if shown_day != target: continue
        r = rows_state.get(rid) or {}
        if r.get("setupStart") or r.get("setupDone") or r.get("jobDone"): continue
        not_run.append({"id": rid, "job": m.get("job"), "customer": m.get("customer"), "location": m.get("location"),
                        "qty": m.get("qty"), "moved_here": rid in moved})

    # ---- Press Times
    press_rows = []
    day_clock = clocks.get(target) or {}
    for printer, p in sorted(press_touch.items()):
        taps = sorted(t for t, _ in p["taps"])
        if not taps and not (day_clock.get(printer) or {}).get("in"): continue
        c = day_clock.get(printer) or {}
        cin, cout = c.get("in"), c.get("out")
        lunches = [(l.get("start"), l.get("end")) for l in (c.get("lunches") or []) if l.get("start") and l.get("end")]
        ls = le = None; src = "not separable"; note = []
        if lunches:
            ls, le = lunches[0]; src = "crew clock"
        elif p["lunch_pauses"]:
            ls, le = p["lunch_pauses"][0]; src = "logged Lunch pause"
        else:
            # infer from the biggest 20–90 min idle gap between this printer's runs centred in 10:30–14:00
            runs = sorted(p["runs"]); best = None
            for (a1, b1), (a2, b2) in zip(runs, runs[1:]):
                gap = a2 - b1
                mid = to_dt(b1 + gap // 2)
                if 20 * 60000 <= gap <= 90 * 60000 and dt.time(10, 30) <= mid.time() <= dt.time(14, 0):
                    if not best or gap > best[1] - best[0]: best = (b1, a2)
            if best:
                ls, le = best; src = "inferred from gap"
            else:
                mid = [(b1, a2) for (a1, b1), (a2, b2) in zip(runs, runs[1:]) if (a2 - b1) >= 5 * 60000 and dt.time(10, 30) <= to_dt(b1).time() <= dt.time(14, 0)]
                if mid:
                    g = max(mid, key=lambda x: x[1] - x[0])
                    note.append(f"largest midday gap {hm(g[0])}–{hm(g[1])} ({mins(g[1]-g[0]):g}m) — {'too short' if (g[1]-g[0]) < 20*60000 else 'too long'} to call lunch, left unlabeled")
        lmin = mins(le - ls) if (ls and le) else None
        if lmin is not None and lmin > PAID_LUNCH: note.append(f"{lmin:g}m, {lmin - PAID_LUNCH:g} over paid {PAID_LUNCH}")
        if cin or cout: note.append(f"clock in {hm(cin)} / out {hm(cout)}")
        else: note.append("no clock-in/out on the iPad — day-length denominator missing")
        # other unlabeled gaps ≥30m between runs
        runs = sorted(p["runs"])
        already = " ".join(note)
        for (a1, b1), (a2, b2) in zip(runs, runs[1:]):
            if (a2 - b1) >= 30 * 60000 and not (ls and b1 <= ls and a2 >= le) and f"gap {hm(b1)}–" not in already:
                note.append(f"unlabeled gap {hm(b1)}–{hm(a2)} ({mins(a2-b1):g}m)")
        press_rows.append({"date": mdy(target), "printer": printer, "first_tap": hm(taps[0]) if taps else "",
                           "lunch_start": hm(ls) if ls else "", "lunch_end": hm(le) if le else "",
                           "lunch_min": lmin if lmin is not None else "", "last_tap": hm(taps[-1]) if taps else "",
                           "lunch_source": src, "notes": "; ".join(note)})

    # ---- sheet-shaped payloads (Zapier create_multiple_spreadsheet_rows)
    def sheet_daily(r):
        return {"COL$A": r["date"], "COL$B": str(r["job"]), "COL$C": r["customer"], "COL$D": r["location"],
                "COL$E": r["printer"], "COL$F": r["colors"], "COL$G": r["screens"], "COL$H": r["qty"],
                "COL$I": r["setup_min"], "COL$J": r["print_min"], "COL$K": r["pph"], "COL$L": r["setup_per_screen"],
                "COL$M": r["stops_min"], "COL$N": ("SOURCE: sync worker. " + r["notes"]).strip(), "COL$O": r["exclude"]}
    def sheet_press(r):
        return {"COL$A": r["date"], "COL$B": r["printer"], "COL$C": r["first_tap"], "COL$D": r["lunch_start"],
                "COL$E": r["lunch_end"], "COL$F": r["lunch_min"], "COL$G": r["last_tap"], "COL$H": r["lunch_source"],
                "COL$I": r["notes"]}

    report = {"target": target, "week": week, "worker_version": st.get("v"), "generated": dt.datetime.now(MT).isoformat(),
              "daily_rows": daily, "daily_rows_sheet": [sheet_daily(r) for r in daily],
              "press_rows": press_rows, "press_rows_sheet": [sheet_press(r) for r in press_rows],
              "held_back": held, "needs_review": review, "not_run": not_run, "dedupe_skipped": skipped, "dedupe_warnings": warnings,
              "orphans": orphans, "moved_cards": moved, "clock_in_seen": bool(any((c or {}).get("in") for c in day_clock.values())),
              "is_friday": is_friday}
    with open(a.out, "w") as f: json.dump(report, f, indent=1, default=str)

    # ---- human summary + DM draft
    dn = tdate.strftime("%a %-m/%-d")
    L = [f"Press stats — {dn}", ""]
    if daily:
        tot_pcs = sum((r["qty"] or 0) for r in daily if not r["exclude"])
        L.append(f"Rows added ({len(daily)}):")
        for r in daily:
            if r["exclude"]:
                L.append(f"• #{r['job']} {r['location'][:48]} — {r['printer']} — EXCLUDED (x): {r['notes'].split(';')[0]}")
            else:
                L.append(f"• #{r['job']} {r['location'][:48]} — {r['printer']} — setup {r['setup_min'] if r['setup_min'] is not None else '?'}m / print {r['print_min'] if r['print_min'] is not None else '?'}m / {r['pph'] or '?'} pcs/hr ({r['qty']} pcs, {r['colors']}c)"
                         + (f" — {r['notes'].split(';')[0]}" if r['notes'] else ""))
        L.append(f"Shop total (logged, non-excluded): {tot_pcs} pcs, {sum((r['print_min'] or 0) for r in daily if not r['exclude']):g}m print, {sum((r['setup_min'] or 0) for r in daily if not r['exclude'] and r['setup_min']):g}m setup")
    else:
        L.append("Rows added: none — no finished runs on the worker for this day.")
    L.append("")
    L.append("Lunch watch:")
    if press_rows:
        for p in press_rows:
            L.append(f"• {p['printer']}: taps {p['first_tap']}–{p['last_tap']}; lunch " +
                     (f"{p['lunch_start']}–{p['lunch_end']} ({p['lunch_min']:g}m, {p['lunch_source']})" if p['lunch_start'] else "not separable") +
                     (f" — {p['notes']}" if p['notes'] else ""))
    else:
        L.append("• no printer activity")
    if held:
        L.append(""); L.append("Unfinished — held back (no Daily Data row):")
        for h in held:
            L.append(f"• #{h['job']} {h['location'][:48]} — {h['crew']} — setup {h['setupStart']} → {h['setupDone']}, no JOB DONE"
                     + (f"; open stop: {h['open_stop']}" if h.get('open_stop') else "") + (" — looks abandoned (sub-minute card)" if h.get('abandoned') else "")
                     + (f" — {h['why']}" if h.get('why') else ""))
    if review:
        L.append(""); L.append("NEEDS A HUMAN CALL — not logged:")
        for h in review:
            L.append(f"• #{h['job']} {h['location'][:48]} — {h['crew']} — {h['span']} — {h['why']}")
    if not_run:
        L.append(""); L.append("NOT RUN (on this day's tab, no taps):")
        for n in not_run:
            L.append(f"• #{n['job']} {n['location'][:48]} ({n['qty']} pcs)" + (" — moved onto this tab by the editor" if n['moved_here'] else ""))
    if skipped:
        L.append(""); L.append("Skipped by dedupe (already on the sheet): " + ", ".join(f"#{s['job']} {s['location'][:30]} ({s['date']})" for s in skipped))
    if warnings:
        L.append(""); L.append("Dedupe warnings (logged, but check): " + " | ".join(warnings))
    if orphans:
        L.append(""); L.append("Orphan taps (row id not in week.json): " + ", ".join(o['id'] for o in orphans))
    if moved:
        L.append(""); L.append("Cards moved on the board this week: " + ", ".join(f"{k}→{v[5:]}" for k, v in moved.items()))
    L.append(""); L.append("Source: sync worker (both iPads merged). Hand-edit audit log only available if an iPad export was pasted." + (" Export used." if edits else ""))
    if is_friday:
        L.append(""); L.append("Week wrap-up needed: reconcile held-back rows, confirm excluded rows, commit data/" + week + ".json archive.")
    L.append("Sheet: https://docs.google.com/spreadsheets/d/15wFW0NtbytfNhYawnQa7rqQ64ovxZNtBYvEwh7YMbhw")
    print("\n".join(L))
    print(f"\n[report written to {a.out}: {len(daily)} daily rows ({sum(1 for r in daily if r['exclude'])} excluded), "
          f"{len(press_rows)} press-time rows, {len(held)} held, {len(review)} need review, {len(not_run)} not run, {len(skipped)} deduped]")

if __name__ == "__main__":
    main()
