#!/usr/bin/env python3
"""Count how much of a code range really changes across a W^X protection cycle. Log only.

madeira-bcd personal (2026-11-05). V8 (NW.js, Electron, CEF renderers) writes code with
write-protect-code-memory: it turns a whole code page (0x3d000 bytes in V.0.7) RW, writes,
and turns it RX again. InvalidationTracker::HandleMemoryProtectionNotification drops every
translation in the range on the RW step ([iOS-xrem] via=protect) and once more on the RX step.
On the 0.1.630 device log of V.0.7 (2026-11-05, FINDINGS.md 5e) 96% of the repeated
compiles of V8 code fell inside such ranges, the renderer's 128 MB code buffer filled every
13-26 s and was replaced, and FEX spent ~21% of the renderer's core compiling.

Whether keeping the translations of unchanged pages would help depends on how many 4 KB
pages a cycle really changes, which no log shows. This overlay measures it and changes
nothing else: on the RW step it copies the range (at most 256 KB, eight ranges at a time,
static memory, only when every page of it is committed and readable); on the next RX step
over that range it compares page by page. FEX still drops the whole range both times.

  [protect-diff] protect-diff-log-v1 #n 0x..-0x.. pages=P changed=C (first eight compares)
  [protect-diff] protect-diff-log-v1 totals: ... (every 256th compare)

MADEIRA_FEX_PROTECT_DIFF_LOG=0 turns it off. Applied by tools/build-xtajit64.sh for
whichever pin; the pinned submodule is never updated.
"""
from pathlib import Path
import sys

MARKER = "protect-diff-log-v1 madeira-bcd"
SUMMARY = ("FEX: a code range's W^X protection cycle is compared page by page and logged "
           "([protect-diff]; log only, MADEIRA_FEX_PROTECT_DIFF_LOG=0 turns it off)")

REL = "Source/Windows/Common/InvalidationTracker.cpp"

HELPER_ANCHOR = (
    "static bool ProtIsWritable(ULONG Prot) {\n"
    "  return (Prot & (PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)) != 0;\n"
    "}\n"
)
HELPER = (
    "\n"
    "/* " + MARKER + " (tools/patch-fex-ios-protect-diff-log.py): log only. A code range made\n"
    " * non-executable is copied (at most IosPdMax bytes, IosPdSlots ranges at once, static memory: nothing is\n"
    " * allocated inside a memory notification); the next executable protection over it is compared with the\n"
    " * copy in FEX_PAGE_SIZE pages. The counts say how much of a W^X cycle (V8's write-protect-code-memory)\n"
    " * really changes code. FEX still invalidates the whole range as before. MADEIRA_FEX_PROTECT_DIFF_LOG=0\n"
    " * turns it off. */\n"
    "#ifdef FEX_IOS_HOST\n"
    "namespace {\n"
    "constexpr int IosPdSlots = 8;\n"
    "constexpr uint64_t IosPdMax = 0x40000;\n"
    "struct IosPdSlot {\n"
    "  uint64_t Base;\n"
    "  uint64_t Size;\n"
    "  uint64_t Stamp;\n"
    "};\n"
    "IosPdSlot IosPdSlotTable[IosPdSlots];\n"
    "alignas(64) uint8_t IosPdData[IosPdSlots][IosPdMax];\n"
    "std::atomic_flag IosPdLock = ATOMIC_FLAG_INIT;\n"
    "struct IosPdCounts {\n"
    "  uint64_t Toggles, Snaps, SkipBig, SkipUnreadable, Evicted, Compared, Pages, Changed, NoSnap, CompareUnreadable;\n"
    "};\n"
    "IosPdCounts IosPdN;\n"
    "uint64_t IosPdStampNext;\n"
    "\n"
    "bool IosPdOn() {\n"
    "  static const bool On = [] {\n"
    "    const char* Opt = getenv(\"MADEIRA_FEX_PROTECT_DIFF_LOG\");\n"
    "    return !(Opt && Opt[0] == '0');\n"
    "  }();\n"
    "  return On;\n"
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
    "std::atomic<uint64_t> IosPdBusy {0};\n"
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
    "// Removed: the range left the executable set (the RW step). Exec: an executable, readable protection.\n"
    "void IosPdNote(bool Removed, bool Exec, uint64_t Base, uint64_t Size) {\n"
    "  if ((!Removed && !Exec) || !IosPdOn()) {\n"
    "    return;\n"
    "  }\n"
    "  if (Removed) {\n"
    "    const bool Fits = Size <= IosPdMax;\n"
    "    const bool Readable = Fits && IosPdReadable(Base, Size);\n"
    "    if (!IosPdLockTake()) {\n"
    "      return;\n"
    "    }\n"
    "    IosPdN.Toggles++;\n"
    "    if (!Fits) {\n"
    "      IosPdN.SkipBig++;\n"
    "    } else if (!Readable) {\n"
    "      IosPdN.SkipUnreadable++;\n"
    "    } else {\n"
    "      int Pick = -1;\n"
    "      for (int i = 0; i < IosPdSlots && Pick < 0; i++) {\n"
    "        if (IosPdSlotTable[i].Size && IosPdSlotTable[i].Base == Base) {\n"
    "          Pick = i;\n"
    "        }\n"
    "      }\n"
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
    "        IosPdN.Evicted++;\n"
    "      }\n"
    "      memcpy(IosPdData[Pick], reinterpret_cast<const void*>(Base), Size);\n"
    "      IosPdSlotTable[Pick] = {Base, Size, ++IosPdStampNext};\n"
    "      IosPdN.Snaps++;\n"
    "    }\n"
    "    IosPdLockDrop();\n"
    "    return;\n"
    "  }\n"
    "\n"
    "  const bool Readable = IosPdReadable(Base, Size);\n"
    "  const uint64_t End = Base + Size;\n"
    "  bool Any = false, Say = false;\n"
    "  uint64_t SayBase = 0, SayEnd = 0, SayPages = 0, SayChanged = 0, SayN = 0;\n"
    "  IosPdCounts Copy {};\n"
    "  if (!IosPdLockTake()) {\n"
    "    return;\n"
    "  }\n"
    "  for (int i = 0; i < IosPdSlots; i++) {\n"
    "    IosPdSlot& S = IosPdSlotTable[i];\n"
    "    if (!S.Size || S.Base >= End || S.Base + S.Size <= Base) {\n"
    "      continue;\n"
    "    }\n"
    "    Any = true;\n"
    "    if (!Readable) {\n"
    "      IosPdN.CompareUnreadable++;\n"
    "      S.Size = 0;\n"
    "      continue;\n"
    "    }\n"
    "    const uint64_t From = std::max(S.Base, Base), To = std::min(S.Base + S.Size, End);\n"
    "    uint64_t Pages = 0, Changed = 0;\n"
    "    for (uint64_t P = From; P < To; P += FEXCore::Utils::FEX_PAGE_SIZE) {\n"
    "      const uint64_t Len = std::min<uint64_t>(FEXCore::Utils::FEX_PAGE_SIZE, To - P);\n"
    "      Pages++;\n"
    "      if (memcmp(IosPdData[i] + (P - S.Base), reinterpret_cast<const void*>(P), Len) != 0) {\n"
    "        Changed++;\n"
    "      }\n"
    "    }\n"
    "    S.Size = 0;\n"
    "    IosPdN.Compared++;\n"
    "    IosPdN.Pages += Pages;\n"
    "    IosPdN.Changed += Changed;\n"
    "    if (IosPdN.Compared <= 8 || !(IosPdN.Compared & 255)) {\n"
    "      Say = true;\n"
    "      SayBase = From;\n"
    "      SayEnd = To;\n"
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
    "  if (!Say) {\n"
    "    return;\n"
    "  }\n"
    "  if (SayN <= 8) {\n"
    "    LogMan::Msg::EFmt(\"[protect-diff] protect-diff-log-v1 #{} {:#x}-{:#x} pages={} changed={}\", SayN, SayBase, SayEnd, SayPages,\n"
    "                      SayChanged);\n"
    "  }\n"
    "  if (!(SayN & 255)) {\n"
    "    LogMan::Msg::EFmt(\"[protect-diff] protect-diff-log-v1 totals: toggles={} copied={} compared={} pages={} changed={} ({}%) \"\n"
    "                      \"exec-without-copy={} evicted={} skipped: over-256KB={} unreadable={} unreadable-at-compare={} busy={} \"\n"
    "                      \"(log only: FEX still drops the whole range; MADEIRA_FEX_PROTECT_DIFF_LOG=0 turns this off)\",\n"
    "                      Copy.Toggles, Copy.Snaps, Copy.Compared, Copy.Pages, Copy.Changed,\n"
    "                      Copy.Pages ? Copy.Changed * 100 / Copy.Pages : 0, Copy.NoSnap, Copy.Evicted, Copy.SkipBig,\n"
    "                      Copy.SkipUnreadable, Copy.CompareUnreadable, IosPdBusy.load(std::memory_order_relaxed));\n"
    "  }\n"
    "}\n"
    "} // namespace\n"
    "#endif\n"
)

DECL_ANCHOR = "  const bool NeedsInvalidate = [&]() {\n    std::unique_lock Lock(IntervalsLock);\n"
DECL = "  bool IosPdRemoved = false; // " + MARKER + "\n"

REMOVE_ANCHOR = (
    "      LogMan::Msg::EFmt(\"[iOS-xrem] via=protect tracker={} {:#x}-{:#x}\", static_cast<void*>(this),\n"
    "                        ProtInterval.Offset, ProtInterval.End);\n"
)
REMOVE = "      IosPdRemoved = true; // " + MARKER + "\n"

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
    "  // " + MARKER + ": log only, before the invalidation (which never changes the bytes compared).\n"
    "  IosPdNote(IosPdRemoved, ProtHasExec(Prot) && ProtIsReadable(Prot), AlignedBase, AlignedSize);\n"
    "#endif\n"
)

EDITS = (
    (HELPER_ANCHOR, HELPER_ANCHOR + HELPER),
    (DECL_ANCHOR, DECL + DECL_ANCHOR),
    (REMOVE_ANCHOR, REMOVE_ANCHOR + REMOVE),
    (CALL_ANCHOR, CALL + CALL_ANCHOR),
)


def patch(source):
    if MARKER in source:
        if any(source.count(new) != 1 for _, new in EDITS) or source.count(MARKER) != 4:
            raise ValueError("partial or changed protect-diff overlay")
        return source
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
        sys.exit("patch-fex-ios-protect-diff-log: " + str(error))
    if result != source:
        path.write_text(result)
    print(SUMMARY)
