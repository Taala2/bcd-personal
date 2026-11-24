#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""madeira-bcd personal (2026-11-24): the live texture census of d3d11-src.dll ([tex-census], tools/patch-d3d11-src-tex-census.py).

CelSector is killed near its memory limit with 1.6-2.2 GB of live private textures and no [bc-census] line: the census says which
textures hold them (kind, side, Metal format) and changes nothing the game sees. No Wine, no GPU.

Checks:
  1. tools/build-d3d11-dll.sh's BCD_PATCHES carries the script with the marker its report leaves in the DLL, before
     patch-d3d11-src-all-arch.py; CI runs this check (the whole list's replay: tests/host/check-d3d11-src-fixes.py);
  2. the header the script writes, compiled on the host: the kind (render target first, then shader write, then levels), the side
     buckets at their edges, the key's round trip, a format number past the slots in slot 0, add and free leaving every tally at 0,
     an allocation without bytes and a key never filed changing nothing, the top formats largest first;
  3. on a copy of the pinned dxmt/src (DXMT_SRC_DIR or the submodule; SKIP without one): the script applies, a second run says
     "already patched", a tree with one file patched is refused, a moved anchor fails by name; in the result a private texture is
     filed after its bytes are known and a buffer-backed one is not, the destructor takes it out, and the report sits in
     mem_census_report after the throttle test (so it follows that report's cadence) and before the buffer lines.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = R / "tools/patch-d3d11-src-tex-census.py"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[:2000]))
    ok = ok and bool(cond)


sh = (R / "tools/build-d3d11-dll.sh").read_text()
m = re.search(r"\nBCD_PATCHES=\((.*?)\)\n", sh, re.S)
pairs = re.findall(r'"([^"]+\.py)" "([^"]*)"', m.group(1)) if m else []
names = [p for p, _ in pairs]
check("BCD_PATCHES has patch-d3d11-src-tex-census.py with the marker of its report",
      ("patch-d3d11-src-tex-census.py", "[tex-census] madeira-bcd live by kind: ") in pairs, pairs)
check("  before patch-d3d11-src-all-arch.py",
      "patch-d3d11-src-tex-census.py" in names and "patch-d3d11-src-all-arch.py" in names
      and names.index("patch-d3d11-src-tex-census.py") < names.index("patch-d3d11-src-all-arch.py"))
check("  and the build script's list of patches describes it", "tools/patch-d3d11-src-tex-census.py " in sh)
ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-dxmt-tex-census.py" in ci)
check("the script's name stays outside the i386 farm's cache key (tools/patch-dxmt-*.py), as a copy-only script's does",
      not list((R / "tools").glob("patch-dxmt-*tex-census*.py")) and "'tools/patch-dxmt-*.py'" in ci
      and not re.search(r"python3 tools/patch-d3d11-src-tex-census\.py", ci))

src = SCRIPT.read_text()
ns = {}
exec(compile(src.replace("\nmain()\n", "\n"), str(SCRIPT), "exec"), ns)
check("the script's report line starts with its BCD_PATCHES marker", '"[tex-census] madeira-bcd live by kind: "' in ns["REPORT"])

TEST = r"""
#include "dxmt_tex_census.hpp"
#include <cstdio>
using namespace dxmt::tex_census;
static int bad = 0;
#define T(c) do { if (!(c)) { std::printf("FAIL %s (line %d)\n", #c, __LINE__); bad = 1; } } while (0)
static bool all_zero() {
  for (uint32_t k = 0; k < KIND_COUNT; k++) {
    if (g_state.kind[k].bytes || g_state.kind[k].n) return false;
    for (uint32_t s = 0; s < SIDE_COUNT; s++) if (g_state.side[k][s].bytes || g_state.side[k][s].n) return false;
    for (uint32_t f = 0; f < kFormatSlots; f++) if (g_state.format[k][f].bytes || g_state.format[k][f].n) return false;
  }
  return !g_state.arrays.bytes && !g_state.arrays.n;
}
int main() {
  T(KindOf(4 | 1, 1) == KIND_RT); T(KindOf(4 | 2, 10) == KIND_RT); T(KindOf(2 | 1, 1) == KIND_WRITE);
  T(KindOf(1, 2) == KIND_MIPPED); T(KindOf(1, 1) == KIND_SINGLE); T(KindOf(0, 0) == KIND_SINGLE);
  T(SideOf(256, 1) == SIDE_LE_256); T(SideOf(1, 257) == SIDE_LE_512); T(SideOf(512, 512) == SIDE_LE_512);
  T(SideOf(1024, 4) == SIDE_LE_1K); T(SideOf(1025, 4) == SIDE_LE_2K); T(SideOf(2048, 2048) == SIDE_LE_2K);
  T(SideOf(2049, 1) == SIDE_GT_2K); T(SideOf(16384, 16384) == SIDE_GT_2K);
  T(FormatSlot(70) == 70); T(FormatSlot(1023) == 1023); T(FormatSlot(1024) == 0); T(FormatSlot(0xffffffffu) == 0);
  uint32_t k = Key(4, 1, 1920, 1080, 1, 80);
  T((k & 0x80000000u) && KeyKind(k) == KIND_RT && KeySide(k) == SIDE_LE_2K && !KeyArray(k) && KeyFormat(k) == 80);
  k = Key(1, 12, 4096, 64, 6, 1023);
  T(KeyKind(k) == KIND_MIPPED && KeySide(k) == SIDE_GT_2K && KeyArray(k) && KeyFormat(k) == 1023);
  T(all_zero());
  T(Add(1, 1, 512, 512, 1, 70, 0) == 0); T(all_zero());
  Sub(0, 4096); Sub(0x1234, 4096); T(all_zero());
  uint32_t a = Add(1, 1, 2048, 1024, 1, 70, 8u << 20);
  uint32_t b = Add(1, 1, 1024, 1024, 1, 80, 4u << 20);
  uint32_t c = Add(1, 1, 256, 256, 1, 80, 1u << 20);
  uint32_t d = Add(4, 1, 1288, 688, 1, 80, 3u << 20);
  uint32_t e = Add(1, 11, 1024, 1024, 4, 125, 6u << 20);
  T(g_state.kind[KIND_SINGLE].bytes == (13u << 20) && g_state.kind[KIND_SINGLE].n == 3);
  T(g_state.kind[KIND_RT].n == 1 && g_state.kind[KIND_MIPPED].n == 1 && g_state.kind[KIND_WRITE].n == 0);
  T(g_state.side[KIND_SINGLE][SIDE_LE_2K].bytes == (8u << 20) && g_state.side[KIND_SINGLE][SIDE_LE_1K].bytes == (4u << 20)
    && g_state.side[KIND_SINGLE][SIDE_LE_256].bytes == (1u << 20));
  T(g_state.format[KIND_SINGLE][80].bytes == (5u << 20) && g_state.format[KIND_SINGLE][80].n == 2);
  T(g_state.arrays.bytes == (6u << 20) && g_state.arrays.n == 1);
  uint32_t top[kTopFormats];
  TopFormats(KIND_SINGLE, top);
  T(top[0] == 70 && top[1] == 80 && top[2] == kFormatSlots && top[3] == kFormatSlots);
  TopFormats(KIND_WRITE, top);
  T(top[0] == kFormatSlots);
  Sub(a, 8u << 20); Sub(b, 4u << 20); Sub(c, 1u << 20); Sub(d, 3u << 20); Sub(e, 6u << 20);
  T(all_zero());
  for (uint32_t f = 1; f <= 6; f++) Add(1, 1, 64, 64, 1, f, f * 100);
  TopFormats(KIND_SINGLE, top);
  T(top[0] == 6 && top[1] == 5 && top[2] == 4 && top[3] == 3);
  if (!bad) std::printf("host test passed\n");
  return bad;
}
"""
cxx = next((shutil.which(c) for c in ("c++", "clang++", "g++") if shutil.which(c)), None)
if not cxx:
    print("skip no host C++ compiler: the header's logic is not compiled here")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-tex-census-") as t:
        (pathlib.Path(t) / "dxmt_tex_census.hpp").write_text(ns["HEADER"])
        (pathlib.Path(t) / "test.cpp").write_text(TEST)
        exe = str(pathlib.Path(t) / "test")
        b = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", "-I", t,
                            str(pathlib.Path(t) / "test.cpp"), "-o", exe], capture_output=True, text=True)
        check("the header compiles on the host with -Wall -Wextra -Werror", b.returncode == 0, b.stdout + b.stderr)
        if b.returncode == 0:
            r = subprocess.run([exe], capture_output=True, text=True)
            check("kind, side, key, slot 0, add and free to 0, nothing for no bytes or no key, top formats largest first",
                  r.returncode == 0 and "host test passed" in r.stdout, r.stdout + r.stderr)

dsrc = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
if not (dsrc / "dxmt/dxmt_texture.cpp").is_file():
    print(f"skip no dxmt sources at {dsrc}: the patch is not applied here (check out the submodule or set DXMT_SRC_DIR)")
else:
    def fresh(t):
        root = pathlib.Path(t)
        shutil.copytree(dsrc / "dxmt", root / "dxmt/src/dxmt")
        return root

    def run(cwd):
        p = subprocess.run([sys.executable, str(SCRIPT)], cwd=cwd, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    with tempfile.TemporaryDirectory(prefix="bcd-tex-census-") as t:
        root = fresh(t)
        rc1, out1 = run(root)
        rc2, out2 = run(root)
        check("the script applies to the pinned dxmt/src", rc1 == 0 and "patched" in out1, out1)
        check("  a second run says already patched", rc2 == 0 and "already patched" in out2, out2)
        d = root / "dxmt/src/dxmt"
        tex = (d / "dxmt_texture.cpp").read_text()
        cen = (d / "dxmt_mem_census.cpp").read_text()
        hpp = (d / "dxmt_texture.hpp").read_text()
        add = tex.index("ta->census_tex_key_ = tex_census::Add(")
        check("a private texture is filed after its census bytes, before they are charged",
              tex.index("ta->census_bytes_ = mem_census_texture_bytes(info);") < add
              < tex.index("mem_census_add(MEMOWN_TEX_PRIVATE, ta->census_bytes_);"))
        check("  a buffer-backed one is not filed", tex.count("tex_census::Add(") == 1
              and add > tex.index("mem_census_add(MEMOWN_TEX_LINEAR, ta->census_bytes_);"))
        dtor = tex[tex.index("TextureAllocation::~TextureAllocation(){"):]
        check("the destructor takes the allocation out", "tex_census::Sub(census_tex_key_, census_bytes_);" in dtor[:dtor.index("};")])
        check("the allocation keeps its key, 0 when not filed", "uint32_t census_tex_key_ = 0;" in hpp)
        rep = cen[cen.index("mem_census_report(const char *why) {"):]
        check("the report is in mem_census_report after the throttle test, before the buffer lines",
              rep.index("if (mem_census_throttled(why)) return;") < rep.index("[tex-census] madeira-bcd live by kind: ")
              < rep.index('ERR("[mem-census]   buffers by size: ",'))
    with tempfile.TemporaryDirectory(prefix="bcd-tex-census-") as t:
        root = fresh(t)
        f = root / "dxmt/src/dxmt/dxmt_texture.hpp"
        f.write_text(f.read_text().replace("/* ml677: bytes to refund on destruction          */",
                                           "/* ml677: bytes to refund on destruction */ /* madeira-bcd personal [tex-census] */"))
        rc, out = run(root)
        check("a tree with one file patched is refused", rc != 0 and "only partly patched" in out, out)
    with tempfile.TemporaryDirectory(prefix="bcd-tex-census-") as t:
        root = fresh(t)
        f = root / "dxmt/src/dxmt/dxmt_mem_census.cpp"
        f.write_text(f.read_text().replace('ERR("[mem-census]   buffers by size: ",', 'ERR("[mem-census]   buffers by bucket: ",'))
        rc, out = run(root)
        check("a moved anchor fails by name", rc != 0 and "dxmt/dxmt_mem_census.cpp" in out and "buffers by size" in out, out)

print("PASS: texture census" if ok else "FAILED: texture census")
sys.exit(0 if ok else 1)
