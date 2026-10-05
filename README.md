# Kepemilikan 5% — daily ownership change viewer

A local, offline site for KSEI's daily "kepemilikan efek diatas 5% berdasarkan SID"
reports. Separate from the monthly 1% project: different source file, different
structure, its own `index.html`.

## Run it

The page holds no data of its own; it reads `csv/` at run time.

```
python3 serve.py            # opens http://localhost:8000
```

Double-clicking `index.html` also works, but browsers stop a `file://` page from
reading sibling files, so it will ask you to pick the folder once. It is read in the
browser and goes nowhere else.

**Either folder layout works.** The page looks for `positions.csv` in `csv/` first, then
next to `index.html`, so if you download the files singly and they all land in one
folder, nothing needs rearranging. `serve.py` reports which layout it found before it
starts, so a missing file does not show up only as a 404 in the log.

## Add a day

```
cp peng-2026-09-03-000XX-lima-persen.xlsx data/
python3 build_five.py          # rewrites csv/, index.html is untouched
```

Each KSEI file spans two trading days, so N files give N+1 snapshots and N days of
change. Dates are read from the sheet's own column headings, not the filename.
Depends on `openpyxl` only.

## Files

```
data/*.xlsx        the KSEI workbooks, untouched
csv/positions.csv  every 5% position, one row per date
csv/accounts.csv   the same, broken out by custodian or broker, with IDX codes
csv/changes.csv    day-over-day movements only
index.html         the site
brokers.csv        IDX broker codes; edit this to add a missing firm
brokers.py         matches KSEI custodian names onto those codes
build_five.py      regenerates the CSVs and index.html
parse_ksei5.py     the workbook reader
```

## Ordering

Both the CSVs and the Changes tab are in emiten order (A–Z) by default. **Sort by** on
the Changes tab switches to biggest change when you want the day's movers ranked
instead, and any column header still sorts on click. The Emiten tab ignores emiten
order — it is a single ticker, so it stays ranked by stake size.

## What the tabs do

- **Changes** — the day's movements. Click a row to see which custodian actually
  moved; a stake can hold its size and still shift between brokers.
- **Emiten** — every holder above 5% of one ticker, moved or not.
- **Owner** — every emiten where one investor sits above 5%.

## Three things the source file gets wrong

**Two thousands separators in one sheet.** Most cells are text with commas
(`538,605,204`), but some were written dot-grouped and Excel parsed them as
decimals — `3.700` became the number `3.7`, `136.802` became `136.802`. Roughly 100
cells per file are affected, each understating a holding by 1000x. Text cells and
decimals are recoverable on sight; a bare integer is not, since `410` could be 410
shares or a mangled `410.000`. Those are resolved against the investor total, which
is text and therefore trustworthy, by finding which reading makes the accounts sum
to the printed total. The build fails loudly if any block will not reconcile.

**The built-in comparison breaks across a record change.** Each file carries its own
before and after columns, but they describe *records*, not holders. When KSEI merged
two duplicate Kimia Farma records in PEHA on 2 Sep, the file reported 238,450,930 →
476,901,860: a 28% stake apparently doubling overnight. Kimia Farma held 56.77% on
both days, through two SIDs. A later file's before column also omits anyone who fell
below 5% that day. So this build reconstructs a snapshot per date and compares those
instead, preferring the file published *on* each date.

**Respellings.** `PT ARTHAKENCANA RAYATAMA` one day, `ARTHAKENCANA RAYATAMA PT` the
next — an apparent full exit and a new 5% crosser. Names are folded together after
stripping `PT`, `Tbk`, `Persero`, `Perusahaan Perseroan`, `Pte`, `Ltd` and
punctuation. A position built from more than one SID record carries a `×n` marker.

## Broker codes

The two-letter code beside each account is IDX's exchange-member identifier — the
same one in a broker summary. KSEI writes firm names freely (`PT. TRIMEGAH SEKURITAS
INDONESIA TBK`, `SINARMAS SEKURITAS, PT`), so names are normalised and matched
against `brokers.csv`, with an alias table for renames.

Of 118 custodian names in these files, 82 resolve to a code. The rest have none, and
mostly should not:

- **20 custodian banks** — Deutsche Bank, Citibank, HSBC, Standard Chartered, Bank
  Mandiri Custody. Sub-registry custodians, not exchange members, so IDX never
  issued them a code. Shown as `bank`.
- **9 closed-member accounts** — `REKENING TAMPUNGAN KSEI UNTUK CLOSED MEMBER`, where
  KSEI parks holdings of a broker whose membership was revoked (Antaboga, Brent,
  Kresna, Onix and others). Shown as `closed`.
- **7 genuinely unmatched** — firms not in the list, printed by name when the build
  runs. Add them to `brokers.csv` to fill them in.

One trap worth knowing: **Yakin Bertumbuh Sekuritas is YB**, renamed from Jasa Utama
Capital in March 2025. **Yugen Bertumbuh Sekuritas is IP** — a different firm with an
almost identical name. The alias table pins both.

The code list came from a third-party compilation of IDX's register (updated April
2026) because idx.co.id blocks automated fetches. Memberships get revoked and firms
rename, so check `brokers.csv` against IDX's own member profile page if a code looks
wrong.

## Flags

`⇄` marks an offsetting move: the same block leaving one holder and arriving at
another in the same emiten on the same day. Sizes match with a small tolerance,
because a transferred block often leaves an odd-lot account behind — INPP's
872,279,000 exit pairs with an 872,278,900 arrival. It is flagged only when the
pairing is unambiguous, and it means a transfer *or* a respelling, not necessarily
an open-market trade.

## Limits

The source only lists holders at or above 5%, so *crossed 5%* and *below 5%* are
threshold crossings rather than entries and exits. The earliest date in the set is
reconstructed from the next day's file and is missing anyone who fell below 5% that
day; adding the file published on that date fixes it. Percentages for a position
held through several SID records are summed from KSEI's own figures and can round
by 0.01.
