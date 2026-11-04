#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""env.MADEIRA_WASM_NO_TIERUP (on unless 0, madeira-bcd personal 2026-11-04): a Chromium renderer gets --no-wasm-tier-up.

V.0.7 (RPG Maker MZ, NW.js 0.48 / V8 8.5; 0.1.630 IPA, iPhone16,1) jumped into freed WebAssembly code: V8 rewrote a slot of
the module's jump table after tier-up through the Mach store emulation, which never invalidates FEX's translation, so the
renderer ran the old slot into code filled with cc ([entry-fail], 22:43 and 23:14, also with --single-threaded-gc). With
--js-flags=--no-wasm-tier-up as a launch argument it played (23:23, 77 s, 0 entry-fail).

Checks:
  - renderer_wasm_cmdline and sc_switch_end, cut from build/ntdll-unix/process_ios.c and compiled on the host, on command
    lines read back the way CommandLineToArgvW splits them: only a --type=renderer process changes (not the browser, the GPU
    process, a utility or crash handler, nor --type=renderer-like words); with no --js-flags= one is appended; the flag goes
    first into the LAST --js-flags= (bare, value-quoted, whole-argument-quoted and empty values) and the earlier one and every
    other argument stay as they were; a command line that already names wasm-tier-up is left alone; a short buffer is refused;
  - NtCreateUserProcess calls it once, after the EA block (the last other command-line change) and before the image is read,
    and only MADEIRA_WASM_NO_TIERUP=0 skips it; the [wasm-tierup] line is bounded;
  - the session end resets the switch, the settings catalog documents it, CI runs this check.
No Wine or device is run.
"""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


src = (root / "build/ntdll-unix/process_ios.c").read_text()


def cut(sig):
    start = src.index(sig)
    return src[start:src.index("\n}\n", start) + 3]


helpers = cut("static int sc_switch_end( const WCHAR *cl, int cl_len, const char *sw )") + "\n" + \
          cut("static int renderer_wasm_cmdline( const WCHAR *cl, int cl_len, WCHAR *out, int cap, int *merged )")

HARNESS = r'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned short WCHAR;
''' + helpers + r'''
int main(int argc, char **argv)
{
    char line[8192];
    while (fgets(line, sizeof(line), stdin))
    {
        int cap, len, k, o, merged = 0;
        WCHAR cl[4096], out[4096];
        char *sp = strchr(line, ' ');
        if (!sp) continue;
        *sp = 0; cap = atoi(line);
        len = (int)strlen(sp + 1);
        while (len && (sp[len] == '\n' || sp[len] == '\r')) len--;
        for (k = 0; k < len; k++) cl[k] = (WCHAR)(unsigned char)sp[1 + k];
        o = renderer_wasm_cmdline(cl, len, out, cap ? cap : len + 48, &merged);
        if (o < 0) { printf("-1\n"); continue; }
        printf("%d ", merged);
        for (k = 0; k < o; k++) putchar((char)out[k]);
        putchar('\n');
    }
    return 0;
}
'''


def argv_split(s):
    """CommandLineToArgvW for these lines (no backslash before a quote in them)."""
    args, cur, inq, have, i = [], [], False, False, 0
    while i < len(s):
        c = s[i]
        if c == '"':
            if inq and i + 1 < len(s) and s[i + 1] == '"':
                cur.append('"'); i += 2; continue
            inq = not inq; have = True
        elif c in " \t" and not inq:
            if have or cur:
                args.append("".join(cur)); cur, have = [], False
        else:
            cur.append(c); have = True
        i += 1
    if have or cur:
        args.append("".join(cur))
    return args


cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("note: no C compiler; the helper was not run")
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "h.c").write_text(HARNESS)
        exe = t / ("h.exe" if os.name == "nt" else "h")
        r = subprocess.run([cc, "-O1", "-o", str(exe), str(t / "h.c")], capture_output=True, text=True)
        check("the helper compiles on the host", r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            G = '"C:\\Games\\V.0.7\\Game.exe"'
            cases = [
                # (name, cap, input, expect: None for unchanged, else (merged, last --js-flags= value, other args))
                ("the browser (no --type=) is left alone", 0, G + " --force-color-profile=srgb", None),
                ("the GPU process is left alone", 0, G + " --type=gpu-process --field-trial-handle=1,2", None),
                ("a utility is left alone", 0, G + " --type=utility --utility-sub-type=network.mojom.NetworkService", None),
                ("a crash handler is left alone", 0, G + " --type=crashpad-handler --monitor-self", None),
                ("--type=renderer-like words are not a renderer", 0, G + " --type=rendererx --x", None),
                ("a renderer without --js-flags= gets one", 0, G + " --type=renderer --no-sandbox --lang=en-US",
                 (0, "--no-wasm-tier-up", ["--type=renderer", "--no-sandbox", "--lang=en-US"])),
                ("a bare value: the flag goes first in the same argument", 0,
                 G + " --type=renderer --js-flags=--expose-gc --no-zygote",
                 (1, "--no-wasm-tier-up --expose-gc", ["--type=renderer", "--no-zygote"])),
                ("a value-quoted list", 0, G + ' --type=renderer --js-flags="--expose-gc --max-old-space-size=4096" --x',
                 (1, "--no-wasm-tier-up --expose-gc --max-old-space-size=4096", ["--type=renderer", "--x"])),
                ("a whole-argument-quoted list", 0, G + ' --type=renderer "--js-flags=--expose-gc --no-opt" --x',
                 (1, "--no-wasm-tier-up --expose-gc --no-opt", ["--type=renderer", "--x"])),
                ("an empty value at the end", 0, G + " --type=renderer --js-flags=",
                 (1, "--no-wasm-tier-up", ["--type=renderer"])),
                ("an empty value before another argument", 0, G + " --type=renderer --js-flags= --x",
                 (1, "--no-wasm-tier-up", ["--type=renderer", "--x"])),
                ("only the LAST --js-flags= changes (Chromium keeps the last)", 0,
                 G + " --js-flags=--a --type=renderer --js-flags=--b",
                 (1, "--no-wasm-tier-up --b", ["--js-flags=--a", "--type=renderer"])),
                ("a renderer that names --no-wasm-tier-up is left alone", 0,
                 G + " --type=renderer --js-flags=--no-wasm-tier-up", None),
                ("a renderer that asks for --wasm-tier-up is left alone", 0,
                 G + ' --type=renderer --js-flags="--expose-gc --wasm-tier-up"', None),
                ("a short buffer is refused", 10, G + " --type=renderer --x", None),
            ]
            feed = "".join("%d %s\n" % (cap, line) for _, cap, line, _ in cases)
            r = subprocess.run([str(exe)], input=feed, capture_output=True, text=True)
            outs = r.stdout.splitlines()
            check("the harness answered every case", len(outs) == len(cases), r.stdout + r.stderr)
            for (name, cap, line, want), got in zip(cases, outs):
                if want is None:
                    check(name, got == "-1", got)
                    continue
                if got == "-1":
                    check(name, False, "unchanged")
                    continue
                merged, newline = int(got.split(" ", 1)[0]), got.split(" ", 1)[1]
                args = argv_split(newline)
                flags = [a for a in args if a.startswith("--js-flags=")]
                rest = [a for a in args[1:] if not flags or a != flags[-1]]
                check(name, merged == want[0] and args[0] == "C:\\Games\\V.0.7\\Game.exe"
                      and flags and flags[-1] == "--js-flags=" + want[1] and rest == want[2],
                      {"line": newline, "argv": args})

# ---- the call
call_at = src.find("/* madeira-bcd personal: [wasm-tierup] -- see renderer_wasm_cmdline.")
check("NtCreateUserProcess calls it once", call_at > 0 and src.count("renderer_wasm_cmdline( params->CommandLine.Buffer") == 1)
check("after the EA block (the last other command-line change) and before the image is read",
      call_at > 0 and src.index("NTSTATUS WINAPI NtCreateUserProcess(") < call_at
      and src.index('"[ea-cef] EADesktop.exe browser: command line NOT rewritten "') < call_at
      < src.index("unixdir = get_unix_curdir( params );", call_at))
call = src[call_at:src.index("unixdir = get_unix_curdir( params );", call_at)] if call_at > 0 else ""
check("only MADEIRA_WASM_NO_TIERUP=0 skips it; the switch is read in one place",
      src.count('getenv( "MADEIRA_WASM_NO_TIERUP" )') == 1 and "(keep && keep[0] == '0') ? NULL" in call)
check("the new command line is installed with its length, the log is bounded",
      "params->CommandLine.Length = o * sizeof(WCHAR);" in call and "wasm_tierup_n++ < 16" in call and "[wasm-tierup]" in call)

# ---- reset, catalog, CI
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
end_start = bridge.index("static void wine_session_end(void)\n{")
check("the session end resets the switch", 'unsetenv("MADEIRA_WASM_NO_TIERUP");' in bridge[end_start:])
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
gen = (root / "build/tools/gen-config-catalog.py").read_text()
check("the settings catalog documents it (generator overlay and generated line)",
      '"env.MADEIRA_WASM_NO_TIERUP": {"category": "Wine core (ntdll)"' in gen
      and 'ConfigOption(key: "env.MADEIRA_WASM_NO_TIERUP", title: "Web-engine WebAssembly without tier-up", kind: .bool, '
          'defaultValue: "1", category: "Wine core (ntdll)"' in cat)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-renderer-wasm-tierup.py" in wf)
print("PASS: Chromium renderers without WebAssembly tier-up" if ok else "FAILED: Chromium renderers without WebAssembly tier-up")
sys.exit(0 if ok else 1)
