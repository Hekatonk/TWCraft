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
    "irisshaders": "https://github.com/IrisShaders/Iris",
    "bad-wither-no-cookie-reloaded": "https://github.com/droidicus/BadWitherNoCookie",
    "inventory-sorter": None,  # no public repo found
    "model-gap-fix": None,     # no public repo found
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
    "sodium": ("Performance", "The renderer replacement; large FPS gain. Official NeoForge build from CaffeineMC.",
               "In-game Video Settings. Replaced Embeddium, whose 1.21.1 branch ended 2025-01-24 and which Iris declares `incompatible` with."),
    "sodium-extra": ("Performance", "Adds back the extra toggles Sodium omits: FPS limiter, particle/fog/animation controls.",
                     "In-game Video Settings -> Extra."),
    "irisshaders": ("Performance", "Shader-pack support (OptiFine-format packs) on top of Sodium.",
                    "Drop shaderpacks in `shaderpacks/`, select in Video Settings. **Beta build on purpose**: the stable 1.8.12 targets Sodium 0.6, this targets Sodium 0.8 to match our 0.8.13 — same pairing All the Mods 10 ships."),
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

    "spark": ("Performance", "Profiler for client and server: what is eating tick time, memory or CPU.",
              "`/spark profiler start` then `/spark profiler stop` for a shareable report; `/spark tps`, `/spark healthreport`."),
    "not-enough-animations": ("Performance", "Adds first-person-style animations to the third-person model.",
                              "`config/notenoughanimations.json` — every animation toggles independently."),

    # --- Stability / fixes ----------------------------------------------
    "connectivity": ("Stability", "Fixes login timeouts, packet-size errors and ghost blocks.", "`config/connectivity.json` — raise timeouts and payload limits for a heavy pack."),
    "attributefix": ("Stability", "Removes vanilla's hard caps on attribute values.", "`config/attributefix.json` — per-attribute min/max. Needed once mods push attributes past vanilla limits."),
    "lmft": ("Stability", "Stops one bad tag entry from voiding an entire tag.", "None. Drop-in; logs offending entries."),
    "crash-assistant": ("Stability", "Shows a GUI after a crash and analyses the log.", "None."),
    "polymorph": ("Stability", "Recipe-conflict resolver — pick which output when recipes collide.", "None until two mods collide; then choose per-recipe in the GUI."),
    "almostunified": ("Stability", "Unifies duplicate ores/ingots across mods to one canonical item.",
                      "`config/almostunified/` — mod priority list. **Currently inert**: no content mods to unify."),

    "yeetusexperimentus": ("Stability", "Skips the \"experimental settings\" warning screen on world creation and server start.",
                           "None. Relevant as soon as KubeJS or datapacks touch worldgen."),

    # --- World / server --------------------------------------------------
    "in-control": ("World & server", "Rule-based control over mob spawning.", "`config/incontrol/spawn.json` etc. Empty rules = no effect; this is the main dial for spawn tuning."),
    "chunky-pregenerator-forge": ("World & server", "Pregenerates chunks in the background so exploration does not stutter.",
        "`/chunky radius <blocks>` then `/chunky start`; `/chunky pause`/`/chunky continue`. Also the pregeneration step for JER worldgen data -- see scripts/check-index.py."),
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
    "enchantment-descriptions": ("Client QoL", "Spells out on the tooltip what each enchantment actually does.",
                                "`config/enchdesc.json` — tooltip format and whether to require Shift."),
    "more-overlays-updated": ("Client QoL", "Light-level and chunk-border overlays for spawn-proofing.",
                              "Keybinds in Controls (light overlay, chunk bounds); `config/moreoverlays-client.toml` for the spawn threshold."),
    "keybind-bundles": ("Client QoL", "Chord keybinds, for when you run out of single keys.",
                        "Configured in game. Pairs with Default Options — bundle assignments are part of what `/defaultoptions saveOptions` captures."),
    "configured": ("Client QoL", "In-game editor for other mods' configs.", "None; it is the UI for everything else."),
    "default-options": ("Client QoL", "Ships pack default keybinds/options that apply on first run only, without overwriting a returning player's options.txt.",
                        "Set binds in game, then `/defaultoptions saveOptions` writes them to `config/defaultoptions/`. `config/defaultoptions/extra/` is copied into the instance on first run. Never ship `defaultoptions.journal.json` — it is the per-install first-run tracker and is git/packwiz-ignored."),
    "catalogue": ("Client QoL", "Redesigned mod list with search and icons.", "None."),

    # --- Recipe viewer ---------------------------------------------------
    "jei": ("Recipe viewer", "Item and recipe browser; the backbone the addons plug into.",
            "`config/jei/` — blacklist items, cheat-mode permissions. `/jei` opens config in game."),
    "just-enough-professions-jep": ("Recipe viewer", "Shows which workstation gives a villager each profession.", "None."),
    "justenoughbreeding": ("Recipe viewer", "Shows what each mob breeds with. Works with JEI, REI and EMI.", "None."),
    "just-enough-archaeology": ("Recipe viewer", "Suspicious sand/gravel loot tables. JEI and EMI.", "None."),
    "smithing-template-viewer": ("Recipe viewer", "Previews how a smithing template looks on armour. JEI and EMI.",
                                 "None. **Beta build**: no release exists for 1.21.1, same file All the Mods 10 ships."),
    "just-enough-resources-jer": ("Recipe viewer", "Adds mob drops, ore distribution by dimension, plant and dungeon loot pages to JEI.",
                                 "`config/world-gen.json` holds the ore distributions (config root, NOT a jeresources/ subfolder -- JER reads FMLPaths.CONFIGDIR). Regenerate with `deploy/build-jer-worldgen.ps1`. **Alpha build**: every 1.21.1 file is alpha, and this exact file is what All the Mods 10: To the Sky ships."),
    "ftb-jei-extras": ("Recipe viewer", "Bridges the FTB mods into JEI.", "None."),
    "ae2-jei-integration": ("Recipe viewer", "Restores AE2 recipe support in JEI.",
                            "None. Hard-requires `ae2` — it will block startup if AE2 is removed."),
    "just-enough-mekanism-multiblocks": ("Recipe viewer", "JEI pages costing out Mekanism multiblock builds.",
                                         "None. Hard-requires `mekanism` — it will block startup if Mekanism is removed."),

    # --- Content ---------------------------------------------------------
    "applied-energistics-2": ("Content", "Digital storage, autocrafting and channel-based networks.",
                              "`config/ae2/` — channel mode (`channels = infinite` removes channel management entirely) and energy rates."),
    "mekanism": ("Content", "Tech: ore processing chains, factories, gas/chemical systems, power.",
                 "`config/mekanism/` — ore-multiplier tiers and machine energy use. Generators/Tools/Additions are separate mods, not installed."),
    "guideme": ("Libraries", "In-game guidebook framework AE2 uses for its manual.", "None. AE2 dependency."),

    # --- AE2 addons ------------------------------------------------------
    "applied-energistics-2-wireless-terminals": ("AE2 addons", "Wireless crafting, pattern-access and fluid terminals.",
        "Craft a Wireless Terminal + Quantum Bridge/booster; range set by AEInfinityBooster's cards if installed."),
    "applied-mekanistics": ("AE2 addons", "Official Mekanism bridge: store and autocraft Mekanism chemicals/gases in the ME network.",
        "None. Needs both AE2 and Mekanism, which the pack has."),
    "polymorphic-energistics": ("AE2 addons", "Polymorph support inside AE2 autocrafting, so ambiguous recipes resolve.",
        "None. Pairs with Polymorph, already in the pack."),
    "ae2-crafting-tree": ("AE2 addons", "Renders an autocrafting job as a tree — the tool for finding why a craft is stuck.",
        "None."),
    "ae2-network-analyser": ("AE2 addons", "Handheld tool visualising channel usage and network topology.", "None."),
    "mega-cells": ("AE2 addons", "Much larger ME storage cells and matching housings.",
        "`config/megacells-common.toml` — cell capacities if you want them toned down."),
    "merequester": ("AE2 addons", "Keeps items and fluids stocked to a set level automatically.", "None."),
    "ae2-import-export-card": ("AE2 addons", "Import and export upgrade cards for AE2 buses.", "None."),
    "aeinfinitybooster": ("AE2 addons", "Infinity Range and Dimension cards for wireless terminals.",
        "`config/aeinfinitybooster.toml` — power draw; the point is removing range limits, so expect balance impact."),
    "ex-pattern-provider": ("AE2 addons", "ExtendedAE: assorted AE2 QoL blocks (pattern providers, crafting extensions).",
        "`config/extendedae-common.toml`."),
    "applied-flux": ("AE2 addons", "Store FE/energy inside the ME network.", "None."),
    "glodium": ("Libraries", "Render/registry/network helper library used by AE2 Network Analyser.", "None. Dependency."),

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
             "FTB suite", "Client QoL", "Recipe viewer", "Content", "AE2 addons",
             "Libraries"]
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
