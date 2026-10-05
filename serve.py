#!/usr/bin/env python3
"""Serve this folder so index.html can read the CSVs directly.

Browsers block a page opened from file:// from reading sibling files, so
without a server index.html has to ask you to pick the folder by hand.

    python3 serve.py            # http://localhost:8000
    python3 serve.py 9000       # another port

The page looks for positions.csv in csv/ first, then next to index.html, so
either layout works. This checks before starting and reports which one it
found, because otherwise a missing file only surfaces as a 404 in the log.
"""

import http.server
import socketserver
import sys
import webbrowser
from functools import partial
from pathlib import Path

NEEDED = ("positions.csv", "accounts.csv")

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
root = Path(__file__).parent.resolve()

if not (root / "index.html").exists():
    sys.exit(f"index.html is not in {root}\n"
             "Run this from the folder holding index.html.")

found = None
for label, folder in (("csv/", root / "csv"), ("alongside index.html", root)):
    if all((folder / n).exists() for n in NEEDED):
        found = (label, folder)
        break

if found is None:
    loose = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.csv"))
    print(f"Cannot find {' and '.join(NEEDED)} in {root}/csv or {root}.\n")
    if loose:
        print("CSV files I can see here:")
        for name in loose:
            print(f"  {name}")
        print("\nIf those are the right files, put them in a folder named csv/,")
        print("or leave them beside index.html - the page accepts either layout.")
    else:
        print("No CSV files here at all. Run:  python3 build_five.py")
    sys.exit(1)

label, folder = found
print(f"Serving {root}")
print(f"CSVs found {label}")
meta = folder / "meta.csv"
if meta.exists():
    for line in meta.read_text(encoding="utf-8").splitlines()[1:]:
        key, _, value = line.partition(",")
        if key in ("dates", "generated"):
            print(f"  {key}: {value}")

handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))

httpd = None
tried = []
for candidate in range(port, port + 20):
    try:
        httpd = socketserver.TCPServer(("127.0.0.1", candidate), handler)
        port = candidate
        break
    except OSError:
        tried.append(candidate)

if httpd is None:
    sys.exit(f"Ports {tried[0]}-{tried[-1]} are all in use.\n"
              f"Free one of them, or run:  python3 serve.py {tried[-1] + 1}")

if tried:
    print(f"Port(s) {', '.join(str(p) for p in tried)} in use, using {port} instead.")

with httpd:
    url = f"http://localhost:{port}/"
    print(f"\n{url}\nCtrl-C to stop.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
