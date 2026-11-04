#!/usr/bin/env python3
"""Order vector and block-copy accesses in Chromium renderer processes only.

madeira-bcd personal (2026-11-04). Each Wine pseudo-process runs its own
ProcessInit (Source/Windows/ARM64EC/Module.cpp) and loads FEX's settings from
its own environment, so the memory-ordering options can differ per process.
On the device (build 627, iPhone16,1):
  - V.0.7 (NW.js 0.48, 64-bit) enters the game only with both
    FEX_VECTORTSOENABLED=1 and FEX_MEMCPYSETTSOENABLED=1; without either one
    CrRendererMain jumps into int3-filled code ([entry-fail], guest bytes cc);
  - with both on in every process (the Compatibility preset) Succum never
    started its renderer: 95 s, 0 frames, ~14 browser-process threads busy in
    nw.dll's short copy routines (twice); without them it loads in ~17 s;
  - Internship, on the same nw.dll as V.0.7, kept ~55 fps with both on
    (57-60 without).
So the strict order goes where the failures are, the processes started with
--type=renderer (NW.js, CEF and Electron alike), and the browser, GPU and
utility processes keep the configured order.

A game file that names FEX_VECTORTSOENABLED or FEX_MEMCPYSETTSOENABLED keeps
its value for that option (a preset writes both). MADEIRA_RENDERER_STRICT_TSO=0
turns this off. One log line per process says what was decided. Only the
FEX_IOS_HOST ARM64EC build changes; the WOW64 module is untouched.

Applied by tools/build-xtajit64.sh for whichever pin. The block goes right
after CONFIG_IS64BIT_MODE is set, so the Launcher full-SMC overlay's block
above that line stays as it wrote it, and the context reads its options later
(CreateNewContext); the pinned submodule is never updated.
"""
from pathlib import Path
import sys

MARKER = "renderer-tso-v1 madeira-bcd"
SUMMARY = ("FEX ARM64EC: renderer processes order vector and block-copy accesses "
           "(MADEIRA_RENDERER_STRICT_TSO=0 keeps the configured order)")

REL = "Source/Windows/ARM64EC/Module.cpp"
ANCHOR = '  FEXCore::Config::Set(FEXCore::Config::CONFIG_IS64BIT_MODE, "1");\n'
ADD = '''#ifdef FEX_IOS_HOST
  /* ''' + MARKER + ''' (tools/patch-fex-ios-renderer-tso.py): a process started with
   * --type=renderer orders vector loads/stores and REP MOVS/STOS as x86 does. V.0.7's renderer
   * jumps into freed (int3) code without both; Succum's browser process crawls with them, so
   * only renderers get them. An explicit FEX_VECTORTSOENABLED or FEX_MEMCPYSETTSOENABLED wins;
   * MADEIRA_RENDERER_STRICT_TSO=0 turns this off. Set before the context takes its options. */
  {
    const char* StrictOpt = getenv("MADEIRA_RENDERER_STRICT_TSO");
    const char* VectorExplicit = getenv("FEX_VECTORTSOENABLED");
    const char* MemcpyExplicit = getenv("FEX_MEMCPYSETTSOENABLED");
    const bool VectorSet = VectorExplicit && VectorExplicit[0];
    const bool MemcpySet = MemcpyExplicit && MemcpyExplicit[0];
    bool Renderer = false;
    const auto* Teb = reinterpret_cast<__TEB*>(IOSLoadTEB());
    const auto* Params = (Teb && Teb->Peb) ? Teb->Peb->ProcessParameters : nullptr;
    if (Params && Params->CommandLine.Buffer) {
      const auto* Cmd = reinterpret_cast<const WCHAR*>(Params->CommandLine.Buffer);
      const size_t Len = Params->CommandLine.Length / sizeof(WCHAR);
      static const char Needle[] = "--type=renderer";
      const size_t NeedleLen = sizeof(Needle) - 1;
      for (size_t i = 0; !Renderer && i + NeedleLen <= Len; i++) {
        size_t k = 0;
        while (k < NeedleLen && Cmd[i + k] == static_cast<WCHAR>(Needle[k])) {
          k++;
        }
        Renderer = k == NeedleLen;
      }
    }
    const bool Enable = Renderer && !(StrictOpt && StrictOpt[0] == '0');
    if (Enable && !VectorSet) {
      FEXCore::Config::Set(FEXCore::Config::CONFIG_VECTORTSOENABLED, "1");
    }
    if (Enable && !MemcpySet) {
      FEXCore::Config::Set(FEXCore::Config::CONFIG_MEMCPYSETTSOENABLED, "1");
    }
    FEX_CONFIG_OPT(VectorEffective, VECTORTSOENABLED);
    FEX_CONFIG_OPT(MemcpyEffective, MEMCPYSETTSOENABLED);
    LogMan::Msg::IFmt("FEX: ''' + MARKER + ''' renderer={} enabled={} vector={}{} memcpyset={}{} exe={}",
                     Renderer, Enable, VectorEffective() != 0, VectorSet ? " (game file)" : "",
                     MemcpyEffective() != 0, MemcpySet ? " (game file)" : "", ExecutableName);
  }
#endif
'''


def patch(source):
    after = ANCHOR + ADD
    if MARKER in source:
        if source.count(after) != 1:
            raise ValueError("partial or changed renderer TSO overlay")
        return source
    if source.count(ANCHOR) != 1:
        raise ValueError("ProcessInit configuration anchor changed")
    return source.replace(ANCHOR, after, 1)


if __name__ == "__main__":
    path = Path(sys.argv[1]) / REL
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-renderer-tso: " + str(error))
    if result != source:
        path.write_text(result)
    print(SUMMARY)
