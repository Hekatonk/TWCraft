#!/usr/bin/env python3
"""Strip non-resource blocks out of a RegionScanner world-gen.json.

RegionScanner counts *every* block it sees, so a raw scan is ~75% terrain and
scenery: air, stone, deepslate, water, clay, leaves, kelp, plus mineshaft and
stronghold furniture (rail, chest, spawner, cobweb, stone_bricks). Shipping that
gives JER's World Gen tab a distribution page for dirt, which is noise.

RegionScanner's own --only-blocks-above filters by *frequency*, which is the
wrong lever: it drops rare things first, so it would cut uranium and emerald long
before it cut dirt. Filtering by block identity is what actually works.

Rule: keep anything whose id ends in `_ore`, plus the explicit KEEP set below for
resources that are not named "ore". Matching on the id *ending* matters --
a substring test on "ore" also matches `minecraft:spore_blossom`.

    python scripts/filter-worldgen.py <in.json> [-o <out.json>]

Writes in place when -o is omitted. Prints what it kept and dropped.
"""
import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# Resources whose block id does not end in "_ore".
KEEP = {
    "minecraft:ancient_debris",
    "minecraft:budding_amethyst",
    "minecraft:glowstone",
    "minecraft:gilded_blackstone",
    "minecraft:sculk_catalyst",
    "mekanism:block_salt",
}


def is_resource(block_id):
    return block_id.endswith("_ore") or block_id in KEEP


def merge(existing, fresh, scanned_dims):
    """Combine a new scan into previously shipped data, per dimension.

    Necessary because JER treats the DIY file as a total replacement, not a
    supplement: Compatibility.init() calls MinecraftCompat.init(false) and
    JERAPI.commit(false) whenever config/world-gen.json exists, so any dimension
    missing from the file has *no* data at all -- not even vanilla's built-in
    numbers. A Nether-only scan written over an Overworld file would therefore
    silently delete every Overworld ore graph.

    Entries for dimensions in this scan are replaced; entries for every other
    dimension are carried through untouched.
    """
    carried = [e for e in existing if e.get("dim") not in scanned_dims]
    return carried + fresh, carried


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("-o", "--out")
    ap.add_argument("--merge-into", metavar="FILE",
                    help="existing world-gen.json to merge with, per dimension")
    args = ap.parse_args()

    data = json.load(open(args.path, encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("Expected a JSON list; got " + type(data).__name__)

    kept, dropped = [], []
    for entry in data:
        block = entry.get("block", "")
        (kept if is_resource(block) else dropped).append(block)

    out = [e for e in data if is_resource(e.get("block", ""))]
    print("kept {} resource entries, dropped {} terrain/scenery entries"
          .format(len(kept), len(dropped)))
    for b in sorted(set(kept)):
        print("   keep  " + b)
    print("   ---")
    for b in sorted(set(dropped))[:12]:
        print("   drop  " + b)
    if len(set(dropped)) > 12:
        print("   drop  ... and {} more".format(len(set(dropped)) - 12))

    if args.merge_into and os.path.exists(args.merge_into):
        existing = json.load(open(args.merge_into, encoding="utf-8"))
        scanned = {e.get("dim") for e in out}
        out, carried = merge(existing, out, scanned)
        print("\nmerged into {}".format(args.merge_into))
        print("   scanned dims  : " + ", ".join(sorted(d for d in scanned if d)))
        print("   carried over  : {} entries from {}".format(
            len(carried), ", ".join(sorted({e.get("dim") for e in carried})) or "nothing"))

    dest = args.out or args.path
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1)
    dims = sorted({e.get("dim") for e in out if e.get("dim")})
    print("wrote {} ({} entries across {})".format(
        os.path.abspath(dest), len(out), ", ".join(dims)))


if __name__ == "__main__":
    main()
