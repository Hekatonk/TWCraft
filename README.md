# TWCraft

A Minecraft **1.21.1** / **NeoForge 21.1.249** modpack, managed with [packwiz](https://packwiz.infra.link/).

## Installing (players)

1. Install [Prism Launcher](https://prismlauncher.org/).
2. Download `TWCraft.zip` from the [latest release](https://github.com/Hekatonk/TWCraft/releases/latest).
3. In Prism: **Add Instance → Import from zip**, pick the file, launch.

The instance ships with no mods. On first launch packwiz-installer downloads
everything and keeps it in sync on every subsequent launch — you never
re-download the instance to update, you just start the game.

Java 21 is required. Prism will offer to download it if you don't have it.

## Working on the pack (maintainers)

Everything under `mods/`, `config/`, `kubejs/`, `defaultconfigs/`,
`resourcepacks/` and `shaderpacks/` is pack content and is shipped to clients.
`index.toml` is generated — never hand-edit it.

```sh
# add a mod -- CurseForge first, Modrinth as the fallback. Both track updates,
# so `update --all` works whichever one a mod came from.
packwiz curseforge add <slug>
packwiz modrinth add <slug>

# Some CurseForge projects set allowModDistribution=false. packwiz adds them
# fine, but the CF API then serves no download URL, so packwiz-installer can't
# fetch the jar and every player has to download it by hand -- which defeats the
# point of this pack. Move those to Modrinth.
#
# NOT `packwiz github add`: it resolves only a repo's LATEST release, so a mod
# maintaining both 1.20.1 and 1.21.1 branches resolves to whichever shipped
# last. It will happily install a 1.20.1 Forge jar into this pack.
#
# Whatever the source, check the installed filename's loader and MC version.

# remove one
packwiz remove jei

# Every mod is PINNED to a specific stable file, so this is a no-op by design --
# it exists to stop packwiz silently moving a mod onto a beta. To update a mod,
# find its newest stable file and re-add at that exact id:
#
#   python scripts/cf-stable.py <slug>
#   packwiz remove <slug>
#   packwiz curseforge add <slug> --file-id <id>
#   packwiz pin <slug>
#
# (re-apply any `side` override afterwards -- re-adding resets it)
packwiz update --all

# after editing config/, kubejs/ or anything else by hand
packwiz refresh
```

`packwiz refresh` rewrites `index.toml` and the hash in `pack.toml`. **Run it
before every commit** — clients validate against those hashes, so an unrefreshed
index means they silently get stale files.

### Testing locally

```sh
packwiz serve                                    # serves the pack on :8080
./deploy/build-prism-instance.ps1 -PackUrl http://localhost:8080/pack.toml
```

Import the resulting zip into Prism to check the pack end to end before publishing.

### Releasing

```sh
packwiz refresh
git commit -am "..." && git push

./deploy/build-prism-instance.ps1 `
  -PackUrl https://raw.githubusercontent.com/Hekatonk/TWCraft/main/pack.toml
```

Attach `deploy/out/TWCraft.zip` to a GitHub release. Players only need a new zip
when the **loader or Minecraft version** changes — mod changes reach them through
packwiz on the next launch.

## Why the instance ships empty

If the launcher installed the mods, packwiz would have no record of them and
could never delete them. Dropping a mod from the pack would leave it behind on
every client forever, and the pack would drift out of sync one stale jar at a
time. Letting packwiz-installer do all the downloading means it owns every file
it later needs to remove.

The corollary: **never put a jar in `mods/` by hand.** Only `.pw.toml` metadata
files belong there.
