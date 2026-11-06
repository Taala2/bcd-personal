#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""madeira-bcd personal: tools/patch-fex-ios-protect-diff.py.

A code range's W^X protection cycle (V8's write-protect-code-memory) keeps the translations of
the pages whose bytes did not change: the RW step copies the range and defers its drop, the RX
step drops the changed pages only; anything it cannot vouch for is dropped whole (FINDINGS.md
5e: 3% of V8's pages changed per cycle in V.0.7). Checks, without a FEX build:
  - on the pinned InvalidationTracker.cpp when the submodule is there, else on the anchors
    alone: the overlay applies once, a second run changes nothing, a moved anchor, a partial
    overlay or the old log-only overlay is refused;
  - taking the five pieces out gives the original file byte for byte;
  - placement: the RW mark only in the via=protect branch after the >=1 GB early return, the
    miss sequence read before the intervals are touched, the call with no lock held and before
    the whole-range drop, the miss hook on the not-executable return of the query, all inside
    FEX_IOS_HOST; nothing allocated in the helper, its lock never waits, drops only after it is
    released;
  - the helper, cut out of the patched file and compiled on the host with stubs, against a
    fake memory map: what it drops, what it keeps and what it leaves to the caller, case by
    case (CASES below, in mode 1), and the modes of env.MADEIRA_FEX_PROTECT_DIFF: unset, 1 or
    anything else on (the default since v4), log measure only, rxwhole keeps on RW and leaves RX
    to the caller, 0 does nothing; bytes changed outside the W^X window (between an RX step and
    the next RW step), counted in every mode and dropped at the RW step in mode 1;
  - tools/build-xtajit64.sh runs it once with a warning fallback and restores the file; the
    log-only overlay is gone from it; CI runs this check.
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


overlay = load('protect_diff', 'tools/patch-fex-ios-protect-diff.py')

real = R / 'FEX' / overlay.REL
if real.is_file():
    source = real.read_text()
    print('note: the pinned FEX source')
else:
    source = ('static bool ProtIsReadable(ULONG Prot) {\n  return true;\n}\n\n' + overlay.HELPER_ANCHOR
              + '\nvoid InvalidationTracker::HandleMemoryProtectionNotification(uint64_t Address, uint64_t Size, ULONG Prot) {\n'
              + overlay.DECL_ANCHOR + '    if (x) {\n      if (AlignedSize >= (1ull << 30)) {\n        return false;\n      }\n'
              + overlay.REMOVE_ANCHOR + '      XIntervals.Remove(ProtInterval);\n    }\n  }();\n\n' + overlay.CALL_ANCHOR
              + '}\n\nFEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRange(uint64_t Address) {\n'
              + overlay.MISS_ANCHOR + '  return {};\n}\n')
    print('note: no FEX submodule here; the anchors alone')

patched = overlay.patch(source)
check('the overlay applies once', patched != source and all(patched.count(new) == 1 for _, new in overlay.EDITS))
check('a second run changes nothing', overlay.patch(patched) == patched)
for name, broken in (('a moved anchor', source.replace(overlay.REMOVE_ANCHOR, overlay.REMOVE_ANCHOR.replace('via=protect', 'via=prot'))),
                     ('a partial overlay', patched.replace(overlay.REMOVE, '')),
                     ('the log-only overlay', source + '\n// protect-diff-log-v1 madeira-bcd\n')):
    try:
        overlay.patch(broken)
        check(name + ' is refused', False)
    except ValueError:
        check(name + ' is refused', True)

undone = patched
for old, new in overlay.EDITS:
    undone = undone.replace(new, old, 1)
check('without the five pieces the file is the original byte for byte', undone == source)

fn = patched[patched.find('void InvalidationTracker::HandleMemoryProtectionNotification('):]
fn = fn[:fn.find('\nvoid InvalidationTracker::HandleProcessExecuteFlagsChange(')]
mark = fn.find('IosPdRemoved = true;')
check('the RW mark is set in the via=protect branch, after the >=1 GB early return',
      mark > fn.find('[iOS-xrem] via=protect') > fn.find('if (AlignedSize >= (1ull << 30))') > 0
      and fn.find('XIntervals.Remove(ProtInterval);', mark) > mark and fn.count('IosPdRemoved = true;') == 1)
check('the miss sequence is read before the intervals are touched',
      0 < fn.find('const uint64_t IosPdSeq0 = IosPdMissSeq.load(') < fn.find('std::unique_lock Lock(IntervalsLock);'))
call = fn.find('if (IosPdNote(IosPdRemoved, ProtHasExec(Prot) && ProtIsReadable(Prot), AlignedBase, AlignedSize, IosPdSeq0,')
whole = fn.find('  if (NeedsInvalidate) {\n    // IntervalsLock cannot be held during invalidation\n    InvalidateIntervalInternal(AlignedBase, AlignedSize);')
check('the call runs after the lock is released, before the whole-range drop, and returns when handled',
      fn.find('}();') < call < whole and fn.find('    return;\n  }\n#endif\n', call) < whole
      and fn.rfind('#ifdef FEX_IOS_HOST', 0, call) > fn.find('}();')
      and 'InvalidateIntervalInternal(InvBase, InvSize);' in fn[call:whole])
check('the miss hook is on the not-executable return of the executable-range query, under FEX_IOS_HOST',
      overlay.MISS in patched and '#ifdef FEX_IOS_HOST\n    IosPdMiss(Address);' in overlay.MISS)
helper_at = patched.find(overlay.HELPER)
check('the helper is inside FEX_IOS_HOST, after ProtIsReadable',
      overlay.HELPER.count('#ifdef FEX_IOS_HOST') == 1 and overlay.HELPER.rstrip().endswith('#endif')
      and patched.find('static bool ProtIsReadable(') < helper_at)
check('nothing is allocated in the helper and its lock never waits',
      not re.search(r'\b(malloc|new |make_unique|make_shared|std::vector|std::function|fextl::)', overlay.HELPER)
      and 'while (IosPdLock.test_and_set' not in overlay.HELPER and 'if (IosPdLock.test_and_set' in overlay.HELPER)
check('the drop callback is only called after the lock is dropped',
      all(overlay.HELPER.rfind('IosPdLockDrop();', 0, m.start()) > overlay.HELPER.rfind('IosPdLockTake()', 0, m.start())
          for m in re.finditer(r'Inval\(Drop\[i\]', overlay.HELPER))
      and len(re.findall(r'Inval\(Drop\[i\]', overlay.HELPER)) == 2)

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
static uint8_t* Arena;
static const uint64_t ArenaSize = 0x1000000;
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
static void Arg(std::string& s, unsigned long long v) { char b[32]; snprintf(b, sizeof b, "%llu", v); s += b; }
static void Arg(std::string& s, const char* v) { s += v; }
template <typename... T> void EFmt(const char* f, T... a) {
  std::string parts[sizeof...(T) + 1]; size_t k = 0;
  ((Arg(parts[k++], a)), ...);
  std::string s; k = 0;
  for (const char* p = f; *p; p++) {
    if (*p == '{') { const char* q = strchr(p, '}'); if (q) { s += parts[k++]; p = q; continue; } }
    s += *p;
  }
  printf("LOG %s\n", s.c_str());
}
}
@READABLE@
@BODY@
static uint64_t B;
static std::string Drops;
static int Calls;
static bool Note(bool Removed, bool Exec, uint64_t Off, uint64_t Size) {
  const uint64_t Seq0 = IosPdMissSeq.load();
  Drops.clear();
  bool H = IosPdNote(Removed, Exec, B + Off, Size, Seq0, [](uint64_t A, uint64_t S) {
    char b[64]; snprintf(b, sizeof b, "%s%llx+%llx", Drops.empty() ? "" : ",", (unsigned long long)(A - B), (unsigned long long)S);
    Drops += b; Calls++;
  });
  printf("R %s %llx+%llx handled=%d drops=[%s]\n", Removed ? "rw" : "rx", (unsigned long long)Off, (unsigned long long)Size, H ? 1 : 0, Drops.c_str());
  return H;
}
int main(int argc, char** argv) {
  static uint8_t Store[0x1000000 + 0x10000];
  Arena = (uint8_t*)(((uintptr_t)Store + 0xffff) & ~(uintptr_t)0xffff);
  for (uint64_t i = 0; i < ArenaSize; i++) Arena[i] = (uint8_t)(i * 7);
  B = (uint64_t)Arena;
  const std::string mode = argc > 1 ? argv[1] : "";
  if (mode == "cases") {
    printf("C cycle\n");      Note(true, false, 0, 0x3d000); Arena[0x2000] ^= 1; Arena[0x3c005] ^= 1; Arena[0x3b000] ^= 1; Note(false, true, 0, 0x3d000);
    printf("C unchanged\n");  Note(true, false, 0, 0x3d000); Note(false, true, 0, 0x3d000);
    printf("C nocopy\n");     Note(false, true, 0x100000, 0x4000);
    printf("C big\n");        Note(true, false, 0x200000, 0x41000);
    printf("C hole\n");       Hole = B + 0x303000; Note(true, false, 0x300000, 0x8000); Hole = 0;
    printf("C neither\n");    Note(false, false, 0, 0x1000);
    printf("C taint\n");      Note(true, false, 0x400000, 0x10000); IosPdMiss(B + 0x404000); Note(false, true, 0x400000, 0x10000);
    printf("C miss-outside\n"); Note(true, false, 0x400000, 0x10000); IosPdMiss(B + 0x900000); Note(false, true, 0x400000, 0x10000);
    printf("C ring-overflow\n"); Note(true, false, 0x400000, 0x10000); for (int i = 0; i < 65; i++) IosPdMiss(B + 0x900000); Note(false, true, 0x400000, 0x10000);
    printf("C sub-range\n");  Note(true, false, 0x500000, 0x10000); Arena[0x506000] ^= 1; Note(false, true, 0x504000, 0x4000);
    printf("C overlap\n");    Note(true, false, 0x600000, 0x10000); Note(true, false, 0x608000, 0x10000); Note(false, true, 0x608000, 0x10000);
    printf("C partial\n");    Note(true, false, 0x700000, 0x8000); Note(false, true, 0x704000, 0x8000);
    printf("C many-runs\n");  Note(true, false, 0x800000, 0x40000); for (int i = 0; i < 17; i++) Arena[0x800000 + i * 0x2000] ^= 1; Note(false, true, 0x800000, 0x40000);
    printf("C evict\n");      for (int i = 0; i < 33; i++) Note(true, false, 0xA00000 + i * 0x10000, 0x4000);
  } else if (mode == "many") {
    for (int i = 0; i < 300; i++) { Note(true, false, 0, 0x3d000); Note(false, true, 0, 0x3d000); }
  } else if (mode == "short") {
    Note(true, false, 0, 0x3d000); Arena[0x2000] ^= 1; Note(false, true, 0, 0x3d000);
  } else if (mode == "out") {
    Note(true, false, 0, 0x3d000); Note(false, true, 0, 0x3d000);
    Arena[0x5000] ^= 1;
    Note(true, false, 0, 0x3d000); Note(false, true, 0, 0x3d000);
    Note(true, false, 0, 0x3d000); Note(false, true, 0, 0x3d000);
    Note(false, true, 0, 0x3d000); Arena[0x7000] ^= 1; Note(false, true, 0, 0x3d000);
    Note(true, false, 0, 0x3d000); Note(false, true, 0, 0x3d000);
    Note(true, false, 0x1000, 0x1000);
  } else if (mode == "sub") {
    Note(true, false, 0x500000, 0x10000); Arena[0x506000] ^= 1; Note(false, true, 0x504000, 0x4000);
    Note(true, false, 0x600000, 0x10000); Note(true, false, 0x608000, 0x10000); Note(false, true, 0x608000, 0x10000);
  }
  const IosPdCounts& N = IosPdN;
  printf("O outchecked=%llu outpages=%llu outchanged=%llu outcycles=%llu outdiscarded=%llu firstwhole=%llu outwhole=%llu\n",
         (unsigned long long)N.OutChecked, (unsigned long long)N.OutPages, (unsigned long long)N.OutChanged,
         (unsigned long long)N.OutCycles, (unsigned long long)N.OutDiscarded, (unsigned long long)N.FirstWhole,
         (unsigned long long)N.OutWhole);
  printf("N toggles=%llu snaps=%llu big=%llu unreadable=%llu evicted=%llu compared=%llu pages=%llu changed=%llu nosnap=%llu kept=%llu redone=%llu tainted=%llu partial=%llu overlap=%llu manyruns=%llu calls=%d\n",
         (unsigned long long)N.Toggles, (unsigned long long)N.Snaps, (unsigned long long)N.SkipBig, (unsigned long long)N.SkipUnreadable,
         (unsigned long long)N.Evicted, (unsigned long long)N.Compared, (unsigned long long)N.Pages, (unsigned long long)N.Changed,
         (unsigned long long)N.NoSnap, (unsigned long long)N.Kept, (unsigned long long)N.Redone, (unsigned long long)N.Tainted,
         (unsigned long long)N.Partial, (unsigned long long)N.Overlap, (unsigned long long)N.ManyRuns, Calls);
  return 0;
}
'''

# Case: the R lines each Note() prints, in order (handled flag and the drops it made itself).
CASES = {
    'cycle': (['rw 0+3d000 handled=0 drops=[]', 'rx 0+3d000 handled=1 drops=[2000+1000,3b000+2000]'],
              'a first cycle: RW is dropped whole by the caller (no closed copy); RX drops the changed pages only, adjacent ones as one run'),
    'unchanged': (['rw 0+3d000 handled=1 drops=[]', 'rx 0+3d000 handled=1 drops=[]'],
                  'an unchanged cycle drops nothing'),
    'nocopy': (['rx 100000+4000 handled=0 drops=[]'], 'an executable protection with no copy is left to the caller (whole range)'),
    'big': (['rw 200000+41000 handled=0 drops=[]'], 'a range over 256 KB is not copied and is dropped whole by the caller'),
    'hole': (['rw 300000+8000 handled=0 drops=[]'], 'a range with an uncommitted page is not copied and is dropped whole'),
    'neither': (['rx 0+1000 handled=0 drops=[]'], 'a protection that is neither is left alone'),
    'taint': (['rw 400000+10000 handled=0 drops=[]', 'rx 400000+10000 handled=0 drops=[]'],
              'an execute miss inside the pending range: dropped whole by the caller'),
    'miss-outside': (['rw 400000+10000 handled=1 drops=[]', 'rx 400000+10000 handled=1 drops=[]'],
                     'an execute miss elsewhere does not taint it'),
    'ring-overflow': (['rw 400000+10000 handled=1 drops=[]', 'rx 400000+10000 handled=0 drops=[]'],
                      'more misses than the ring holds count as a miss inside'),
    'sub-range': (['rw 500000+10000 handled=0 drops=[]', 'rx 504000+4000 handled=1 drops=[500000+4000,508000+8000,506000+1000]'],
                  'RX over part of a copy: the rest of the copy is dropped whole, the part compared'),
    'overlap': (['rw 600000+10000 handled=0 drops=[]', 'rw 608000+10000 handled=0 drops=[600000+10000]', 'rx 608000+10000 handled=1 drops=[]'],
                'a new RW range over a pending copy drops that copy whole first'),
    'partial': (['rw 700000+8000 handled=0 drops=[]', 'rx 704000+8000 handled=0 drops=[700000+4000]'],
                'RX that only partly meets a copy: the copy outside is dropped, the caller drops the range'),
    'many-runs': (['rw 800000+40000 handled=0 drops=[]', 'rx 800000+40000 handled=0 drops=[]'],
                  'more than 16 runs of changed pages: dropped whole by the caller'),
}

cxx = os.environ.get('CXX') or shutil.which('c++') or shutil.which('clang++') or shutil.which('g++')
if not cxx or readable is None:
    print('note: no C++ compiler; the helper was not run')
else:
    # ignore_cleanup_errors: on Windows a scanner may still hold the fresh h.exe when the directory goes.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        t = Path(tmp)
        (t / 'h.cpp').write_text(HARNESS.replace('@READABLE@', readable.group(0)).replace('@BODY@', body))
        exe = t / ('h.exe' if os.name == 'nt' else 'h')
        r = subprocess.run([cxx, '-std=c++17', '-O1', '-o', str(exe), str(t / 'h.cpp')], capture_output=True, text=True)
        check('the helper compiles on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            def run(mode, value=None):
                env = dict(os.environ)
                env.pop('MADEIRA_FEX_PROTECT_DIFF', None)
                if value is not None:
                    env['MADEIRA_FEX_PROTECT_DIFF'] = value
                out = subprocess.run([str(exe), mode], capture_output=True, text=True, env=env).stdout
                counts = {k: int(v) for line in out.splitlines()[-2:] for k, v in (kv.split('=') for kv in line.split()[1:])}
                return out, counts

            out, n = run('cases', '1')
            check('mode 1 says so once', out.count('protect-diff-v4 mode: on (') == 1, out[:400])
            got, case = {}, None
            for line in out.splitlines():
                if line.startswith('C '):
                    case = line[2:]
                    got[case] = []
                elif line.startswith('R ') and case:
                    got[case].append(line[2:])
            for name, (want, what) in CASES.items():
                check(what, got.get(name) == want, name + ': ' + repr(got.get(name)))
            ev = got.get('evict', [])
            check('a 33rd pending range evicts the oldest and drops it whole (each a first cycle, dropped whole by the caller)',
                  len(ev) == 33 and all(l.endswith('handled=0 drops=[]') for l in ev[:32])
                  and ev[32] == 'rw c00000+4000 handled=0 drops=[a00000+4000]', repr(ev[-2:]))
            check('the counters add up for the cases',
                  n['compared'] == 8 and n['tainted'] == 2 and n['manyruns'] == 1 and n['partial'] == 1 and n['overlap'] == 1
                  and n['evicted'] == 1 and n['big'] == 1 and n['unreadable'] == 1 and n['nosnap'] == 1, str(n))
            check('kept and redone count the pages of the compares that were handled',
                  n['kept'] + n['redone'] == 61 + 61 + 16 + 4 + 16 and n['redone'] == 3 + 1, str(n))
            check('the first kept page is said once', out.count('the first unchanged page of a W^X cycle kept its translations') == 1, out[:600])

            out, n = run('many', '1')
            lines = [l for l in out.splitlines() if l.startswith('LOG ')]
            rs = [l[2:] for l in out.splitlines() if l.startswith('R ')]
            check('300 unchanged cycles: only the first RW is dropped whole (by the caller), all 18300 pages kept',
                  n['compared'] == 300 and n['kept'] == 300 * 61 and n['calls'] == 0 and n['firstwhole'] == 1
                  and rs[0] == 'rw 0+3d000 handled=0 drops=[]' and all('drops=[]' in l and 'handled=1' in l for l in rs[1:]), str(n))
            check('eight compare lines, the first-kept line, one totals line at the 256th',
                  sum('protect-diff-v4 #' in l for l in lines) == 8 and sum('totals (on):' in l for l in lines) == 1
                  and 'toggles=256 copied=256 compared=256 pages=15616 changed=0 (0%) kept=15616 redone=0' in lines[-1], '\n'.join(lines[-2:]))

            out, n = run('short', 'log')
            rs = [l for l in out.splitlines() if l.startswith('R ')]
            check('MADEIRA_FEX_PROTECT_DIFF=log: measured only, never handled, nothing dropped by the helper',
                  n['compared'] == 1 and n['changed'] == 1 and n['kept'] == 0 and n['calls'] == 0
                  and len(rs) == 2 and all('handled=0 drops=[]' in l for l in rs)
                  and out.count('protect-diff-v4 mode: measure only (') == 1, out)
            for value, what in ((None, 'MADEIRA_FEX_PROTECT_DIFF unset (the default)'), ('1', 'MADEIRA_FEX_PROTECT_DIFF=1'),
                                ('yes', 'an unknown MADEIRA_FEX_PROTECT_DIFF value')):
                out, n = run('short', value)
                rs = [l[2:] for l in out.splitlines() if l.startswith('R ')]
                check(what + ': on, the first RW dropped whole by the caller, the RX step only the changed page',
                      rs == ['rw 0+3d000 handled=0 drops=[]', 'rx 0+3d000 handled=1 drops=[2000+1000]']
                      and n['kept'] == 60 and n['redone'] == 1 and n['calls'] == 1
                      and out.count('protect-diff-v4 mode: on (') == 1, out)
            out, n = run('many', 'log')
            check('300 cycles with log: totals say measure only, nothing kept',
                  sum('totals (measure only):' in l for l in out.splitlines()) == 1 and n['kept'] == 0 and n['calls'] == 0
                  and 'first unchanged page' not in out, str(n))
            out, n = run('many')
            check('300 cycles by default: totals say on, all pages kept',
                  sum('totals (on):' in l for l in out.splitlines()) == 1 and n['kept'] == 300 * 61, str(n))

            out, n = run('short', 'rxwhole')
            rs = [l[2:] for l in out.splitlines() if l.startswith('R ')]
            check('rxwhole: RW keeps the range, RX leaves the whole range to the caller, nothing kept per page',
                  rs == ['rw 0+3d000 handled=1 drops=[]', 'rx 0+3d000 handled=0 drops=[]']
                  and n['compared'] == 1 and n['changed'] == 1 and n['kept'] == 0 and n['calls'] == 0
                  and out.count('protect-diff-v4 mode: rxwhole (') == 1 and 'first unchanged page' not in out, out)
            out, n = run('sub', 'rxwhole')
            rs = [l[2:] for l in out.splitlines() if l.startswith('R ')]
            check('rxwhole: what of a copy lies outside the RX range, and a copy a new RW range overlaps, are dropped here',
                  rs == ['rw 500000+10000 handled=1 drops=[]', 'rx 504000+4000 handled=0 drops=[500000+4000,508000+8000]',
                         'rw 600000+10000 handled=1 drops=[]', 'rw 608000+10000 handled=1 drops=[600000+10000]',
                         'rx 608000+10000 handled=0 drops=[]'], repr(rs))
            # Outside the W^X window: bytes changed between an RX step and the next RW step of the same range.
            for value in ('log', 'rxwhole', '1'):
                out, n = run('out', value)
                logs = [l for l in out.splitlines() if 'outside #' in l]
                check('outside the window (' + value + '): one change counted, one RW step with changes said, '
                      'a second RX step restarts from its bytes, a partial range discards the closed copy',
                      n['outchecked'] == 3 and n['outpages'] == 3 * 61 and n['outchanged'] == 1 and n['outcycles'] == 1
                      and n['outdiscarded'] == 1 and len(logs) == 1 and 'outside #1 ' in logs[0]
                      and ': 1 of 61 pages changed since the last RX step' in logs[0], str(n) + ' ' + repr(logs))
            out, n = run('out', 'log')
            check('outside the window, measure only: the helper drops nothing and handles nothing',
                  n['calls'] == 0 and all('handled=0 drops=[]' in l for l in out.splitlines() if l.startswith('R ')), out)
            out, n = run('out', '1')
            rs = [l[2:] for l in out.splitlines() if l.startswith('R ')]
            check('mode 1 drops at the RW step the page changed outside the window; a range with no closed copy is dropped whole there',
                  rs[0] == 'rw 0+3d000 handled=0 drops=[]' and rs[2] == 'rw 0+3d000 handled=1 drops=[5000+1000]'
                  and rs[3] == 'rx 0+3d000 handled=1 drops=[]' and rs[-1] == 'rw 1000+1000 handled=0 drops=[]'
                  and n['calls'] == 1 and n['firstwhole'] == 2, repr(rs) + ' ' + str(n))
            out, n = run('sub', 'log')
            rs = [l[2:] for l in out.splitlines() if l.startswith('R ')]
            check('measure only: the helper drops nothing itself in the same sequence',
                  n['calls'] == 0 and len(rs) == 5 and all('handled=0 drops=[]' in l for l in rs), repr(rs))
            out, n = run('short', '0')
            check('MADEIRA_FEX_PROTECT_DIFF=0: nothing copied, compared, handled or logged',
                  n['toggles'] == 0 and n['compared'] == 0 and 'LOG ' not in out
                  and all('handled=0 drops=[]' in l for l in out.splitlines() if l.startswith('R ')), out)

script = (R / 'tools/build-xtajit64.sh').read_text()
line = 'python3 "$R/tools/patch-fex-ios-protect-diff.py" "$R/FEX" || echo "::warning::'
check('tools/build-xtajit64.sh runs it once with a warning fallback, and no longer the log-only overlay',
      script.count(line) == 1 and 'patch-fex-ios-protect-diff-log.py' not in script
      and not (R / 'tools/patch-fex-ios-protect-diff-log.py').exists())
check('InvalidationTracker.cpp is restored after the build',
      re.search(r'git -C FEX checkout -- [^\n]*Source/Windows/Common/InvalidationTracker\.cpp', script) is not None)
ci = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check, and no longer the log-only one',
      ci.count('run: python3 tests/host/check-fex-protect-diff.py') == 1 and 'check-fex-protect-diff-log.py' not in ci)

print('PASS: W^X cycles drop only changed pages' if ok else 'FAILED: W^X cycles drop only changed pages')
sys.exit(0 if ok else 1)
