#!/usr/bin/env python3
"""Regenerate MODS.md from mods/*.pw.toml.

Links and versions come from the pack metadata plus api.cfwidget.com, so they
cannot drift from what is actually installed. The prose -- what each mod does and
which config dials matter -- lives in NOTES below and is maintained by hand.

    python scripts/gen-mods-md.py

When adding a mod, add a NOTES entry for it. The script fails loudly if one is
missing, so MODS.md can never quietly fall behind the pack.
"""
import glob
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "TWCraft-packwiz/1.0"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# GitHub repos that Modrinth does not know about, or gets wrong. A Modrinth
# title-search fallback confidently returned four unrelated repos (a "Quasi
# Crafter Connectivity", a "BTA Cupboards"), so sources are pinned here by hand
# rather than guessed. None means genuinely closed-source.
SRC = {
    "catalogue": "https://github.com/MrCrayfish/Catalogue",
    "crash-utilities": "https://github.com/Darkere/CrashUtilities",
    "alltheleaks": "https://github.com/pietro-lopes/AllTheLeaks",
    "extreme-sound-muffler": "https://github.com/LeoBeliik/ExtremeSoundMuffler",
    "ferritecore": "https://github.com/malte0811/FerriteCore",
    "ftb-backups-3": "https://github.com/FTBTeam/FTB-Backups-3",
    "ftb-library-forge": "https://github.com/FTBTeam/FTB-Library",
    "ftb-quests-forge": "https://github.com/FTBTeam/FTB-Quests",
    "ftb-chunks-forge": "https://github.com/FTBTeam/FTB-Chunks",
    "ftb-teams-forge": "https://github.com/FTBTeam/FTB-Teams",
    "connectivity": None,      # someaddon, no public repo found
    "cupboard": None,          # someaddon, no public repo found
    "memory-settings": None,   # someaddon, no public repo found
    # Modrinth's "bookshelf" slug is LOOHP's decorative bookshelf mod, not
    # Darkhax's library. Every source link here was checked against the repo's
    # own description; do not trust a slug match alone.
    "bookshelf": "https://github.com/Darkhax-Minecraft/Bookshelf",
}

# Loader/build suffixes that are not part of a version number.
_TRAILING = ("neoforge", "forge", "fabric", "quilt", "all", "universal")

# slug -> (group, what it does, config dials)
NOTES = {
    # --- Scripting -------------------------------------------------------
    "kubejs": ("Scripting", "Server/client/startup scripting for recipes, tags, loot and custom content.",
               "Scripts live in `kubejs/`. `/reload` applies server scripts; startup scripts need a restart."),
    "rhino": ("Scripting", "JavaScript engine KubeJS runs on.", "None. Must satisfy KubeJS's `[2101.2.7-build.81,)` range."),
    "probejs": ("Scripting", "Generates TypeScript typings for KubeJS autocomplete.",
                "Run `/probejs dump` in game. Output lands in `kubejs/probe/` and is gitignored and packwizignored."),
    "better-advanced-tooltips": ("Scripting", "Tag/component tooltips in F3+H mode.", "None. Optional KubeJS companion."),

    # --- Performance -----------------------------------------------------
    "modernfix": ("Performance", "Broad startup/memory optimisations; the single biggest load-time win.",
                  "`config/modernfix-mixins.properties` — dynamic resources and a few features are opt-in."),
    "ferritecore": ("Performance", "Cuts blockstate memory use substantially.", "`config/ferritecore-mixin.toml`. Defaults are fine; disable only to isolate a crash."),
    "embeddium": ("Performance", "Sodium-derived renderer; large FPS gain.", "In-game Video Settings. Conflicts with Sodium — never both."),
    "immediatelyfast": ("Performance", "Batches immediate-mode rendering (HUD, text, GUIs).", "`config/immediatelyfast.json`. Disable `experimental` toggles if HUD mods misbehave."),
    "entityculling": ("Performance", "Skips rendering entities hidden behind blocks.", "`config/entityculling.json` — tracing interval and per-entity opt-outs."),
    "alltheleaks": ("Performance", "Patches memory leaks in MC, NeoForge and common mods.", "None. Drop-in."),
    "fastworkbench": ("Performance", "Caches crafting-table recipe lookups.", "None."),
    "fastfurnace": ("Performance", "Caches furnace recipe lookups.", "None."),
    "fastsuite": ("Performance", "Speeds the JSON recipe system generally.", "None. Scales with total recipe count."),
    "model-gap-fix": ("Performance", "Fixes seams between item/block model layers.", "`config/modelgapfix.json` if item rendering looks off."),
    "zfastnoise": ("Performance", "Faster noise generation for worldgen.", "None. Drop-in."),
    "alternate-current": ("Performance", "Rewrites the redstone dust update algorithm; much faster and order-compatible.", "None."),
    "clumps": ("Performance", "Merges XP orbs into single entities.", "`config/clumps.toml` — clump radius."),
    "get-it-together-drops": ("Performance", "Merges dropped item entities more aggressively.", "`config/getittogetherdrops.json` — merge radius and per-item exclusions."),
    "im-fast": ("Performance", "Suppresses \"moved too quickly/wrongly\" server rejections.", "`config/imfast.toml` — speed thresholds."),

    # --- Stability / fixes ----------------------------------------------
    "connectivity": ("Stability", "Fixes login timeouts, packet-size errors and ghost blocks.", "`config/connectivity.json` — raise timeouts and payload limits for a heavy pack."),
    "attributefix": ("Stability", "Removes vanilla's hard caps on attribute values.", "`config/attributefix.json` — per-attribute min/max. Needed once mods push attributes past vanilla limits."),
    "lmft": ("Stability", "Stops one bad tag entry from voiding an entire tag.", "None. Drop-in; logs offending entries."),
    "crash-assistant": ("Stability", "Shows a GUI after a crash and analyses the log.", "None."),
    "polymorph": ("Stability", "Recipe-conflict resolver — pick which output when recipes collide.", "None until two mods collide; then choose per-recipe in the GUI."),
    "almostunified": ("Stability", "Unifies duplicate ores/ingots across mods to one canonical item.",
                      "`config/almostunified/` — mod priority list. **Currently inert**: no content mods to unify."),

    # --- World / server --------------------------------------------------
    "in-control": ("World & server", "Rule-based control over mob spawning.", "`config/incontrol/spawn.json` etc. Empty rules = no effect; this is the main dial for spawn tuning."),
    "observable": ("World & server", "Profiles which entities/chunks are costing tick time.", "`/observable` in game. No config."),
    "crash-utilities": ("World & server", "Admin commands for diagnosing a struggling server.", "`config/crashutilities.toml`."),
    "memory-settings": ("World & server", "Warns at startup if allocated RAM is unreasonable.", "`config/memorysettings.txt` — set min/max expectations for the pack."),
    "better-compatibility-checker": ("World & server", "Compares pack name/version between client and server and refuses mismatches.",
                                     "`config/bcc-common.toml` — **must** carry the pack name and version, or it cannot compare."),
    "no-villager-death-messages": ("World & server", "Removes villager death spam from the console.", "None."),
    "ftb-backups-3": ("World & server", "Scheduled world backups.", "`config/ftbbackups3.snbt` — interval, retention count, compression."),
    "no-chat-reports": ("World & server", "Removes chat signing/reporting.", "`config/noChatReports/` — server can force-disable."),
    "neoauth": ("World & server", "Re-authenticates a stale Microsoft session without a launcher restart.", "None. Single 2024 build; check first if logins break."),

    # --- FTB suite -------------------------------------------------------
    "ftb-library-forge": ("FTB suite", "Shared library and GUI toolkit for the FTB mods.", "None directly."),
    "ftb-teams-forge": ("FTB suite", "Team/party system underpinning Chunks and Quests.", "`config/ftbteams.snbt` — auto-create party, display names."),
    "ftb-chunks-forge": ("FTB suite", "Chunk claiming, force-loading and a built-in minimap.",
                         "`config/ftbchunks.snbt` — **max claimed and force-loaded chunks per player**. Its minimap overlaps Xaero's; disable one."),
    "ftb-quests-forge": ("FTB suite", "Quest/progression system.", "Quests live in `config/ftbquests/`. Edit in game with `/ftbquests editing_mode`."),

    # --- Client QoL ------------------------------------------------------
    "jade": ("Client QoL", "Looked-at block/entity info overlay (HWYLA/WAILA successor).", "In-game config (`\\` key) — per-provider toggles and overlay position."),
    "appleskin": ("Client QoL", "Shows saturation and hunger restored on food tooltips.", "None."),
    "controlling": ("Client QoL", "Search and conflict detection in the keybind screen.", "None."),
    "mouse-tweaks": ("Client QoL", "Drag-move, drag-drop and RMB item spreading in inventories.", "`config/mousetweaks.json` if a modded GUI misbehaves."),
    "inventory-sorter": ("Client QoL", "Middle-click sorting for any inventory.", "`config/inventorysorter.toml` — sort key and per-container blacklist."),
    "crafting-tweaks": ("Client QoL", "Rotate/balance/clear the crafting grid by hotkey.", "Keybinds in Controls; `config/craftingtweaks.json`."),
    "netherportalfix": ("Client QoL", "Keeps Nether portal round-trips returning to the right portal.", "None."),
    "extreme-sound-muffler": ("Client QoL", "Muffle individual sounds from an in-game GUI.", "In-game GUI (default `\\`). Per-sound, client-only."),
    "bad-wither-no-cookie-reloaded": ("Client QoL", "Localises wither/dragon death sounds so they aren't global.", "`config/bwncr.toml` — sound blocklist."),
    "colorful-hearts": ("Client QoL", "Collapses stacked heart rows into one coloured row.", "`config/colorfulhearts.json` — heart style, absorption display."),
    "fuelgoeshere": ("Client QoL", "Shift-clicking fuel sends it to the fuel slot, not the input slot.", "None."),
    "accelerated-decay": ("Client QoL", "Speeds up leaf decay after chopping a tree.", "`config/accelerated-decay.toml` — decay rate."),
    "client-tweaks": ("Client QoL", "Grab-bag of small client fixes and conveniences.", "`config/clienttweaks.toml` — every tweak toggles independently."),
    "corpse": ("Client QoL", "Death drops go into a lootable corpse instead of scattering.", "`config/corpse.json` — despawn time, whether others can rob it. **Beta build; no release exists for 1.21.1.**"),
    "configured": ("Client QoL", "In-game editor for other mods' configs.", "None; it is the UI for everything else."),
    "catalogue": ("Client QoL", "Redesigned mod list with search and icons.", "None."),

    # --- Libraries -------------------------------------------------------
    "architectury-api": ("Libraries", "Cross-loader abstraction layer.", "None."),
    "balm": ("Libraries", "BlayTheNinth's shared abstraction layer.", "None."),
    "bookshelf": ("Libraries", "Darkhax's shared utility library.", "None."),
    "placebo": ("Libraries", "Shadows' shared library (FastWorkbench et al).", "None."),
    "prickle": ("Libraries", "JSON-based config framework used by AttributeFix.", "None."),
    "cloth-config": ("Libraries", "Config screen framework.", "None."),
    "cupboard": ("Libraries", "someaddon's shared utility library.", "None."),
    "kotlin-for-forge": ("Libraries", "Kotlin runtime for mods written in Kotlin.", "None."),
    "searchables": ("Libraries", "Shared search-bar framework used by Controlling.", "None."),
}


def j(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            return json.load(r)
    except Exception:
        return None


def load_mods():
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "mods", "*.pw.toml"))):
        t = open(path, encoding="utf-8").read()
        pid = re.search(r"project-id = (\d+)", t)
        out.append({
            "slug": os.path.basename(path)[:-8],
            "name": re.search(r'^name = "(.*)"', t, re.M).group(1),
            "file": re.search(r'^filename = "(.*)"', t, re.M).group(1),
            "side": re.search(r'^side = "(.*)"', t, re.M).group(1),
            "pinned": "pin = true" in t,
            "pid": pid.group(1) if pid else None,
        })
    return out


def enrich(m):
    if m["pid"]:
        d = j("https://api.cfwidget.com/" + m["pid"])
        if d:
            m["cf"] = (d.get("urls") or {}).get("curseforge")
    if m["slug"] in SRC:
        m["src"] = SRC[m["slug"]]
    else:
        p = j("https://api.modrinth.com/v2/project/" + m["slug"])
        m["src"] = (p or {}).get("source_url")
    return m


def version_of(filename):
    """Best-effort human version pulled out of the jar filename.

    Jar names are wildly inconsistent (`architectury-13.0.11-neoforge`,
    `zfastnoise-1.0.13+1.21.1+neoforge`, `kotlinforforge-5.12.0-all`), so strip
    leading name tokens until something starts with a digit, then drop trailing
    loader tokens.
    """
    stem = filename[:-4] if filename.endswith(".jar") else filename
    parts = stem.split("-")
    while parts and not parts[0][:1].isdigit():
        parts.pop(0)
    if not parts:
        return stem
    # A lone leading digit is part of the mod's name, not its version
    # ("ftb-backups-3-21.1.5" is Backups *3* at version 21.1.5).
    if len(parts) > 1 and len(parts[0]) == 1 and parts[0].isdigit():
        parts.pop(0)
    while len(parts) > 1 and parts[-1].lower() in _TRAILING:
        parts.pop()
    ver = "-".join(parts)
    # Same treatment for '+'-delimited loader suffixes.
    plus = ver.split("+")
    while len(plus) > 1 and plus[-1].lower() in _TRAILING:
        plus.pop()
    return "+".join(plus)


def main():
    mods = load_mods()
    missing = [m["slug"] for m in mods if m["slug"] not in NOTES]
    if missing:
        raise SystemExit("No NOTES entry for: " + ", ".join(missing)
                         + "\nAdd one to scripts/gen-mods-md.py, then re-run.")
    with ThreadPoolExecutor(max_workers=8) as ex:
        mods = list(ex.map(enrich, mods))

    groups = {}
    for m in mods:
        group, what, dials = NOTES[m["slug"]]
        groups.setdefault(group, []).append((m, what, dials))

    order = ["Scripting", "Performance", "Stability", "World & server",
             "FTB suite", "Client QoL", "Libraries"]
    lines = [
        "# TWCraft mods",
        "",
        "Minecraft **1.21.1** / **NeoForge 21.1.249**. "
        f"**{len(mods)} mods**, every one pinned to a specific file.",
        "",
        "Generated by `scripts/gen-mods-md.py` — edit the notes there, not here.",
        "Links and versions come from the pack metadata, so they cannot drift from",
        "what is installed.",
        "",
        "`side` is what packwiz installs: `both` goes to clients and servers,",
        "`client` is skipped on a server.",
        "",
    ]
    for g in order:
        rows = sorted(groups.get(g, []), key=lambda r: r[0]["name"].lower())
        if not rows:
            continue
        lines += [f"## {g}", "",
                  "| Mod | Version | Side | What it does | Config dials |",
                  "|---|---|---|---|---|"]
        for m, what, dials in rows:
            links = []
            if m.get("cf"):
                links.append(f"[CF]({m['cf']})")
            if m.get("src"):
                links.append(f"[src]({m['src']})")
            link_txt = " ".join(links)
            name = m["name"].replace("|", "\\|")
            lines.append(
                f"| **{name}**<br>{link_txt} | `{version_of(m['file'])}` | {m['side']} "
                f"| {what} | {dials} |")
        lines.append("")

    nosrc = sorted(m["slug"] for m in mods if not m.get("src"))
    lines += [
        "## Notes",
        "",
        "- **Every mod is pinned.** `packwiz update --all` is a no-op by design; see",
        "  the update flow in `README.md`.",
        "- **No public source repo** for: " + ", ".join(f"`{s}`" for s in nosrc)
        + " — closed-source, CurseForge only.",
        "- **Corpse** and **In Control!** have no release-channel build for 1.21.1;",
        "  both run the beta, matching what All the Mods 10 ships.",
        "",
    ]
    out = os.path.join(ROOT, "MODS.md")
    open(out, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    print(f"wrote {out} ({len(mods)} mods, {len(order)} groups)")


if __name__ == "__main__":
    main()
