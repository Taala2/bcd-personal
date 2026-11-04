#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""madeira-bcd personal: tools/patch-fex-ios-l1-reclaim.py.

A new thread's lookup cache is zeroed by decommit + commit, not by ZeroScrub's
read, which instantiates its 2 MB on iOS (FINDINGS.md 5b). Checks, without a
FEX build:
  - on the pinned LookupCache.cpp when the submodule is there (CI), else on a
    copy of its anchor: the overlay applies once, a second run changes
    nothing, a moved anchor or a partial overlay is refused;
  - the original scrub stays on the MADEIRA_FEX_L1_SCRUB=1 side byte for
    byte (re-indented); the default side hands the L1 array (and L2, when on)
    back with VirtualDontNeed, whose Windows definition decommits and
    recommits; it sits inside the constructor's FEX_IOS_HOST block;
  - tools/build-xtajit64.sh runs it once for whichever pin; CI runs this check.
No device is run.
"""
from pathlib import Path
import importlib.util
import sys

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('  [' + detail + ']') if detail and not cond else ''))
    ok = ok and bool(cond)


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, R / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


overlay = load('l1_reclaim', 'tools/patch-fex-ios-l1-reclaim.py')

real = R / 'FEX' / overlay.REL
if real.is_file():
    source = real.read_text()
    print('note: the pinned FEX source')
else:
    source = ('LookupCache::LookupCache(FEXCore::Context::ContextImpl* CTX) {\n#ifdef FEX_IOS_HOST\n'
              + overlay.OLD + '#endif\n}\n')
    print('note: no FEX submodule here; the anchor alone')

patched = overlay.patch(source)
check('the overlay applies once', patched != source and patched.count(overlay.NEW) == 1)
check('a second run changes nothing', overlay.patch(patched) == patched)
check('the original scrub is gone from the default path', overlay.OLD not in patched)
for name, broken in (('a moved anchor', source.replace(overlay.OLD, overlay.OLD.replace('ZeroScrub', 'Scrub'))),
                     ('a partial overlay', source.replace(overlay.OLD, '/* ' + overlay.MARKER + ' */\n' + overlay.OLD))):
    try:
        overlay.patch(broken)
        check(name + ' is refused', False)
    except ValueError:
        check(name + ' is refused', True)

new = overlay.NEW
reindented = ''.join('  ' + line + '\n' for line in overlay.OLD.splitlines())
check('MADEIRA_FEX_L1_SCRUB=1 keeps the read-and-scrub byte for byte (re-indented)',
      '  } else\n  {\n' + reindented + '  }\n' in new
      and 'getenv("MADEIRA_FEX_L1_SCRUB")' in new and "return Opt && Opt[0] == '1';" in new)
check('the default path decommits and recommits the L1 array, and L2 only when it exists',
      'FEXCore::Allocator::VirtualDontNeed(reinterpret_cast<void*>(L1Pointer), MAX_L1_SIZE);' in new
      and 'if (L2Enabled) {\n      FEXCore::Allocator::VirtualDontNeed(reinterpret_cast<void*>(PageMemory), L2TableSize);' in new
      and 'ZeroScrub' not in new.split('} else')[0])
check('one line per process says which way it went',
      new.count('LogMan::Msg::EFmt(') == 2 and 'IosL1Said.exchange(true' in new and overlay.MARKER in new)
ios = patched.rfind('#ifdef FEX_IOS_HOST', 0, patched.find(overlay.NEW))
check('it sits inside the FEX_IOS_HOST block that held the scrub',
      ios >= 0 and patched.find('#endif', ios) > patched.find(overlay.NEW) + len(overlay.NEW) - 1)

hooks = R / 'FEX/FEXCore/include/FEXCore/Utils/AllocatorHooks.h'
if hooks.is_file():
    h = hooks.read_text()
    win = h[:h.find('#else\nusing MMAP_Hook')]
    body = win[win.find('inline void VirtualDontNeed('):]
    body = body[:body.find('\n}\n')]
    check('VirtualDontNeed on Windows is MEM_DECOMMIT then MEM_COMMIT with the same protection',
          '::VirtualFree(Ptr, Size, MEM_DECOMMIT);' in body and '::VirtualAlloc(Ptr, Size, MEM_COMMIT, Info.Protect);' in body
          and 'bool Recommit = true' in body)
else:
    print('note: no AllocatorHooks.h here; its VirtualDontNeed is checked in CI')

build = (R / 'tools/build-xtajit64.sh').read_text()
gate = build.find('if grep -q "ec-ret-direct-v1 madeira-bcd"')
pin_end = build.find('\nfi\n', gate)
i_mine = build.find('python3 "$R/tools/patch-fex-ios-l1-reclaim.py" "$R/FEX" || echo "::warning::')
check('build-xtajit64.sh runs it once for whichever pin; a refusal only warns',
      build.count('patch-fex-ios-l1-reclaim.py') == 1 and 0 <= gate < pin_end < i_mine
      and build.find('\nbuild\n', i_mine) > i_mine)
restore = [l for l in build.splitlines() if l.startswith('git -C FEX checkout -- "$CPUF"')]
check('the restore after the build puts LookupCache.cpp back (the CI checks after it read the pinned file)',
      len(restore) == 1 and ' ' + overlay.REL in restore[0] and build.find(restore[0]) > i_mine)
ev = load('fexpatch_eval', 'tests/host/fexpatch_eval.py')
check('it is not part of the older pin\'s replayed chain', not any('l1-reclaim' in line for line in ev.overlay_chain(R)))
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'run: python3 tests/host/check-fex-l1-reclaim.py' in wf)

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
