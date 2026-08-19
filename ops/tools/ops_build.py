#!/usr/bin/env python3
"""
Mayhem Ops daily builder.
  pull.json (raw Printavo pull, written by the session) + history.json (repeat index)
    -> ops.json          PUBLIC board data (no contacts, no pricing)
    -> pending.json      PRIVATE pending-approval payload (POSTed to the Worker, never committed)
    -> email.md          morning email body

Lanes: FILMS (Joe) · WVN order · DTF print/order · LASER make (leather/engrave/UV) · DIGI (embroidery digitizing)
Task IDs are stable: "<visualId>:<lane>" — KV state keys on them. Never add volatile text.
"""
import json, re, datetime, sys, os
from zoneinfo import ZoneInfo
from repeat_match import match, norm_customer

DENVER = ZoneInfo("America/Denver")
TODAY = datetime.datetime.now(DENVER).date()

# ---- tuning (Kolton adjusts here) --------------------------------------------
LEAD = {  # lane: (amber_days, red_days) before due date
    "WVN":   (14, 7),
    "LASER": (10, 5),
    "DTF":   (7, 4),
    "DIGI":  (5, 3),
    "FILMS": (3, 1),
}
EXCLUDE_STATUS = {
    "145340",  # Order Complete
    "142599",  # Ready for Pickup
    "142600",  # Shipped
    "197389",  # Ask for review
    "215155",  # Brandmakers complete
    "542109",  # Creation Cartel shipped
    "212737", "326764",  # Dead quote / Dont schedule
    "248476",  # Request final payment  (produced; collections territory, Tuesday run)
    "542108",  # Creation Cartel ready-needs-payment  (shipping skill territory)
}
ON_HOLD = {"142603"}

RX = {
    "woven":   re.compile(r"(?<!non[- ])woven", re.I),
    "leather": re.compile(r"leather", re.I),
    "dtf":     re.compile(r"\bdtf\b|digital print|digital.printed|printed transfer", re.I),
    "laser":   re.compile(r"laser|engrav|tumbler|golf ball|poker chip|stress ball", re.I),
    "digitiz": re.compile(r"digitiz", re.I),
    "custsup": re.compile(r"customer.suppl|cust.suppl|supplied blank", re.I),
    "prior":   re.compile(r"screens? already burned|same (?:art/)?screens", re.I),
    "refjob":  re.compile(r"#(\d{4})"),
    # imprint DETAILS that imply screen print even when typeOfWork+matrix are null
    # (constant in live data: Lattitude/Brand Makers/manual quotes)
    "colordet": re.compile(r"\d+\s*-\s*color|multi-?color|\d+c\b", re.I),
    "spnote":  re.compile(r"\bscreens?\b|press check", re.I),
    "embtxt":  re.compile(r"embroid|embrid", re.I),   # 'embridery' typo appears in live data
    "art":     re.compile(r"artwork|graphic design", re.I),
    # dedicated patch PRODUCT lines (vs garments that merely mention the patch)
    "patchline": re.compile(r"patch(es)?\s*-|^\s*(leather|woven)\s+patch", re.I),
}


def denver_date(iso):
    try:
        return datetime.datetime.fromisoformat(iso).astimezone(DENVER).date()
    except ValueError:
        return datetime.date.fromisoformat(iso[:10])


def blob(job):
    parts = [job["nick"], job.get("note", "")]
    for g in job["groups"]:
        for it in g["items"]:
            parts += [it.get("n", ""), it.get("d", "")]
        for im in g["imprints"]:
            parts.append(im.get("d", ""))
    return " ".join(p for p in parts if p)


def urgency(lane, due):
    amber, red = LEAD[lane]
    days = (due - TODAY).days
    if days <= red:
        return "red", days
    if days <= amber:
        return "amber", days
    return "ok", days


def qty_matching(job, rx):
    """Sum quantities for a service. Prefer dedicated patch PRODUCT lines
    ("Leather Patches - Leather Patches") over garment lines that merely
    mention the patch ("Richardson Trucker LEATHER PATCH") — counting both
    double-counts (6380 proved it: 85 patches read as 170)."""
    product, any_match = 0, 0
    for g in job["groups"]:
        for it in g["items"]:
            txt = it.get("n", "") + " " + it.get("d", "")
            if rx.search(txt):
                any_match += it.get("q", 0) or 0
                if RX["patchline"].search(txt):
                    product += it.get("q", 0) or 0
    return product or any_match


def build():
    pull = json.load(open("pull.json"))
    hist = json.load(open("history.json"))
    try:
        prev = json.load(open("ops-prev.json"))
        prev_ids = {j["visualId"] for j in prev.get("jobs", [])}
    except FileNotFoundError:
        prev_ids = None  # first run: nobody is "new"

    jobs_out, lanes = [], {"FILMS": [], "WVN": [], "DTF": [], "LASER": [], "DIGI": []}

    for job in pull["jobs"]:
        if job["statusId"] in EXCLUDE_STATUS:
            continue
        due = denver_date(job["due"])
        on_hold = job["statusId"] in ON_HOLD
        b = blob(job)
        cust = job["customer"].strip()
        services, tasks, flags = set(), [], []

        # ---- classify imprints ------------------------------------------------
        # Heat-press-only jobs with N-color imprints = printed TRANSFERS, not screens
        # (Munson 6354: "2-color front logo" heat pressed -> Kolton prints DTF for it)
        hp_only = job["statusId"] == "145027"
        sp_locs, emb_locs = [], []
        for g in job["groups"]:
            gtxt = " ".join(it.get("n", "") + " " + it.get("d", "") for it in g["items"])
            for im in g["imprints"]:
                t, d, c = im.get("t"), (im.get("d") or "").strip(), im.get("c")
                col = (c or "").replace(" color", "c")
                looks_colored = (c and "color" in c) or RX["colordet"].search(d)
                if t == "Screen Printing" or (t is None and looks_colored):
                    if hp_only:
                        services.add("DTF")   # colored art on a heat-press job = transfers to print
                    else:
                        sp_locs.append(f"{d or 'location'} ({col})" if col else (d or "location"))
                        services.add("SP")
                elif t == "Embroidery":
                    emb_locs.append(d or "location")
                    services.add("EMB")
                elif t == "Heat Press":
                    services.add("HP")
            # keyword services from group text
            if RX["woven"].search(gtxt): services.add("WVN")
            if RX["leather"].search(gtxt): services.add("LTHR")
            if RX["dtf"].search(gtxt): services.add("DTF")
            if RX["laser"].search(gtxt): services.add("LASER")
            if RX["embtxt"].search(gtxt): services.add("EMB")
            # "SCREEN" setup line items (Lattitude convention) prove screen print
            if not hp_only and any((it.get("n") or "").strip().upper() in ("SCREEN", "SETUP")
                                   for it in g["items"]):
                services.add("SP")
            # "PRINT: Left chest logo" convention in item descriptions (UMA 6415 style)
            if not hp_only and re.search(r"\bPRINT:", gtxt, re.I):
                services.add("SP")
        # status/note-level classification when imprints were null
        if job["status"] == "Embroidery Only" and "EMB" not in services:
            services.add("EMB")
        if not hp_only and "SP" not in services and RX["spnote"].search(job.get("note", "")):
            services.add("SP")   # "2 screens" / "press check" / "screens already burned"
        if RX["dtf"].search(b): services.add("DTF")
        if RX["digitiz"].search(b): services.add("DIGI")
        if RX["custsup"].search(b): flags.append("COUNT-IN customer blanks")
        if on_hold: flags.append("ON HOLD")
        if job["statusId"] == "549932": flags.append("Straight Mayhem")
        if not services and RX["art"].search(b):
            services.add("ART"); flags.append("ART — Joe design time")

        # ---- FILMS row --------------------------------------------------------
        if "SP" in services:
            prior_m = RX["prior"].search(job.get("note", ""))
            ref = None
            if prior_m:
                refj = RX["refjob"].search(job.get("note", ""))
                ref = refj.group(1) if refj else None
            conf, pj, shared = match(hist, cust, job["nick"],
                                     [im.get("d", "") for g in job["groups"] for im in g["imprints"]],
                                     ["SP"], job["v"])
            u, days = urgency("FILMS", due)
            row = {
                "id": f'{job["v"]}:films', "v": job["v"], "job": job["nick"] or cust,
                "cust": cust, "due": str(due), "days": days,
                "urgency": "ok" if on_hold else u,
                "locs": sp_locs[:6],
                "actions": ["onfile", "made"],
            }
            if prior_m:
                row["repeat"] = {"conf": "standing", "prior": ref,
                                 "why": "screens already standing" + (f" (#{ref})" if ref else "")}
            elif conf == "strong":
                row["repeat"] = {"conf": "strong", "prior": pj["v"], "why": ", ".join(shared[:4])}
            elif conf == "weak":
                row["repeat"] = {"conf": "weak", "prior": pj["v"] if pj else None,
                                 "why": "repeat customer"}
            lanes["FILMS"].append(row)
            tasks.append(row["id"])

        # ---- WVN order row ----------------------------------------------------
        if "WVN" in services:
            q = qty_matching(job, RX["woven"])
            u, days = urgency("WVN", due)
            lanes["WVN"].append({"id": f'{job["v"]}:woven', "v": job["v"], "job": job["nick"] or cust,
                                 "cust": cust, "due": str(due), "days": days,
                                 "urgency": "ok" if on_hold else u,
                                 "qty": q or None, "actions": ["ordered"]})
            tasks.append(f'{job["v"]}:woven')

        # ---- LASER row (leather patches made in-house + engraving + UV) -------
        if "LTHR" in services or "LASER" in services:
            q = qty_matching(job, RX["leather"]) or qty_matching(job, RX["laser"])
            u, days = urgency("LASER", due)
            what = "leather patches" if "LTHR" in services else "laser/UV job"
            lanes["LASER"].append({"id": f'{job["v"]}:laser', "v": job["v"], "job": job["nick"] or cust,
                                   "cust": cust, "due": str(due), "days": days,
                                   "urgency": "ok" if on_hold else u,
                                   "what": what, "qty": q or None, "actions": ["made"]})
            tasks.append(f'{job["v"]}:laser')

        # ---- DTF row ----------------------------------------------------------
        if "DTF" in services:
            u, days = urgency("DTF", due)
            lanes["DTF"].append({"id": f'{job["v"]}:dtf', "v": job["v"], "job": job["nick"] or cust,
                                 "cust": cust, "due": str(due), "days": days,
                                 "urgency": "ok" if on_hold else u,
                                 "actions": ["printed", "ordered"]})
            tasks.append(f'{job["v"]}:dtf')

        # ---- DIGI row ---------------------------------------------------------
        if "EMB" in services or "DIGI" in services:
            c = hist["customers"].get(norm_customer(cust))
            prior_emb = bool(c and "EMB" in c.get("svc", []))
            u, days = urgency("DIGI", due)
            lanes["DIGI"].append({"id": f'{job["v"]}:digi', "v": job["v"], "job": job["nick"] or cust,
                                  "cust": cust, "due": str(due), "days": days,
                                  "urgency": "ok" if on_hold else u,
                                  "locs": emb_locs[:4],
                                  "hint": "prior emb jobs — DST likely on file" if prior_emb else None,
                                  "actions": ["onfile", "sent", "received"]})
            tasks.append(f'{job["v"]}:digi')

        if not services:
            flags.append("UNCLASSIFIED — open job and check")

        # mockup thumb: local mocks/<v>_1.jpg (this run) or already-published one
        img = None
        if os.path.exists(f'mocks/{job["v"]}_1.jpg'):
            img = f'/ops/data/mocks/{job["v"]}_1.jpg'
        else:
            try:
                import urllib.request
                rq = urllib.request.Request(
                    f'https://mayhemink.github.io/ops/data/mocks/{job["v"]}_1.jpg', method="HEAD")
                with urllib.request.urlopen(rq, timeout=8) as resp:
                    if resp.status == 200:
                        img = f'/ops/data/mocks/{job["v"]}_1.jpg'
            except Exception:
                pass

        jobs_out.append({
            "id": job["id"], "visualId": job["v"], "img": img,
            "url": f'https://www.printavo.com/invoices/{job["id"]}',
            "nickname": job["nick"], "customer": cust,
            "due": str(due), "days": (due - TODAY).days,
            "overdue": due < TODAY and not on_hold, "onHold": on_hold,
            "status": job["status"], "qty": job["qty"],
            "services": sorted(services), "tasks": tasks, "flags": flags,
            "new": (prev_ids is not None and job["v"] not in prev_ids),
        })

    for lane in lanes.values():
        lane.sort(key=lambda r: r["due"])
    jobs_out.sort(key=lambda j: (not j["overdue"], j["due"]))

    # ---- must-do strip ---------------------------------------------------------
    RANK = {"red": 0, "amber": 1}
    must = [r | {"lane": ln} for ln, rows in lanes.items() for r in rows
            if r["urgency"] == "red" or (r["urgency"] == "amber" and r["days"] <= 3)]
    must.sort(key=lambda r: (RANK.get(r["urgency"], 2), r["days"]))

    # ---- pending payload (PRIVATE) --------------------------------------------
    # Quotes sent >14 days ago are dropped (stale — probably already resolved),
    # EXCEPT when money is down or there's an admin flag: those are never hidden.
    pend = []
    for q in pull["quotes"]:
        age = (TODAY - datetime.date.fromisoformat(q["created"])).days
        if age > 14 and not (q["paid"] > 0 or q.get("approval") in ("approved", "declined")):
            continue
        entry = {**q, "url": f'https://www.printavo.com/quotes/{q["id"]}', "ageDays": age}
        if q.get("approval") == "approved":
            entry["adminFlag"] = "Customer APPROVED — move the status in Printavo"
        elif q.get("approval") == "declined":
            entry["adminFlag"] = f'DECLINED by {q.get("approvalBy","customer")} {q.get("approvalAt","")} — still on calendar'
        else:
            first = (q["contact"].split() or ["there"])[0].title()
            paid_line = (f" We've got your ${q['paid']:.0f} deposit on file, so the second you approve "
                         "we'll get you on the schedule.") if q["paid"] else \
                        " Once you give it the thumbs up we'll get you on the schedule."
            entry["draft"] = {
                "subject": f'Mayhem Ink — quote #{q["v"]}' + (f' ({q["nick"].strip()})' if q["nick"].strip() else ""),
                "body": (f"Hey {first},\n\nJust checking in on the quote we sent over"
                         f"{' for ' + q['nick'].strip() if q['nick'].strip() else ''} (#{q['v']})."
                         f"{paid_line} If anything on it needs tweaking, tell me and I'll fix it same day."
                         f"\n\nThanks!\nJoe\nMayhem Ink Screen Printing"),
            }
        pend.append(entry)
    pend.sort(key=lambda e: (-(e["paid"] > 0), -e["ageDays"]))

    ops = {
        "built": datetime.datetime.now(DENVER).isoformat(timespec="seconds"),
        "window": pull["window"], "jobs": jobs_out, "lanes": lanes, "must": must,
        "pendingCount": len(pend),
        "pendingPaidCount": sum(1 for e in pend if e["paid"] > 0 and "adminFlag" not in e),
    }
    json.dump(ops, open("ops.json", "w"), separators=(",", ":"))
    json.dump({"built": ops["built"], "quotes": pend}, open("pending.json", "w"), separators=(",", ":"))

    # PII guard on the PUBLIC file only
    raw = open("ops.json").read()
    if re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", raw) or re.search(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b", raw) \
       or re.search(r"\$\s?\d", raw):
        print("REFUSING: PII/pricing leaked into ops.json", file=sys.stderr); sys.exit(1)

    # ---- morning email ---------------------------------------------------------
    L = [f"**Mayhem Ops — {TODAY.strftime('%a %b %-d')}**", ""]
    if must:
        L.append(f"Must-do today ({len(must)}):")
        for r in must[:8]:
            L.append(f"- [{r['lane']}] #{r['v']} {r['job'].strip()} — {r['cust'].strip()} (due {r['due']}, {r['days']}d)")
    else:
        L.append("Nothing burns today — you're ahead.")
    L.append("")
    L.append("Lanes: " + " · ".join(f"{ln} {len(rows)}" for ln, rows in lanes.items() if rows))
    new = [j for j in jobs_out if j["new"]]
    if new:
        L.append("New since yesterday: " + ", ".join(f'#{j["visualId"]} {j["nickname"] or j["customer"]}' for j in new))
    unc = [j for j in jobs_out if any("UNCLASSIFIED" in f for f in j["flags"])]
    if unc:
        L.append("Check me (unclassified): " + ", ".join(f'#{j["visualId"]} {j["nickname"] or j["customer"]}' for j in unc))
    paid_pend = [e for e in pend if e["paid"] > 0 and "adminFlag" not in e]
    L.append("")
    L.append(f"Pending approval: {len(pend)} quotes, {len(paid_pend)} with money down." +
             (f" Oldest poke: #{pend[0]['v']} (sent {pend[0]['ageDays']}d ago)." if pend else ""))
    admin = [e for e in pend if "adminFlag" in e]
    for e in admin:
        L.append(f"- ADMIN: #{e['v']} {e['customer'].strip()} — {e['adminFlag']}")
    L.append("")
    L.append("Board: https://mayhemink.github.io/ops/")
    open("email.md", "w").write("\n".join(L))

    # ---- console review --------------------------------------------------------
    print(f"jobs on board: {len(jobs_out)}   (excluded finished/pickup/collections statuses)")
    for ln, rows in lanes.items():
        print(f"\n{ln} lane ({len(rows)}):")
        for r in rows:
            rep = ""
            if r.get("repeat"):
                rep = f"  [REPEAT-{r['repeat']['conf']}" + (f" #{r['repeat']['prior']}" if r['repeat'].get('prior') else "") + f": {r['repeat']['why']}]"
            if r.get("hint"): rep += f"  [{r['hint']}]"
            extra = f" qty={r['qty']}" if r.get("qty") else ""
            print(f"  {r['urgency']:5s} {r['days']:+3d}d  #{r['v']} {r['job'].strip()[:44]:46s}{extra}{rep}")
    print(f"\nMUST-DO ({len(must)}):")
    for r in must:
        print(f"  {r['urgency']:5s} [{r['lane']}] #{r['v']} {r['job'].strip()[:50]} ({r['days']}d)")
    print(f"\npending: {len(pend)} ({len(paid_pend)} paid, {len(admin)} admin flags)")
    for j in jobs_out:
        if j["flags"]:
            print(f"  flag #{j['visualId']}: {j['flags']}")


if __name__ == "__main__":
    build()
