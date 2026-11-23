# Vulkan in BCD

madeira-bcd personal: upstream's own docs/VULKAN.md is not in the public bcd-games tree, although tests/host/check-moltenvk-patched.py,
tests/host/check-vulkan-wow64.py, tests/host/check-kk-vt-interp.py and tests/host/check-kk-lsfg.py read it. This copy is written in the fork
and so far has only the sections those checks need. If upstream publishes its file, take upstream's.

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

## Apple frame interpolation for Vulkan games (builds 703 to 706): KosmicKrisp patch 0036

BCD's Apple frame interpolation (VideoToolbox's low-latency frame interpolation, iOS 26) used to live only in DXMT's present, so a
Windows Vulkan game never got it. Playport patch `0036-wsi-metal-apple-frame-interpolation-MADEIRA_VT_INTERP.patch` adds it to
KosmicKrisp's Metal WSI (`wsi_common_metal_vti.m`):
- the app exports `MADEIRA_VT_INTERP=1` or `3` for a game whose own `vt-interpolation` line asks: one or three pictures between two frames;
- the game's swapchain image is copied into one of three stand-in textures of the module's own; only the module's worker takes the
  layer's drawables, so the game's thread never waits for the model;
- three errors, or a format or size the processor refuses, stop it with one `[vt-interp] kosmickrisp` line in the log;
- without `MADEIRA_VT_INTERP`, every swapchain is made, acquired and presented exactly as before.

Not done: Apple frame interpolation for Vulkan games on MoltenVK.

Check: `tests/host/check-kk-vt-interp.py`.

## Lossless Scaling for Vulkan games (build 706): the same patch

With `MADEIRA_LSFG_HOST_CTL` (the app's control file, for a game whose own `lossless-scaling` line asks), the same stand-ins and worker
carry Lossless Scaling's frames instead of VideoToolbox's pictures:
- `wsi_common_metal_lsfg.m` runs the frame generation's chain of passes (that of BCD's DXMT port, after lsfg-vk 1.0.0) as Metal compute
  on the worker's queue;
- the shaders are the person's own: the app reads them out of their Lossless.dll and translates them to MSL (`MADEIRA_LSFG_HOST_MSL`).
  None of them is in BCD;
- the control and status files are DXMT's;
- `wsi_common_metal_lsfg.m` is GPL-3.0-or-later (its pass chain comes from Porthole's GPL code); the rest of patch 0036 is MIT.

Check: `tests/host/check-kk-lsfg.py`.
