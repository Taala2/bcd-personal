#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""madeira-bcd personal: tools/patch-fex-ios-callret-noscrub.py.

The elided call-ret stack is not read at thread start: ZeroScrub's read brought about 8 MB per live
thread into the iOS footprint (FINDINGS.md 5b, build 17df614). Checks, without a FEX build:
  - on the pinned FEX source when the submodule is there (CI), else on a copy of its two anchors:
    the overlay applies once, a second run changes nothing, a moved anchor or a partial overlay is
    refused;
  - the patched text keeps the original scrub on the else side, compiles the skip only for
    FEX_IOS_HOST and ARCHITECTURE_arm64ec, and reads IosCallRetStackElided, the flag
    patch-fex-ios-callret-elide.py defines;
  - tools/build-xtajit64.sh runs it once, right after the elide overlay; CI runs this check.
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


spec = importlib.util.spec_from_file_location('noscrub', R / 'tools/patch-fex-ios-callret-noscrub.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)

real = R / 'FEX' / overlay.REL
if real.is_file():
    source = real.read_text()
    print('note: the pinned FEX source')
else:
    source = ('#include <FEXCore/Utils/Allocator.h>\n\nnamespace FEX::Windows::CallRetStack {\n'
              'bool InitializeThread(FEXCore::Core::InternalThreadState* Thread) {\n'
              + overlay.EDITS[1][0] + '  return true;\n}\n}\n')
    print('note: no FEX submodule here; the anchors alone')

patched = overlay.patch(source)
check('the overlay applies', patched != source and all(patched.count(new) == 1 for _, new in overlay.EDITS))
check('a second run changes nothing', overlay.patch(patched) == patched)
for name, broken in (('a moved anchor', source.replace('namespace FEX::Windows::CallRetStack {\n', 'namespace X {\n')),
                     ('a partial overlay', source.replace(overlay.EDITS[0][0], overlay.EDITS[0][1]))):
    try:
        overlay.patch(broken)
        check(name + ' is refused', False)
    except ValueError:
        check(name + ' is refused', True)

new_block = overlay.EDITS[1][1]
check('the original scrub stays on the else side, byte for byte',
      overlay.EDITS[1][0].split('  {\n', 1)[1] in new_block and '  } else\n#endif\n  {\n' in new_block)
check('the skip is compiled only for FEX_IOS_HOST and ARCHITECTURE_arm64ec',
      new_block.startswith('#ifdef FEX_IOS_HOST\n#if defined(ARCHITECTURE_arm64ec)\n')
      and '#if defined(FEX_IOS_HOST) && defined(ARCHITECTURE_arm64ec)\n' in overlay.EDITS[0][1])
elide = (R / 'tools/patch-fex-ios-callret-elide.py').read_text()
check('it reads the flag the elide overlay defines',
      'if (IosCallRetStackElided) {' in new_block and 'extern "C" uint32_t IosCallRetStackElided = 0;' in elide)

build = (R / 'tools/build-xtajit64.sh').read_text()
gate = build.find('if grep -q "ec-ret-direct-v1 madeira-bcd"')
pin_end = build.find('\nfi\n', gate)
i_elide = build.find('python3 "$R/tools/patch-fex-ios-callret-elide.py" "$R/FEX"')
i_mine = build.find('python3 "$R/tools/patch-fex-ios-callret-noscrub.py" "$R/FEX" || echo "::warning::')
check('build-xtajit64.sh runs it once for whichever pin (after the pin branches, the elide overlay included), '
      'a refusal only warns', build.count('patch-fex-ios-callret-noscrub.py') == 1
      and 0 <= gate < i_elide < pin_end < i_mine)
ev_spec = importlib.util.spec_from_file_location('fexpatch_eval', R / 'tests/host/fexpatch_eval.py')
ev = importlib.util.module_from_spec(ev_spec)
ev_spec.loader.exec_module(ev)
check('it is not part of the older pin\'s replayed chain (the other overlays\' checks are unchanged)',
      not any('callret-noscrub' in line for line in ev.overlay_chain(R)))
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'run: python3 tests/host/check-fex-callret-noscrub.py' in wf)

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
