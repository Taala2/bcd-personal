#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""madeira-bcd personal (2026-11-24): large single-level RGBA8 textures at half size in d3d11-src.dll under MADEIRA_RGBA_HALF=1
([rgba-half], tools/patch-d3d11-src-rgba-half.py).

CelSector is killed after creating 20 single-level 4096x4096-class RGBA8 textures in 2.4 s, filled from staging copies. No Wine,
no GPU.

Checks:
  1. tools/build-d3d11-dll.sh's BCD_PATCHES carries the script with the marker of its creation log, after patch-dxmt-bc-half.py
     (it uses Texture::scaleShift) and before patch-d3d11-src-all-arch.py; the build script describes it; CI runs this check; the
     name stays outside the i386 farm's cache key;
  2. the header the script writes, compiled on the host: only "1" turns it on, the eight 4-byte formats and nothing next to them,
     the size rule at its edges (a side over 2,048 px, both sides even, one level, one slice), the copy region rule (even origin
     and size, inside the level, no overflow), the 2x2 box with rounding and a padded source pitch, the log rate;
  3. on a copy of the pinned dxmt/src (DXMT_SRC_DIR or the submodule; SKIP without one) with patch-dxmt-bc-half.py applied: the
     script applies, a second run says "already patched", a tree without bc-half is refused, a tree with one file patched is
     refused, a moved anchor fails by name; in the result the size decision follows bc-half's and comes before the physical
     descriptor is used, a copy is offered to RgbaHalfCopy right after the validity test and before bc-half's drop and the mip
     translation, and initial data is downscaled before bc-half's upload guard.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = R / "tools/patch-d3d11-src-rgba-half.py"
BC_HALF = R / "tools/patch-dxmt-bc-half.py"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[:2000]))
    ok = ok and bool(cond)


sh = (R / "tools/build-d3d11-dll.sh").read_text()
m = re.search(r"\nBCD_PATCHES=\((.*?)\)\n", sh, re.S)
pairs = re.findall(r'"([^"]+\.py)" "([^"]*)"', m.group(1)) if m else []
names = [p for p, _ in pairs]
check("BCD_PATCHES has patch-d3d11-src-rgba-half.py with the marker of its creation log",
      ("patch-d3d11-src-rgba-half.py", "[rgba-half] madeira-bcd #") in pairs, pairs)
check("  after patch-dxmt-bc-half.py and before patch-d3d11-src-all-arch.py",
      all(n in names for n in ("patch-dxmt-bc-half.py", "patch-d3d11-src-rgba-half.py", "patch-d3d11-src-all-arch.py"))
      and names.index("patch-dxmt-bc-half.py") < names.index("patch-d3d11-src-rgba-half.py")
      < names.index("patch-d3d11-src-all-arch.py"))
check("  and the build script's list of patches describes it", "tools/patch-d3d11-src-rgba-half.py " in sh)
ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-dxmt-rgba-half.py" in ci)
check("the script's name stays outside the i386 farm's cache key (tools/patch-dxmt-*.py), as a copy-only script's does",
      not list((R / "tools").glob("patch-dxmt-*rgba-half*.py")) and "'tools/patch-dxmt-*.py'" in ci
      and not re.search(r"python3 tools/patch-d3d11-src-rgba-half\.py", ci))

src = SCRIPT.read_text()
ns = {}
exec(compile(src.replace("\nmain()\n", "\n"), str(SCRIPT), "exec"), ns)
check("the creation log starts with its BCD_PATCHES marker",
      any('ERR("[rgba-half] madeira-bcd #", n,' in new for _, new in ns["EDITS"][ns["DEVICE"]]))

TEST = r"""
#include "dxmt_rgba_half.hpp"
#include <cstdio>
#include <cstring>
using namespace dxmt::rgba_half;
static int bad = 0;
#define T(c) do { if (!(c)) { std::printf("FAIL %s (line %d)\n", #c, __LINE__); bad = 1; } } while (0)
int main() {
  T(EnabledValue("1"));
  T(!EnabledValue(nullptr)); T(!EnabledValue("")); T(!EnabledValue("0")); T(!EnabledValue("2"));
  T(!EnabledValue(" 1")); T(!EnabledValue("1 ")); T(!EnabledValue("11")); T(!EnabledValue("on"));
  const unsigned yes[] = {27, 28, 29, 87, 88, 90, 91, 92, 93};
  for (unsigned f : yes) T(Format(f));
  const unsigned no[] = {0, 2, 10, 24, 26, 30, 31, 32, 71, 77, 86, 89, 94, 98, 99, 115};
  for (unsigned f : no) T(!Format(f));
  T(Eligible(4096, 4096, 1, 1)); T(Eligible(4096, 2048, 1, 1)); T(Eligible(2, 2050, 1, 1)); T(Eligible(2050, 2, 1, 1));
  T(!Eligible(2048, 2048, 1, 1)); T(!Eligible(1920, 1080, 1, 1));
  T(!Eligible(4096, 4095, 1, 1)); T(!Eligible(4097, 4096, 1, 1));
  T(!Eligible(4096, 4096, 2, 1)); T(!Eligible(4096, 4096, 0, 1)); T(!Eligible(4096, 4096, 1, 2)); T(!Eligible(4096, 0, 1, 1));
  T(RegionOk(0, 0, 4096, 4096, 4096, 4096)); T(RegionOk(2, 4, 4094, 4092, 4096, 4096)); T(RegionOk(4094, 4094, 2, 2, 4096, 4096));
  T(!RegionOk(1, 0, 2, 2, 4096, 4096)); T(!RegionOk(0, 3, 2, 2, 4096, 4096));
  T(!RegionOk(0, 0, 4095, 4096, 4096, 4096)); T(!RegionOk(0, 0, 4096, 1, 4096, 4096));
  T(!RegionOk(0, 0, 0, 2, 4096, 4096)); T(!RegionOk(0, 0, 2, 0, 4096, 4096));
  T(!RegionOk(2, 0, 4096, 2, 4096, 4096)); T(!RegionOk(4096, 0, 2, 2, 4096, 4096)); T(!RegionOk(4098, 0, 2, 2, 4096, 4096));
  T(!RegionOk(2, 2, ~0ull - 1, 2, 4096, 4096)); T(!RegionOk(~0ull - 1, 0, 2, 2, 4096, 4096));
  /* 4x2 texels, a 20-byte pitch (4 padding bytes per row that must not be read into the result). */
  unsigned char s[40];
  std::memset(s, 0xEE, sizeof s);
  const unsigned char r0[16] = {0, 10, 255, 1,   2, 20, 255, 1,   100, 0, 0, 255,   101, 1, 1, 254};
  const unsigned char r1[16] = {4, 30, 255, 2,   6, 40, 254, 2,   102, 2, 2, 253,   103, 3, 3, 252};
  std::memcpy(s, r0, 16);
  std::memcpy(s + 20, r1, 16);
  unsigned char d[8];
  std::memset(d, 0x55, sizeof d);
  Downscale2x2(s, 20, 4, 2, d);
  /* (0+2+4+6+2)>>2=3, (10+20+30+40+2)>>2=25, (255+255+255+254+2)>>2=255, (1+1+2+2+2)>>2=2; second texel likewise. */
  const unsigned char want[8] = {3, 25, 255, 2,   102, 2, 2, 254};
  T(std::memcmp(d, want, 8) == 0);
  unsigned char w[4 * 4 * 4], h[2 * 2 * 4];
  std::memset(w, 255, sizeof w);
  Downscale2x2(w, 16, 4, 4, h);
  bool all255 = true;
  for (unsigned char c : h) all255 = all255 && c == 255;
  T(all255);
  T(ShouldLog(1)); T(ShouldLog(8)); T(!ShouldLog(9)); T(!ShouldLog(63)); T(ShouldLog(64)); T(!ShouldLog(65)); T(ShouldLog(128));
  T(kMinLargestSide == 2049);
  T(g_created.load() == 0 && g_copied.load() == 0 && g_dropped.load() == 0);
  if (!bad) std::printf("host test passed\n");
  return bad;
}
"""
cxx = next((shutil.which(c) for c in ("c++", "clang++", "g++") if shutil.which(c)), None)
if not cxx:
    print("skip no host C++ compiler: the header's logic is not compiled here")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-rgba-half-") as t:
        (pathlib.Path(t) / "dxmt_rgba_half.hpp").write_text(ns["HEADER"])
        (pathlib.Path(t) / "test.cpp").write_text(TEST)
        exe = str(pathlib.Path(t) / "test")
        b = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-I", t,
                            str(pathlib.Path(t) / "test.cpp"), "-o", exe], capture_output=True, text=True)
        check("the header compiles on the host with -Wall -Wextra -Werror", b.returncode == 0, b.stdout + b.stderr)
        if b.returncode == 0:
            r = subprocess.run([exe], capture_output=True, text=True)
            check("only \"1\" on, the eight formats, the size and region rules at their edges, the 2x2 box, the log rate",
                  r.returncode == 0 and "host test passed" in r.stdout, r.stdout + r.stderr)

dsrc = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
if not (dsrc / "d3d11/d3d11_texture_device.cpp").is_file():
    print(f"skip no dxmt sources at {dsrc}: the patch is not applied here (check out the submodule or set DXMT_SRC_DIR)")
else:
    def run(script, cwd):
        p = subprocess.run([sys.executable, str(script)], cwd=cwd, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def fresh(t, bc_half=True):
        root = pathlib.Path(t)
        for sub in ("d3d11", "dxmt"):
            shutil.copytree(dsrc / sub, root / "dxmt/src" / sub)
        if bc_half:
            rc, out = run(BC_HALF, root)
            if rc != 0:
                check("patch-dxmt-bc-half.py applies to the pinned dxmt/src", False, out)
        return root

    with tempfile.TemporaryDirectory(prefix="bcd-rgba-half-") as t:
        root = fresh(t)
        rc1, out1 = run(SCRIPT, root)
        rc2, out2 = run(SCRIPT, root)
        check("the script applies to the pinned dxmt/src after patch-dxmt-bc-half.py", rc1 == 0 and "patched" in out1, out1)
        check("  a second run says already patched", rc2 == 0 and "already patched" in out2, out2)
        s = root / "dxmt/src"
        check("  the header is written", (s / "dxmt/dxmt_rgba_half.hpp").read_text() == ns["HEADER"])
        dev = (s / "d3d11/d3d11_texture_device.cpp").read_text()
        ctx = (s / "d3d11/d3d11_context_impl.cpp").read_text()
        ini = (s / "dxmt/dxmt_resource_initializer.cpp").read_text()
        dec = dev.index("if (!mip_bias && !scale_shift && RgbaHalfEnabled() &&")
        check("the size decision follows bc-half's and comes before the physical descriptor is used",
              dev.index('ERR("[bc-half] madeira-bcd #", n,') < dec < dev.index("    if (mip_bias) {\n      physDesc.Width")
              < dev.index("texture->setScaleShift(scale_shift);"))
        check("  it reads MADEIRA_RGBA_HALF once, through rgba_half::EnabledValue",
              dev.count('env::getEnvVar("MADEIRA_RGBA_HALF")') == 1 and "rgba_half::EnabledValue(" in dev)
        cp = ctx.index("  CopyTexture(TextureCopyCommand &&cmd) {")
        body = ctx[cp:]
        call = body.index("    if (RgbaHalfCopy(cmd))")
        check("a copy is offered to RgbaHalfCopy right after the validity test, before bc-half's drop and the mip translation",
              body.index("    if (cmd.Invalid)\n      return;\n") < call < body.index("auto half_src = GetTexture(cmd.pSrc);")
              < body.index("ml746: TRANSLATE MIP INDICES"))
        check("  RgbaHalfCopy is defined once, before CopyTexture",
              ctx.count("RgbaHalfCopy(TextureCopyCommand &cmd) {") == 1 and ctx.index("RgbaHalfCopy(TextureCopyCommand &cmd) {") < cp)
        check("initial data is downscaled before bc-half's upload guard",
              ini.index("rgba_half::Downscale2x2((const uint8_t *)data, row_pitch, width_sub * 2, height_sub * 2,")
              < ini.index("upload of a half-size texture skipped"))
    with tempfile.TemporaryDirectory(prefix="bcd-rgba-half-") as t:
        root = fresh(t, bc_half=False)
        rc, out = run(SCRIPT, root)
        check("a tree without patch-dxmt-bc-half.py is refused", rc != 0 and "patch-dxmt-bc-half.py is not applied" in out, out)
    with tempfile.TemporaryDirectory(prefix="bcd-rgba-half-") as t:
        root = fresh(t)
        f = root / "dxmt/src/dxmt/dxmt_resource_initializer.cpp"
        f.write_text(f.read_text() + "\n/* madeira-bcd personal [rgba-half] */\n")
        rc, out = run(SCRIPT, root)
        check("a tree with one file patched is refused", rc != 0 and "only partly patched" in out, out)
    with tempfile.TemporaryDirectory(prefix="bcd-rgba-half-") as t:
        root = fresh(t)
        f = root / "dxmt/src/d3d11/d3d11_context_impl.cpp"
        f.write_text(f.read_text().replace("  void\n  CopyTexture(TextureCopyCommand &&cmd) {",
                                           "  void\n  CopyTexture(TextureCopyCommand &&command) {"))
        rc, out = run(SCRIPT, root)
        check("a moved anchor fails by name", rc != 0 and "d3d11/d3d11_context_impl.cpp" in out, out)

print("PASS: RGBA8 half size" if ok else "FAILED: RGBA8 half size")
sys.exit(0 if ok else 1)
