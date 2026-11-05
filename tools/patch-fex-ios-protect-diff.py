#!/usr/bin/env python3
"""Across a code range's W^X protection cycle, drop only the pages whose bytes changed.

madeira-bcd personal (2026-11-05). V8 (NW.js, Electron, CEF renderers) writes code with
write-protect-code-memory: it turns a whole code page (0x3d000 bytes in V.0.7) RW, writes,
and turns it RX again. InvalidationTracker::HandleMemoryProtectionNotification dropped every
translation in the range on the RW step ([iOS-xrem] via=protect) and once more on the RX step.
The log-only first version of this overlay (protect-diff-log-v1, CI 41983510744) measured
V.0.7 on the device: of 310701 pages in 5376 cycles 3% changed. The renderer's 128 MB code
buffer filled every 5-22 s, ~21% of its core went to compiling, 80-90% of it again
(FINDINGS.md 5e).

Now, for a range that leaves the executable set (the RW step):
  - its bytes are copied (at most 256 KB, 32 ranges at a time, static memory, only when every
    page is committed and readable) and its translations are kept; without a copy (too big,
    unreadable, slots busy) it is dropped as before;
  - a pending range that the new one overlaps, or the oldest one when all slots are taken, is
    dropped whole first: its invalidation was only deferred.
On the next executable protection (the RX step), if one pending copy covers the range:
  - pages whose bytes differ from the copy are dropped (runs of 4 KB pages), the others kept;
  - the copy is dropped whole instead when an execute query missed inside it while it was
    pending (a NoExec block may be cached there; a 64-entry ring of misses, overflow counts
    as a miss), or when the changed pages make more than 16 runs.
Any other executable protection is handled as before (the whole range), and pending copies it
only partly meets are dropped whole. FEX's own write traps, frees and section unmaps are
untouched: they still invalidate whatever they did.

Log lines: [protect-diff] protect-diff-v2 ... (first eight compares, totals every 256th, one
line when the first page is kept). env.MADEIRA_FEX_PROTECT_DIFF: unset or 1 on; log = measure
only (FEX drops whole ranges, as the log-only version); 0 = off, upstream behaviour.

Applied by tools/build-xtajit64.sh for whichever pin; the pinned submodule is never updated.
"""
from pathlib import Path
import sys

MARKER = "protect-diff-v2 madeira-bcd"
SUMMARY = ("FEX: a code range's W^X cycle drops only the pages whose bytes changed "
           "([protect-diff]; env.MADEIRA_FEX_PROTECT_DIFF=log measures only, 0 restores whole-range drops)")

REL = "Source/Windows/Common/InvalidationTracker.cpp"

HELPER_ANCHOR = (
    "static bool ProtIsWritable(ULONG Prot) {\n"
    "  return (Prot & (PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)) != 0;\n"
    "}\n"
)
HELPER = (
    "\n"
    "/* " + MARKER + " (tools/patch-fex-ios-protect-diff.py). A code range made non-executable is copied\n"
    " * and keeps its translations; the next executable protection over it drops only the pages whose\n"
    " * bytes changed. Anything this cannot vouch for is dropped whole, as before. Static memory only:\n"
    " * nothing is allocated inside a memory notification. env.MADEIRA_FEX_PROTECT_DIFF: log = measure\n"
    " * only, 0 = off. */\n"
    "#ifdef FEX_IOS_HOST\n"
    "namespace {\n"
    "constexpr int IosPdSlots = 32;\n"
    "constexpr uint64_t IosPdMax = 0x40000;\n"
    "constexpr int IosPdMaxRuns = 16;\n"
    "constexpr uint64_t IosPdRing = 64;\n"
    "struct IosPdSlot {\n"
    "  uint64_t Base;\n"
    "  uint64_t Size;\n"
    "  uint64_t Stamp;\n"
    "  uint64_t MissSeq;\n"
    "};\n"
    "IosPdSlot IosPdSlotTable[IosPdSlots];\n"
    "alignas(64) uint8_t IosPdData[IosPdSlots][IosPdMax];\n"
    "std::atomic_flag IosPdLock = ATOMIC_FLAG_INIT;\n"
    "std::atomic<uint64_t> IosPdBusy {0};\n"
    "struct IosPdCounts {\n"
    "  uint64_t Toggles, Snaps, SkipBig, SkipUnreadable, Evicted, Compared, Pages, Changed, NoSnap, CompareUnreadable;\n"
    "  uint64_t Kept, Redone, Tainted, Partial, Overlap, ManyRuns;\n"
    "};\n"
    "IosPdCounts IosPdN;\n"
    "uint64_t IosPdStampNext;\n"
    "\n"
    "// Execute queries that found no executable range: a ring, written seqlock-style.\n"
    "std::atomic<uint64_t> IosPdMissSeq {0};\n"
    "std::atomic<uint64_t> IosPdMissAddr[IosPdRing];\n"
    "std::atomic<uint64_t> IosPdMissTag[IosPdRing];\n"
    "\n"
    "void IosPdMiss(uint64_t Address) {\n"
    "  const uint64_t N = IosPdMissSeq.fetch_add(1, std::memory_order_acq_rel);\n"
    "  auto& Tag = IosPdMissTag[N % IosPdRing];\n"
    "  Tag.store(0, std::memory_order_relaxed);\n"
    "  std::atomic_thread_fence(std::memory_order_release);\n"
    "  IosPdMissAddr[N % IosPdRing].store(Address, std::memory_order_relaxed);\n"
    "  Tag.store(N + 1, std::memory_order_release);\n"
    "}\n"
    "\n"
    "// A miss numbered [From, To) inside [Base, End)? Also true when the ring cannot tell.\n"
    "bool IosPdMissedIn(uint64_t From, uint64_t To, uint64_t Base, uint64_t End) {\n"
    "  if (To - From > IosPdRing) {\n"
    "    return true;\n"
    "  }\n"
    "  for (uint64_t N = From; N < To; N++) {\n"
    "    auto& Tag = IosPdMissTag[N % IosPdRing];\n"
    "    if (Tag.load(std::memory_order_acquire) != N + 1) {\n"
    "      return true;\n"
    "    }\n"
    "    const uint64_t A = IosPdMissAddr[N % IosPdRing].load(std::memory_order_relaxed);\n"
    "    std::atomic_thread_fence(std::memory_order_acquire);\n"
    "    if (Tag.load(std::memory_order_relaxed) != N + 1 || (A >= Base && A < End)) {\n"
    "      return true;\n"
    "    }\n"
    "  }\n"
    "  return false;\n"
    "}\n"
    "\n"
    "// 0 off, 1 on, 2 measure only.\n"
    "int IosPdMode() {\n"
    "  static const int Mode = [] {\n"
    "    const char* Opt = getenv(\"MADEIRA_FEX_PROTECT_DIFF\");\n"
    "    if (Opt && Opt[0] == '0') {\n"
    "      return 0;\n"
    "    }\n"
    "    if (Opt && Opt[0] == 'l') {\n"
    "      return 2;\n"
    "    }\n"
    "    return 1;\n"
    "  }();\n"
    "  return Mode;\n"
    "}\n"
    "\n"
    "// Every page of [Base, Base + Size) committed, readable, not a guard page.\n"
    "bool IosPdReadable(uint64_t Base, uint64_t Size) {\n"
    "  const uint64_t End = Base + Size;\n"
    "  uint64_t At = Base;\n"
    "  while (At < End) {\n"
    "    MEMORY_BASIC_INFORMATION Info;\n"
    "    if (NtQueryVirtualMemory(NtCurrentProcess(), reinterpret_cast<void*>(At), MemoryBasicInformation, &Info, sizeof(Info), nullptr)) {\n"
    "      return false;\n"
    "    }\n"
    "    if (Info.State != MEM_COMMIT || (Info.Protect & PAGE_GUARD) || !ProtIsReadable(Info.Protect)) {\n"
    "      return false;\n"
    "    }\n"
    "    const uint64_t Next = reinterpret_cast<uint64_t>(Info.BaseAddress) + Info.RegionSize;\n"
    "    if (Next <= At) {\n"
    "      return false;\n"
    "    }\n"
    "    At = Next;\n"
    "  }\n"
    "  return true;\n"
    "}\n"
    "\n"
    "// Never waits: a thread that faults while copying and re-enters here must not spin on its own lock.\n"
    "bool IosPdLockTake() {\n"
    "  if (IosPdLock.test_and_set(std::memory_order_acquire)) {\n"
    "    IosPdBusy.fetch_add(1, std::memory_order_relaxed);\n"
    "    return false;\n"
    "  }\n"
    "  return true;\n"
    "}\n"
    "\n"
    "void IosPdLockDrop() {\n"
    "  IosPdLock.clear(std::memory_order_release);\n"
    "}\n"
    "\n"
    "struct IosPdRange {\n"
    "  uint64_t Base, Size;\n"
    "};\n"
    "\n"
    "// Removed: the range left the executable set (the RW step). Exec: an executable, readable protection.\n"
    "// Seq0: IosPdMissSeq read before the notification touched the intervals. Inval(Base, Size) drops\n"
    "// translations; it is only called with no lock of this file held. Returns true when the caller must\n"
    "// not drop the whole range itself (everything needed was dropped here).\n"
    "template<typename F>\n"
    "bool IosPdNote(bool Removed, bool Exec, uint64_t Base, uint64_t Size, uint64_t Seq0, F&& Inval) {\n"
    "  const int Mode = (Removed || Exec) ? IosPdMode() : 0;\n"
    "  if (!Mode) {\n"
    "    return false;\n"
    "  }\n"
    "  const bool Fix = Mode == 1;\n"
    "  const uint64_t End = Base + Size;\n"
    "  IosPdRange Drop[IosPdSlots + IosPdMaxRuns + 2];\n"
    "  int NDrop = 0;\n"
    "  bool Handled = false;\n"
    "\n"
    "  if (Removed) {\n"
    "    const bool Fits = Size <= IosPdMax;\n"
    "    const bool Readable = Fits && IosPdReadable(Base, Size);\n"
    "    if (!IosPdLockTake()) {\n"
    "      return false;\n"
    "    }\n"
    "    IosPdN.Toggles++;\n"
    "    // A pending copy this range overlaps: its deferred drop happens now, whole.\n"
    "    for (int i = 0; i < IosPdSlots; i++) {\n"
    "      IosPdSlot& S = IosPdSlotTable[i];\n"
    "      if (S.Size && S.Base < End && S.Base + S.Size > Base) {\n"
    "        Drop[NDrop++] = {S.Base, S.Size};\n"
    "        S.Size = 0;\n"
    "        IosPdN.Overlap++;\n"
    "      }\n"
    "    }\n"
    "    if (!Fits) {\n"
    "      IosPdN.SkipBig++;\n"
    "    } else if (!Readable) {\n"
    "      IosPdN.SkipUnreadable++;\n"
    "    } else {\n"
    "      int Pick = -1;\n"
    "      for (int i = 0; i < IosPdSlots && Pick < 0; i++) {\n"
    "        if (!IosPdSlotTable[i].Size) {\n"
    "          Pick = i;\n"
    "        }\n"
    "      }\n"
    "      if (Pick < 0) {\n"
    "        Pick = 0;\n"
    "        for (int i = 1; i < IosPdSlots; i++) {\n"
    "          if (IosPdSlotTable[i].Stamp < IosPdSlotTable[Pick].Stamp) {\n"
    "            Pick = i;\n"
    "          }\n"
    "        }\n"
    "        Drop[NDrop++] = {IosPdSlotTable[Pick].Base, IosPdSlotTable[Pick].Size};\n"
    "        IosPdN.Evicted++;\n"
    "      }\n"
    "      memcpy(IosPdData[Pick], reinterpret_cast<const void*>(Base), Size);\n"
    "      IosPdSlotTable[Pick] = {Base, Size, ++IosPdStampNext, Seq0};\n"
    "      IosPdN.Snaps++;\n"
    "      Handled = Fix;\n"
    "    }\n"
    "    IosPdLockDrop();\n"
    "    if (Fix) {\n"
    "      for (int i = 0; i < NDrop; i++) {\n"
    "        Inval(Drop[i].Base, Drop[i].Size);\n"
    "      }\n"
    "    }\n"
    "    return Handled;\n"
    "  }\n"
    "\n"
    "  const bool Readable = IosPdReadable(Base, Size);\n"
    "  const uint64_t SeqNow = IosPdMissSeq.load(std::memory_order_acquire);\n"
    "  bool Any = false, Say = false, SayKept = false;\n"
    "  uint64_t SayBase = 0, SayEnd = 0, SayPages = 0, SayChanged = 0, SayN = 0;\n"
    "  IosPdCounts Copy {};\n"
    "  if (!IosPdLockTake()) {\n"
    "    return false;\n"
    "  }\n"
    "  for (int i = 0; i < IosPdSlots; i++) {\n"
    "    IosPdSlot& S = IosPdSlotTable[i];\n"
    "    if (!S.Size || S.Base >= End || S.Base + S.Size <= Base) {\n"
    "      continue;\n"
    "    }\n"
    "    Any = true;\n"
    "    const uint64_t SEnd = S.Base + S.Size;\n"
    "    // Whatever of the copy lies outside this range is dropped whole (the caller covers the inside\n"
    "    // when it is not handled here).\n"
    "    if (S.Base < Base) {\n"
    "      Drop[NDrop++] = {S.Base, Base - S.Base};\n"
    "    }\n"
    "    if (SEnd > End) {\n"
    "      Drop[NDrop++] = {End, SEnd - End};\n"
    "    }\n"
    "    const bool Covers = S.Base <= Base && SEnd >= End;\n"
    "    if (!Covers) {\n"
    "      IosPdN.Partial++;\n"
    "      S.Size = 0;\n"
    "      continue;\n"
    "    }\n"
    "    if (!Readable) {\n"
    "      IosPdN.CompareUnreadable++;\n"
    "      S.Size = 0;\n"
    "      continue;\n"
    "    }\n"
    "    const bool Tainted = IosPdMissedIn(S.MissSeq, SeqNow, S.Base, SEnd);\n"
    "    IosPdRange Runs[IosPdMaxRuns];\n"
    "    int NRuns = 0;\n"
    "    bool ManyRuns = false;\n"
    "    uint64_t Pages = 0, Changed = 0;\n"
    "    for (uint64_t P = Base; P < End; P += FEXCore::Utils::FEX_PAGE_SIZE) {\n"
    "      const uint64_t Len = std::min<uint64_t>(FEXCore::Utils::FEX_PAGE_SIZE, End - P);\n"
    "      Pages++;\n"
    "      if (memcmp(IosPdData[i] + (P - S.Base), reinterpret_cast<const void*>(P), Len) != 0) {\n"
    "        Changed++;\n"
    "        if (NRuns && Runs[NRuns - 1].Base + Runs[NRuns - 1].Size == P) {\n"
    "          Runs[NRuns - 1].Size += Len;\n"
    "        } else if (NRuns < IosPdMaxRuns) {\n"
    "          Runs[NRuns++] = {P, Len};\n"
    "        } else {\n"
    "          ManyRuns = true;\n"
    "        }\n"
    "      }\n"
    "    }\n"
    "    S.Size = 0;\n"
    "    IosPdN.Compared++;\n"
    "    IosPdN.Pages += Pages;\n"
    "    IosPdN.Changed += Changed;\n"
    "    if (Tainted) {\n"
    "      IosPdN.Tainted++;\n"
    "    } else if (ManyRuns) {\n"
    "      IosPdN.ManyRuns++;\n"
    "    } else if (Fix) {\n"
    "      for (int r = 0; r < NRuns; r++) {\n"
    "        Drop[NDrop++] = Runs[r];\n"
    "      }\n"
    "      Handled = true;\n"
    "      SayKept = IosPdN.Kept == 0 && Pages > Changed;\n"
    "      IosPdN.Kept += Pages - Changed;\n"
    "      IosPdN.Redone += Changed;\n"
    "    }\n"
    "    if (IosPdN.Compared <= 8 || !(IosPdN.Compared & 255)) {\n"
    "      Say = true;\n"
    "      SayBase = Base;\n"
    "      SayEnd = End;\n"
    "      SayPages = Pages;\n"
    "      SayChanged = Changed;\n"
    "      SayN = IosPdN.Compared;\n"
    "      Copy = IosPdN;\n"
    "    }\n"
    "  }\n"
    "  if (!Any) {\n"
    "    IosPdN.NoSnap++;\n"
    "  }\n"
    "  IosPdLockDrop();\n"
    "\n"
    "  if (Fix) {\n"
    "    for (int i = 0; i < NDrop; i++) {\n"
    "      Inval(Drop[i].Base, Drop[i].Size);\n"
    "    }\n"
    "  }\n"
    "  if (SayKept) {\n"
    "    LogMan::Msg::EFmt(\"[protect-diff] protect-diff-v2: the first unchanged page of a W^X cycle kept its translations \"\n"
    "                      \"({:#x}-{:#x}: {} of {} pages changed; env.MADEIRA_FEX_PROTECT_DIFF=0 drops whole ranges)\",\n"
    "                      SayBase, SayEnd, SayChanged, SayPages);\n"
    "  }\n"
    "  if (Say && SayN <= 8) {\n"
    "    LogMan::Msg::EFmt(\"[protect-diff] protect-diff-v2 #{} {:#x}-{:#x} pages={} changed={}\", SayN, SayBase, SayEnd, SayPages,\n"
    "                      SayChanged);\n"
    "  }\n"
    "  if (Say && !(SayN & 255)) {\n"
    "    LogMan::Msg::EFmt(\"[protect-diff] protect-diff-v2 totals ({}): toggles={} copied={} compared={} pages={} changed={} ({}%) \"\n"
    "                      \"kept={} redone={} whole: tainted={} many-runs={} partial={} overlap={} evicted={} \"\n"
    "                      \"exec-without-copy={} skipped: over-256KB={} unreadable={} unreadable-at-compare={} busy={}\",\n"
    "                      Fix ? \"on\" : \"measure only\", Copy.Toggles, Copy.Snaps, Copy.Compared, Copy.Pages, Copy.Changed,\n"
    "                      Copy.Pages ? Copy.Changed * 100 / Copy.Pages : 0, Copy.Kept, Copy.Redone, Copy.Tainted, Copy.ManyRuns,\n"
    "                      Copy.Partial, Copy.Overlap, Copy.Evicted, Copy.NoSnap, Copy.SkipBig, Copy.SkipUnreadable,\n"
    "                      Copy.CompareUnreadable, IosPdBusy.load(std::memory_order_relaxed));\n"
    "  }\n"
    "  return Handled;\n"
    "}\n"
    "} // namespace\n"
    "#endif\n"
)

DECL_ANCHOR = "  const bool NeedsInvalidate = [&]() {\n    std::unique_lock Lock(IntervalsLock);\n"
DECL = (
    "#ifdef FEX_IOS_HOST\n"
    "  bool IosPdRemoved = false; // " + MARKER + "\n"
    "  const uint64_t IosPdSeq0 = IosPdMissSeq.load(std::memory_order_acquire);\n"
    "#endif\n"
)

REMOVE_ANCHOR = (
    "      LogMan::Msg::EFmt(\"[iOS-xrem] via=protect tracker={} {:#x}-{:#x}\", static_cast<void*>(this),\n"
    "                        ProtInterval.Offset, ProtInterval.End);\n"
)
REMOVE = (
    "#ifdef FEX_IOS_HOST\n"
    "      IosPdRemoved = true; // " + MARKER + "\n"
    "#endif\n"
)

CALL_ANCHOR = (
    "  if (NeedsInvalidate) {\n"
    "    // IntervalsLock cannot be held during invalidation\n"
    "    InvalidateIntervalInternal(AlignedBase, AlignedSize);\n"
    "  }\n"
    "}\n"
    "\n"
    "void InvalidationTracker::HandleProcessExecuteFlagsChange(ULONG Flags) {\n"
)
CALL = (
    "#ifdef FEX_IOS_HOST\n"
    "  // " + MARKER + ": no lock held here, as for the invalidation below.\n"
    "  if (IosPdNote(IosPdRemoved, ProtHasExec(Prot) && ProtIsReadable(Prot), AlignedBase, AlignedSize, IosPdSeq0,\n"
    "                [this](uint64_t InvBase, uint64_t InvSize) { InvalidateIntervalInternal(InvBase, InvSize); })) {\n"
    "    return;\n"
    "  }\n"
    "#endif\n"
)

MISS_ANCHOR = (
    "  const auto XResult = XIntervals.Query(Address);\n"
    "  if (!XResult.Enclosed) {\n"
    "    return {};\n"
    "  }\n"
)
MISS = (
    "  const auto XResult = XIntervals.Query(Address);\n"
    "  if (!XResult.Enclosed) {\n"
    "#ifdef FEX_IOS_HOST\n"
    "    IosPdMiss(Address); // " + MARKER + ": a NoExec block may be cached for this address\n"
    "#endif\n"
    "    return {};\n"
    "  }\n"
)

EDITS = (
    (HELPER_ANCHOR, HELPER_ANCHOR + HELPER),
    (DECL_ANCHOR, DECL + DECL_ANCHOR),
    (REMOVE_ANCHOR, REMOVE_ANCHOR + REMOVE),
    (CALL_ANCHOR, CALL + CALL_ANCHOR),
    (MISS_ANCHOR, MISS),
)


def patch(source):
    if MARKER in source:
        if any(source.count(new) != 1 for _, new in EDITS) or source.count(MARKER) != 5:
            raise ValueError("partial or changed protect-diff overlay")
        return source
    if "protect-diff-log-v1" in source:
        raise ValueError("the log-only protect-diff overlay is already applied")
    for old, _ in EDITS:
        if source.count(old) != 1:
            raise ValueError("InvalidationTracker anchor changed: " + old.splitlines()[0].strip())
    for old, new in EDITS:
        source = source.replace(old, new, 1)
    return source


if __name__ == "__main__":
    path = Path(sys.argv[1]) / REL
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-protect-diff: " + str(error))
    if result != source:
        path.write_text(result)
    print(SUMMARY)
