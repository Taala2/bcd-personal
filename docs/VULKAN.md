# Vulkan in BCD

madeira-bcd personal: upstream's own docs/VULKAN.md is not in the public bcd-games tree, although tests/host/check-moltenvk-patched.py
and tests/host/check-vulkan-wow64.py read it. This copy is written in the fork and so far has only the sections those checks need. If upstream
publishes its file, take upstream's.

## 32-bit programlar (build 660): the same `vulkan = moltenvk`

Programlar means "programs". An i386 game gets Vulkan with the same `vulkan = moltenvk` key. Only in a session with that key, the bridge
links the two i386 DLLs into syswow64, and the unix binder gives the 32-bit winevulkan.dll its wow64 table. With the key off, nothing is
linked.

A 32-bit program on iOS lives in a guest window: its address `a` is host address B + a (docs/WOW64.md). Wine's generated WoW64 thunks turn
32-bit pointers into host pointers with UlongToPtr, which is right only where guest and host addresses are the same number.
`tools/patch-wine-vulkan-wow64.py` runs on the wine tree at build time and makes these changes:
- make_vulkan and vulkan_thunks.c convert addresses with ios_wow_host_ptr and keep numbers as they are;
- win32u's Vulkan layer checks a mapping against the caller's window;
- win32u frees the host memory that VK_EXT_external_memory_host allocates for a 32-bit mapping together with the Vulkan memory.

Check: `tests/host/check-vulkan-wow64.py`.

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
