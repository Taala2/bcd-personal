# KosmicKrisp patches (Playport)

madeira-bcd personal: upstream's own copy of this file is not in the public bcd-games tree, although its checks
(check-kk-snorm-blend.py, check-kk-msl-fail.py, check-kk-ms-store-drop.py, check-kk-vt-interp.py) read it. This copy is written in the fork from the
patches' own headers. If upstream publishes its file, take upstream's.

These patches are applied in name order on top of the Mesa tree that `fetch-playport.py` downloads (checked by its SHA-256).
`build-kosmickrisp.sh` and `build-kosmickrisp-bcd.sh` apply every `*.patch` here. 0001 to 0031 are the Playport authors' patches;
0032 to 0036 are BCD's. A changed patch means a fresh source tree (`kk-stale-source.sh`, build 676). The CI cache key of the
KosmicKrisp build covers this folder.

## BCD's patches with a switch or a log line

- `0033-kk-snorm-color-attachments-report-blending.patch` (build 673, X-Plane 12). KosmicKrisp holds back
  VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BLEND_BIT for every SNORM format, although it blends in the fragment shader
  (nir_lower_blend handles SNORM render targets). With `MADEIRA_KK_SNORM_BLEND=1`, SNORM color formats report the bit, and one
  `[kk-snorm-blend]` line is logged. Without the key, the driver reports exactly what it did. Check: `tests/host/check-kk-snorm-blend.py`.
- `0034-kk-log-the-msl-source-metal-refused.patch` (build 674). When Metal refuses a library, the first three refused MSL sources
  are logged with line numbers (`[kk-msl-fail]`, at most 800 lines each). This happens only on a failure; nothing else changes.
  Check: `tests/host/check-kk-msl-fail.py`.
- `0035-kk-drop-stores-to-multisampled-storage-images.patch` (build 675, X-Plane 12). Metal has no writable multisampled texture.
  With `MADEIRA_KK_MS_STORE_DROP=1`, a multisampled storage image is bound read-only, and stores and fences on it are left out
  (`[kk-ms-store-drop]`). Without the key, the MSL is exactly what it was. Check: `tests/host/check-kk-ms-store-drop.py`.
- `0036-wsi-metal-apple-frame-interpolation-MADEIRA_VT_INTERP.patch` (builds 703 to 706, X-Plane 12). Apple frame interpolation
  (`MADEIRA_VT_INTERP=1` or `3`) and Lossless Scaling frame generation (`MADEIRA_LSFG_HOST_CTL`) in the Metal WSI
  (`wsi_common_metal_vti.m`, `wsi_common_metal_lsfg.m`). The game's swapchain image is copied into one of three stand-in textures of
  the module's own: kk_queue.c does not wait for or signal a stand-in as it does a drawable, and a stand-in that was not presented goes
  back to its ring. Only the module's worker takes the layer's drawables. Without either key, every swapchain is made, acquired and
  presented exactly as before. See docs/VULKAN.md. Checks: `tests/host/check-kk-vt-interp.py`, `tests/host/check-kk-lsfg.py`.
