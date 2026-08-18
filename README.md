# Blockheads 1.7 mod IPA

GitHub Action rebuilds an **unsigned** IPA for **Sideloadly**.

The first build crashed because a new Mach-O segment was appended after `LINKEDIT`. This rebuild patches unused Fabric code **inside** `__TEXT` and leaves the load commands alone. Sideloadly then signs it.

## One-time: upload the original CrackerXI IPA

```
gh release create base "C:\Users\cedri\Downloads\Blockheads_1.7_64bit_CrackerXI.ipa" --title "original" --notes "base IPA"
```

## Build

Actions → **Build Sideloadly IPA** → Run workflow.

Download the artifact `Blockheads_1.7_mod.ipa` and open it in Sideloadly.

## What the patch does

- Copies the bundled `portalChest` into `Library/Application Support/portalChest` the first time a world loads.
- A new Blockhead gets a Portal Chest (item 1074) in inventory.

Delete any old Blockheads install (or the sandbox `portalChest`) before the first launch if you want the bundled chest to copy.
