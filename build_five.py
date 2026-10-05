#!/usr/bin/env python3
"""Build the daily 5% ownership site and CSV exports from KSEI's workbooks.

    python3 build_five.py            # data/*.xlsx -> csv/*.csv + index.html

Each KSEI file spans two trading days, so N files give N+1 daily snapshots and
N days of change. Where two files cover the same date, the file published *on*
that date wins: a later file's "before" column only lists holders who still hold
5% on the later date, and it reports a merged SID record's own prior value
rather than the true prior total.
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import brokers
from parse_ksei5 import read_day

# Corporate-form words that KSEI restyles freely between publications.
DROP_TOKENS = {
    "PT", "TBK", "PERSERO", "PERUSAHAAN", "PERSEROAN", "THE", "LTD", "LIMITED",
    "PTE", "INC", "NV", "SA", "PLC", "CO", "CORP", "COMPANY", "LLC",
}


def norm_name(name):
    s = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper())
    return " ".join(t for t in s.split() if t and t not in DROP_TOKENS)


def collect(files):
    """Return {date: {(code, key): position}} plus issuer and investor metadata."""
    snapshots = {}       # date -> dict
    provenance = {}      # date -> "own publication" | "carried back"
    issuer_names, display, meta = {}, {}, {}

    days = []
    for path in files:
        date_a, date_b, blocks, stats = read_day(path)
        days.append((date_a, date_b, blocks, path, stats))
        print(f"  {path.name:<44} {date_a} \u2192 {date_b}   "
              f"{stats['blocks']} positions, {stats['repaired']} cells repaired, "
              f"{stats['unreconciled']} unreconciled")
        if stats["unreconciled"] or stats["mismatched"]:
            print(f"    ! {path.name} did not fully reconcile against KSEI's own totals")

    days.sort(key=lambda d: d[1])

    for date_a, date_b, blocks, path, _ in days:
        for side, date in (("a", date_a), ("b", date_b)):
            # A date's own publication is authoritative; only fall back to a
            # later file's "before" column when nothing else covers that date.
            authoritative = (side == "b")
            if date in snapshots and not (authoritative and provenance[date] != "own"):
                continue
            if date in snapshots and provenance[date] == "own":
                continue

            snap = {}
            for blk in blocks:
                m0 = blk["members"][0]
                code, raw = m0["code"], m0["investor"]
                shares = blk[f"agg_{side}"] or 0
                if not shares:
                    continue
                key = (code, norm_name(raw))
                issuer_names.setdefault(code, m0["issuer"])
                display[key] = raw
                meta[key] = (m0["status"], m0["domicile"], m0["nationality"])

                p = snap.setdefault(key, {"shares": 0, "bp": 0, "records": 0, "accounts": {}})
                p["shares"] += shares
                p["bp"] += blk[f"pct_{side}"] or 0
                p["records"] += 1
                for m in blk["members"]:
                    v = m[f"sh_{side}"] or 0
                    if v:
                        p["accounts"][m["custodian"]] = p["accounts"].get(m["custodian"], 0) + v
            snapshots[date] = snap
            provenance[date] = "own" if authoritative else "carried"

    return snapshots, provenance, issuer_names, display, meta


def diff_days(prev, curr):
    """Investor-level change between two snapshots, plus which custodians moved."""
    out = []
    for key in set(prev) | set(curr):
        a = prev.get(key, {"shares": 0, "bp": 0, "records": 0, "accounts": {}})
        b = curr.get(key, {"shares": 0, "bp": 0, "records": 0, "accounts": {}})
        if a["shares"] == b["shares"]:
            moved = {c for c in set(a["accounts"]) | set(b["accounts"])
                     if a["accounts"].get(c, 0) != b["accounts"].get(c, 0)}
            if not moved:
                continue
        out.append({"key": key, "a": a, "b": b})
    return out


def offset_tolerance(size):
    return max(1000, size // 10000)      # 0.01%, floored at 1,000 shares


def find_offsets(changes):
    """Same block leaving one holder and arriving at another inside one emiten.

    Sizes are matched with a small tolerance rather than exactly: a transferred
    block often leaves an odd-lot account behind, so INPP's 872,279,000 exit
    pairs with an 872,278,900 arrival. A pair is only flagged when exactly one
    candidate on each side falls inside the tolerance, so near-ties are left
    alone rather than guessed at.
    """
    by_issuer = {}
    for ch in changes:
        by_issuer.setdefault(ch["key"][0], []).append(ch)

    for group in by_issuer.values():
        plus, minus = [], []
        for ch in group:
            d = ch["b"]["shares"] - ch["a"]["shares"]
            if d > 0:
                plus.append((d, ch))
            elif d < 0:
                minus.append((-d, ch))

        for size, ch in plus:
            tol = offset_tolerance(size)
            near = [m for m in minus if abs(m[0] - size) <= tol]
            if len(near) != 1:
                continue
            rival = [p for p in plus if abs(p[0] - near[0][0]) <= tol]
            if len(rival) != 1:
                continue
            ch["offset"] = near[0][1]["key"][1]
            near[0][1]["offset"] = ch["key"][1]


def status_of(ch):
    a, b = ch["a"]["shares"], ch["b"]["shares"]
    if not a:
        return "in"
    if not b:
        return "out"
    if b > a:
        return "up"
    if b < a:
        return "down"
    return "shuffle"      # total unchanged, but it moved between custodians


KIND_LABEL = {
    "exact": "exchange member", "alias": "exchange member", "tokens": "exchange member",
    "bank": "custodian bank", "closed": "closed member (KSEI holding account)",
    "unmatched": "not in broker table",
}


def main(data_dir, csv_dir):
    files = sorted(p for p in data_dir.glob("*.xls*") if not p.name.startswith("~$"))
    if not files:
        sys.exit(f"No KSEI 5% workbooks found in {data_dir}/")

    snapshots, provenance, issuer_names, display, meta = collect(files)
    dates = sorted(snapshots)
    print(f"\n  {len(dates)} daily snapshots: {', '.join(dates)}")
    carried = [d for d in dates if provenance[d] == "carried"]
    if carried:
        print(f"  {', '.join(carried)} reconstructed from the next day's file "
              f"(holders who left that day are not in it)")

    # ---------------- broker codes ----------------
    match = brokers.build_matcher()
    resolved, kinds = {}, {}
    for snap in snapshots.values():
        for pos in snap.values():
            for c in pos["accounts"]:
                if c not in resolved:
                    resolved[c], kinds[c] = match(c)

    tally = {}
    for c, kind in kinds.items():
        tally[kind] = tally.get(kind, 0) + 1
    coded = sum(v for k, v in tally.items() if k in ("exact", "alias", "tokens"))
    print(f"\n  Broker codes: {coded} of {len(resolved)} custodians matched "
          f"({tally.get('bank', 0)} custodian banks and "
          f"{tally.get('closed', 0)} closed-member accounts have no code by definition)")
    missing = sorted(c for c, k in kinds.items() if k == "unmatched")
    if missing:
        print(f"  {len(missing)} not in brokers.csv \u2014 add them there if you want codes:")
        for c in missing:
            print(f"    {c}")

    def code_of(name):
        return resolved.get(name), kinds.get(name, "unmatched")

    csv_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- CSV: daily snapshots, positions ----------------
    with open(csv_dir / "positions.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "emiten", "issuer_name", "investor", "local_foreign",
                    "domicile", "nationality", "shares", "pct", "sid_records"])
        for d in dates:
            for key, p in sorted(snapshots[d].items()):
                st, dom, nat = meta[key]
                w.writerow([d, key[0], issuer_names[key[0]], display[key], st, dom, nat,
                            p["shares"], f"{p['bp'] / 100:.2f}", p["records"]])

    # ---------------- CSV: daily snapshots, accounts ----------------
    with open(csv_dir / "accounts.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "emiten", "investor", "broker_code", "custodian",
                    "custodian_type", "shares"])
        for d in dates:
            for key, p in sorted(snapshots[d].items()):
                for cust, v in sorted(p["accounts"].items()):
                    cd, kind = code_of(cust)
                    w.writerow([d, key[0], display[key], cd or "", cust,
                                KIND_LABEL[kind], v])

    # ---------------- CSV: day-over-day changes ----------------
    all_changes = []
    with open(csv_dir / "changes.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date_before", "date_after", "emiten", "issuer_name", "investor",
                    "local_foreign", "domicile", "shares_before", "shares_after",
                    "delta_shares", "delta_pct_of_position", "pct_before", "pct_after",
                    "delta_pp", "status", "brokers_moved", "offset_by"])
        for prev_d, curr_d in zip(dates, dates[1:]):
            changes = diff_days(snapshots[prev_d], snapshots[curr_d])
            find_offsets(changes)
            for ch in sorted(changes, key=lambda c: c["key"]):
                key, a, b = ch["key"], ch["a"], ch["b"]
                st, dom, _ = meta[key]
                d_sh = b["shares"] - a["shares"]
                moved = sorted(c for c in set(a["accounts"]) | set(b["accounts"])
                               if a["accounts"].get(c, 0) != b["accounts"].get(c, 0))
                moved = [f"{code_of(c)[0]} {c}" if code_of(c)[0] else c for c in moved]
                w.writerow([
                    prev_d, curr_d, key[0], issuer_names[key[0]], display[key], st, dom,
                    a["shares"], b["shares"], d_sh,
                    f"{d_sh / a['shares'] * 100:.2f}" if a["shares"] else "",
                    f"{a['bp'] / 100:.2f}", f"{b['bp'] / 100:.2f}",
                    f"{(b['bp'] - a['bp']) / 100:.2f}",
                    status_of(ch), " | ".join(moved), ch.get("offset", ""),
                ])
            all_changes.append((prev_d, curr_d, changes))
            print(f"  {prev_d} \u2192 {curr_d}: {len(changes)} positions moved")

    # ---------------- CSV: what the page needs to know about the run ----------------
    with open(csv_dir / "meta.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "value"])
        w.writerow(["dates", "|".join(dates)])
        w.writerow(["carried", "|".join(carried)])
        w.writerow(["generated", datetime.now().strftime("%Y-%m-%d %H:%M")])
        w.writerow(["source_files", "|".join(p.name for p in files)])

    total = sum(f.stat().st_size for f in csv_dir.glob("*.csv")) / 1024
    print(f"\n  {csv_dir}/  positions.csv, accounts.csv, changes.csv, meta.csv  ({total:.0f} KB)")
    print("  index.html reads these at run time; it holds no data of its own.")


if __name__ == "__main__":
    here = Path(__file__).parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(here / "data"))
    ap.add_argument("--csv", default=str(here / "csv"))
    args = ap.parse_args()
    main(Path(args.data), Path(args.csv))
