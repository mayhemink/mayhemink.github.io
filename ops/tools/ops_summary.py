#!/usr/bin/env python3
"""
Mayhem Ops Summary — plain-text body for the private "Mayhem Ops Summary" Google Doc.
  ops.json (this run's build, or the live one when stale) + pull.json (amounts, notes, quotes)
  + state.json (Worker GET /state: check-offs + lane moves) + notes.json  ->  summary.txt

Usage: python3 ops_summary.py --run "6am rebuild" [--stale "Printavo unreachable at 6:00am"]
This file holds NO data and NO keys (it is committed to the public repo). Its OUTPUT has
dollar amounts — it goes only to the private Doc, never to the repo.
"""
import argparse, datetime, json, os, re
from zoneinfo import ZoneInfo

DENVER = ZoneInfo("America/Denver")
NOW = datetime.datetime.now(DENVER)
TODAY = NOW.date()
LANE_WORD = {"FILMS": "films", "WVN": "woven patches", "DTF": "DTF", "LASER": "leather/laser",
             "DIGI": "digitizing", "OUTSOURCE": "outsourced"}
SUFFIX_LANE = {"films": "FILMS", "woven": "WVN", "dtf": "DTF", "laser": "LASER", "digi": "DIGI"}
SENT_Q = re.compile(r"sent for approval|follow up", re.I)


def load(path, default=None):
    try:
        return json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def day(d):            # date -> "Tue 11/3"
    return f"{d:%a} {d.month}/{d.day}"


def stamp(iso):        # iso -> "Wed 10/28 1:29p"
    t = datetime.datetime.fromisoformat(iso).astimezone(DENVER)
    h = t.strftime("%-I:%M") + ("a" if t.hour < 12 else "p")
    return f"{day(t.date())} {h}"


def money(x):
    return f"${x:,.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="rebuild")
    ap.add_argument("--stale", default="")
    a = ap.parse_args()

    ops = load("ops.json")
    pull = load("pull.json", {"jobs": [], "quotes": []}) or {"jobs": [], "quotes": []}
    st = load("state.json", {}) or {}
    states, ov = st.get("states", {}), st.get("overrides", {})
    nj = load("notes.json", {}) or {}
    notes = nj.get("notes", nj) if isinstance(nj, dict) else {}
    notes = {k: v for k, v in notes.items() if isinstance(v, str)}
    pj = {j["v"]: j for j in pull.get("jobs", [])}

    L = ["MAYHEM OPS SUMMARY"]
    built = datetime.datetime.fromisoformat(ops["built"]).astimezone(DENVER)
    if a.stale:
        L.append(f"STALE — {a.stale}. Data below is from the last good build, {built:%a %b %-d, %-I:%M %p} MT.")
    L.append(f"Run: {NOW:%a %b %-d, %Y %-I:%M %p} MT ({a.run}) · board built {built:%a %-I:%M %p}")

    jobs, pay = ops["jobs"], ops.get("payment", [])
    end = TODAY + datetime.timedelta(days=7)
    late = [j for j in jobs if j.get("overdue")]
    soon = [j for j in jobs if not j.get("overdue") and TODAY <= datetime.date.fromisoformat(j["due"]) <= end]
    L.append(f"Open jobs: {len(jobs) + len(pay)} · late {len(late)} · due next 7 days {len(soon)}"
             f" · waiting for final payment {len(pay)}")

    def touched(j):
        if j.get("touched"):
            return f"touched {stamp(j['touched'])}"
        return "touched: bulk update only" if j.get("touchedBatch") else "touched: —"

    def note(v):
        return f" · note: {notes[v]}" if notes.get(v) else ""

    def open_prep(j):
        v, out = j["visualId"], []
        for tid in j.get("tasks", []):
            s = states.get(tid)
            lane = (ov.get(tid) or {}).get("lane") or SUFFIX_LANE.get(tid.split(":")[1], "FILMS")
            word = LANE_WORD.get(lane, lane.lower())
            if not s:
                out.append(word)
            elif s.get("how") == "sent":
                out.append(f"{word} (sent out, not back)")
        p = pj.get(v)
        if p and "COUNT-IN customer blanks" not in j.get("flags", []):
            garments = any((it.get("n") or "").strip().upper() not in ("", "SCREEN", "SETUP")
                           and (it.get("q") or 0) > 0 for g in p["groups"] for it in g["items"])
            if garments and not re.search(r"blanks\s*eta", p.get("note", ""), re.I):
                out.append("apparel (no BLANKS ETA)")
        if any("UNCLASSIFIED" in f for f in j.get("flags", [])):
            out.append("check job (unclassified)")
        return ("open: " + ", ".join(out)) if out else "prep done"

    L += ["", f"LATE ({len(late)})"]
    for j in late:
        L.append(f"#{j['visualId']} {j['customer'].strip()} · due {day(datetime.date.fromisoformat(j['due']))}"
                 f" · {touched(j)}{note(j['visualId'])}")
    if not late:
        L.append("Nothing late.")

    L += ["", f"DUE NEXT 7 DAYS (today through {day(end)}) — open prep"]
    by = {}
    for j in soon:
        by.setdefault(j["due"], []).append(j)
    for d in sorted(by):
        dd = datetime.date.fromisoformat(d)
        L.append(day(dd) + (" (today)" if dd == TODAY else ""))
        for j in by[d]:
            L.append(f"  #{j['visualId']} {j['customer'].strip()} · {(j['nickname'] or '').strip() or '—'}"
                     f" · {open_prep(j)}{note(j['visualId'])}")
    if not soon:
        L.append("Nothing due in the next 7 days.")

    owed_total = sum((pj.get(j["visualId"]) or {}).get("owed") or 0 for j in pay)
    L += ["", f"WAITING FOR FINAL PAYMENT ({len(pay)} · {money(owed_total)} still owed)"]
    for j in pay:
        p = pj.get(j["visualId"]) or {}
        tot, owed = p.get("total"), p.get("owed")
        amt = f"total {money(tot)}" if tot is not None else "total ?"
        if owed is None:
            tail = "amount owed unknown"
        elif owed <= 0.005:
            tail = "paid in full, status not moved"
        else:
            tail = f"owes {money(owed)}"
        L.append(f"#{j['visualId']} {j['customer'].strip()} · {amt} · {tail}{note(j['visualId'])}")
    if not pay:
        L.append("Nobody.")

    qs = pull.get("quotes", [])
    sent = [q for q in qs if SENT_Q.search(q.get("status", "")) and q.get("approval") not in ("approved", "declined")]
    sent.sort(key=lambda q: q.get("created", ""), reverse=True)
    age_of = lambda q: (TODAY - datetime.date.fromisoformat(q["created"][:10])).days
    # same 14-day cutoff as the board's Pending Quotes tab; money down never hides
    old = [q for q in sent if age_of(q) > 14 and not q.get("paid")]
    sent = [q for q in sent if q not in old]
    unsent = [q for q in qs if (q.get("status") or "").strip().lower() == "quote" and age_of(q) <= 14]
    L += ["", f"QUOTES SENT, NOT APPROVED ({len(sent)} · {money(sum(q.get('total') or 0 for q in sent))}) — newest first"]
    for q in sent:
        age = (TODAY - datetime.date.fromisoformat(q["created"][:10])).days
        paid = f" · {money(q['paid'])} paid" if q.get("paid") else ""
        L.append(f"#{q['v']} {q['customer'].strip()} · {money(q.get('total') or 0)}{paid} · waiting {age}d")
    if old:
        L.append(f"Sent over 14 days ago, no money down: {len(old)} (" + ", ".join("#" + q["v"] for q in old) + ")")
    if unsent:
        L.append(f"Not sent yet: {len(unsent)} (" + ", ".join("#" + q["v"] for q in unsent) + ")")
    for q in qs:
        if q.get("approval") == "declined":
            L.append(f"Admin: #{q['v']} {q['customer'].strip()} DECLINED — move it in Printavo")
        elif q.get("approval") == "approved":
            L.append(f"Admin: #{q['v']} {q['customer'].strip()} APPROVED — move the status in Printavo")

    every = {j["visualId"]: j for j in jobs + pay}
    ask = [(v, n) for v, n in notes.items() if re.search(r"ask joe", n, re.I)]
    L += ["", f"ASK JOE ({len(ask)})"]
    for v, n in ask:
        j = every.get(v)
        due = f" · due {day(datetime.date.fromisoformat(j['due']))}" if j else " · not on the board"
        L.append(f"#{v} {(j or {}).get('customer', '').strip()}{due} · {n}")
    if not ask:
        L.append("Nothing.")

    open("summary.txt", "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
