"""Map KSEI custodian names onto IDX broker codes.

KSEI writes account holders however it likes -- `PT. TRIMEGAH SEKURITAS
INDONESIA TBK`, `SINARMAS SEKURITAS, PT`, `PT KAY HIAN SEKURITAS` -- so the
names never match IDX's register exactly.

Two kinds of name will never get a code, and they are not failures:

* custodian banks (Deutsche Bank, Citibank, HSBC, Standard Chartered, Bank
  Mandiri Custody). These are sub-registry custodians, not exchange members, so
  IDX never issued them a two-letter broker code.
* securities firms that are not exchange members.

Anything else that comes back unmatched is reported by the build so the broker
table can be corrected rather than silently losing a code.
"""

import csv
import re
from pathlib import Path

# Renames IDX's register has not caught up with, or that KSEI writes differently.
ALIASES = {
    "CGS INTERNATIONAL SEKURITAS INDONESIA": "YU",   # was CGS-CIMB
    "CGS INTERNATIONAL SECURITIES INDONESIA": "YU",
    "KAY HIAN SEKURITAS": "AI",                      # KSEI drops the UOB prefix
    "ERDIKHA ELIT": "AO",
    "SINARMAS SEKURITAS": "DH",
    "PANIN SEKURITAS": "GR",
    "TRIMEGAH SEKURITAS INDONESIA": "LG",
    "RELIANCE SEKURITAS INDONESIA": "LS",
    "YULIE SEKURITAS INDONESIA": "RS",
    "MINNA PADI INVESTAMA SEKURITAS": "MU",
    "JP MORGAN SEKURITAS INDONESIA": "BK",
    # Renamed from Jasa Utama Capital in Mar 2025, code unchanged. Not to be
    # confused with Yugen Bertumbuh Sekuritas, which is a different firm (IP).
    "JASA UTAMA CAPITAL SEKURITAS": "YB",
    "SUKADANA PRIMA SEKURITAS": "AD",                # was OSO Sekuritas Indonesia
    "PLUANG MAJU SEKURITAS": "RO",                   # was Nilai Inti Sekuritas
    "LABA SEKURITAS INDONESIA": "TF",                # was Universal Broker Indonesia Sekuritas
}

# Words that carry no identifying weight when comparing two firm names.
NOISE = {"PT", "TBK", "PERSERO", "PERSEROAN", "PERUSAHAAN", "THE", "LTD",
         "LIMITED", "PTE", "INC", "NV", "SA", "PLC", "CO", "CORP", "COMPANY"}

BANK_MARKERS = ("BANK", "CITIBANK", "DEUTSCHE", "STANDARD CHARTERED",
                "HSBC INDONESIA", "CUSTODY", "CUSTODIAN", "BUT ")

# "REKENING TAMPUNGAN KSEI UNTUK CLOSED MEMBER" -- a KSEI holding account for a
# broker whose exchange membership was revoked. The firm has no active code.
CLOSED_MARKERS = ("TAMPUNGAN", "CLOSED MEMBER", "(TAMP")


def normalize(name):
    s = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper())
    return " ".join(t for t in s.split() if t and t not in NOISE)


def is_closed_member(name):
    up = str(name).upper()
    return any(marker in up for marker in CLOSED_MARKERS)


def is_bank(name):
    up = re.sub(r"[^A-Z ]", " ", str(name).upper())
    return any(marker in up for marker in BANK_MARKERS)


def load_table(path=None):
    path = Path(path or Path(__file__).parent / "brokers.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        return [(r["code"].strip(), r["name"].strip()) for r in csv.DictReader(fh)]


def build_matcher(table=None):
    table = table or load_table()
    exact, by_tokens = {}, []
    for code, name in table:
        key = normalize(name)
        exact[key] = code
        exact[key.replace(" INDONESIA", "")] = code
        by_tokens.append((code, set(key.split())))
    aliases = {normalize(k): v for k, v in ALIASES.items()}

    def match(custodian):
        """Return (code, reason). code is None when there is legitimately none."""
        key = normalize(custodian)
        if key in aliases:
            return aliases[key], "alias"
        if key in exact:
            return exact[key], "exact"
        trimmed = key.replace(" INDONESIA", "")
        if trimmed in exact:
            return exact[trimmed], "exact"

        # All of the register's distinctive words appear in KSEI's name, and the
        # match is unique. "SEKURITAS" alone is far too weak to match on.
        tokens = set(key.split())
        hits = [c for c, t in by_tokens
                if t <= tokens and len(t - {"SEKURITAS", "INDONESIA", "ASIA", "CAPITAL"}) >= 1]
        if len(hits) == 1:
            return hits[0], "tokens"

        if is_closed_member(custodian):
            return None, "closed"
        return None, "bank" if is_bank(custodian) else "unmatched"

    return match
