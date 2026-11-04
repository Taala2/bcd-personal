#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""madeira-bcd personal: tools/patch-fex-ios-renderer-tso.py.

Chromium renderer processes order vector and block-copy accesses (V.0.7 needs
both, Succum's browser process crawls with them; FINDINGS.md 5b). Checks,
without a FEX build:
  - on the pinned Module.cpp when the submodule is there (CI), else on a copy
    of its anchor: the overlay applies once after the Launcher full-SMC
    opt-in, a second run of either changes nothing, a moved anchor or a
    partial overlay is refused;
  - the block sits after CONFIG_IS64BIT_MODE and before CreateNewContext,
    which reads the options; it is compiled only for FEX_IOS_HOST, matches
    --type=renderer, sets both options only when the game file names neither
    one, and only with MADEIRA_RENDERER_STRICT_TSO=1 (off by default);
  - tools/build-xtajit64.sh runs it once, for whichever pin, after the
    Launcher full-SMC opt-in; CI runs this check.
No device is run.
"""
from pathlib import Path
import importlib.util
import re
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


overlay = load('renderer_tso', 'tools/patch-fex-ios-renderer-tso.py')
smc = load('launcher_smc', 'tools/patch-fex-ios-launcher-smc.py')

real = R / 'FEX' / overlay.REL
if real.is_file():
    source = real.read_text()
    print('note: the pinned FEX source')
else:
    source = ('void ProcessInit() {\n  const auto ExecutableName = Name();\n' + overlay.ANCHOR
              + '\n  FEXCore::Profiler::Init("", "");\n'
              '    CTX = FEXCore::Context::Context::CreateNewContext(HostFeatures);\n}\n')
    print('note: no FEX submodule here; the anchor alone')

with_smc = smc.patch(source)
patched = overlay.patch(with_smc)
check('the overlay applies once', patched != with_smc and patched.count(overlay.ADD) == 1)
check('a second run changes nothing', overlay.patch(patched) == patched)
check('the Launcher full-SMC overlay still recognises its block', smc.patch(patched) == patched)
check('it also applies without the Launcher overlay', overlay.patch(source).count(overlay.ADD) == 1)
for name, broken in (('a moved anchor', with_smc.replace(overlay.ANCHOR, '  Set(IS64BIT);\n')),
                     ('a partial overlay', with_smc.replace(overlay.ANCHOR, overlay.ANCHOR + '/* ' + overlay.MARKER + ' */\n'))):
    try:
        overlay.patch(broken)
        check(name + ' is refused', False)
    except ValueError:
        check(name + ' is refused', True)

i_anchor = patched.find(overlay.ANCHOR)
i_block = patched.find(overlay.ADD)
i_ctx = patched.find('CreateNewContext(')
i_name = patched.find('const auto ExecutableName')
check('the block follows CONFIG_IS64BIT_MODE and precedes CreateNewContext (and ExecutableName exists by then)',
      0 <= i_name < i_anchor < i_block < i_ctx)

block = overlay.ADD
check('compiled only for FEX_IOS_HOST', block.startswith('#ifdef FEX_IOS_HOST\n') and block.rstrip().endswith('#endif'))
needle = re.search(r'static const char Needle\[\] = "([^"]*)";', block)
check('it looks for --type=renderer in the command line',
      needle is not None and needle.group(1) == '--type=renderer' and 'ProcessParameters' in block
      and 'CommandLine.Length / sizeof(WCHAR)' in block)
check('both options are set only when the game file names neither one',
      'if (Enable && !VectorSet) {\n      FEXCore::Config::Set(FEXCore::Config::CONFIG_VECTORTSOENABLED, "1");' in block
      and 'if (Enable && !MemcpySet) {\n      FEXCore::Config::Set(FEXCore::Config::CONFIG_MEMCPYSETTSOENABLED, "1");' in block
      and 'getenv("FEX_VECTORTSOENABLED")' in block and 'getenv("FEX_MEMCPYSETTSOENABLED")' in block)
check('off by default: only MADEIRA_RENDERER_STRICT_TSO=1 turns it on (Internship stopped with it, FINDINGS.md 5d)',
      "const bool Enable = Renderer && StrictOpt && StrictOpt[0] == '1';" in block
      and 'getenv("MADEIRA_RENDERER_STRICT_TSO")' in block and 'OFF BY DEFAULT' in overlay.__doc__)
check('one log line per process names the decision', block.count('LogMan::Msg::IFmt(') == 1 and overlay.MARKER in block)
check('nothing else in the options is touched', block.count('FEXCore::Config::Set(') == 2)

build = (R / 'tools/build-xtajit64.sh').read_text()
gate = build.find('if grep -q "ec-ret-direct-v1 madeira-bcd"')
pin_end = build.find('\nfi\n', gate)
i_smc = build.rfind('python3 "$R/tools/patch-fex-ios-launcher-smc.py"', 0, pin_end)
i_mine = build.find('python3 "$R/tools/patch-fex-ios-renderer-tso.py" "$R/FEX" || echo "::warning::')
check('build-xtajit64.sh runs it once for whichever pin, after the Launcher full-SMC opt-in; a refusal only warns',
      build.count('patch-fex-ios-renderer-tso.py') == 1 and 0 <= gate < i_smc < pin_end < i_mine
      and build.find('\nbuild\n', i_mine) > i_mine)
ev = load('fexpatch_eval', 'tests/host/fexpatch_eval.py')
check('it is not part of the older pin\'s replayed chain (the other overlays\' checks are unchanged)',
      not any('renderer-tso' in line for line in ev.overlay_chain(R)))
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'run: python3 tests/host/check-fex-renderer-tso.py' in wf)

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
