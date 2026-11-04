#!/usr/bin/env python3
"""Zero each new thread's lookup cache by decommit and commit, not by reading it.

madeira-bcd personal (2026-11-04). The LookupCache constructor
(FEXCore/Source/Interface/Core/LookupCache.cpp) runs ml606's ZeroScrub over
the whole L1 array of every new thread (2 MB, l1-only mode on the device) and,
with L2 on, over the L2 table. ZeroScrub reads every 16 KB page and writes
only the stale ones, on the assumption that "untouched anon pages map the
shared zero page, no footprint" - Linux's behaviour. On iOS a read of an
untouched anonymous page instantiates it, which is how the call-ret stacks
filled the FEX band until patch-fex-ios-callret-noscrub.py (device, build
4eb682e: fex band 925 -> ~390 MB in V.0.7). Every one of the 3791 scrubs in the
six device logs of 2026-11-04 reported l1-stale=0x0.

Unlike the elided call-ret stack, this cache must start zeroed (stale bytes
send the dispatcher's cbz miss to garbage), so the scrub is not skipped: the
range is handed back with VirtualDontNeed (VirtualFree MEM_DECOMMIT, then
MEM_COMMIT with the same protection). That is the reset ResetCallRetStack()
already relies on (Core.cpp, ml609 note): the cache is not pool-aliased, so
Wine's decommit_pages() (build/ntdll-unix/virtual_ios.c) maps fresh zero-fill
pages over it ("mmap-over", or "swap-hole" in a swap reservation) and its
read-back checks the first 64 bytes only. The decommit contract gives zeros
without touching the other pages. One line per process says so;
MADEIRA_FEX_L1_SCRUB=1 keeps the read-and-scrub byte for byte.

Applied by tools/build-xtajit64.sh for whichever pin; the pinned submodule is
never updated.
"""
from pathlib import Path
import sys

MARKER = "l1-reclaim-v1 madeira-bcd"
SUMMARY = ("FEX: a new thread's lookup cache is zeroed by decommit, not read page by page "
           "(MADEIRA_FEX_L1_SCRUB=1 reads it as before)")

REL = "FEXCore/Source/Interface/Core/LookupCache.cpp"
OLD = (
    "  size_t StaleL2 = L2Enabled ? FEXCore::Allocator::ZeroScrub(reinterpret_cast<void*>(PageMemory), L2TableSize) : 0;\n"
    "  size_t StaleL1 = FEXCore::Allocator::ZeroScrub(reinterpret_cast<void*>(L1Pointer), MAX_L1_SIZE);\n"
    "  LogMan::Msg::EFmt(\"[TI-IC] zero-scrub rev=ml606 l2-stale=0x{:x} l1-stale=0x{:x}\", StaleL2, StaleL1);\n"
)
NEW = (
    "  /* " + MARKER + " (tools/patch-fex-ios-l1-reclaim.py): on iOS the read below instantiates every\n"
    "   * page it checks (2 MB of L1 per thread). Decommit + commit gives the zeros Wine's decommit_pages()\n"
    "   * guarantees here (fresh zero-fill pages over a range that is not pool-aliased) without touching the\n"
    "   * rest of the range. MADEIRA_FEX_L1_SCRUB=1 keeps the read-and-scrub. */\n"
    "  static const bool IosL1Scrub = [] {\n"
    "    const char* Opt = getenv(\"MADEIRA_FEX_L1_SCRUB\");\n"
    "    return Opt && Opt[0] == '1';\n"
    "  }();\n"
    "  if (!IosL1Scrub) {\n"
    "    if (L2Enabled) {\n"
    "      FEXCore::Allocator::VirtualDontNeed(reinterpret_cast<void*>(PageMemory), L2TableSize);\n"
    "    }\n"
    "    FEXCore::Allocator::VirtualDontNeed(reinterpret_cast<void*>(L1Pointer), MAX_L1_SIZE);\n"
    "    static std::atomic<bool> IosL1Said {false};\n"
    "    if (!IosL1Said.exchange(true, std::memory_order_relaxed)) {\n"
    "      LogMan::Msg::EFmt(\"[TI-IC] zero-reclaim " + MARKER + ": the lookup cache is zeroed by decommit, not read \"\n"
    "                        \"(MADEIRA_FEX_L1_SCRUB=1 reads it as before)\");\n"
    "    }\n"
    "  } else\n"
    "  {\n"
    + "".join("  " + line + "\n" for line in OLD.splitlines()) +
    "  }\n"
)


def patch(source):
    if MARKER in source:
        if source.count(NEW) != 1 or OLD in source.replace(NEW, ""):
            raise ValueError("partial or changed L1 reclaim overlay")
        return source
    if source.count(OLD) != 1:
        raise ValueError("LookupCache zero-scrub anchor changed")
    return source.replace(OLD, NEW, 1)


if __name__ == "__main__":
    path = Path(sys.argv[1]) / REL
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-l1-reclaim: " + str(error))
    if result != source:
        path.write_text(result)
    print(SUMMARY)
