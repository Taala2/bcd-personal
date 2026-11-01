#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Run the real anonymous-alias LDADDAL doubleword emulator, including concurrent adds.

Internship [demo] (NW.js, build 627, iPhone16,1, 2026-11-01) has its renderer fault on
f8e800c6, LDADDAL X8,X6,[X6], on V8 heap pages behind an existing writable alias
("[store-undecoded] ... alias OK", then "[mach_exc] UNHANDLED"). Only this 64-bit AL
family is enabled, ONLY with MADEIRA_ANON_LDADDAL64=1; default off and changing
sessions preserve the old path. Invalid encodings, registers and addresses decline
without writing. No Wine or device is run.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
start = source.index('static int ios_mach_emulate_ldaddal64(')
end = source.index('\n}\n', start) + 3
get_start = source.index('static int ios_anon_ldaddal64_on(void)')
get_end = source.index('\n}\n', get_start) + 3
helper = source[get_start:get_end] + '\n' + source[start:end]
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
cleanup_start = bridge.index('static void wine_session_end(void)\n{')
cleanup_end = bridge.index('    // Stop wineserver', cleanup_start)
cleanup = bridge[cleanup_start:cleanup_end]
reset = 'unsetenv("MADEIRA_ANON_LDADDAL64");'
assert reset in cleanup, 'the shared normal/abandoned-session cleanup must reset the opt-in'
harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
#include <stdlib.h>
''' + helper + '\nstatic void end_game_session(void) { ' + reset + ' }\n' + r'''
static uint64_t dword __attribute__((aligned(16)));
static uint64_t returns[4];
static void *increment(void *arg) {
    uintptr_t id = (uintptr_t)arg;
    uint64_t g[29] = {0};
    g[8] = 1;
    for (int i = 0; i < 40000; ++i) {
        if (!ios_mach_emulate_ldaddal64(0xf8e800c6u, (uintptr_t)&dword, g)) return (void *)1;
        returns[id] += g[6];
    }
    return 0;
}
#define CHECK(x) do { if (!(x)) { printf("FAIL line %d\n", __LINE__); return 1; } } while (0)
int main(void) {
    uint64_t g[29], before[29];
    for (int i = 0; i < 29; ++i) g[i] = 0xaaaa000000000000ull + i;
    dword = 0x00000001fffffffeull;
    g[6] = (uintptr_t)&dword;  /* Rn is X6 too in the device encoding; the helper never reads Rn */
    g[8] = 0x0000000100000003ull;
    memcpy(before, g, sizeof g);
    CHECK(!unsetenv("MADEIRA_ANON_LDADDAL64"));
    CHECK(!ios_mach_emulate_ldaddal64(0xf8e800c6u, (uintptr_t)&dword, g));
    CHECK(dword == 0x00000001fffffffeull && !memcmp(g, before, sizeof g));
    const char *off[] = {"", "0", "true", "yes", "2", "01", "1x"};
    for (unsigned i = 0; i < sizeof off / sizeof *off; ++i) {
        CHECK(!setenv("MADEIRA_ANON_LDADDAL64", off[i], 1));
        CHECK(!ios_mach_emulate_ldaddal64(0xf8e800c6u, (uintptr_t)&dword, g));
        CHECK(dword == 0x00000001fffffffeull && !memcmp(g, before, sizeof g));
    }
    /* The 32-bit switch does not enable the doubleword form. */
    CHECK(!unsetenv("MADEIRA_ANON_LDADDAL64") && !setenv("MADEIRA_ANON_LDADDAL32", "1", 1));
    CHECK(!ios_mach_emulate_ldaddal64(0xf8e800c6u, (uintptr_t)&dword, g));
    CHECK(dword == 0x00000001fffffffeull && !memcmp(g, before, sizeof g));
    CHECK(!unsetenv("MADEIRA_ANON_LDADDAL32"));
    CHECK(!setenv("MADEIRA_ANON_LDADDAL64", "1", 1));
    /* The device instruction: a full 64-bit add with a carry out of the low word. */
    CHECK(ios_mach_emulate_ldaddal64(0xf8e800c6u, (uintptr_t)&dword, g));
    CHECK(dword == 0x0000000300000001ull && g[6] == 0x00000001fffffffeull);
    for (int i = 0; i < 29; ++i) CHECK(i == 6 || g[i] == before[i]);
    /* Source and destination overlap: read the operand before replacing X8. */
    dword = 7; g[8] = 9;
    CHECK(ios_mach_emulate_ldaddal64((0xf8e800c6u & ~31u) | 8u, (uintptr_t)&dword, g));
    CHECK(dword == 16 && g[8] == 7);
    /* XZR as source adds zero; XZR as destination discards the old value. */
    dword = 11; g[6] = 99;
    CHECK(ios_mach_emulate_ldaddal64((0xf8e800c6u & ~(31u << 16)) | (31u << 16), (uintptr_t)&dword, g));
    CHECK(dword == 11 && g[6] == 11);
    g[8] = 5; memcpy(before, g, sizeof g);
    CHECK(ios_mach_emulate_ldaddal64((0xf8e800c6u & ~31u) | 31u, (uintptr_t)&dword, g));
    CHECK(dword == 16 && !memcmp(before, g, sizeof g));
    const uint32_t refuse[] = {
        0xb8e800c6u, /* W-width (the 32-bit helper's form) */
        0x38e800c6u, 0x78e800c6u, /* byte / halfword */
        0xf82800c6u, 0xf8a800c6u, 0xf86800c6u, /* non-AL orderings */
        0xf8e810c6u, 0xf8e880c6u, /* LDCLR / SWP */
        0xc8e8fce0u, 0xf9000000u, /* CAS / STR */
        (0xf8e800c6u & ~(31u << 16)) | (29u << 16),
        (0xf8e800c6u & ~31u) | 30u,
    };
    memcpy(before, g, sizeof g);
    for (unsigned i = 0; i < sizeof refuse / sizeof *refuse; ++i) {
        CHECK(!ios_mach_emulate_ldaddal64(refuse[i], (uintptr_t)&dword, g));
        CHECK(dword == 16 && !memcmp(before, g, sizeof g));
    }
    CHECK(!ios_mach_emulate_ldaddal64(0xf8e800c6u, 0, g));
    for (unsigned off8 = 1; off8 < 8; ++off8)
        CHECK(!ios_mach_emulate_ldaddal64(0xf8e800c6u, (uintptr_t)&dword + off8, g));
    CHECK(dword == 16 && !memcmp(before, g, sizeof g));
    pthread_t threads[4]; dword = 0;
    for (uintptr_t i = 0; i < 4; ++i) CHECK(!pthread_create(&threads[i], 0, increment, (void *)i));
    for (int i = 0; i < 4; ++i) { void *result; CHECK(!pthread_join(threads[i], &result) && !result); }
    CHECK(dword == 160000);
    /* Every fetch-add returned a distinct old value, not just a final total. */
    CHECK(returns[0] + returns[1] + returns[2] + returns[3] == 159999ull * 160000 / 2);
    /* Run the production session reset: an enabled game ends, then a game with
     * no key must go back to the old path in the same host process. */
    end_game_session();
    CHECK(getenv("MADEIRA_ANON_LDADDAL64") == NULL);
    memcpy(before, g, sizeof g);
    CHECK(!ios_mach_emulate_ldaddal64(0xf8e800c6u, (uintptr_t)&dword, g));
    CHECK(dword == 160000 && !memcmp(before, g, sizeof g));
    puts("PASS: atomic LDADDAL doubleword semantics, refusal, concurrency and session reset");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='bcd-anon-ldadd64-') as tmp:
    path = Path(tmp)
    (path / 'check.c').write_text(harness)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-Wall', '-Wextra', '-Werror',
                    '-pthread', str(path / 'check.c'), '-o', str(path / 'check')], check=True)
    subprocess.run([str(path / 'check')], check=True)

branch = source[source.index('/* madeira-bcd personal: the X-width form'):source.index('/* ml350 DISCRIMINATOR:')]
assert 'if (!emulated && !in_jit && !anon_cas_handoff &&' in branch
assert '(insn & 0xffe0fc00u) == 0xf8e00000u' in branch
assert '!(fault_addr & 7)' in branch
assert 'ios_jit_anon_alias_lookup( fault_addr + 7 ) == rw_addr + 7' in branch
assert 'ios_mach_emulate_ldaddal64(insn, rw_addr, state.__x)' in branch
assert 'if (!ios_anon_ldaddal64_on()) return 0;' in helper
# The word path is still first and unchanged: the doubleword branch follows it.
assert source.index('ios_mach_emulate_ldaddal32(insn, rw_addr, state.__x)') < source.index('ios_mach_emulate_ldaddal64(insn, rw_addr, state.__x)')
assert 'run: python3 tests/host/check-anon-ldadd64.py' in (root / '.github/workflows/build-ipa.yml').read_text()
print('PASS: default-off per-game gate and aligned, fully covered anonymous-alias path')
