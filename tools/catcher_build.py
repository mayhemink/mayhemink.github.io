#!/usr/bin/env python3
"""Build data/catcher.json (the Catcher app's size manifest) from the weekly board.

Runs as part of the SAME build/push as data/week.json so the two never drift.

  python3 catcher_build.py --week week.json --prev catcher.json \
      --map catcher-map.json --printavo printavo_dir --out out_dir

Inputs
  --week      the week.json about to be published
  --prev      the currently published data/catcher.json (omit only for a first build)
  --map       the currently published data/catcher-map.json plus any NEW row entries
  --printavo  directory of raw Printavo GraphQL responses, one or more invoices per file,
              each shaped {"invoice": {...}} or {"data": {...}} or {"i1": {...}, ...}
              using the query in the timecards skill (lineItemGroups > imprints + lineItems
              > sizes). Only needed for rows that are new, changed, or being re-verified.
Outputs (in --out)
  catcher.json              -> publish to data/catcher.json
  catcher-map.json          -> publish to data/catcher-map.json
  catcher-archive/<wk>.json -> only on a week rollover; publish alongside

Map entry, keyed by the week.json row id:
  {"invoice":"24452792","imprints":["26716423"],"groups":["52600821"],
   "lineItems":["108437444"],        # optional: garment subset; default = every garment line
   "label":"Front · black ink",      # BIG heading on the card
   "noteExtra":"...",                # optional: catcher-only line added after the board note
   "artworkLabel":"...",             # optional
   "manual":[{"key":"women-teal","style":"DM108L","color":"Heather Teal","sizes":{"XS":4,"S":6}}],  # Kolton-supplied sizes when Printavo has none; needs no --printavo data
   "sizes":["size_s","size_m","size_l"],  # optional: only these sizes of the mapped line items (size-run split across rows)
   "youth":["<lineItemId>"],         # optional: youth garment keyed with adult sizes in Printavo -> show YS, YM...
   "style":{"<lineItemId>":"text"}, "color":{"<lineItemId>":"text"}}   # optional display fixes

Rules (do not loosen):
  * A row's sizes must sum EXACTLY to the board qty or the card is published LOCKED.
  * Unchanged physical runs keep their old card byte-for-byte (ids AND order) so counts
    already saved on the iPads never lock.
  * A changed run keeps its runId; the old definition goes to archivedCards. The app then
    locks any saved count for office review - counts are never silently re-attached.
  * No prices, no contact info. Invoice/line-item ids only.
"""
import argparse, copy, datetime, glob, json, os, re, sys

SIZE_ORDER = ["size_yxs", "size_ys", "size_ym", "size_yl", "size_yxl",
              "size_6m", "size_12m", "size_18m", "size_24m", "size_2t", "size_3t", "size_4t", "size_5t",
              "size_xxs", "size_xs", "size_s", "size_m", "size_l", "size_xl",
              "size_2xl", "size_3xl", "size_4xl", "size_5xl", "size_6xl", "size_other"]
NON_GARMENT = re.compile(r"\b(screen|set-?up|setup|fee|stencil|sign|shipping|freight|rush|digitiz|art charge|film)\b", re.I)
PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
PENDING_MSG = "Sizes not verified yet. Ask the office to refresh this location before counting."


def label_for(size):
    return size.replace("size_", "").upper()


def signature(row):
    return json.dumps([row["job"], row["location"], row["qty"]], ensure_ascii=False, separators=(",", ":"))


def fingerprint(card):
    return json.dumps([card["runId"], card["expected"],
                       [[g["id"], [[s["id"], s["expected"]] for s in g["sizes"]]] for g in card["groups"]]])


def cellset(card):
    return sorted((s["id"], s["expected"]) for g in card["groups"] for s in g["sizes"])


def load_invoices(folder):
    out = {}

    def walk(node):
        if isinstance(node, dict):
            if "lineItemGroups" in node and "id" in node:
                out[str(node["id"])] = node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))) if folder else []:
        walk(json.load(open(path)))
    return out


def day_label(day, label=None):
    if label:
        return label
    d = datetime.date.fromisoformat(day)
    return "%s · %s %d" % (d.strftime("%A"), d.strftime("%b"), d.day)


def guess_label(location):
    head = re.split(r"\s[—–-]\s", location)[0].lower()
    for pat, lab in [(r"\blc\b|left chest", "Left chest"), (r"\brc\b|right chest", "Right chest"),
                     (r"full front|\bff\b", "Full front"), (r"full back|\bfb\b", "Full back"),
                     (r"yoke", "Back yoke"), (r"back", "Back"),
                     (r"left sleeve|l sleeve", "Left sleeve"), (r"right sleeve|r sleeve", "Right sleeve"),
                     (r"sleeve", "Sleeve"), (r"front|center chest", "Front")]:
        if re.search(pat, head):
            return lab
    return re.split(r"\s[—–-]\s", location)[0][:40]


def derive_map(card):
    """Recover a map entry from a card built before the map file existed."""
    parts = card["runId"].split(":")
    if len(parts) < 3 or not card.get("groups"):
        return None
    imprints = [] if parts[1] == "no-imprint" else parts[1].split("+")
    groups = [k.split(":")[1] for k in card.get("batchKeys", []) if ":" in k]
    items = [i for g in card["groups"] for i in g.get("lineItemIds", [])]
    return {"invoice": parts[0], "imprints": imprints, "groups": groups, "lineItems": items,
            "label": card.get("locationLabel")}


def build_groups(spec, invoice, problems):
    wanted = [str(g) for g in (spec.get("groups") or ([spec["group"]] if spec.get("group") else []))]
    subset = [str(i) for i in spec.get("lineItems") or []]
    youth = [str(i) for i in spec.get("youth") or []]
    only_sizes = list(spec.get("sizes") or [])   # size-run split of one line item across rows
    nodes = {str(g["id"]): g for g in invoice["lineItemGroups"]["nodes"]}
    if invoice["lineItemGroups"].get("pageInfo", {}).get("hasNextPage"):
        problems.append("invoice has more line item groups than were fetched - page past them")
    groups, seen_imprints = [], set()
    for gid in wanted:
        lig = nodes.get(gid)
        if not lig:
            problems.append("line item group %s not found on invoice" % gid)
            continue
        seen_imprints |= {str(i["id"]) for i in lig.get("imprints", {}).get("nodes", [])}
        if lig["lineItems"].get("pageInfo", {}).get("hasNextPage"):
            problems.append("group %s has more than one page of line items - fetch the rest" % gid)
        merged = {}
        for li in sorted(lig["lineItems"]["nodes"], key=lambda x: x.get("position") or 0):
            lid = str(li["id"])
            if subset and lid not in subset:
                continue
            if not subset and not li.get("itemNumber") and not li.get("color") and NON_GARMENT.search(li.get("description") or ""):
                continue
            style = ((spec.get("style") or {}).get(lid) or li.get("itemNumber") or li.get("description") or "Style not on the order").strip()
            color = ((spec.get("color") or {}).get(lid) or li.get("color") or "Color not on the order").strip()
            if li.get("items") is not None and not only_sizes and sum(x.get("count") or 0 for x in li.get("sizes") or []) != li["items"]:
                problems.append("line item %s: sizes add to %d but Printavo says %d items (transcription or order error)"
                                % (lid, sum(x.get("count") or 0 for x in li.get("sizes") or []), li["items"]))
            g = merged.setdefault((style, color), {"id": "%s:%s" % (gid, lid), "style": style, "color": color,
                                                   "cells": {}, "lineItemIds": []})
            g["lineItemIds"].append(lid)
            for s in li.get("sizes") or []:
                if s.get("count") and (not only_sizes or s["size"] in only_sizes):
                    g["cells"].setdefault(s["size"], []).append({"lineItemId": lid, "size": s["size"], "expected": s["count"]})
        for g in merged.values():
            order = sorted(g["cells"], key=lambda k: SIZE_ORDER.index(k) if k in SIZE_ORDER else 98)
            kid = "Y" if any(i in youth for i in g["lineItemIds"]) else ""
            sizes = [{"id": "%s:%s" % (g["id"], label_for(k)), "label": (kid if not label_for(k).startswith("Y") and k != "size_other" else "") + label_for(k),
                      "expected": sum(x["expected"] for x in g["cells"][k]), "sources": g["cells"][k]} for k in order]
            if sizes:
                groups.append({"id": g["id"], "style": g["style"], "color": g["color"], "sizes": sizes,
                               "lineItemIds": g["lineItemIds"]})
    for imp in spec.get("imprints") or []:
        if str(imp) not in seen_imprints:
            problems.append("imprint %s is not on the mapped group(s) any more" % imp)
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", required=True)
    ap.add_argument("--prev")
    ap.add_argument("--map")
    ap.add_argument("--printavo")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-pending", action="store_true",
                    help="publish locked placeholder cards for rows that could not be verified")
    a = ap.parse_args()

    week = json.load(open(a.week))
    prev = json.load(open(a.prev)) if a.prev else {"week": None, "cards": [], "archivedCards": []}
    specs = json.load(open(a.map)).get("rows", {}) if a.map and os.path.exists(a.map) else {}
    invoices = load_invoices(a.printavo)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rollover = prev["week"] is not None and prev["week"] != week["week"]
    prev_cards = {c["id"]: c for c in prev["cards"]}
    archived = [] if rollover else list(prev.get("archivedCards", []))

    cards, new_map, report, pending = [], {}, [], []
    seen = set()
    for d in week["days"]:
        for row in d["rows"]:
            rid = row["id"]
            if rid in seen:
                sys.exit("DUPLICATE row id in week.json: " + rid)
            seen.add(rid)
            old = prev_cards.get(rid)
            spec = specs.get(rid) or (derive_map(old) if old else None)
            sig = signature(row)
            note = " ".join(x for x in [row.get("note", ""), (row.get("alert") or "").replace("\n", " ")] if x)
            if (spec or {}).get("noteExtra"):
                note = (note + " " + spec["noteExtra"]).strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["day"]):
                note = (note + " HOLD — Extra card: confirm with Kolton before running.").strip()
            image = week.get("img", {}).get(row.get("img")) if row.get("img") else None
            inv = invoices.get(str(spec["invoice"])) if spec else None
            card, status, problems = None, "", []

            if spec and spec.get("manual"):
                groups = [{"id": "manual:%s:%s" % (rid, m["key"]), "style": m["style"], "color": m["color"],
                           "sizes": [{"id": "manual:%s:%s:%s" % (rid, m["key"], lab), "label": lab, "expected": n} for lab, n in m["sizes"].items()],
                           "source": m.get("source", "Kolton-supplied quantities; not mapped to Printavo line items")} for m in spec["manual"]]
                total = sum(s["expected"] for g in groups for s in g["sizes"])
                if total != row["qty"]:
                    problems.append("manual sizes add to %d but the board says %d" % (total, row["qty"]))
                else:
                    card = {"id": rid, "job": row["job"], "groups": groups, "expected": row["qty"],
                            "runId": old["runId"] if old and not old.get("pending") else "manual:%s:%s" % (spec.get("invoice", "x"), rid),
                            "batchKeys": list(spec.get("batchKeys") or [])}
                    if old and not old.get("pending") and cellset(old) == cellset(card):
                        card["groups"] = old["groups"]; status = "manual, unchanged"
                    else:
                        status = "manual (Kolton-supplied sizes)"
            elif spec and inv:
                groups = build_groups(spec, inv, problems)
                total = sum(s["expected"] for g in groups for s in g["sizes"])
                if total != row["qty"]:
                    problems.append("sizes add to %d but the board says %d" % (total, row["qty"]))
                if not problems:
                    card = {"id": rid, "job": row["job"], "groups": groups, "expected": row["qty"],
                            "runId": old["runId"] if old and not old.get("pending") else
                            "%s:%s:%s" % (spec["invoice"], "+".join(map(str, spec.get("imprints") or [])) or "no-imprint", rid),
                            "batchKeys": ["%s:%s" % (spec["invoice"], g) for g in (spec.get("groups") or [spec.get("group")])]}
                    if old and not old.get("pending") and cellset(old) == cellset(card) and old["expected"] == card["expected"]:
                        card["groups"] = old["groups"]          # verbatim: ids, order, hand-tuned labels
                        card["batchKeys"] = old.get("batchKeys", card["batchKeys"])
                        status = "verified, unchanged"
                    else:
                        status = "REBUILT - sizes changed" if old and not old.get("pending") else "new"
            if card is None and old and not old.get("pending") and old.get("scheduleSignature") == sig and not problems:
                card = {k: old[k] for k in ("id", "job", "groups", "expected", "runId", "batchKeys") if k in old}
                status = "carried forward (not re-checked against Printavo)"
            if card is None:
                why = "; ".join(problems) or ("no Printavo data supplied for invoice %s" % spec["invoice"] if spec else "no catcher-map entry")
                if old and not old.get("pending"):
                    why = "row changed on the board (%s)" % why
                card = {"id": rid, "job": row["job"], "groups": [], "expected": row["qty"], "runId": "pending:" + rid,
                        "batchKeys": [], "pending": True, "blocked": PENDING_MSG}
                status = "PENDING - " + why
                pending.append((rid, why))

            if old and not old.get("pending") and (card.get("pending") or fingerprint(old) != fingerprint(card)):
                gone = copy.deepcopy(old)
                gone.update({"archivedAt": now, "archivedReason": "replaced: " + status})
                archived.append(gone)
            card.update({"customer": row["customer"], "location": PHONE.sub("phone number in artwork", row["location"]), "day": d["day"],
                         "scheduleSignature": sig,
                         "locationLabel": (spec or {}).get("label") or (old or {}).get("locationLabel") or guess_label(row["location"]),
                         "note": note, "image": image,
                         "artworkLabel": (spec or {}).get("artworkLabel") or (old or {}).get("artworkLabel") or "Reference from the weekly press board"})
            if old and old.get("priorLocations"):
                card["priorLocations"] = old["priorLocations"]
            cards.append(card)
            if spec:
                new_map[rid] = spec
            report.append("%-24s %-10s %5d  %s" % (rid, d["day"], row["qty"], status))

    if not rollover:
        for rid, old in prev_cards.items():
            if rid not in seen and not old.get("pending"):
                gone = copy.deepcopy(old)
                gone.update({"archivedAt": now, "archivedReason": "removed from the board"})
                archived.append(gone)
                report.append("%-24s %-10s %5s  removed from board -> archived" % (rid, "-", "-"))

    run_ids = [c["runId"] for c in cards]
    if len(set(run_ids)) != len(run_ids):
        sys.exit("DUPLICATE runId - two rows map to the same physical run: %s" % sorted({r for r in run_ids if run_ids.count(r) > 1}))

    manifest = {"schema": 1, "week": week["week"], "generatedAt": now,
                "source": "Weekly press board and verified Printavo order sizes",
                "days": [{"day": d["day"], "label": day_label(d["day"], d.get("label"))} for d in week["days"]],
                "cards": cards, "archivedCards": archived}
    os.makedirs(a.out, exist_ok=True)
    json.dump(manifest, open(os.path.join(a.out, "catcher.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump({"week": week["week"], "rows": new_map}, open(os.path.join(a.out, "catcher-map.json"), "w"),
              ensure_ascii=False, indent=1)
    if rollover:
        os.makedirs(os.path.join(a.out, "catcher-archive"), exist_ok=True)
        json.dump(prev, open(os.path.join(a.out, "catcher-archive", prev["week"] + ".json"), "w"),
                  ensure_ascii=False, separators=(",", ":"))

    print("\n".join(report))
    print("\n%d cards · %d pending · %d archived · week %s%s" % (len(cards), len(pending), len(archived), week["week"],
                                                                " · ROLLOVER from " + prev["week"] if rollover else ""))
    if pending and not a.allow_pending:
        print("\nNOT READY TO PUBLISH - fix these or rerun with --allow-pending to ship them LOCKED:")
        for rid, why in pending:
            print("  %s: %s" % (rid, why))
        sys.exit(2)


if __name__ == "__main__":
    main()
