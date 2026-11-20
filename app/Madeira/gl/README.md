# gl/

madeira-bcd personal: upstream's own copy of this file is not in the public bcd-games tree, although
tests/host/check-moltenvk-patched.py reads it. This copy is written in the fork. If upstream publishes its file, take upstream's.

This folder holds the GL and Vulkan libraries that the build stages here (they are gitignored). The Xcode phase signs each
`*.dylib` in it.

- `libOSMesa.dylib`: OSMesa with Zink, built by `build/mesa-ios/build.sh`.
- `libMoltenVK.dylib`: plain MoltenVK v1.4.2, built by `build/moltenvk-ios/build.sh`. It is the library for `vulkan = moltenvk` and for Zink.
- `libvulkan_kosmickrisp.dylib`: KosmicKrisp (Mesa's Vulkan on Metal), built by `build/mesa-ios/build-kosmickrisp-bcd.sh`.
- `libMoltenVK_patched.dylib`: the patched MoltenVK, built by `build/moltenvk-ios/build-patched.sh`. It loads only for a game whose
  own file says `vulkan = moltenvk-patched`.

Each library has a `.version` file beside it.
