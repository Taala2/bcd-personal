#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""madeira-bcd personal (2026-11-24, CelSector): MADEIRA_RGBA_HALF=1 creates large single-level RGBA8 textures at half size
([rgba-half], d3d11-src.dll only, off unless the value is exactly "1").

CelSector (Unity, 64-bit Direct3D 11, a GPU that samples BC) is killed at 6,051-6,059 of its 6,143 MB limit. With
env.DXMT_CENSUS_THROTTLE = 0 the [tex-census] reports up to the kill show why: in 2.4 s the game created 20 more single-level
RGBA8 textures with a side over 2,048 px, 64 MB each (4096x4096 class), and freed none; that bucket went from 824 MB/15 to
2,056 MB/35 while every other one stayed flat. They are not BC and have one level, so neither mip clamp nor [bc-half] can touch
them. They are filled by a copy from a STAGING resource: the buffer owner's requested bytes rise ~63 MB per texture
(StagingResource::pool) while init-upload stays flat. The game renders at 1424x656, below half of 4,096.

With MADEIRA_RGBA_HALF=1 (game file: env.MADEIRA_RGBA_HALF = 1; any other value or unset changes nothing):
  * a 2D texture in R8G8B8A8 or B8G8R8A8/X8 (typeless, UNORM, sRGB: DXGI 27-29, 87, 88, 90-93), Usage DEFAULT or IMMUTABLE,
    bound only as a shader resource, without CPU access, misc flags or multisampling, one level, one slice and one side over
    2,048 px (rgba_half::Candidate, rgba_half::RejectReason) gets a Metal texture of half its width and height, rounded up,
    through the Texture::scaleShift() of patch-dxmt-bc-half (so this patch runs after it). The D3D descriptor stays logical.
    An odd side is taken only without initial data (2026-11-25: in CelSector's last burst 14 of 20 such textures stayed full
    size while all 6 halved ones had even sides). A candidate that is not taken is logged with its fields and the reason.
  * a copy from a staging resource into such a texture (CopyResource, CopySubresourceRegion) with an even destination origin and
    an even size, or an odd size that ends at the level's edge, is downscaled on the CPU at call time (a 2x2 box per channel,
    rounded, the last odd column or row averaged with itself; in sRGB formats the average is taken on the encoded bytes) and
    uploaded at half the origin and size, as ml747 does for BC staging data. Any other copy into it is dropped and counted
    ("[rgba-half] madeira-bcd: copy into a half-size texture dropped (#n: reason)").
  * initial data at creation (both sides even, see above) is downscaled the same way in ResourceInitializer::initWithData.
  * an update (UpdateSubresource) into such a texture, and a copy out of it, stay dropped by patch-dxmt-bc-half's guards
    ("[bc-half] madeira-bcd: update/copy on a half-size texture dropped").
Sampling with normalised coordinates is unchanged and the texture is blurrier. A shader that reads it with Load() or asks
GetDimensions() sees the physical size.
Logs, first 8 then every 64th: "[rgba-half] madeira-bcd #n single-level WxH format=F -> physical W'xH' (initial data|no
initial data)", "[rgba-half] madeira-bcd: copy #n WxH -> W'xH'" and "[rgba-half] madeira-bcd: not halved #n WxH format=F
levels=.. array=.. usage=.. bind=.. cpu=.. misc=.. samples=.. (initial data|no initial data): reason"; once when on:
"[rgba-half] madeira-bcd on: ...".
The policy and the downscale are in dxmt_rgba_half.hpp, which has no Windows or Metal type, so
tests/host/check-dxmt-rgba-half.py compiles it on the host.

d3d11-src.dll only (tools/build-d3d11-dll.sh, BCD_PATCHES, after patch-dxmt-bc-half.py): a 64-bit game gets it with
env.MADEIRA_D3D11_SRC = 1. Idempotent; refuses a partly patched tree; fails by name when an anchor moved. Run in the directory
whose "dxmt" is the tree to patch.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd personal [rgba-half]"
NAME = "patch-d3d11-src-rgba-half"
BC_HALF = "madeira-bcd [bc-half]"
HDR = "dxmt/dxmt_rgba_half.hpp"
DEVICE = "d3d11/d3d11_texture_device.cpp"
CTX = "d3d11/d3d11_context_impl.cpp"
INIT = "dxmt/dxmt_resource_initializer.cpp"

HEADER = """#pragma once
/* """ + MARKER + """ (2026-11-24, CelSector): large single-level RGBA8 textures at half size under MADEIRA_RGBA_HALF=1.
 * No Windows or Metal type here: tests/host/check-dxmt-rgba-half.py compiles this header on the host. */
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace dxmt::rgba_half {

/* One side over 2,048 px: CelSector's 64 MB textures are 4096x4096 class, its <=2K ones (~10 MB) stay full size. */
constexpr uint32_t kMinLargestSide = 2049;

/* Exactly "1" turns it on; anything else (unset, "0", "2", " 1") leaves every texture as it was. */
inline bool EnabledValue(const char *v) {
  return v && v[0] == '1' && v[1] == '\\0';
}

/* Four 8-bit channels: R8G8B8A8 typeless/UNORM/sRGB (27-29), B8G8R8A8/X8 UNORM (87, 88), typeless/sRGB (90-93). */
inline bool Format(uint32_t dxgi_format) {
  return (dxgi_format >= 27 && dxgi_format <= 29) || dxgi_format == 87 || dxgi_format == 88 ||
         (dxgi_format >= 90 && dxgi_format <= 93);
}

/* D3D11_USAGE_DEFAULT, D3D11_USAGE_IMMUTABLE, D3D11_BIND_SHADER_RESOURCE. */
constexpr uint32_t kUsageDefault = 0, kUsageImmutable = 1, kBindShaderResource = 0x8;

/* The physical side of a logical one: half, rounded up. With an odd side the last physical texel covers one logical column
 * (or row) alone. */
inline uint32_t HalfSide(uint32_t v) {
  return (uint32_t)(((uint64_t)v + 1u) >> 1);
}

/* A texture this patch is about: one of the formats with a side over 2,048 px. A candidate is either taken at half size or
 * logged with its RejectReason. */
inline bool Candidate(uint32_t dxgi_format, uint32_t width, uint32_t height) {
  return Format(dxgi_format) && (width >= kMinLargestSide || height >= kMinLargestSide);
}

struct Desc {
  uint32_t width, height, mip_levels, array_size, usage, bind_flags, cpu_access, misc_flags, sample_count;
  bool initial_data;
};

/* nullptr: take the candidate at half size; otherwise why not. An odd side is taken only without initial data:
 * ResourceInitializer::initWithData sees only the physical size and so could not tell an odd logical side from an even one.
 * (2026-11-25, CelSector: 14 of 20 4096x4096-class textures in its last burst were not taken while all 6 taken had even
 * sides, which an odd side explains.) */
inline const char *RejectReason(const Desc &d) {
  if (d.mip_levels != 1) return "more than one level";
  if (d.array_size != 1) return "an array";
  if (d.sample_count > 1) return "multisampled";
  if (d.usage != kUsageDefault && d.usage != kUsageImmutable) return "usage is not DEFAULT or IMMUTABLE";
  if (d.bind_flags != kBindShaderResource) return "bound as more than a shader resource";
  if (d.cpu_access) return "CPU access";
  if (d.misc_flags) return "misc flags";
  if (d.width < 2 || d.height < 2) return "a side under 2 px";
  if (d.initial_data && ((d.width | d.height) & 1u)) return "an odd side with initial data";
  return nullptr;
}

/* A copy region of the logical level: an even origin, inside the level, and an even size unless the region ends at the
 * level's edge (then its last physical texel takes the odd column or row alone, as the texture's own last texel does). */
inline bool RegionOk(uint64_t x, uint64_t y, uint64_t w, uint64_t h, uint64_t level_w, uint64_t level_h) {
  return w && h && !(x & 1u) && !(y & 1u) && x <= level_w && y <= level_h && w <= level_w - x && h <= level_h - y &&
         (!(w & 1u) || w == level_w - x) && (!(h & 1u) || h == level_h - y);
}

/* src_w x src_h texels of 4 bytes (rows src_pitch bytes apart) -> HalfSide(src_w) x HalfSide(src_h), tightly packed. A 2x2
 * box per channel, rounded; with an odd side the last column (row) is averaged with itself, so nothing outside src_w x src_h
 * is read. */
inline void Downscale2x2(const uint8_t *src, size_t src_pitch, uint32_t src_w, uint32_t src_h, uint8_t *dst) {
  const uint32_t dw = HalfSide(src_w), dh = HalfSide(src_h);
  for (uint32_t y = 0; y < dh; y++) {
    const uint8_t *r0 = src + (size_t)(2 * y) * src_pitch;
    const uint8_t *r1 = (2 * y + 1 < src_h) ? r0 + src_pitch : r0;
    uint8_t *o = dst + (size_t)y * dw * 4;
    for (uint32_t x = 0; x < dw; x++) {
      const uint8_t *a = r0 + (size_t)x * 8;
      const uint8_t *b = r1 + (size_t)x * 8;
      const size_t nx = (2 * x + 1 < src_w) ? 4 : 0;
      for (uint32_t c = 0; c < 4; c++)
        o[(size_t)x * 4 + c] = (uint8_t)((a[c] + a[nx + c] + b[c] + b[nx + c] + 2u) >> 2);
    }
  }
}

inline bool ShouldLog(uint64_t n) {
  return n <= 8 || (n % 64) == 0;
}

inline std::atomic<uint64_t> g_created{0};
inline std::atomic<uint64_t> g_copied{0};
inline std::atomic<uint64_t> g_dropped{0};
inline std::atomic<uint64_t> g_rejected{0};

} // namespace dxmt::rgba_half
"""

EDITS = {}

EDITS[DEVICE] = [
    ("""#include "d3d11_mip_clamp_policy.hpp"   /* ml2000: d3d11.mipClampAuto */
""",
     """#include "d3d11_mip_clamp_policy.hpp"   /* ml2000: d3d11.mipClampAuto */
#include "dxmt_rgba_half.hpp"   /* """ + MARKER + """ */
"""),
    ("""template <typename tag>
HRESULT CreateDeviceTextureInternal(MTLD3D11Device *pDevice,
""",
     """/* """ + MARKER + """: MADEIRA_RGBA_HALF, read once. */
static bool
RgbaHalfEnabled() {
  static const bool on = [] {
    const bool v = rgba_half::EnabledValue(env::getEnvVar("MADEIRA_RGBA_HALF").c_str());
    if (v)
      ERR("[rgba-half] madeira-bcd on: single-level RGBA8/BGRA8 shader-resource textures with a side over ",
          rgba_half::kMinLargestSide - 1, " px at half size (MADEIRA_RGBA_HALF=1)");
    return v;
  }();
  return on;
}

template <typename tag>
HRESULT CreateDeviceTextureInternal(MTLD3D11Device *pDevice,
"""),
    ("""" headroom=", headroom, " MB");
      }
    }
    if (mip_bias) {
""",
     """" headroom=", headroom, " MB");
      }
    }
    /* """ + MARKER + """ (2026-11-24, CelSector: 20 single-level 4096x4096-class RGBA8 textures in 2.4 s before the kill,
     * filled from staging): only `info` shrinks; the descriptor the game reads stays logical. The context downscales a staging
     * copy into it (RgbaHalfCopy), initWithData downscales initial data, and patch-dxmt-bc-half's guards drop the rest. A
     * candidate that is not taken is logged with the reason. */
    if (!mip_bias && !scale_shift && RgbaHalfEnabled() &&
        rgba_half::Candidate((uint32_t)finalDesc.Format, finalDesc.Width, finalDesc.Height)) {
      const rgba_half::Desc half_desc = {finalDesc.Width, finalDesc.Height, finalDesc.MipLevels, finalDesc.ArraySize,
                                         (uint32_t)finalDesc.Usage, (uint32_t)finalDesc.BindFlags,
                                         (uint32_t)finalDesc.CPUAccessFlags, (uint32_t)finalDesc.MiscFlags,
                                         (uint32_t)finalDesc.SampleDesc.Count, pInitialData != nullptr};
      if (const char *why = rgba_half::RejectReason(half_desc)) {
        const uint64_t n = rgba_half::g_rejected.fetch_add(1, std::memory_order_relaxed) + 1;
        if (rgba_half::ShouldLog(n))
          ERR("[rgba-half] madeira-bcd: not halved #", n, " ", finalDesc.Width, "x", finalDesc.Height, " format=",
              (uint32_t)finalDesc.Format, " levels=", half_desc.mip_levels, " array=", half_desc.array_size, " usage=",
              half_desc.usage, " bind=", half_desc.bind_flags, " cpu=", half_desc.cpu_access, " misc=",
              half_desc.misc_flags, " samples=", half_desc.sample_count,
              pInitialData ? " (initial data): " : " (no initial data): ", why);
      } else {
        scale_shift = 1;
        physDesc.Width  = rgba_half::HalfSide(finalDesc.Width);
        physDesc.Height = rgba_half::HalfSide(finalDesc.Height);
        info.width      = physDesc.Width;
        info.height     = physDesc.Height;
        const uint64_t n = rgba_half::g_created.fetch_add(1, std::memory_order_relaxed) + 1;
        if (rgba_half::ShouldLog(n))
          ERR("[rgba-half] madeira-bcd #", n, " single-level ", finalDesc.Width, "x", finalDesc.Height, " format=",
              (uint32_t)finalDesc.Format, " -> physical ", physDesc.Width, "x", physDesc.Height,
              pInitialData ? " (initial data)" : " (no initial data)");
      }
    }
    if (mip_bias) {
"""),
]

EDITS[CTX] = [
    ("""#include "dxmt_texture.hpp"
""",
     """#include "dxmt_texture.hpp"
#include "dxmt_rgba_half.hpp"   /* """ + MARKER + """ */
"""),
    ("""  void
  CopyTexture(TextureCopyCommand &&cmd) {
    if (cmd.Invalid)
      return;
""",
     """  /* """ + MARKER + """: a copy into a half-size RGBA8 texture. Returns true when it was handled here: a staging source with
   * an even region is downscaled on the CPU at call time (as ml747 decodes BC staging data) and uploaded at half the origin
   * and size; anything else is dropped and counted. False leaves every other copy to the code below. */
  bool
  RgbaHalfCopy(TextureCopyCommand &cmd) {
    auto dst = GetTexture(cmd.pDst);
    if (!dst || dst->scaleShift() != 1 || !rgba_half::Format((uint32_t)cmd.Dst.Format))
      return false;
    auto staging_src = GetStagingResource(cmd.pSrc, cmd.SrcSubresource);
    const void *base = nullptr;
    const char *why = nullptr;
    if (!staging_src)
      why = "the source is not a staging resource";
    else if (cmd.SrcFormat.BytesPerTexel != 4 || (cmd.SrcFormat.Flag & MTL_DXGI_FORMAT_BC))
      why = "the source format is not 4 bytes per texel";
    else if (cmd.Dst.MipLevel || cmd.Dst.ArraySlice || cmd.SrcSize.depth != 1 || cmd.DstOrigin.z)
      why = "level, slice or depth";
    else if (!rgba_half::RegionOk(cmd.DstOrigin.x, cmd.DstOrigin.y, cmd.SrcSize.width, cmd.SrcSize.height,
                                  cmd.Dst.Width, cmd.Dst.Height))
      why = "an odd origin, an odd size short of the edge, or outside the level";
    else if (!(base = staging_src->mappedImmediateMemory()))
      why = "the staging memory is not mapped";
    const uint32_t src_w = (uint32_t)cmd.SrcSize.width, src_h = (uint32_t)cmd.SrcSize.height;
    const uint32_t dst_w = rgba_half::HalfSide(src_w), dst_h = rgba_half::HalfSide(src_h);
    const size_t pitch = (size_t)dst_w * 4, bytes = pitch * dst_h;
    std::unique_ptr<uint8_t[]> half;
    if (!why) {
      half.reset(new (std::nothrow) uint8_t[bytes]);
      if (!half)
        why = "out of memory";
    }
    if (why) {
      const uint64_t n = rgba_half::g_dropped.fetch_add(1, std::memory_order_relaxed) + 1;
      if (rgba_half::ShouldLog(n))
        ERR("[rgba-half] madeira-bcd: copy into a half-size texture dropped (#", n, ": ", why, ")");
      return true;
    }
    const size_t src_off = (size_t)cmd.SrcOrigin.z * staging_src->bytesPerImage +
                           (size_t)cmd.SrcOrigin.y * staging_src->bytesPerRow + (size_t)cmd.SrcOrigin.x * 4;
    rgba_half::Downscale2x2((const uint8_t *)base + src_off, staging_src->bytesPerRow, src_w, src_h, half.get());

    SwitchToBlitEncoder(CommandBufferState::UpdateBlitEncoderActive);
    auto [half_buffer, half_offset] = AllocateStagingBuffer(bytes, 16);
    half_buffer.updateContents(half_offset, half.get(), bytes);
    const WMTOrigin origin = {cmd.DstOrigin.x >> 1, cmd.DstOrigin.y >> 1, 0};
    const WMTSize size = {(uint64_t)dst_w, (uint64_t)dst_h, 1};
    const uint32_t slice = cmd.Dst.ArraySlice, level = cmd.Dst.MipLevel;
    EmitOP([dst_ = std::move(dst), half_buffer, half_offset, pitch, bytes, origin, size, slice,
            level](ArgumentEncodingContext &enc) {
      auto dst_tex = enc.access(dst_, level, slice, DXMT_ENCODER_RESOURCE_ACESS_WRITE);
      auto &c = enc.encodeBlitCommand<wmtcmd_blit_copy_from_buffer_to_texture>();
      c.type = WMTBlitCommandCopyFromBufferToTexture;
      c.src = half_buffer;
      c.src_offset = half_offset;
      c.bytes_per_row = (uint32_t)pitch;
      c.bytes_per_image = (uint32_t)bytes;
      c.size = size;
      c.dst = dst_tex;
      c.slice = slice;
      c.level = level;
      c.origin = origin;
    });
    const uint64_t n = rgba_half::g_copied.fetch_add(1, std::memory_order_relaxed) + 1;
    if (rgba_half::ShouldLog(n))
      ERR("[rgba-half] madeira-bcd: copy #", n, " ", src_w, "x", src_h, " -> ", dst_w, "x", dst_h);
    return true;
  }

  void
  CopyTexture(TextureCopyCommand &&cmd) {
    if (cmd.Invalid)
      return;
    if (RgbaHalfCopy(cmd))   /* """ + MARKER + """ */
      return;
"""),
]

EDITS[INIT] = [
    ("""#include "dxmt_bcn.hpp"
""",
     """#include "dxmt_bcn.hpp"
#include "dxmt_rgba_half.hpp"   /* """ + MARKER + """ */
"""),
    ("""  /* """ + BC_HALF + """: a half-size texture takes only the half-size decode above; anything else (no decode, out of memory)
""",
     """  /* """ + MARKER + """: a half-size RGBA8 texture created with its data. The caller's level 0 is the LOGICAL size, twice
   * the physical width and height (a texture with initial data is halved only when both sides are even,
   * rgba_half::RejectReason); downscale it here so the guard below sees a half-size image. */
  if (texture->scaleShift() == 1 && level == 0 && !decoded && !bc_kind && block_size == 1u && texel_size == 4 &&
      !is_1d_tex && !is_3d_tex) {
    const size_t out_bytes = (size_t)width_sub * height_sub * 4;
    decoded.reset(new (std::nothrow) uint8_t[out_bytes]);
    if (decoded) {
      rgba_half::Downscale2x2((const uint8_t *)data, row_pitch, width_sub * 2, height_sub * 2, decoded.get());
      data = decoded.get();
      row_pitch = (size_t)width_sub * 4;
      depth_pitch = out_bytes;
    }
  }
  /* """ + BC_HALF + """: a half-size texture takes only the half-size decode above; anything else (no decode, out of memory)
"""),
]


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
    missing = [rel for rel, s in texts.items() if BC_HALF not in s]
    if missing:
        sys.exit(f"{NAME}: patch-dxmt-bc-half.py is not applied ({', '.join(missing)}): it provides Texture::scaleShift")
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
