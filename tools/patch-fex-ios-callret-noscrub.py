#!/usr/bin/env python3
"""Do not read the call-ret stack at thread start while the JIT does not use it.

madeira-bcd personal (2026-11-04). InitializeThread (Source/Windows/Common/
CallRetStack.h) runs ml362's ZeroScrub over the whole 8 MiB call-ret stack of
every new thread: it reads every 16 KB page and writes only pages that hold
stale bytes. Its note assumes "reading an untouched anonymous page maps the
shared zero page instead -- no footprint", which is Linux's behaviour. On the
device the FEX band's dirtiest regions are exactly these stacks, 7-8 MB dirty
of 0x804000 each (V.0.7, build 17df614, [phys-map] ml1074 fex dirtiest matched
to [vname] FEXMem_CallRetStacks), while every scrub reports stale=0x0 and the
JIT does not touch the stack at all with callret-elide-v1 on (the default):
the read is what brings the pages in, about 8 MB per live thread (fex band
553-776 MB in two NW.js games, memory killed near the 6143 MB limit).

With the stack elided nothing reads it, so its content does not matter and
the scrub is skipped (one log line per process says so). MADEIRA_FEX_CALLRET_ELIDE=0
keeps the stack and the scrub, byte for byte. The WOW64 module and every
non-iOS build are unchanged (the declaration and the skip are compiled only
for FEX_IOS_HOST and ARCHITECTURE_arm64ec, where patch-fex-ios-callret-elide.py
defines IosCallRetStackElided).

Applied by tools/build-xtajit64.sh after patch-fex-ios-callret-elide.py; the
pinned submodule is never updated.
"""
from pathlib import Path
import sys

MARKER = "callret-noscrub-v1 madeira-bcd"
SUMMARY = "FEX ARM64EC: the elided call-ret stack is not read at thread start (MADEIRA_FEX_CALLRET_ELIDE=0 keeps the scrub)"

REL = "Source/Windows/Common/CallRetStack.h"

EDITS = (
    (
        "namespace FEX::Windows::CallRetStack {\n",
        "#if defined(FEX_IOS_HOST) && defined(ARCHITECTURE_arm64ec)\n"
        "/* " + MARKER + " (tools/patch-fex-ios-callret-noscrub.py): defined in FEXCore\n"
        " * Arm64Emitter.cpp by patch-fex-ios-callret-elide.py; nonzero when the JIT does not use the stack. */\n"
        "extern \"C\" uint32_t IosCallRetStackElided;\n"
        "#endif\n"
        "namespace FEX::Windows::CallRetStack {\n",
    ),
    (
        "#ifdef FEX_IOS_HOST\n"
        "  {\n"
        "    size_t Stale = FEXCore::Allocator::ZeroScrub(Thread->CallRetStackBase, FEXCore::Core::InternalThreadState::CALLRET_STACK_SIZE);\n"
        "    LogMan::Msg::EFmt(\"[callret] zero-scrub rev=ml362 stale=0x{:x}\", Stale);\n"
        "  }\n"
        "#endif\n",
        "#ifdef FEX_IOS_HOST\n"
        "#if defined(ARCHITECTURE_arm64ec)\n"
        "  /* " + MARKER + ": ZeroScrub reads every page, and on iOS a read brings an untouched page in\n"
        "   * (7-8 MB dirty per stack measured, stale=0x0 every time). Elided, nothing reads the stack. */\n"
        "  if (IosCallRetStackElided) {\n"
        "    static std::atomic<bool> Said {false};\n"
        "    if (!Said.exchange(true)) {\n"
        "      LogMan::Msg::EFmt(\"[callret] zero-scrub skipped: the stack is elided ({})\", \"" + MARKER + "\");\n"
        "    }\n"
        "  } else\n"
        "#endif\n"
        "  {\n"
        "    size_t Stale = FEXCore::Allocator::ZeroScrub(Thread->CallRetStackBase, FEXCore::Core::InternalThreadState::CALLRET_STACK_SIZE);\n"
        "    LogMan::Msg::EFmt(\"[callret] zero-scrub rev=ml362 stale=0x{:x}\", Stale);\n"
        "  }\n"
        "#endif\n",
    ),
)


def patch(text):
    """The patched header; already patched text is returned unchanged. A moved anchor or a
    partial overlay raises ValueError."""
    done = [text.count(new) == 1 for _, new in EDITS]
    if all(done):
        return text
    if any(done):
        raise ValueError(f"{REL}: partial overlay")
    for old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f"{REL}: anchor changed: {old.splitlines()[0][:80]!r}")
        text = text.replace(old, new, 1)
    return text


def main(argv):
    if len(argv) != 2:
        print(f"usage: {argv[0]} <FEX source root>", file=sys.stderr)
        return 2
    path = Path(argv[1]) / REL
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        print(f"{Path(argv[0]).name}: {error}", file=sys.stderr)
        return 1
    if result != source:
        path.write_text(result)
    print(SUMMARY)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
