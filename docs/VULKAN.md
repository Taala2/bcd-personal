# Vulkan in BCD

madeira-bcd personal: upstream's own docs/VULKAN.md is not in the public bcd-games tree, although tests/host/check-moltenvk-patched.py
reads it. This copy is written in the fork and so far has only the section that check needs. If upstream publishes its file, take upstream's.

## Yamalı MoltenVK (build 669): `vulkan = moltenvk-patched`

Yamalı means "patched". With `vulkan = moltenvk-patched` in a game's own file, Wine's Vulkan runs on `gl/libMoltenVK_patched.dylib`.
That library is MoltenVK v1.4.2 plus later MoltenVK work, nivogli's Enshrouded changes, and RageMC's fixes, with SPIRV-Cross plus one
patch (bcd-games pull request #57 by RageMC).

`build/moltenvk-ios/build-patched.sh` builds it in its own folders. If that build fails, the second library is only left out:
a game that asks for it keeps Vulkan off, and the log says so.

Nothing else changes:
- `vulkan = moltenvk`, `vulkan = kosmickrisp` and Zink load the plain, unmodified `libMoltenVK.dylib`;
- in a `moltenvk-patched` session, Zink takes the patched library too, so no session loads two MoltenVKs.

Check: `tests/host/check-moltenvk-patched.py`.
