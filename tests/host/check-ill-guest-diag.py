#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[ill-guest]: the guest bytes FEX could not decode (ios_ill_guest_diag.h); no Wine runs.

Jetboard Joust (a tester's iPad16,3, build 592, seven Send Logs of 2026-10-23) gets through the .NET runtime's compare-and-swap on its RWX code
pages and ends 2 s later in "Invalid instruction in entry block: 7193D880E0" -> SIGILL in the dispatcher -> the runtime's own FailFast (c000001d).
The logs never say what is AT that address; the ILL report prints the dispatcher's registers and the L1 entry only. The report added here prints
64 bytes of the guest's view of the block and, for an anonymous RWX alias of the JIT pool, the same bytes through the alias pointer.

What this checks (no device log runs here):
  1. Source shape: one header, included once after ios_int3_x86_diag.h and before the SIGILL handler; one call, inside ill_handler, after the
     "ILL diag: L1 entry" line and only for an L1 entry whose guest field is x6; the header writes, protects and stops nothing, reads guest
     memory only through ios_av_args_read, reads no environment variable (there is no switch: it only runs on the path that ends the process)
     and is capped at 3 reports.
  2. Compiled runs (macOS): the production header is compiled with the production ios_av_args_read over real memory pages and a fake alias
     lookup: a plain page (bytes and "no anonymous RWX alias"), an alias that agrees, an alias with a DIFFERENT byte (named with its offset), a block
     at the start of a mapped page (the bytes before it print as ??), an address that is not a 64-bit guest's, and the cap.
  3. The real signal_arm64_ios.c still compiles for iOS when a configured wine tree is there; the CI step exists.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
signal_src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
header = (root / 'build/ntdll-unix/ios_ill_guest_diag.h').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def strip_comments(t):
    t = re.sub(r'/\*.*?\*/', '', t, flags=re.S)
    return re.sub(r'//[^\n]*', '', t)


code = strip_comments(header)

# ---------------------------------------------------------------- 1. source shape
inc = '#include "ios_ill_guest_diag.h"'
check('the header is included exactly once, after ios_int3_x86_diag.h and before the SIGILL handler',
      signal_src.count(inc) == 1 and signal_src.index('#include "ios_int3_x86_diag.h"') < signal_src.index(inc)
      < signal_src.index('static void ill_handler( int signal, siginfo_t *siginfo, void *sigcontext )'))
call = 'if (l1pair[0] && entry[1] == x6) ios_ill_guest_bytes( x6, pc );'
check('one call site (the definition is in the header)', signal_src.count(call) == 1 and signal_src.count('ios_ill_guest_bytes(') == 1)
handler = signal_src.index('static void ill_handler( int signal, siginfo_t *siginfo, void *sigcontext )')
site = signal_src.index(call)
l1 = signal_src.index('ERR("ILL diag: L1 entry:', handler)
check('...inside ill_handler, after the "ILL diag: L1 entry" line, and only for an L1 entry whose guest field is x6',
      handler < l1 < site < signal_src.index('ios_dump_jit_pool( "ILL diag" )', site))
forbidden = ('thread_set_state', 'thread_suspend', 'thread_terminate', 'vm_protect', 'mprotect(', 'mach_vm_write', 'vm_write(',
             'mmap(', 'free(', 'malloc(', 'abort(', 'exit(', 'sigaction', 'fopen(', 'open(', 'mach_vm_read_overwrite', 'raise(', 'kill(', 'getenv(')
check('no write, protect, stop, exit, file, allocation or environment call, and no direct memory read (ios_av_args_read is the one reader)',
      not [f for f in forbidden if f in code], str([f for f in forbidden if f in code]))
raw_deref = re.findall(r'\*\s*\(\s*(?:const\s+)?(?:volatile\s+)?(?:u?int\d+_t|void|char|unsigned\w*|uintptr_t)\s*\*\s*\)', code)
check('no raw dereference of a guest address', not raw_deref, str(raw_deref))
check('capped at 3 reports per session', 'IOS_ILLG_MAX_REPORTS 3' in header and '> IOS_ILLG_MAX_REPORTS) return;' in code
      and 'static volatile int reports;' in code)
check('only for the address of a 64-bit guest (above the first 64 KB, below 2^47)',
      'if (guest < IOS_ILLG_BEFORE + 0x10000 || guest >= 0x800000000000ULL) return;' in code)

# ---------------------------------------------------------------- 2. compiled runs
cc = os.environ.get('CC', 'cc')
PRELUDE = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/mach_error.h>

#define arm_thread_state64_t fake_arm_thread_state64_t
typedef struct { uint64_t __x[29]; uint64_t __fp, __lr, __sp, __pc; } fake_arm_thread_state64_t;
#undef __darwin_arm_thread_state64_get_pc
#define __darwin_arm_thread_state64_get_pc(ts) ((ts).__pc)
unsigned long long ios_jit_module_base_for_va( unsigned long long va, unsigned long long *size_out ) { (void)va; (void)size_out; return 0; }
static const char *ios_pe_module_name( uint64_t base ) { (void)base; return "?"; }
void *ios_jit_rx_base_global;
size_t ios_jit_pool_size_global;
int ios_jit_low_contains( uintptr_t a ) { (void)a; return 0; }

/* the alias table of the test: [guest_lo, guest_lo + 0x4000) -> alias_lo */
static uintptr_t g_guest_lo, g_alias_lo;
uintptr_t ios_jit_anon_alias_lookup(uintptr_t a)
{
    return g_guest_lo && a >= g_guest_lo && a < g_guest_lo + 0x4000 ? g_alias_lo + (a - g_guest_lo) : 0;
}
'''

MAIN = r'''
/* A FIXED allocation that fails says why, on stderr (the checks print it as their detail): the address and size asked for, the
 * kernel's answer, the page size and the first mapping at or above the address. stdout ("alloc-failed") and exit(3) stay as they were. */
static void alloc_failed_report(uint64_t a, size_t n, kern_return_t kr)
{
    mach_vm_address_t ra = a;
    mach_vm_size_t rs = 0;
    vm_region_basic_info_data_64_t ri;
    mach_msg_type_number_t rc = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t ro = MACH_PORT_NULL;
    kern_return_t rk = mach_vm_region(mach_task_self(), &ra, &rs, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&ri, &rc, &ro);

    fprintf(stderr, "[alloc_at] mach_vm_allocate(0x%llx, 0x%zx, VM_FLAGS_FIXED) = %d (%s); page size 0x%lx\n",
            (unsigned long long)a, n, kr, mach_error_string(kr), (unsigned long)vm_page_size);
    if (rk == KERN_SUCCESS)
        fprintf(stderr, "[alloc_at]   first mapping at or above it: [0x%llx, 0x%llx) prot %d/%d%s\n", (unsigned long long)ra,
                (unsigned long long)(ra + rs), ri.protection, ri.max_protection, ra < a + n ? " -- OVERLAPS the request" : "");
    else
        fprintf(stderr, "[alloc_at]   mach_vm_region = %d (%s)\n", rk, mach_error_string(rk));
}
static uint8_t *page(uint64_t at, size_t n)
{
    mach_vm_address_t m = at;
    kern_return_t kr = mach_vm_allocate(mach_task_self(), &m, n, VM_FLAGS_FIXED);
    if (kr != KERN_SUCCESS) { alloc_failed_report(at, n, kr); puts("alloc-failed"); exit(3); }
    return (uint8_t *)m;
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    uint8_t *g = page(0x6000000000ULL, 0x8000), *a = page(0x6100000000ULL, 0x8000);
    /* the guest view: the block's address 0x6000001e0 sits 0x1e0 into the first page; bytes ramp 0,1,2,... so every row is checkable */
    for (unsigned i = 0; i < 0x4000; i++) { g[i] = (uint8_t)i; a[i] = (uint8_t)i; }
    g_guest_lo = 0x6000000000ULL; g_alias_lo = 0x6100000000ULL;
    if (!strcmp(s, "plain")) {
        g_guest_lo = 0;                                                      /* no alias covers it */
        ios_ill_guest_bytes(0x60000001e0ULL, 0x169ff84e8ULL);
    } else if (!strcmp(s, "same")) {
        ios_ill_guest_bytes(0x60000001e0ULL, 0x169ff84e8ULL);
    } else if (!strcmp(s, "diff")) {
        a[0x1e0 + 5] = 0xff;                                                 /* the alias differs five bytes into the block */
        ios_ill_guest_bytes(0x60000001e0ULL, 0x169ff84e8ULL);
    } else if (!strcmp(s, "diff-before")) {
        a[0x1e0 - 3] = 0xee;                                                 /* ...or three bytes before it */
        ios_ill_guest_bytes(0x60000001e0ULL, 0x169ff84e8ULL);
    } else if (!strcmp(s, "edge")) {
        /* the block is at the very start of a mapped range: the 16 bytes before it are not there */
        uint8_t *e = page(0x6300000000ULL, 0x4000);
        for (unsigned i = 0; i < 0x4000; i++) e[i] = (uint8_t)(i + 1);
        g_guest_lo = 0;
        ios_ill_guest_bytes(0x6300000000ULL, 0x169ff84e8ULL);
    } else if (!strcmp(s, "cap")) {
        g_guest_lo = 0;
        for (int i = 0; i < 5; i++) ios_ill_guest_bytes(0x60000001e0ULL, 0x169ff84e8ULL);
    } else if (!strcmp(s, "bad")) {
        ios_ill_guest_bytes(0, 0x169ff84e8ULL);
        ios_ill_guest_bytes(0x1000, 0x169ff84e8ULL);
        ios_ill_guest_bytes(0x10005, 0x169ff84e8ULL);                         /* inside the first 64 KB + 16 */
        ios_ill_guest_bytes(0x800000000000ULL, 0x169ff84e8ULL);
        ios_ill_guest_bytes(~0ULL, 0x169ff84e8ULL);
        puts("bad-done");
    } else if (!strcmp(s, "unmapped")) {
        g_guest_lo = 0;
        ios_ill_guest_bytes(0x6f00000000ULL, 0x169ff84e8ULL);                  /* nothing there: all ?? */
    }
    return 0;
}
'''

if sys.platform != 'darwin' or not shutil.which(cc):
    print('skip the compiled runs: macOS and a C compiler are needed')
else:
    av_header = (root / 'build/ntdll-unix/ios_av_args_diag.h').read_text()
    with tempfile.TemporaryDirectory(prefix='illguest-') as d:
        d = Path(d)
        (d / 'h.c').write_text(PRELUDE + '\n' + av_header + '\n' + header + '\n' + MAIN)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function', str(d / 'h.c'), '-o', str(d / 'h')],
                           capture_output=True, text=True)
        check('the production header compiles (host cc, -Wall -Wextra -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            def run(scn):
                return subprocess.run([str(d / 'h'), scn], capture_output=True, text=True)

            def lines(r):
                return [l for l in r.stderr.splitlines() if l.startswith('[ill-guest]')]

            ramp = ' '.join('%02x' % ((0x1e0 - 16 + i) & 0xff) for i in range(64))
            r = run('plain')
            ls = lines(r)
            check('a plain page: one headline with the 64 bytes (16 before the block, 48 from it), the block address, the host pc and "64 of 64 readable"',
                  len(ls) == 2 and ls[0].startswith('[ill-guest] #1 FEX could not translate the block at guest 0x60000001e0 (host pc 0x169ff84e8)')
                  and '(64 of 64 readable): ' + ramp in ls[0], r.stderr)
            check('...and says that no anonymous RWX alias covers it', 'no anonymous RWX alias covers that address' in ls[1], r.stderr)
            r = run('same')
            ls = lines(r)
            check('an alias that agrees: "the two views agree" with the alias pointer', len(ls) == 2 and 'the two views agree' in ls[1]
                  and 'alias pointer 0x61000001e0' in ls[1], r.stderr)
            r = run('diff')
            ls = lines(r)
            check('an alias that differs five bytes into the block: DIFFERENT, +5, the alias bytes with ff at that place',
                  len(ls) == 2 and 'DIFFERENT bytes (first difference at +5 from the block)' in ls[1]
                  and ' '.join(['%02x' % ((0x1e0 - 16 + i) & 0xff) if i != 21 else 'ff' for i in range(64)]) in ls[1], r.stderr)
            r = run('diff-before')
            ls = lines(r)
            check('a difference before the block is reported with a negative offset', len(ls) == 2 and 'first difference at -3 from the block' in ls[1], r.stderr)
            r = run('edge')
            ls = lines(r)
            check('a block at the start of a mapped range: the 16 bytes before it print as ?? and the count says 48 of 64',
                  len(ls) >= 1 and '(48 of 64 readable): ' + ' '.join(['??'] * 16) + ' 01 02 03' in ls[0], r.stderr)
            r = run('unmapped')
            ls = lines(r)
            check('nothing mapped: 0 of 64 readable, all ??', len(ls) >= 1 and '(0 of 64 readable): ' + ' '.join(['??'] * 64) in ls[0], r.stderr)
            r = run('cap')
            heads = [l for l in lines(r) if 'FEX could not translate' in l]
            check('five calls: three reports, then silence', len(heads) == 3 and heads[2].startswith('[ill-guest] #3'), r.stderr)
            r = run('bad')
            check('an address that is no 64-bit guest\'s prints nothing', r.stderr == '' and r.stdout.strip() == 'bad-done', r.stderr + r.stdout)
            check('every line carries the prefix', all(l.startswith('[ill-guest]') for l in run('same').stderr.splitlines()))

# ---------------------------------------------------------------- 3. iOS compile, CI
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('build-ipa.yml runs this check', 'python3 tests/host/check-ill-guest-diag.py' in ci and ci.count('check-ill-guest-diag.py') == 1)

wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
config = wine / 'build-macos/include/config.h'
if shutil.which('xcrun') and config.exists():
    try:
        sdk = subprocess.run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ''
    if sdk:
        build = root / 'build/ntdll-unix'
        flags = ['-fsyntax-only', '-arch', 'arm64', '-isysroot', sdk, '-miphoneos-version-min=17.0', '-O2', '-fPIC',
                 '-fno-strict-aliasing', '-Wall', '-Wno-implicit-function-declaration', '-Wno-int-conversion',
                 '-include', str(config), '-include', str(build / 'shims/wine_ios_exit.h'), f'-I{build}', f'-I{build / "shims"}',
                 f'-I{wine / "build-macos/dlls/ntdll"}', f'-I{wine / "dlls/ntdll"}', f'-I{wine / "dlls/ntdll/unix"}',
                 f'-I{wine / "build-macos/include"}', f'-I{wine / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES',
                 '-DLTC_SOURCE', '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB',
                 '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context', '-Dset_thread_context=ntdll_set_thread_context']
        r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(build / 'signal_arm64_ios.c')], capture_output=True, text=True)
        check('signal_arm64_ios.c compiles for iOS (syntax only, pinned wine headers)', r.returncode == 0, r.stderr[-2000:])
        check('...and the new header adds no warning', 'ios_ill_guest' not in r.stderr and 'illg' not in r.stderr, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

print('PASS: [ill-guest]' if ok else 'FAILED: [ill-guest]')
sys.exit(0 if ok else 1)
