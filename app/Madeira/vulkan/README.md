# Windows Vulkan DLLs (bundle folder)

madeira-bcd personal: the public export of thebcd69/bcd-games leaves this README out; this copy names what the folder holds.

CI fills this folder with `tools/build-vulkan-dlls.sh`; the DLLs are not committed (`.gitignore`). They are Wine's
`winevulkan.dll` and `vulkan-1.dll` from the wine submodule, kept out of the farms on purpose so that a program probing
for `vulkan-1.dll` finds what it found before unless its game turns Vulkan on (`vulkan = moltenvk`).

- `arm64ec-windows/`: for 64-bit programs, built by `build/wine-pe/build-modules.sh winevulkan vulkan-1` from
  wine/build-arm64ec.
- `i386-windows/`: for 32-bit programs (build 660), the same two modules built for i386 from wine/build-i386, the tree of
  the i386 farm.

Both sets are PE code only; the unix side is in libntdll_unix.a and libwin32u_unix.a (`tools/patch-wine-vulkan-wow64.py`
for 32-bit callers).
