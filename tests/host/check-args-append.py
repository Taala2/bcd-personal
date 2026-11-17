#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""madeira-bcd personal: env.MADEIRA_ARGS_APPEND (build/bcd_args_append.h, app/Madeira/WineProcessBridge.m).

"<program> <arguments...>" adds the arguments to the command line of the program BCD starts when that
program is <program>. Checks, without a device:
  - the header compiled on the host and run case by case: the named program, another program, the Wine
    desktop (explorer with the game's path among its arguments), an argument already given once, quotes,
    the 64-argument cap, an empty or one-word value;
  - the bridge includes it, calls it after MADEIRA_ARGS is split and before argv is built, logs
    [args-append], and clears the variable for the next game;
  - the personal NW.js rule and the settings catalog carry the key, and CI runs this check.
No device is run.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('  [' + detail + ']') if detail and not cond else ''))
    ok = ok and bool(cond)


HARNESS = r'''
#include <stdio.h>
#include "@HEADER@"
static void run(const char *name, const char *exe, const char *have, const char *spec) {
    static char have_buf[2048], append_buf[1024];
    char *argv[64];
    int argc = 0;
    const char *why = "";
    char *tok[64];
    int n = have ? bcd_args_append_split(have, have_buf, sizeof have_buf, tok, 64) : 0;
    for (int i = 0; i < n; i++) argv[argc++] = tok[i];
    int added = bcd_args_append_apply(exe, argv, &argc, 64, spec, append_buf, sizeof append_buf, &why);
    printf("C %s added=%d why=%s argv=", name, added, why);
    for (int i = 0; i < argc; i++) printf("%s[%s]", i ? "," : "", argv[i]);
    printf("\n");
}
int main(void) {
    const char *nw = "Game.exe --in-process-gpu --disable-features=AudioServiceOutOfProcess --enable-features=NetworkServiceInProcess2";
    run("named", "C:\\Games\\V.0.7\\V.0.7\\Game.exe", NULL, nw);
    run("case", "C:\\Games\\X\\GAME.EXE", NULL, nw);
    run("other", "C:\\windows\\system32\\BCDDock.exe", NULL, nw);
    run("desktop", "C:\\windows\\system32\\explorer.exe", "/desktop=shell,1280x720 C:\\Games\\X\\Game.exe", nw);
    run("desktop-other", "C:\\windows\\system32\\explorer.exe", "/desktop=shell,1280x720 C:\\windows\\system32\\services.exe", nw);
    run("player-args", "C:\\Games\\X\\Game.exe", "--in-process-gpu --foo", nw);
    run("quotes", "C:\\Games\\X\\Game.exe", NULL, "Game.exe \"--a b\" --c");
    run("empty", "C:\\Games\\X\\Game.exe", NULL, "");
    run("unset", "C:\\Games\\X\\Game.exe", NULL, NULL);
    run("one-word", "C:\\Games\\X\\Game.exe", NULL, "Game.exe");
    {
        char big[1024] = "Game.exe";
        for (int i = 0; i < 70; i++) { char t[16]; snprintf(t, sizeof t, " -%d", i); strcat(big, t); }
        run("cap", "C:\\Games\\X\\Game.exe", "--x --y", big);
    }
    return 0;
}
'''

CASES = {
    'named': 'added=3 why=appended argv=[--in-process-gpu],[--disable-features=AudioServiceOutOfProcess],[--enable-features=NetworkServiceInProcess2]',
    'case': 'added=3 why=appended argv=[--in-process-gpu],[--disable-features=AudioServiceOutOfProcess],[--enable-features=NetworkServiceInProcess2]',
    'other': 'added=0 why=another program argv=',
    'desktop': 'added=3 why=appended argv=[/desktop=shell,1280x720],[C:\\Games\\X\\Game.exe],[--in-process-gpu],'
               '[--disable-features=AudioServiceOutOfProcess],[--enable-features=NetworkServiceInProcess2]',
    'desktop-other': 'added=0 why=another program argv=[/desktop=shell,1280x720],[C:\\windows\\system32\\services.exe]',
    'player-args': 'added=2 why=appended argv=[--in-process-gpu],[--foo],[--disable-features=AudioServiceOutOfProcess],'
                   '[--enable-features=NetworkServiceInProcess2]',
    'quotes': 'added=2 why=appended argv=[--a b],[--c]',
    'empty': 'added=0 why=unset argv=',
    'unset': 'added=0 why=unset argv=',
    'one-word': 'added=0 why=no arguments after the program name argv=',
}
WHAT = {
    'named': 'the named program gets the arguments, in order',
    'case': 'the program name is compared without case',
    'other': 'another program (BCD Dock) gets nothing',
    'desktop': 'in the Wine desktop the game explorer starts counts, the arguments go at the end',
    'desktop-other': 'explorer starting something else gets nothing',
    'player-args': "an argument the player already gave is not added twice",
    'quotes': 'double quotes group a token and are dropped, as for MADEIRA_ARGS',
    'empty': 'an empty value does nothing',
    'unset': 'an unset variable does nothing',
    'one-word': 'a program name without arguments does nothing',
}

header = R / 'build/bcd_args_append.h'
cc = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
if not cc:
    print('note: no C compiler; the header was not run')
else:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        t = Path(tmp)
        (t / 'h.c').write_text(HARNESS.replace('@HEADER@', header.as_posix()))
        exe = t / ('h.exe' if os.name == 'nt' else 'h')
        b = subprocess.run([cc, '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Werror', '-o', str(exe), str(t / 'h.c')],
                           capture_output=True, text=True)
        check('the header compiles on the host (-Wall -Werror)', b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
            got = {}
            for line in out.splitlines():
                if line.startswith('C '):
                    name, rest = line[2:].split(' ', 1)
                    got[name] = rest
            for name, want in CASES.items():
                check(WHAT[name], got.get(name) == want, name + ': ' + repr(got.get(name)))
            cap = got.get('cap', '')
            check('at most 64 arguments in all', cap.startswith('added=62 why=appended') and cap.count('[') == 64, cap[:160])

bridge = (R / 'app/Madeira/WineProcessBridge.m').read_text(encoding='utf-8')
inc = bridge.find('#include "../../build/bcd_args_append.h"')
split = bridge.find('const char *madeira_args = getenv("MADEIRA_ARGS");')
call = bridge.find('bcd_args_append_apply(exe_path, extra_argv, &extra_argc, 64, append,')
build_argv = bridge.find('char *argv[72];')
check('the bridge includes the header', inc != -1)
check('it is called after MADEIRA_ARGS is split and before argv is built, with the 64-token cap',
      -1 not in (split, call, build_argv) and split < call < build_argv and 'char *extra_argv[64]' in bridge)
check('the decision is logged as [args-append]', '"[args-append] env.MADEIRA_ARGS_APPEND: %s' in bridge)
check('the next game does not inherit it', 'unsetenv("MADEIRA_ARGS_APPEND");' in bridge)

recs = (R / 'recommended/personal-recommendations.json').read_text(encoding='utf-8')
check('the personal NW.js rule sets it for Game.exe',
      '"env.MADEIRA_ARGS_APPEND = Game.exe --in-process-gpu --disable-features=AudioServiceOutOfProcess '
      '--enable-features=NetworkServiceInProcess2,NetworkServiceInProcess"' in recs)
catalog = (R / 'app/Madeira/ConfigCatalog.generated.swift').read_text(encoding='utf-8')
check('the settings catalog lists the key with both sources',
      'ConfigOption(key: "env.MADEIRA_ARGS_APPEND"' in catalog
      and '"app/Madeira/WineProcessBridge.m", "build/bcd_args_append.h"' in catalog)
ci = (R / '.github/workflows/build-ipa.yml').read_text(encoding='utf-8')
check('CI runs this check', ci.count('run: python3 tests/host/check-args-append.py') == 1)

print('PASS: arguments added to the game program' if ok else 'FAILED: arguments added to the game program')
sys.exit(0 if ok else 1)
