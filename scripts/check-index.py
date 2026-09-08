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


def ignored_files(paths):
    """Of `paths`, return those git is configured to ignore.

    Deliberately not `git ls-files`: a mod added but not yet staged is untracked,
    which is normal and harmless -- `git add -A` will pick it up. The real
    failure is a file git will *never* commit because it is ignored, since that
    is the one that leaves a live index entry pointing at nothing.
    """
    if not paths:
        return set()
    proc = subprocess.run(["git", "check-ignore", "--stdin"], cwd=ROOT,
                          input="\n".join(sorted(paths)),
                          capture_output=True, text=True)
    # exit 0 = some ignored, 1 = none ignored, >1 = real error
    if proc.returncode > 1:
        raise SystemExit("git check-ignore failed: " + proc.stderr.strip())
    return set(line.strip() for line in proc.stdout.splitlines() if line.strip())


def jer_reminder():
    """Warn while JER has no worldgen data shipped with the pack.

    Just Enough Resources only knows vanilla ore distributions out of the box.
    Modded ores (Mekanism's osmium, tin, uranium, lead, fluorite, salt) stay
    blank until config/world-gen.json exists. Shipping that file
    means every player gets the data rather than each generating it.

    JER's own /jer_profile command registers but is not functional on this
    version -- it points at an external tool instead. That tool is RegionScanner
    (github.com/RundownRhino/RegionScanner), a Rust CLI that scans the region
    files of an already-generated world. tools/ holds the binary and is both
    git- and packwiz-ignored.

    Generate once the content mod list has settled: the data is a snapshot of
    whatever worldgen existed at scan time, so adding an ore mod invalidates it.
    """
    # JER reads FMLPaths.CONFIGDIR/world-gen.json -- the config/ root, not a
    # jeresources/ subfolder. Wrong location loads silently with no graphs.
    wanted = os.path.join(ROOT, "config", "world-gen.json")
    if os.path.exists(wanted):
        return
    print("\nTODO: JER worldgen data not present.")
    print("   Modded ores (Mekanism etc) show no distribution in JEI without it.")
    print("   /jer_profile is registered but not implemented on 1.21.1.")
    print("   Run the whole pipeline unattended, from the pack root:")
    print("       .\\deploy\\build-jer-worldgen.ps1")
    print("   It installs a throwaway server under server/, syncs the pack with")
    print("   side=server, pregenerates each dimension with Chunky, scans the")
    print("   region files with RegionScanner, and writes the JSON into config/.")
    print("   Add dimensions as ore mods arrive:")
    print("       -Dims minecraft:overworld,minecraft:the_nether,mypack:mining")
    print("   Do this once the content mod list has settled; it is a snapshot.")


def main():
    indexed = indexed_files()

    # Indexed but gitignored -> the file never reaches the repo, so every client
    # reads it from index.toml and gets a 404.
    ghosts = sorted(ignored_files(indexed))
    if ghosts:
        print("FAIL: indexed but GITIGNORED -- clients will 404 on these:")
        for g in ghosts:
            print("   " + g)
        print("\nFix: add the pattern to .packwizignore (packwiz does not read"
              " .gitignore), then re-run `packwiz refresh`.")
        return 1

    print("OK: none of the {} indexed files are gitignored".format(len(indexed)))
    jer_reminder()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
