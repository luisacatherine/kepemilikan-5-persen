"""Parse KSEI's daily "kepemilikan efek diatas 5% berdasarkan SID" workbook.

The file is awkward in three ways:

1. It is already a two-day comparison. Each file carries `Kepemilikan Per <D-1>`
   and `Kepemilikan Per <D>` side by side, so one file is one trading day of
   change, and consecutive files chain into a timeline.

2. Rows are securities *accounts*, not investors. An investor's name, the emiten,
   and the investor-level total appear only on the first row of each block and
   must be filled down.

3. Numbers use two different thousands separators in the same sheet. Most cells
   are text with commas ("538,605,204"), but some were written dot-grouped and
   Excel silently parsed them as decimals: "3.700" became the number 3.7 and
   "136.802" became 136.802. Left alone these understate a holding by 1000x.

Text cells and floats are recoverable on sight. Bare integers are not -- 410 is
either 410 shares or a mangled "410.000" -- so they are resolved against the
investor total, which is text and therefore trustworthy.
"""

import re
from itertools import combinations
from pathlib import Path

import openpyxl

# Column letters in the sheet, 1-indexed.
C_CODE, C_ISSUER, C_CUSTODIAN, C_INVESTOR, C_ACCOUNT = 2, 3, 4, 5, 6
C_NATIONALITY, C_DOMICILE, C_STATUS = 9, 10, 11
C_SH_A, C_AGG_A, C_PCT_A = 12, 13, 14
C_SH_B, C_AGG_B, C_PCT_B = 15, 16, 17

DATE_RE = re.compile(r"Kepemilikan Per\s+(\d{2}-[A-Z]{3}-\d{4})", re.I)
MONTHS = dict(zip("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(),
                  range(1, 13)))


def iso(stamp):
    d, m, y = stamp.split("-")
    return f"{y}-{MONTHS[m.upper()]:02d}-{int(d):02d}"


def parse_share(v):
    """Return (value, certain). `certain=False` means the cell could be 1000x."""
    if v is None:
        return None, True
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace(".", "")
        if s in ("", "-"):
            return None, True
        try:
            return int(s), True
        except ValueError:
            return None, True
    if isinstance(v, bool):
        return None, True
    if isinstance(v, float):
        # Excel already ate a dot-grouped string; the fraction holds the digits.
        return round(v * 1000), True
    if isinstance(v, int):
        return v, v == 0          # zero is the same either way
    return None, True


def parse_pct(v):
    if v is None:
        return None
    try:
        return round(float(str(v).replace(",", "").strip()) * 100)  # basis points
    except ValueError:
        return None


def reconcile(cells, total):
    """Pick the reading of each uncertain cell that makes the block sum to `total`.

    `cells` is a list of (value, certain). Each uncertain cell is worth either v
    or v*1000, so the shortfall must be covered by scaling some subset of them.
    Returns the resolved list, or None when nothing reconciles.
    """
    # A blank cell means the account held nothing on that side -- shares moved in
    # or out of that custodian during the day -- so it counts as zero, not missing.
    values = [(v or 0) for v, _ in cells]
    if total is None:
        return None
    base = sum(values)
    need = total - base
    if need == 0:
        return values

    idx = [i for i, (v, certain) in enumerate(cells) if not certain and v]
    if not idx or len(idx) > 14:
        return None
    for size in range(1, len(idx) + 1):
        for combo in combinations(idx, size):
            if sum(values[i] * 999 for i in combo) == need:
                out = list(values)
                for i in combo:
                    out[i] *= 1000
                return out
    return None


def read_day(path):
    """Return (date_before, date_after, account_rows, stats)."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active

    date_a = date_b = None
    header_row = None
    for row in ws.iter_rows(min_row=1, max_row=8):
        for cell in row:
            if isinstance(cell.value, str):
                m = DATE_RE.search(cell.value)
                if m:
                    if date_a is None:
                        date_a = iso(m.group(1))
                    elif date_b is None:
                        date_b = iso(m.group(1))
            if cell.value == "No":
                header_row = cell.row
    if not (date_a and date_b):
        raise ValueError(f"{path.name}: could not find both 'Kepemilikan Per' dates")

    rows, blocks = [], []
    block = None
    code = issuer = investor = None

    for r in ws.iter_rows(min_row=header_row + 2, values_only=True):
        cells = list(r) + [None] * (18 - len(r))
        get = lambda i: cells[i - 1]

        if get(C_CODE):
            code = str(get(C_CODE)).strip()
        if get(C_ISSUER):
            issuer = str(get(C_ISSUER)).strip()
        if get(C_INVESTOR):
            investor = str(get(C_INVESTOR)).strip()
        if not code or get(C_CUSTODIAN) is None:
            continue

        agg_a, agg_a_ok = parse_share(get(C_AGG_A))
        agg_b, agg_b_ok = parse_share(get(C_AGG_B))
        pct_a, pct_b = parse_pct(get(C_PCT_A)), parse_pct(get(C_PCT_B))

        row = {
            "code": code, "issuer": issuer, "investor": investor,
            "custodian": str(get(C_CUSTODIAN) or "").strip(),
            "account": str(get(C_ACCOUNT) or "").strip(),
            "nationality": str(get(C_NATIONALITY) or "").strip(),
            "domicile": str(get(C_DOMICILE) or "").strip(),
            "status": str(get(C_STATUS) or "").strip(),
            "sh_a": parse_share(get(C_SH_A)),
            "sh_b": parse_share(get(C_SH_B)),
        }
        rows.append(row)

        # A non-empty aggregate cell starts a new investor block.
        if agg_a is not None or agg_b is not None:
            block = {
                "agg_a": agg_a, "agg_a_ok": agg_a_ok,
                "agg_b": agg_b, "agg_b_ok": agg_b_ok,
                "pct_a": pct_a, "pct_b": pct_b, "members": [],
            }
            blocks.append(block)
        if block is not None:
            block["members"].append(row)

    wb.close()

    # ---- repair the ambiguous account cells against each investor total ----
    stats = {"blocks": len(blocks), "rows": len(rows), "repaired": 0,
             "no_total": 0, "unreconciled": 0}
    for blk in blocks:
        for side, agg_key, ok_key in (("sh_a", "agg_a", "agg_a_ok"),
                                      ("sh_b", "agg_b", "agg_b_ok")):
            cells = [m[side] for m in blk["members"]]
            targets = [blk[agg_key]]
            if not blk[ok_key] and blk[agg_key]:
                targets.append(blk[agg_key] * 1000)   # the total itself may be mangled

            fixed = None
            for target in targets:
                fixed = reconcile(cells, target)
                if fixed is not None:
                    blk[agg_key] = target
                    break

            if fixed is None:
                # No aggregate to check against is not the same as a failure.
                stats["no_total" if blk[agg_key] is None else "unreconciled"] += 1
                fixed = [v for v, _ in cells]
            for m, v in zip(blk["members"], fixed):
                if m[side][0] != v:
                    stats["repaired"] += 1
                m[side] = v

    for blk in blocks:
        for m in blk["members"]:
            for side in ("sh_a", "sh_b"):
                if isinstance(m[side], tuple):
                    m[side] = m[side][0]

    # Verify: every repaired block must now sum to the total KSEI printed.
    stats["mismatched"] = sum(
        1 for blk in blocks
        for side, agg in (("sh_a", "agg_a"), ("sh_b", "agg_b"))
        if blk[agg] is not None
        and sum(m[side] or 0 for m in blk["members"]) != blk[agg]
    )
    return date_a, date_b, blocks, stats


if __name__ == "__main__":
    import sys
    for p in sorted(Path(sys.argv[1]).glob("*.xlsx")):
        a, b, blocks, st = read_day(p)
        print(f"{p.name}: {a} -> {b}  {st}")
