#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""madeira-bcd personal (2026-11-24): the live Metal texture memory by kind, side and format ([tex-census], d3d11-src.dll only).

CelSector (Unity, 64-bit Direct3D 11) is killed at 96-99 % of its 6,143 MB limit with DXMT's tex-private at 1.6-2.2 GB live (logs
7ff7e08a, e7e692ba, 5b36ff2a). The memory census names the owner (tex-private) but not which textures, and the logs hold no
[bc-census] line at all: that census prints on every 64th BC texture, so the game created fewer than 64 of them, and the two mip
clamps (BC only, two levels or more) cannot reach the rest. Before any texture is made smaller, this says which ones hold the memory.

Read-only: nothing the game or Metal sees changes. Each private texture allocation is filed, when it is made, under
  * a kind: render-target (usage has RenderTarget), shader-write (ShaderWrite without RenderTarget), sampled-mipped (two levels or
    more) or sampled-1-level;
  * a side bucket of its larger side: <=256, <=512, <=1K, <=2K, >2K;
  * its Metal pixel format number (WMTPixelFormat; out-of-range numbers share slot 0);
and taken out again when it is freed, with the bytes the memory census charged it (mem_census_texture_bytes: a BC format counts at
its decoded size even on a GPU that samples BC, so its number there is an upper bound). Every [mem-census] report (DXMT's own
cadence: the throttle's 10 s, a memory warning always) is followed by
  [tex-census] madeira-bcd live by kind: render-target=MB/n shader-write=MB/n sampled-mipped=MB/n sampled-1-level=MB/n | arrays=MB/n
  [tex-census]   <kind> by side: <=256=MB/n <=512=MB/n <=1K=MB/n <=2K=MB/n >2K=MB/n | top formats: fmt70=MB/n ...  (each kind with any)
The policy and the tallies are in dxmt_tex_census.hpp, which has no Windows or Metal type, so tests/host/check-dxmt-tex-census.py
compiles it on the host.

d3d11-src.dll only (tools/build-d3d11-dll.sh, BCD_PATCHES): a 64-bit game gets it with env.MADEIRA_D3D11_SRC = 1. Idempotent; fails
by name when an anchor moved. Run in the directory whose "dxmt" is the tree to patch.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd personal [tex-census]"
NAME = "patch-d3d11-src-tex-census"
HDR = "dxmt/dxmt_tex_census.hpp"
TEX_H = "dxmt/dxmt_texture.hpp"
TEX_C = "dxmt/dxmt_texture.cpp"
CEN_C = "dxmt/dxmt_mem_census.cpp"

HEADER = """#pragma once
/* """ + MARKER + """ (2026-11-24): the live private texture memory by kind, side and Metal pixel format.
 * Read-only bookkeeping beside the memory census (tools/patch-d3d11-src-tex-census.py). No Windows or Metal type, so
 * tests/host/check-dxmt-tex-census.py compiles it on the host. */
#include <atomic>
#include <cstdint>

namespace dxmt::tex_census {

enum Kind : uint32_t { KIND_RT = 0, KIND_WRITE, KIND_MIPPED, KIND_SINGLE, KIND_COUNT };
static const char *const kKindName[KIND_COUNT] = {"render-target", "shader-write", "sampled-mipped", "sampled-1-level"};
enum Side : uint32_t { SIDE_LE_256 = 0, SIDE_LE_512, SIDE_LE_1K, SIDE_LE_2K, SIDE_GT_2K, SIDE_COUNT };
static const char *const kSideName[SIDE_COUNT] = {"<=256", "<=512", "<=1K", "<=2K", ">2K"};
constexpr uint32_t kFormatSlots = 1024;    /* WMTPixelFormat numbers are below 600 */
constexpr uint32_t kTopFormats = 4;
constexpr uint32_t kUsageShaderWrite = 2;  /* WMTTextureUsageShaderWrite */
constexpr uint32_t kUsageRenderTarget = 4; /* WMTTextureUsageRenderTarget */

inline uint32_t KindOf(uint32_t usage, uint32_t levels) {
  if (usage & kUsageRenderTarget) return KIND_RT;
  if (usage & kUsageShaderWrite) return KIND_WRITE;
  return levels > 1 ? KIND_MIPPED : KIND_SINGLE;
}

inline uint32_t SideOf(uint32_t width, uint32_t height) {
  const uint32_t s = width > height ? width : height;
  return s <= 256 ? SIDE_LE_256 : s <= 512 ? SIDE_LE_512 : s <= 1024 ? SIDE_LE_1K : s <= 2048 ? SIDE_LE_2K : SIDE_GT_2K;
}

inline uint32_t FormatSlot(uint32_t format) { return format < kFormatSlots ? format : 0; }

/* The key an allocation keeps until it is freed: kind (2 bits), side (3 bits), array flag (1 bit), format slot (10 bits);
 * bit 31 says the key is valid (an allocation never filed keeps 0). */
inline uint32_t Key(uint32_t usage, uint32_t levels, uint32_t width, uint32_t height, uint32_t slices, uint32_t format) {
  return 0x80000000u | KindOf(usage, levels) | (SideOf(width, height) << 2) | ((slices > 1 ? 1u : 0u) << 5) |
         (FormatSlot(format) << 6);
}
inline uint32_t KeyKind(uint32_t key) { return key & 3u; }
inline uint32_t KeySide(uint32_t key) { return (key >> 2) & 7u; }
inline bool KeyArray(uint32_t key) { return (key >> 5) & 1u; }
inline uint32_t KeyFormat(uint32_t key) { return (key >> 6) & (kFormatSlots - 1); }

struct Tally {
  std::atomic<uint64_t> bytes{0};
  std::atomic<uint64_t> n{0};
};

struct State {
  Tally kind[KIND_COUNT];
  Tally side[KIND_COUNT][SIDE_COUNT];
  Tally format[KIND_COUNT][kFormatSlots];
  Tally arrays;
};

inline State g_state{};

inline void Move(Tally &t, uint64_t bytes, bool add) {
  if (add) {
    t.bytes.fetch_add(bytes, std::memory_order_relaxed);
    t.n.fetch_add(1, std::memory_order_relaxed);
  } else {
    t.bytes.fetch_sub(bytes, std::memory_order_relaxed);
    t.n.fetch_sub(1, std::memory_order_relaxed);
  }
}

inline void Apply(uint32_t key, uint64_t bytes, bool add) {
  if (!(key & 0x80000000u) || !bytes) return;
  const uint32_t k = KeyKind(key), s = KeySide(key);
  Move(g_state.kind[k], bytes, add);
  Move(g_state.side[k][s < SIDE_COUNT ? s : SIDE_GT_2K], bytes, add);
  Move(g_state.format[k][KeyFormat(key)], bytes, add);
  if (KeyArray(key)) Move(g_state.arrays, bytes, add);
}

/* File an allocation; returns the key it keeps (0 when it has no bytes). */
inline uint32_t Add(uint32_t usage, uint32_t levels, uint32_t width, uint32_t height, uint32_t slices, uint32_t format,
                    uint64_t bytes) {
  if (!bytes) return 0;
  const uint32_t key = Key(usage, levels, width, height, slices, format);
  Apply(key, bytes, true);
  return key;
}
inline void Sub(uint32_t key, uint64_t bytes) { Apply(key, bytes, false); }

/* The kTopFormats format slots of a kind with the most live bytes, largest first; unused entries are kFormatSlots. */
inline void TopFormats(uint32_t kind, uint32_t out[kTopFormats]) {
  uint64_t best[kTopFormats];
  for (uint32_t i = 0; i < kTopFormats; i++) { out[i] = kFormatSlots; best[i] = 0; }
  for (uint32_t f = 0; f < kFormatSlots; f++) {
    const uint64_t b = g_state.format[kind][f].bytes.load(std::memory_order_relaxed);
    if (!b) continue;
    for (uint32_t i = 0; i < kTopFormats; i++) {
      if (b > best[i]) {
        for (uint32_t j = kTopFormats - 1; j > i; j--) { best[j] = best[j - 1]; out[j] = out[j - 1]; }
        best[i] = b;
        out[i] = f;
        break;
      }
    }
  }
}

} // namespace dxmt::tex_census
"""

REPORT = """  {   /* """ + MARKER + """ */
    using namespace tex_census;
    auto &st = g_state;
    ERR("[tex-census] madeira-bcd live by kind: ",
        kKindName[0], "=", st.kind[0].bytes.load() >> 20, "MB/", st.kind[0].n.load(), " ",
        kKindName[1], "=", st.kind[1].bytes.load() >> 20, "MB/", st.kind[1].n.load(), " ",
        kKindName[2], "=", st.kind[2].bytes.load() >> 20, "MB/", st.kind[2].n.load(), " ",
        kKindName[3], "=", st.kind[3].bytes.load() >> 20, "MB/", st.kind[3].n.load(),
        " | arrays=", st.arrays.bytes.load() >> 20, "MB/", st.arrays.n.load());
    for (uint32_t k = 0; k < KIND_COUNT; k++) {
      if (!st.kind[k].n.load()) continue;
      uint32_t top[kTopFormats];
      TopFormats(k, top);
      std::string fmts;
      for (uint32_t i = 0; i < kTopFormats && top[i] < kFormatSlots; i++)
        fmts += " fmt" + std::to_string(top[i]) + "=" + std::to_string(st.format[k][top[i]].bytes.load() >> 20) + "MB/" +
                std::to_string(st.format[k][top[i]].n.load());
      ERR("[tex-census]   ", kKindName[k], " by side: ",
          kSideName[0], "=", st.side[k][0].bytes.load() >> 20, "MB/", st.side[k][0].n.load(), " ",
          kSideName[1], "=", st.side[k][1].bytes.load() >> 20, "MB/", st.side[k][1].n.load(), " ",
          kSideName[2], "=", st.side[k][2].bytes.load() >> 20, "MB/", st.side[k][2].n.load(), " ",
          kSideName[3], "=", st.side[k][3].bytes.load() >> 20, "MB/", st.side[k][3].n.load(), " ",
          kSideName[4], "=", st.side[k][4].bytes.load() >> 20, "MB/", st.side[k][4].n.load(),
          " | top formats:", fmts);
    }
  }
"""

EDITS = {
    TEX_H: [
        ("""  uint64_t census_bytes_ = 0;   /* ml677: bytes to refund on destruction          */
""",
         """  uint64_t census_bytes_ = 0;   /* ml677: bytes to refund on destruction          */
  uint32_t census_tex_key_ = 0; /* """ + MARKER + """: tex_census key, 0 when not filed */
"""),
    ],
    TEX_C: [
        ("""#include "dxmt_mem_census.hpp"
""",
         """#include "dxmt_mem_census.hpp"
#include "dxmt_tex_census.hpp"   /* """ + MARKER + """ */
"""),
        ("""  mem_census_sub((MemOwner)census_owner_, census_bytes_);   /* ml677 */
""",
         """  mem_census_sub((MemOwner)census_owner_, census_bytes_);   /* ml677 */
  tex_census::Sub(census_tex_key_, census_bytes_);          /* """ + MARKER + """ */
"""),
        ("""  ta->census_bytes_ = mem_census_texture_bytes(info);
  mem_census_add(MEMOWN_TEX_PRIVATE, ta->census_bytes_);
""",
         """  ta->census_bytes_ = mem_census_texture_bytes(info);
  ta->census_tex_key_ = tex_census::Add((uint32_t)info.usage, info.mipmap_level_count, info.width, info.height,
                                        info.array_length, (uint32_t)info.pixel_format, ta->census_bytes_);   /* """ + MARKER + """ */
  mem_census_add(MEMOWN_TEX_PRIVATE, ta->census_bytes_);
"""),
    ],
    CEN_C: [
        ("""#include "util_madeira_switch.hpp"
""",
         """#include "util_madeira_switch.hpp"
#include "dxmt_tex_census.hpp"   /* """ + MARKER + """ */
#include <string>
"""),
        ("""  ERR("[mem-census]   buffers by size: ",
""",
         REPORT + """  ERR("[mem-census]   buffers by size: ",
"""),
    ],
}


def main():
    texts = {}
    for rel in EDITS:
        path = ROOT / rel
        if not path.exists():
            sys.exit(f"{NAME}: {path} not found (run in the directory whose dxmt is the tree to patch)")
        texts[rel] = path.read_text()
    hdr = ROOT / HDR
    done = [rel for rel, s in texts.items() if MARKER in s] + ([HDR] if hdr.exists() else [])
    if len(done) == len(EDITS) + 1:
        print(f"{NAME}: already patched")
        return
    if done:
        sys.exit(f"{NAME}: only partly patched ({', '.join(done)}): start from an unpatched tree")
    for rel, pairs in EDITS.items():
        for old, _ in pairs:
            if texts[rel].count(old) != 1:
                sys.exit(f"{NAME}: an anchor was not found exactly once in {rel} (DXMT changed): " + old.strip().splitlines()[0].strip())
    for rel, pairs in EDITS.items():
        for old, new in pairs:
            texts[rel] = texts[rel].replace(old, new, 1)
    hdr.write_text(HEADER)
    for rel, s in texts.items():
        (ROOT / rel).write_text(s)
    print(f"{NAME}: patched {HDR} (new), " + ", ".join(EDITS))


main()
