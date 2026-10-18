"""Keeps sw.js's VERSION in step with the files it precaches (the same scheme as QuizQrusher's check_shared.py and CwikCard's).

Usage (from the repo root):
  python -I tools/sw_version.py          check: exit 1 if VERSION is stale
  python -I tools/sw_version.py --sync   write the new VERSION into sw.js

VERSION = the first 12 hex digits of sha256 over every precached file (its path, a NUL byte, its bytes), so any change to
index.html, the manifest or an icon makes sw.js change too, and browsers install the new version.
"""
import hashlib
import re
import sys

SW_VERSION = re.compile(r'const VERSION = "([^"]*)";')


def main():
    sw = open("sw.js", encoding="utf-8").read()
    listed = re.search(r"const PRECACHE = \[(.*?)\];", sw, re.S)
    if not listed:
        sys.exit("sw.js has no PRECACHE list")
    h = hashlib.sha256()
    for path in re.findall(r'"([^"]+)"', listed.group(1)):
        path = "index.html" if path == "./" else path
        h.update(path.encode() + b"\0" + open(path, "rb").read())
    want = h.hexdigest()[:12]
    have = SW_VERSION.search(sw)
    if "--sync" in sys.argv:
        open("sw.js", "w", encoding="utf-8", newline="").write(SW_VERSION.sub('const VERSION = "%s";' % want, sw, count=1))
        print("sw.js VERSION", want)
    elif not have or have.group(1) != want:
        sys.exit("sw.js VERSION is stale (run --sync): has %s, files say %s" % (have.group(1) if have else "none", want))
    else:
        print("sw.js VERSION is current:", want)


main()
