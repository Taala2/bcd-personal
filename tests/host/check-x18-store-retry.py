#!/usr/bin/env python3
"""A store OF x18 (opengl32's `.teb = NtCurrentTeb()`) retries when x18 was zeroed; no Wine runs.

iOS zeroes x18 when it preempts a thread. The x18 patcher in build/ntdll-unix/virtual_ios.c runs
`str x18, [sp, #n]` (X18_ROLE_RT, opengl32 only, gl-backend on) through the "via x18" trampoline:
load x18 from TPIDRRO_EL0, then the store unchanged. A preemption between the load and the store
writes 0 without faulting, so the GL call reaches the unix side with params->teb == NULL and faults
at 0x1238 (build 664, Metamorph on zink, 2026-11-19: ext_glUnmapBuffer, ext_glFenceSync, ...).

Compiles the production ios_insn_x18_role, ios_insn_x18_role_ex and ios_x18_store_retry and checks:
  - the three store forms of x18 are X18_ROLE_RT only when stores are on;
  - the retry word is `cbz x18, -16` (back to the trampoline's mrs);
  - a model of the trampoline, with x18 zeroed before each of its words in turn, always stores the
    TEB with the retry word, and stores 0 without it (the bug this guards);
and textually that the patcher emits the retry word after the store for X18_ROLE_RT only, within a
24-byte check. Needs python3 and a C compiler (AddressSanitizer/UBSan when available).
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()


def function(source: str, signature: str) -> str:
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


defines = native[native.index('#define X18_ROLE_NONE 0'):native.index('static int ios_insn_x18_role(uint32_t insn)')]
code = '#include <stdint.h>\n#include <stdio.h>\n#include <stdlib.h>\n' + defines
code += function(native, 'static int ios_insn_x18_role(uint32_t insn)')
code += function(native, 'static int ios_insn_x18_role_ex(uint32_t insn, int stores)')
code += function(native, 'static uint32_t ios_x18_store_retry(int back_bytes)')
code += r'''
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
#define TEB 0x71fffe0000ull

/* The trampoline as the patcher writes it: mrs x18; and x18; ldr x18, [slot]; <store>; [cbz x18]; b back.
 * The model runs it with x18 zeroed once, just before word `zero_at` (or never, -1), and returns what the
 * store wrote. Words: 0 mrs, 1 and, 2 ldr, 3 store, 4 cbz (when retry), then the branch back. */
static uint64_t run( int retry, int zero_at )
{
    uint64_t x18 = 0, stored = 0xdeadull;
    int pc = 0, steps = 0, zeroed = 0, words = retry ? 5 : 4;
    while (pc < words)
    {
        if (++steps > 64) FAIL("the model did not leave the trampoline\n");
        if (!zeroed && pc == zero_at) { x18 = 0; zeroed = 1; }
        switch (pc)
        {
        case 0: x18 = 0x1000ull; break;   /* mrs x18, TPIDRRO_EL0 (the TSD base) */
        case 1: x18 &= ~7ull; break;
        case 2: x18 = TEB; break;         /* ldr x18, [x18, #slot] */
        case 3: stored = x18; break;      /* str x18, [sp, #n] */
        case 4: {                         /* cbz x18, -16 */
            uint32_t w = ios_x18_store_retry( 16 );
            int32_t imm = (int32_t)(w << 8) >> 13;   /* imm19, sign-extended */
            if (x18 == 0) { pc += imm; continue; }
            break;
        }
        }
        pc++;
    }
    return stored;
}

int main( void )
{
    const uint32_t str_sp = 0xF9000BF2;   /* str x18, [sp, #0x10]   (opengl32 glUnmapBuffer) */
    const uint32_t stur = 0xF81F03F2;     /* stur x18, [sp, #-16] */
    const uint32_t stp = 0xA90007F2;      /* stp x18, x1, [sp] */
    if (ios_insn_x18_role_ex( str_sp, 1 ) != X18_ROLE_RT || ios_insn_x18_role_ex( stur, 1 ) != X18_ROLE_RT
        || ios_insn_x18_role_ex( stp, 1 ) != X18_ROLE_RT)
        FAIL("a store of x18 is not X18_ROLE_RT with stores on\n");
    if (ios_insn_x18_role_ex( str_sp, 0 ) != X18_ROLE_NONE) FAIL("a store of x18 is patched with stores off\n");
    if (ios_x18_store_retry( 16 ) != 0xB4FFFF92) FAIL("retry word 0x%08x, want 0xB4FFFF92 (cbz x18, -16)\n", ios_x18_store_retry( 16 ));
    for (int at = -1; at <= 5; at++)
        if (run( 1, at ) != TEB) FAIL("with the retry word, x18 zeroed before word %d stores 0x%llx\n", at, (unsigned long long)run( 1, at ));
    if (run( 0, 3 ) != 0) FAIL("the model no longer shows the bug: without the retry, a zero before the store should store 0\n");
    puts( "PASS: a store of x18 retries after a zeroed x18 and always writes the TEB; without the retry it writes 0" );
    return 0;
}
'''

patcher = function(native, 'int ios_jit_patch_x18(char *text_rw, char *text_rx, size_t text_size,')
fallback = patcher[patcher.index('Use x18 itself.'):patcher.index('/* Branch back to return address */')]
assert 'int store_retry = (role == X18_ROLE_RT);' in fallback
assert 'if (tramp_off + (store_retry ? 24 : 20) > tramp_size)' in fallback
store_at = fallback.index('*(uint32_t *)(tramp_rw + tramp_off) = insn;')
retry_at = fallback.index('if (store_retry)')
assert store_at < retry_at, 'the retry word comes after the store'
assert '*(uint32_t *)(tramp_rw + tramp_off) = ios_x18_store_retry(16);' in fallback[retry_at:]
# The budget pass gives each site 32 bytes; the retry form is 24 with the branch back.
assert 'need += 32;' in function(native, 'size_t ios_jit_x18_tramp_need( const char *text, size_t text_size, int stores )')
print('PASS: the patcher emits the retry word after a store of x18 only, within a 24-byte check')

with tempfile.TemporaryDirectory(prefix='madeira-x18-store-') as directory:
    folder = Path(directory)
    source = folder / 'check.c'
    source.write_text(code)
    exe = folder / 'check'
    cc = os.environ.get('CC', 'cc')
    flags = [cc, '-std=gnu11', '-Wall', '-Wextra', '-Wno-unused-function', '-Wno-tautological-compare', '-Werror', '-g', str(source), '-o', str(exe)]
    sanitize = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    if subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True).returncode != 0:
        subprocess.run(flags, check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    print(out.stdout, end='')
    assert out.returncode == 0, out.stderr
