#!/usr/bin/env python3
"""
Repeat detection for the Mayhem Ops board — the "REPEAT? films may be on file" badge.

Deliberately allowed to be wrong. It says REPEAT? with a link to the prior job and lets
Joe make the final call. It must NEVER auto-clear a task on its own.

Confidence:
  strong  — same customer + real art-token overlap  -> "REPEAT? #1234"
  weak    — same customer, ran this service before   -> "repeat customer"
  none    — first time we've seen them
"""
import json, re

STOP = set("""the a an and or of for to in on with new order orders job jobs
shirt shirts tee tees tshirt t-shirt hat hats hoodie hoodies polo polos
front back left right chest full sleeve sleeves print prints logo color colors ink
2023 2024 2025 2026 jan feb mar apr may jun jul aug sep oct nov dec
purchase number size sizes wide tall inch inches only both all none tbd
qty each per side top bottom center middle small medium large xlarge
customer supplied blank blanks garment garments piece pieces item items
january february march april june july august september october november december
sept sept. same made need needs please note notes
underbase underlay overlay halftone mesh squeegee pms screen screens film films""".split())


def norm_customer(name: str) -> str:
    k = (name or "").strip().lower()
    k = re.sub(r"[.,]", "", k)
    k = re.sub(r"\b(llc|inc|co|corp|ltd)\b", "", k)
    return re.sub(r"\s+", " ", k).strip()


def art_tokens(nickname: str, imprints) -> set:
    blob = " ".join([nickname or ""] + list(imprints or []))
    return {w for w in re.findall(r"[a-z][a-z0-9']{2,}", blob.lower()) if w not in STOP}


def match(hist, customer, nickname, imprints, svc_needed=None, exclude_v=None):
    """Return (confidence, prior_job_or_None, shared_tokens)."""
    c = hist["customers"].get(norm_customer(customer))
    if not c:
        return "none", None, []

    mine = art_tokens(nickname, imprints)
    best, best_score, best_shared = None, 0, []
    for j in c["jobs"]:
        if exclude_v and j["v"] == exclude_v:
            continue
        shared = mine & set(j.get("art", []))
        # weight distinctive words: a 3-word overlap on generic terms is weak,
        # a single rare word ("dovetail", "beauty") is strong
        score = sum(2 if len(w) > 6 else 1 for w in shared)
        if score > best_score:
            best, best_score, best_shared = j, score, sorted(shared)

    if best and best_score >= 3:
        return "strong", best, best_shared

    # weak = we know this customer, but the art doesn't obviously line up.
    # exclude_v applies here too — a job must never cite itself as its own prior.
    others = [j for j in c["jobs"] if not (exclude_v and j["v"] == exclude_v)]
    if not others:
        return "none", None, []
    if svc_needed and any(s in c["svc"] for s in svc_needed):
        return "weak", others[0], best_shared
    return "weak", others[0], best_shared


if __name__ == "__main__":
    hist = json.load(open("history.json"))

    # Real jobs pulled from Printavo's current production window (Aug-Sep 2026).
    tests = [
        ("Zapped Headwear", "6377", "Hats and Patches For hat bar", [""], ["WVN", "LTHR"]),
        ("Munson Woodworking", "6354", "Munson Woodworking - dove tail logo",
         ["Front logo / 2-color", "Back logo / 2-color"], ["SP"]),
        ("Lattitude Marketing", "6367", "K2 / Headbands, Tshirts, Socks Purchase Order #4900-17607",
         ["Left Chest / Mountain 6\" wide / Tan", "Full Back / Beauty 13\" wide / Tan"], ["SP"]),
        ("End Overdose", "6352", "End Overdose 2026 t shirts", [""], ["SP"]),
        ("Battle Born", "6350", "Battle Born 2026 July 27th", ["front", "back", "sleeve"], ["SP"]),
        ("Brand Makers", "6380", "Aug 2026 order", ["front left chest", "full back"], ["SP"]),
        ("Some Brand New Customer", "9999", "First ever order", ["left chest"], ["SP"]),
    ]

    print(f"{'JOB':<6} {'CUSTOMER':<24} {'CONF':<7} PRIOR   SHARED ART TOKENS")
    print("-" * 92)
    for cust, v, nick, imp, svc in tests:
        conf, prior, shared = match(hist, cust, nick, imp, svc, exclude_v=v)
        pv = f"#{prior['v']}" if prior else "-"
        pd = prior["due"] if prior else ""
        print(f"{v:<6} {cust[:23]:<24} {conf:<7} {pv:<7} {','.join(shared[:6]) or '(service history only)'}  {pd}")
