# BCD contributors

madeira-bcd personal: upstream's own README-BCD.md is not in the public bcd-games tree, although
tests/host/check-moltenvk-patched.py reads it. tools/export-public.py publishes upstream's README-BCD.md as README.md, so README.md
is its full text (RageMC is credited there too). This short copy is written in the fork. If upstream publishes its file, take upstream's.
The full credits and licences are in THIRD-PARTY-NOTICES.md.

- [RageMC](https://github.com/RageMC): the patched MoltenVK, an opt-in second Vulkan library (`vulkan = moltenvk-patched`, build 669,
  bcd-games pull request #57). It includes the MoltenVK changes of nivogli (github.com/nivogli/MoltenVK).
