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
     the half side rounded up, the candidate rule (a side over 2,048 px) and each reject reason (an odd side only with initial
     data), the copy region rule (even origin, even size or an odd one ending at the edge, inside the level, no overflow), the
     2x2 box with rounding, a padded source pitch and odd sides, the log rate;
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
  T(HalfSide(4096) == 2048); T(HalfSide(4095) == 2048); T(HalfSide(4093) == 2047); T(HalfSide(2) == 1);
  T(HalfSide(1) == 1); T(HalfSide(0) == 0); T(HalfSide(0xFFFFFFFFu) == 0x80000000u);
  T(Candidate(28, 4096, 4096)); T(Candidate(28, 2049, 2)); T(Candidate(87, 2, 4095)); T(Candidate(91, 4093, 4091));
  T(!Candidate(28, 2048, 2048)); T(!Candidate(28, 1920, 1080)); T(!Candidate(2, 4096, 4096)); T(!Candidate(71, 4096, 4096));
  const Desc base = {4096, 4096, 1, 1, kUsageDefault, kBindShaderResource, 0, 0, 1, false};
  T(RejectReason(base) == nullptr);
  auto with = [&](auto edit) { Desc d = base; edit(d); return RejectReason(d); };
  auto is = [](const char *got, const char *want) { return got && std::strcmp(got, want) == 0; };
  T(!with([](Desc &d) { d.usage = kUsageImmutable; }));
  T(!with([](Desc &d) { d.sample_count = 0; }));
  T(!with([](Desc &d) { d.initial_data = true; }));
  T(!with([](Desc &d) { d.width = 4093; d.height = 4091; }));
  T(!with([](Desc &d) { d.width = 4084; d.height = 4091; }));
  T(is(with([](Desc &d) { d.width = 4093; d.initial_data = true; }), "an odd side with initial data"));
  T(is(with([](Desc &d) { d.height = 4091; d.initial_data = true; }), "an odd side with initial data"));
  T(is(with([](Desc &d) { d.mip_levels = 2; }), "more than one level"));
  T(is(with([](Desc &d) { d.mip_levels = 0; }), "more than one level"));
  T(is(with([](Desc &d) { d.array_size = 2; }), "an array"));
  T(is(with([](Desc &d) { d.sample_count = 4; }), "multisampled"));
  T(is(with([](Desc &d) { d.usage = 2; }), "usage is not DEFAULT or IMMUTABLE"));
  T(is(with([](Desc &d) { d.usage = 3; }), "usage is not DEFAULT or IMMUTABLE"));
  T(is(with([](Desc &d) { d.bind_flags = kBindShaderResource | 0x20; }), "bound as more than a shader resource"));
  T(is(with([](Desc &d) { d.bind_flags = 0; }), "bound as more than a shader resource"));
  T(is(with([](Desc &d) { d.cpu_access = 0x10000; }), "CPU access"));
  T(is(with([](Desc &d) { d.misc_flags = 0x4; }), "misc flags"));
  T(is(with([](Desc &d) { d.height = 1; }), "a side under 2 px"));
  T(RegionOk(0, 0, 4096, 4096, 4096, 4096)); T(RegionOk(2, 4, 4094, 4092, 4096, 4096)); T(RegionOk(4094, 4094, 2, 2, 4096, 4096));
  T(RegionOk(0, 0, 4093, 4091, 4093, 4091)); T(RegionOk(2, 0, 4091, 2, 4093, 4096)); T(RegionOk(4092, 4090, 1, 1, 4093, 4091));
  T(!RegionOk(0, 0, 4091, 2, 4093, 4096)); T(!RegionOk(1, 0, 4092, 2, 4093, 4096)); T(!RegionOk(0, 0, 4094, 2, 4093, 4096));
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
  /* 3x3 texels (every channel the same value), a 16-byte pitch with 0xEE padding, and nothing after the last texel: the odd
   * column and row are averaged with themselves. Expected (0+4+12+16+2)>>2=8, (8+8+20+20+2)>>2=14, (24+28+24+28+2)>>2=26,
   * (4*32+2)>>2=32. */
  unsigned char *odd = new unsigned char[16 * 2 + 12];
  std::memset(odd, 0xEE, 16 * 2 + 12);
  const unsigned char v[3][3] = {{0, 4, 8}, {12, 16, 20}, {24, 28, 32}};
  for (int y = 0; y < 3; y++)
    for (int x = 0; x < 3; x++)
      std::memset(odd + y * 16 + x * 4, v[y][x], 4);
  unsigned char o[16];
  std::memset(o, 0x55, sizeof o);
  Downscale2x2(odd, 16, 3, 3, o);
  const unsigned char want_odd[4] = {8, 14, 26, 32};
  bool odd_ok = true;
  for (int i = 0; i < 16; i++) odd_ok = odd_ok && o[i] == want_odd[i / 4];
  T(odd_ok);
  delete[] odd;
  T(ShouldLog(1)); T(ShouldLog(8)); T(!ShouldLog(9)); T(!ShouldLog(63)); T(ShouldLog(64)); T(!ShouldLog(65)); T(ShouldLog(128));
  T(kMinLargestSide == 2049);
  T(g_created.load() == 0 && g_copied.load() == 0 && g_dropped.load() == 0 && g_rejected.load() == 0);
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
            check("only \"1\" on, the eight formats, the half side, the candidate rule and each reject reason, the region rule "
                  "at its edges, the 2x2 box with odd sides, the log rate",
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
        blk = dev[dec:dev.index("    if (mip_bias) {\n      physDesc.Width")]
        check("  a candidate goes through rgba_half::RejectReason; a rejected one is logged with the reason, a taken one gets "
              "the half side rounded up",
              "rgba_half::Candidate((uint32_t)finalDesc.Format, finalDesc.Width, finalDesc.Height)" in blk
              and "if (const char *why = rgba_half::RejectReason(half_desc)) {" in blk
              and 'ERR("[rgba-half] madeira-bcd: not halved #", n,' in blk and ", why);" in blk
              and "physDesc.Width  = rgba_half::HalfSide(finalDesc.Width);" in blk
              and "physDesc.Height = rgba_half::HalfSide(finalDesc.Height);" in blk and ">> 1" not in blk)
        cp = ctx.index("  CopyTexture(TextureCopyCommand &&cmd) {")
        body = ctx[cp:]
        call = body.index("    if (RgbaHalfCopy(cmd))")
        check("a copy is offered to RgbaHalfCopy right after the validity test, before bc-half's drop and the mip translation",
              body.index("    if (cmd.Invalid)\n      return;\n") < call < body.index("auto half_src = GetTexture(cmd.pSrc);")
              < body.index("ml746: TRANSLATE MIP INDICES"))
        check("  RgbaHalfCopy is defined once, before CopyTexture",
              ctx.count("RgbaHalfCopy(TextureCopyCommand &cmd) {") == 1 and ctx.index("RgbaHalfCopy(TextureCopyCommand &cmd) {") < cp)
        rh = ctx[ctx.index("RgbaHalfCopy(TextureCopyCommand &cmd) {"):cp]
        check("  its upload size is the half side rounded up",
              "dst_w = rgba_half::HalfSide(src_w), dst_h = rgba_half::HalfSide(src_h);" in rh
              and "const WMTSize size = {(uint64_t)dst_w, (uint64_t)dst_h, 1};" in rh and "src_w >> 1" not in rh)
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
