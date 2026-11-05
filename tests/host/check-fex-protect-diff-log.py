#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""madeira-bcd personal: tools/patch-fex-ios-protect-diff-log.py.

Log only: a code range's W^X protection cycle (V8's write-protect-code-memory) is copied
on the RW step and compared page by page on the RX step ([protect-diff]); FEX still drops
the whole range (FINDINGS.md 5e). Checks, without a FEX build:
  - on the pinned InvalidationTracker.cpp when the submodule is there, else on the anchors
    alone: the overlay applies once, a second run changes nothing, a moved anchor or a
    partial overlay is refused;
  - log only: taking the four inserted pieces out gives the original file byte for byte, so
    every invalidation and interval edit is untouched;
  - the RW mark is set only in the via=protect branch, after the >=1 GB early return; the
    call sits before the invalidation, inside FEX_IOS_HOST, and the helper too;
  - the helper, cut out of the patched file and compiled on the host with stubs for the
    Windows calls, counts what it should: a 0x3d000 range with two changed pages, an
    executable protection with no copy, a range over 256 KB, a range with an uncommitted
    page, a ninth range evicting the oldest, the totals line at the 256th compare, the
    first eight compare lines, and nothing at all with MADEIRA_FEX_PROTECT_DIFF_LOG=0;
  - tools/build-xtajit64.sh runs it once with the warning fallback; CI runs this check.
No device is run.
"""
from pathlib import Path
import importlib.util
import os
import re
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


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, R / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


overlay = load('protect_diff_log', 'tools/patch-fex-ios-protect-diff-log.py')

real = R / 'FEX' / overlay.REL
if real.is_file():
    source = real.read_text()
    print('note: the pinned FEX source')
else:
    source = ('static bool ProtIsReadable(ULONG Prot) {\n  return true;\n}\n\n' + overlay.HELPER_ANCHOR
              + '\nvoid InvalidationTracker::HandleMemoryProtectionNotification(uint64_t Address, uint64_t Size, ULONG Prot) {\n'
              + overlay.DECL_ANCHOR + '    if (x) {\n      if (AlignedSize >= (1ull << 30)) {\n        return false;\n      }\n'
              + overlay.REMOVE_ANCHOR + '      XIntervals.Remove(ProtInterval);\n    }\n  }();\n\n' + overlay.CALL_ANCHOR)
    print('note: no FEX submodule here; the anchors alone')

patched = overlay.patch(source)
check('the overlay applies once', patched != source and all(patched.count(new) == 1 for _, new in overlay.EDITS))
check('a second run changes nothing', overlay.patch(patched) == patched)
for name, broken in (('a moved anchor', source.replace(overlay.REMOVE_ANCHOR, overlay.REMOVE_ANCHOR.replace('via=protect', 'via=prot'))),
                     ('a partial overlay', patched.replace(overlay.REMOVE, ''))):
    try:
        overlay.patch(broken)
        check(name + ' is refused', False)
    except ValueError:
        check(name + ' is refused', True)

undone = patched
for old, new in overlay.EDITS:
    undone = undone.replace(new, old, 1)
check('log only: without the four inserted pieces the file is the original byte for byte', undone == source)

fn = patched[patched.find('void InvalidationTracker::HandleMemoryProtectionNotification('):]
fn = fn[:fn.find('\nvoid InvalidationTracker::HandleProcessExecuteFlagsChange(')]
mark = fn.find(overlay.REMOVE)
check('the RW mark is set in the via=protect branch, after the >=1 GB early return',
      mark > fn.find('[iOS-xrem] via=protect') > fn.find('if (AlignedSize >= (1ull << 30))') > 0
      and fn.find('XIntervals.Remove(ProtInterval);', mark) > mark and fn.count('IosPdRemoved = true;') == 1)
call = fn.find('IosPdNote(IosPdRemoved, ProtHasExec(Prot) && ProtIsReadable(Prot), AlignedBase, AlignedSize);')
check('the call sits after the lock is released and before the invalidation, inside FEX_IOS_HOST',
      fn.find('}();') < call < fn.find('  if (NeedsInvalidate) {\n    // IntervalsLock cannot be held during invalidation')
      and fn.rfind('#ifdef FEX_IOS_HOST', 0, call) > fn.find('}();') and fn.find('#endif', call) < fn.find('if (NeedsInvalidate)'))
helper_at = patched.find(overlay.HELPER)
check('the helper is inside FEX_IOS_HOST, after ProtIsReadable',
      overlay.HELPER.count('#ifdef FEX_IOS_HOST') == 1 and overlay.HELPER.rstrip().endswith('#endif')
      and patched.find('static bool ProtIsReadable(') < helper_at)
check('nothing is allocated in the helper and it never waits',
      not re.search(r'\b(malloc|new |make_unique|make_shared|std::vector|fextl::)', overlay.HELPER)
      and 'while (IosPdLock.test_and_set' not in overlay.HELPER and 'if (IosPdLock.test_and_set' in overlay.HELPER)

# The helper, compiled on the host with stubs.
H = overlay.HELPER
body = H[H.find('namespace {'):H.find('} // namespace') + len('} // namespace')]
readable = re.search(r'static bool ProtIsReadable\(ULONG Prot\) \{.*?\n\}\n', patched, re.S)
check('ProtIsReadable is found for the harness', readable is not None)
HARNESS = r'''
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
typedef unsigned long ULONG;
typedef long NTSTATUS;
typedef size_t SIZE_T;
#define PAGE_NOACCESS 0x01
#define PAGE_READONLY 0x02
#define PAGE_READWRITE 0x04
#define PAGE_WRITECOPY 0x08
#define PAGE_EXECUTE 0x10
#define PAGE_EXECUTE_READ 0x20
#define PAGE_EXECUTE_READWRITE 0x40
#define PAGE_EXECUTE_WRITECOPY 0x80
#define PAGE_GUARD 0x100
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
enum { MemoryBasicInformation };
struct MEMORY_BASIC_INFORMATION { void* BaseAddress; void* AllocationBase; ULONG AllocationProtect; SIZE_T RegionSize; ULONG State; ULONG Protect; ULONG Type; };
static void* NtCurrentProcess() { return (void*)-1; }
/* Fake map: [Arena, Arena + ArenaSize) committed PAGE_READWRITE in 4 KB regions, except one hole. */
static uint8_t* Arena;
static const uint64_t ArenaSize = 0x400000;
static uint64_t Hole = 0;
static NTSTATUS NtQueryVirtualMemory(void*, void* A, int, MEMORY_BASIC_INFORMATION* I, SIZE_T, SIZE_T*) {
  uint64_t a = (uint64_t)A & ~0xfffull, b = (uint64_t)Arena;
  if (a < b || a >= b + ArenaSize) return 1;
  I->BaseAddress = (void*)a; I->AllocationBase = Arena; I->RegionSize = 0x1000;
  I->State = (Hole && a == Hole) ? MEM_RESERVE : MEM_COMMIT; I->Protect = PAGE_READWRITE;
  return 0;
}
namespace FEXCore::Utils { constexpr size_t FEX_PAGE_SIZE = 4096; }
namespace LogMan::Msg {
static void Arg(std::string& s, uint64_t v) { char b[32]; snprintf(b, sizeof b, "%llu", (unsigned long long)v); s += b; }
template <typename... T> void EFmt(const char* f, T... a) {
  uint64_t v[] = {(uint64_t)a..., 0}; std::string s; size_t k = 0;
  for (const char* p = f; *p; p++) {
    if (*p == '{') { const char* q = strchr(p, '}'); if (q) { Arg(s, v[k++]); p = q; continue; } }
    s += *p;
  }
  printf("LOG %s\n", s.c_str());
}
}
@READABLE@
@BODY@
int main(int argc, char** argv) {
  static uint8_t Store[0x400000 + 0x10000];
  Arena = (uint8_t*)(((uintptr_t)Store + 0xffff) & ~(uintptr_t)0xffff);
  for (uint64_t i = 0; i < ArenaSize; i++) Arena[i] = (uint8_t)(i * 7);
  const uint64_t B = (uint64_t)Arena;
  const std::string mode = argc > 1 ? argv[1] : "";
  if (mode == "basic") {
    IosPdNote(true, false, B, 0x3d000);              /* RW step: copied */
    Arena[0x2000] ^= 1; Arena[0x3c000 + 5] ^= 1;     /* two pages written */
    IosPdNote(false, true, B, 0x3d000);              /* RX step: compared */
    IosPdNote(false, true, B + 0x100000, 0x4000);    /* executable, never copied */
    IosPdNote(true, false, B, 0x41000);              /* over 256 KB */
    Hole = B + 0x203000;
    IosPdNote(true, false, B + 0x200000, 0x8000);    /* an uncommitted page */
    Hole = 0;
    IosPdNote(false, false, B, 0x1000);              /* neither: ignored */
    for (int i = 0; i < 9; i++) IosPdNote(true, false, B + 0x80000 + i * 0x10000, 0x4000);  /* ninth evicts the oldest */
  } else if (mode == "many") {
    for (int i = 0; i < 300; i++) { IosPdNote(true, false, B, 0x3d000); IosPdNote(false, true, B, 0x3d000); }
  } else if (mode == "off") {
    IosPdNote(true, false, B, 0x3d000); IosPdNote(false, true, B, 0x3d000);
  }
  const IosPdCounts& N = IosPdN;
  printf("N toggles=%llu snaps=%llu big=%llu unreadable=%llu evicted=%llu compared=%llu pages=%llu changed=%llu nosnap=%llu cmpunread=%llu\n",
         (unsigned long long)N.Toggles, (unsigned long long)N.Snaps, (unsigned long long)N.SkipBig, (unsigned long long)N.SkipUnreadable,
         (unsigned long long)N.Evicted, (unsigned long long)N.Compared, (unsigned long long)N.Pages, (unsigned long long)N.Changed,
         (unsigned long long)N.NoSnap, (unsigned long long)N.CompareUnreadable);
  return 0;
}
'''
cxx = os.environ.get('CXX') or shutil.which('c++') or shutil.which('clang++') or shutil.which('g++')
if not cxx or readable is None:
    print('note: no C++ compiler; the helper was not run')
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / 'h.cpp').write_text(HARNESS.replace('@READABLE@', readable.group(0)).replace('@BODY@', body))
        exe = t / ('h.exe' if os.name == 'nt' else 'h')
        r = subprocess.run([cxx, '-std=c++17', '-O1', '-o', str(exe), str(t / 'h.cpp')], capture_output=True, text=True)
        check('the helper compiles on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            def run(mode, env_off=False):
                env = dict(os.environ)
                env.pop('MADEIRA_FEX_PROTECT_DIFF_LOG', None)
                if env_off:
                    env['MADEIRA_FEX_PROTECT_DIFF_LOG'] = '0'
                out = subprocess.run([str(exe), mode], capture_output=True, text=True, env=env).stdout
                counts = dict(kv.split('=') for kv in out.splitlines()[-1].split()[1:])
                return out, {k: int(v) for k, v in counts.items()}

            out, n = run('basic')
            check('a 0x3d000 range with two written pages: 61 pages compared, 2 changed',
                  n['compared'] == 1 and n['pages'] == 61 and n['changed'] == 2, str(n))
            check('the first compare is logged with its range', 'LOG [protect-diff] protect-diff-log-v1 #1 ' in out
                  and 'pages=61 changed=2' in out, out[:400])
            check('an executable protection with no copy is counted', n['nosnap'] == 1, str(n))
            check('a range over 256 KB is skipped', n['big'] == 1, str(n))
            check('a range with an uncommitted page is not copied', n['unreadable'] == 1, str(n))
            check('a protection that is neither is ignored, a ninth range evicts the oldest',
                  n['toggles'] == 12 and n['snaps'] == 10 and n['evicted'] == 1, str(n))
            out, n = run('many')
            check('300 unchanged cycles: 300 compares, nothing changed',
                  n['compared'] == 300 and n['changed'] == 0 and n['pages'] == 300 * 61, str(n))
            lines = [l for l in out.splitlines() if l.startswith('LOG ')]
            check('eight compare lines, then one totals line at the 256th',
                  len(lines) == 9 and sum('totals:' in l for l in lines) == 1
                  and 'toggles=256 copied=256 compared=256 pages=15616 changed=0 (0%)' in lines[-1], '\n'.join(lines[-2:]))
            out, n = run('off', env_off=True)
            check('MADEIRA_FEX_PROTECT_DIFF_LOG=0: nothing is copied, compared or logged',
                  n['toggles'] == 0 and n['compared'] == 0 and 'LOG ' not in out, out)

script = (R / 'tools/build-xtajit64.sh').read_text()
line = 'python3 "$R/tools/patch-fex-ios-protect-diff-log.py" "$R/FEX" || echo "::warning::'
check('tools/build-xtajit64.sh runs it once with a warning fallback', script.count(line) == 1)
check('InvalidationTracker.cpp is restored after the build',
      re.search(r'git -C FEX checkout -- [^\n]*Source/Windows/Common/InvalidationTracker\.cpp', script) is not None)
ci = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', ci.count('run: python3 tests/host/check-fex-protect-diff-log.py') == 1)

print('PASS: the W^X protection-cycle log' if ok else 'FAILED: the W^X protection-cycle log')
sys.exit(0 if ok else 1)
