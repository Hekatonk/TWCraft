#!/usr/bin/env python3
"""Find the newest *stable* CurseForge file for this pack's MC version + loader.

packwiz cannot do this itself. Its CurseForge resolver ranks candidate files by
Minecraft version, then loader, then file ID -- and file ID is just upload
recency. `releaseType` is parsed off the API and then never consulted; there is a
literal `// TODO: manage alpha/beta/release correctly` in curseforge.go. So any
beta uploaded after the last stable release wins, silently.

This prints the file ID of the newest release-type file, to feed to:

    packwiz curseforge add <slug> --file-id <id>
    packwiz pin <slug>

Data comes from api.cfwidget.com, a public read-only CurseForge proxy, because
api.curseforge.com needs a personal API key. cfwidget builds its cache lazily and
answers HTTP 202 while a project is still being fetched, so retry on 202.

    python scripts/cf-stable.py <slug> [<slug> ...]
    python scripts/cf-stable.py 1637623            # by numeric project ID
    python scripts/cf-stable.py --any-type <slug>  # list every channel
"""
import json
import sys
import time
import urllib.error
import urllib.request

MC = "1.21.1"
LOADER = "NeoForge"
UA = {"User-Agent": "TWCraft-packwiz/1.0"}

# Some CurseForge titles carry emoji (Jade is literally "Jade \U0001f50d"), which
# blow up on a cp1252 Windows console. Degrade those characters instead of dying.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def fetch(slug, attempts=6):
    # A bare number is a CurseForge project ID; cfwidget serves those directly.
    # Useful when a project's CurseForge slug differs from its Modrinth one.
    if slug.isdigit():
        url = "https://api.cfwidget.com/" + slug
    else:
        url = "https://api.cfwidget.com/minecraft/mc-mods/" + slug
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as r:
                if r.status == 202:  # still being indexed; come back shortly
                    time.sleep(3 + 2 * i)
                    continue
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 202:
                time.sleep(3 + 2 * i)
                continue
            if e.code == 404:
                # cfwidget's slug index has gaps -- a 404 here means cfwidget
                # cannot serve it, NOT that the project is absent from
                # CurseForge. Entity Culling (448233) 404s by slug and resolves
                # fine by ID. Always confirm with the numeric project ID before
                # concluding a mod is missing from CurseForge.
                return None
            raise
    raise SystemExit(slug + ": cfwidget returned no data after retries")


def candidates(data, any_type=False):
    out = []
    for f in data.get("files", []):
        vs = f.get("versions") or []
        if MC not in vs or LOADER not in vs:
            continue
        if not any_type and f.get("type") != "release":
            continue
        out.append(f)
    # Highest file ID is the most recently uploaded, matching packwiz's ordering.
    return sorted(out, key=lambda f: f.get("id", 0), reverse=True)


def report(slug, any_type):
    data = fetch(slug)
    if data is None:
        print("\n=== " + slug + " ===")
        print("  cfwidget has no data for this slug. This does NOT mean the mod"
              " is absent from CurseForge --")
        print("  cfwidget's slug index has gaps. Get the project ID and retry"
              " with it before giving up:")
        print("    packwiz curseforge add https://www.curseforge.com/minecraft"
              "/mc-mods/" + slug)
        print("  (a bare slug makes packwiz *search*, which can match the wrong"
              " project; a full URL does an exact lookup)")
        return
    title = data.get("title", slug)
    print("\n=== {} (project {}) ===".format(title, data.get("id")))
    found = candidates(data, any_type)
    if not found:
        total = len(candidates(data, any_type=True))
        print("  NO stable {}/{} file ({} file(s) exist in other channels)"
              .format(MC, LOADER, total))
        return
    for f in found[:5]:
        print("  id={:<10} {:<8} {:<58} {}".format(
            f["id"], f.get("type"), f.get("name"),
            (f.get("uploaded_at") or "")[:10]))
    print("  -> packwiz curseforge add {} --file-id {}".format(slug, found[0]["id"]))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    any_type = "--any-type" in sys.argv
    for slug in args:
        report(slug, any_type)


if __name__ == "__main__":
    main()
