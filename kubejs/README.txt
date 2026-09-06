KubeJS scripts. Loaded by KubeJS at the matching stage:

  startup_scripts/  registry work -- new items/blocks/fluids. Reloads only on
                    a full game restart.
  server_scripts/   recipes, tags, loot. Reloads with /reload.
  client_scripts/   tooltips, JEI/EMI tweaks, anything display-only.

These are indexed by packwiz and ship to every client, so a syntax error here
breaks the pack for everyone -- test with /reload before committing.

probe/ and typings/ are ProbeJS output: generated in-game, tens of MB, and
excluded from both git and the packwiz index. Do not commit them.
