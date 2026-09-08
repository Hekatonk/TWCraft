#!/usr/bin/env python3
"""Verify every file packwiz indexes is actually committed to git.

A file that is indexed but gitignored is the nastiest failure this pack can
have: `packwiz refresh` records it, clients read it from index.toml, then fetch
it from the repo and get a 404 -- so the pack breaks for everyone while looking
perfectly fine locally. That happened with a `grep.exe.stackdump` that a
segfaulting grep dropped in the pack root: gitignored, but not packwizignored.

Run before committing:

    python scripts/check-index.py

Exits non-zero and names the offenders if the two lists disagree. The fix is
almost always to add the pattern to .packwizignore -- .gitignore alone is not
enough, packwiz does not read it.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def indexed_files():
    path = os.path.join(ROOT, "index.toml")
    text = open(path, encoding="utf-8").read()
    return set(re.findall(r'^file = "(.*)"', text, re.M))


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return set(line.strip() for line in out.splitlines() if line.strip())


def main():
    indexed = indexed_files()
    tracked = tracked_files()

    # Indexed but not committed -> clients will 404 on it.
    ghosts = sorted(indexed - tracked)
    if ghosts:
        print("FAIL: indexed but NOT tracked by git -- clients will 404 on these:")
        for g in ghosts:
            print("   " + g)
        print("\nFix: add the pattern to .packwizignore (packwiz does not read"
              " .gitignore), then re-run `packwiz refresh`.")
        return 1

    print("OK: all {} indexed files are tracked by git".format(len(indexed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
