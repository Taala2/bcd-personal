#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""env.MADEIRA_WASM_GUARD_TRIM: a V8 WebAssembly memory's 10 GB reservation gets 6 or 4 GB.

Internship [demo] (NW.js 0.48, V8 8.5; build 627, iPhone16,1, 2026-11-02 00:23) stays on its
loading screen: the renderer got one 10 GB (0x280000000) reserve-only PAGE_NOACCESS request,
the next 64 from its main thread and its worker failed with the largest gap at 6144 MB. V8 8.5
reserves that size for every WebAssembly.Memory on 64-bit whatever the flags (backing-store.cc:
kUseGuardRegions, kFullGuardSize = 10 GiB, buffer start at base + kNegativeGuardSize = 2 GiB);
--disable-features=WebAssemblyTrapHandler --js-flags=--no-wasm-trap-handler reached the renderer
and changed nothing.

Checks:
  - the helper, cut out of build/ntdll-unix/virtual_ios.c and compiled on the host with a stub
    allocator over a model of free holes: unset or not exactly '1', another size, a commit or
    another protection leave the request alone (the allocator is not called); on, 6 GB is given
    unhinted where it fits, else 4 GB, and with neither the old failure stands with the output
    untouched; at most 32 log lines;
  - it is called on both failure paths: the kernel-pick reserve after the band census and before
    the gap probes, and the hinted reserve after its last unhinted retry, before sc_decided, whose
    success path hands the trimmed size back;
  - the session end resets the switch, CI runs this check, the settings catalog documents it.
No Wine or device is run.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
start = src.index('static NTSTATUS ios_wasm_guard_trim(')
end = src.index('\n}\n', start) + 3
helper = src[start:end]
assert src.count('getenv( "MADEIRA_WASM_GUARD_TRIM" )') == 1, 'the switch is read in one place'
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
cleanup_start = bridge.index('static void wine_session_end(void)\n{')
cleanup_end = bridge.index('    // Stop wineserver', cleanup_start)
reset = 'unsetenv("MADEIRA_WASM_GUARD_TRIM");'
assert reset in bridge[cleanup_start:cleanup_end], 'the shared normal/abandoned-session cleanup must reset the opt-in'

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
typedef unsigned long SIZE_T, ULONG_PTR;
typedef unsigned int ULONG, NTSTATUS;
#define MEM_COMMIT  0x1000
#define MEM_RESERVE 0x2000
#define PAGE_NOACCESS 0x01
#define PAGE_READWRITE 0x04
#define STATUS_SUCCESS 0
#define STATUS_NO_MEMORY 0xc0000017
#define G 0x40000000ul
static struct { ULONG_PTR lo, hi; } holes[8];
static int nholes, calls, log_lines;
#define dprintf test_dprintf   /* stdio.h declares the real one */
static int test_dprintf( int fd, const char *fmt, ... )
{
    va_list ap;
    (void)fd;
    log_lines++;
    va_start( ap, fmt ); vprintf( fmt, ap ); va_end( ap );
    return 0;
}
static NTSTATUS allocate_virtual_memory( void **ret, SIZE_T *size_ptr, ULONG type, ULONG protect,
                                         ULONG_PTR limit_low, ULONG_PTR limit_high,
                                         ULONG_PTR align, ULONG attributes )
{
    calls++;
    if (*ret || limit_low || limit_high || align || attributes) return 0xbad;
    if (type != MEM_RESERVE || protect != PAGE_NOACCESS) return 0xbad;
    for (int i = 0; i < nholes; i++)
        if (holes[i].hi - holes[i].lo >= *size_ptr)
        {
            *ret = (void *)holes[i].lo;
            holes[i].lo += *size_ptr;
            return STATUS_SUCCESS;
        }
    return STATUS_NO_MEMORY;
}
''' + helper + '\nstatic void end_game_session(void) { ' + reset + ' }\n' + r'''
static int fails;
static void expect( const char *what, const char *env, SIZE_T want, ULONG type, ULONG protect,
                    NTSTATUS want_st, ULONG_PTR want_at, SIZE_T want_size, int want_calls )
{
    void *p = (void *)0x1234; SIZE_T s = 0x5678; NTSTATUS st;
    if (env) setenv( "MADEIRA_WASM_GUARD_TRIM", env, 1 ); else unsetenv( "MADEIRA_WASM_GUARD_TRIM" );
    calls = 0;
    st = ios_wasm_guard_trim( type, protect, want, &p, &s );
    if (st != want_st || (ULONG_PTR)p != want_at || s != want_size || calls != want_calls)
    {
        printf( "FAIL %s: st=%x at=%p size=%#lx calls=%d\n", what, st, p, s, calls );
        fails++;
    }
}
#define UNTOUCHED STATUS_NO_MEMORY, 0x1234, 0x5678
int main( void )
{
    const SIZE_T ten = 0x280000000ul;
    /* the device's largest gap was 6144 MB, then two 4 GB holes */
    holes[0].lo = 0x767fff0000ul; holes[0].hi = 0x7800000000ul;
    holes[1].lo = 0x7900000000ul; holes[1].hi = 0x7a00000000ul;
    holes[2].lo = 0x7b00000000ul; holes[2].hi = 0x7c00000000ul;
    nholes = 3;
    const char *off[] = { "", "0", "true", "yes", "2", "01", "1x" };
    expect( "unset", NULL, ten, MEM_RESERVE, PAGE_NOACCESS, UNTOUCHED, 0 );
    for (unsigned i = 0; i < sizeof off / sizeof *off; i++)
        expect( off[i], off[i], ten, MEM_RESERVE, PAGE_NOACCESS, UNTOUCHED, 0 );
    expect( "8 GB", "1", 8 * G, MEM_RESERVE, PAGE_NOACCESS, UNTOUCHED, 0 );
    expect( "16 GB", "1", 16 * G, MEM_RESERVE, PAGE_NOACCESS, UNTOUCHED, 0 );
    expect( "10 GB + 64 KB", "1", ten + 0x10000, MEM_RESERVE, PAGE_NOACCESS, UNTOUCHED, 0 );
    expect( "a commit", "1", ten, MEM_RESERVE | MEM_COMMIT, PAGE_NOACCESS, UNTOUCHED, 0 );
    expect( "a commit alone", "1", ten, MEM_COMMIT, PAGE_NOACCESS, UNTOUCHED, 0 );
    expect( "read-write", "1", ten, MEM_RESERVE, PAGE_READWRITE, UNTOUCHED, 0 );
    /* on: 6 GB in the 6 GB gap, then 4 GB in each 4 GB hole, then nothing */
    expect( "6 GB", "1", ten, MEM_RESERVE, PAGE_NOACCESS, STATUS_SUCCESS, 0x767fff0000ul, 6 * G, 1 );
    expect( "4 GB", "1", ten, MEM_RESERVE, PAGE_NOACCESS, STATUS_SUCCESS, 0x7900000000ul, 4 * G, 2 );
    expect( "4 GB again", "1", ten, MEM_RESERVE, PAGE_NOACCESS, STATUS_SUCCESS, 0x7b00000000ul, 4 * G, 2 );
    expect( "no hole: the old failure", "1", ten, MEM_RESERVE, PAGE_NOACCESS, UNTOUCHED, 2 );
    /* the log is capped */
    for (int i = 0; i < 40; i++)
        expect( "capped", "1", ten, MEM_RESERVE, PAGE_NOACCESS, UNTOUCHED, 2 );
    if (log_lines != 32) { printf( "FAIL log lines=%d\n", log_lines ); fails++; }
    /* the production session reset: a later game without the key keeps the old path */
    setenv( "MADEIRA_WASM_GUARD_TRIM", "1", 1 );
    end_game_session();
    if (getenv( "MADEIRA_WASM_GUARD_TRIM" )) { puts( "FAIL reset" ); fails++; }
    holes[0].lo = 0x7000000000ul; holes[0].hi = 0x7200000000ul;
    calls = 0;
    {
        void *p = (void *)0x1234; SIZE_T s = 0x5678;
        if (ios_wasm_guard_trim( MEM_RESERVE, PAGE_NOACCESS, ten, &p, &s ) != STATUS_NO_MEMORY || calls
            || p != (void *)0x1234 || s != 0x5678) { puts( "FAIL after reset" ); fails++; }
    }
    puts( fails ? "HARNESS FAILED" : "PASS: 10 GB reserve-only request trimmed to 6/4 GB only when on, capped log, session reset" );
    return fails != 0;
}
'''
with tempfile.TemporaryDirectory(prefix='bcd-wasm-guard-trim-') as tmp:
    path = Path(tmp)
    (path / 'check.c').write_text(harness)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-Wall', '-Wextra', '-Werror',
                    str(path / 'check.c'), '-o', str(path / 'check')], check=True)
    subprocess.run([str(path / 'check')], check=True)

# The kernel-pick failure path: after the band census, before the gap probes.
kp = src[src.index('ios_band_census( "jumbo reserve failed" );'):]
kp = kp[:kp.index('ios_va_gap_probe( "jumbo reserve failed" );')]
assert 'if (!ios_wasm_guard_trim( type, protect, *size_ptr, ret, size_ptr )) return STATUS_SUCCESS;' in kp
assert kp.index('ios_wasm_guard_trim(') < kp.index('if (probed++ < 2)')
# The hinted failure path: after the last unhinted retry, before sc_decided, whose success hands the size back.
hd = src[src.index('                if (st2)\n                {\n                    pick = NULL;'):]
hd = hd[:hd.index('st = STATUS_SUCCESS;')]
assert hd.index('st2 = allocate_virtual_memory( &pick, &sz, type, protect, 0, 0, 0, 0 );') < \
    hd.index('st2 = ios_wasm_guard_trim( type, protect, *size_ptr, &pick, &sz );') < hd.index('sc_decided:')
assert '*ret = pick;\n                    *size_ptr = sz;' in hd
assert src.count('ios_wasm_guard_trim(') == 3, 'one definition and two call sites'
assert 'run: python3 tests/host/check-wasm-guard-trim.py' in (root / '.github/workflows/build-ipa.yml').read_text()
cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
assert ('ConfigOption(key: "env.MADEIRA_WASM_GUARD_TRIM", title: "Smaller reservation for web-engine WebAssembly memory", '
        'kind: .bool, defaultValue: "0", category: "Memory & JIT pool"') in cat
print('PASS: both reserve failure paths call it, session reset, CI step and catalog entry')
