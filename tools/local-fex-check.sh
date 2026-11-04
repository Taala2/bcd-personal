#!/usr/bin/env bash
# madeira-bcd personal: build the patched FEX ARM64EC module (xtajit64.dll) on this machine, without a Mac.
#
# Applies the same overlays as tools/build-xtajit64.sh, in the same order, read from that script each run (the
# pin branch it would take, then the lines it runs for whichever pin), compiles the arm64ecfex target with the CI
# flags, and puts the FEX tree back as the CI script does. Nothing is copied into app/Madeira: the shipped DLL is
# never touched. A compile error shows here in minutes instead of after a CI run.
#
#   tools/local-fex-check.sh            overlays + build
#   tools/local-fex-check.sh --tests    then the host checks of the FEX overlays (tests/host/check-fex-*.py and the
#                                       other checks that read build-xtajit64.sh), on the restored tree
#   tools/local-fex-check.sh --keep     leave the overlays applied (to read the patched sources)
#
# Needs (docs/personal/LOCAL-ENV.md): toolchains/llvm-mingw-*-ucrt-x86_64, toolchains/cmake-*/, toolchains/ninja,
# toolchains/bin/python3.cmd (FEX's CMake runs "python3"), the FEX submodule with its build dependencies.
# Git Bash on Windows, or any bash.
set -u
R="$(cd "$(dirname "$0")/.." && pwd)"
cd "$R"
TESTS=0; KEEP=0
for a in "$@"; do
    case "$a" in
        --tests) TESTS=1 ;;
        --keep) KEEP=1 ;;
        *) echo "unknown option $a"; exit 2 ;;
    esac
done

T="$R/toolchains"
MINGW="${MINGW:-$(ls -d "$T"/llvm-mingw-*-ucrt-x86_64/bin 2>/dev/null | head -1)}"
CMAKE_DIR="$(ls -d "$T"/cmake-*/bin 2>/dev/null | head -1)"
{ [ -n "$MINGW" ] && { [ -x "$MINGW/clang" ] || [ -x "$MINGW/clang.exe" ]; }; } \
    || { echo "no llvm-mingw in $T (docs/personal/LOCAL-ENV.md)"; exit 1; }
[ -f FEX/Source/Windows/ARM64EC/Module.cpp ] || { echo "no FEX submodule (docs/personal/LOCAL-ENV.md)"; exit 1; }
ORIG_PATH="$PATH"
export PATH="$T/bin:$MINGW:${CMAKE_DIR:+$CMAKE_DIR:}$T/ninja:$PATH"
command -v cmake >/dev/null && command -v ninja >/dev/null || { echo "cmake or ninja missing (docs/personal/LOCAL-ENV.md)"; exit 1; }
# build-xtajit64.sh's lines call python3; Windows has python.exe (CMake finds toolchains/bin/python3.cmd itself)
command -v python3 >/dev/null 2>&1 && python3 --version >/dev/null 2>&1 || python3() { python "$@"; }
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1   # several checks read sources with the default encoding (cp1251 here)
B="${FEX_ARM64EC_BUILD:-$R/FEX/build-arm64ec}"
CPUF="Source/Windows/Common/CPUFeatures.cpp"
JOBS="$(nproc 2>/dev/null || echo 4)"
S="$R/tools/build-xtajit64.sh"

# The overlay lines build-xtajit64.sh would run on this pin, and its restore lines.
mapfile -t STEPS < <(python - "$S" "$R/FEX/Source/Windows/ARM64EC/Module.cpp" <<'PY'
import sys
s = open(sys.argv[1], encoding='utf-8').read()
pinned = 'ec-ret-direct-v1 madeira-bcd' in open(sys.argv[2], encoding='utf-8', errors='replace').read()
gate = s.index('if grep -q "ec-ret-direct-v1 madeira-bcd"')
else_at = s.index('\nelse\n', gate)
fi_at = s.index('\nfi\n', else_at)
build_at = s.index('\nbuild\n', fi_at)
branch = s[gate:else_at] if pinned else s[else_at:fi_at]
for l in (branch + s[fi_at:build_at]).splitlines():
    l = l.strip()
    if l.startswith('python3 "$R/tools/patch-'):
        print('APPLY\t' + l)
for l in s[build_at:].splitlines():
    l = l.strip()
    if l.startswith('git -C FEX checkout -- ') or l.startswith('git -C FEX/External/rpmalloc checkout -- '):
        print('RESTORE\t' + l)
PY
)
APPLY=(); RESTORE=()
for st in "${STEPS[@]}"; do
    st="${st%$'\r'}"
    case "$st" in
        APPLY$'\t'*) APPLY+=("${st#APPLY$'\t'}") ;;
        RESTORE$'\t'*) RESTORE+=("${st#RESTORE$'\t'}") ;;
    esac
done
[ "${#APPLY[@]}" -gt 0 ] && [ "${#RESTORE[@]}" -gt 0 ] || { echo "could not read the overlay steps from $S"; exit 1; }

restore() {
    for l in "${RESTORE[@]}"; do eval "$l" 2>/dev/null; done
    git -C FEX diff --name-only | while read -r f; do git -C FEX checkout -- "$f"; done
}
[ "$KEEP" = 1 ] || trap restore EXIT

echo "=== FEX $(git -C FEX rev-parse --short HEAD): ${#APPLY[@]} overlays from build-xtajit64.sh ==="
git -C FEX diff --name-only | while read -r f; do git -C FEX checkout -- "$f"; done
fail=0
for l in "${APPLY[@]}"; do
    out="$(eval "$l" 2>&1)"; rc=$?
    name="$(printf '%s' "$l" | sed -E 's#.*tools/(patch-[^" ]*).*#\1#')"
    if [ $rc -ne 0 ] || printf '%s' "$out" | grep -q '::warning::'; then
        echo "  FAIL $name: $(printf '%s' "$out" | tail -1)"; fail=1
    else
        echo "  ok   $name"
    fi
done
[ $fail = 0 ] || { echo "an overlay did not apply; stopping before the build"; exit 1; }

if [ ! -f "$B/build.ninja" ]; then
    echo "=== configuring $B (once) ==="
    cmake -S "$R/FEX" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_TOOLCHAIN_FILE="$R/FEX/Data/CMake/toolchain_mingw.cmake" \
        -DMINGW_TRIPLE=arm64ec-w64-mingw32 -DTUNE_CPU=cortex-a78 \
        -DFEX_IOS_HOST_BUILD=ON \
        -DCMAKE_C_FLAGS=-DFEX_IOS_HOST=1 -DCMAKE_CXX_FLAGS=-DFEX_IOS_HOST=1 \
        -DCMAKE_ASM_FLAGS=-DFEX_IOS_HOST=1 \
        -DENABLE_LTO=OFF -DENABLE_FEX_ALLOCATOR=ON -DENABLE_JEMALLOC_GLIBC_ALLOC=ON \
        -DBUILD_FEXCONFIG=OFF -DENABLE_CCACHE=OFF -DBUILD_TESTING=OFF -DBUILD_THUNKS=OFF \
        -DENABLE_ASSERTIONS=OFF > "$B.cfg.log" 2>&1 || { tail -30 "$B.cfg.log"; exit 1; }
fi

echo "=== building arm64ecfex (-j$JOBS; log $B.build.log) ==="
start=$(date +%s)
if ! cmake --build "$B" --target arm64ecfex -j"$JOBS" > "$B.build.log" 2>&1; then
    grep -E -m 30 "error|FAILED" "$B.build.log"
    echo "BUILD FAILED after $(( $(date +%s) - start )) s"
    exit 1
fi
echo "  built $B/Bin/libarm64ecfex.dll in $(( $(date +%s) - start )) s"

if [ "$TESTS" = 1 ]; then
    # the checks apply each overlay to the pinned source themselves, so they read the restored tree
    restore; trap - EXIT
    echo "=== host checks of the FEX overlays (on the restored FEX tree) ==="
    # A check that compiles a POSIX fixture (setenv, sys/mman.h, dprintf, ASan) or pipes into tar cannot run on
    # Windows: it is listed as POSIX and left to CI (or WSL), not counted as a failure of the change. llvm-mingw's
    # cc stays on PATH: the fixtures that need nothing POSIX build and pass with it. Two checks fail here for a
    # reason of this host only, named below; on any other result they count as failures.
    posix='sys/mman\.h|undeclared (identifier|function) .(setenv|unsetenv|dprintf|posix_memalign)|posix_memalign|tape0|WinError 2'
    declare -A WINONLY=(
        [tests/host/check-fex-arena-census.py]="its fixture prints addresses as long, 32 bits on Windows (LLP64): AssertionError on failed_range"
        [tests/host/check-fex-sha-off.py]="it compares source paths with '/', Windows gives '\\'"
    )
    winsig='AssertionError|app\\\\Madeira'
    tfail=0; nposix=0
    for t in $( (ls tests/host/check-fex-*.py; git grep -l -E 'build-xtajit64\.sh' -- 'tests/host/check-*.py') | sort -u); do
        log="$B.$(basename "$t" .py).log"
        if python "$t" > "$log" 2>&1; then echo "  ok    $t"
        elif [ -n "${WINONLY[$t]:-}" ] && grep -qE "$winsig" "$log"; then echo "  WIN   $t (${WINONLY[$t]})"; nposix=$((nposix + 1))
        elif grep -qE "$posix" "$log"; then echo "  POSIX $t (needs a POSIX host: CI or WSL)"; nposix=$((nposix + 1))
        else echo "  FAIL  $t (log $log)"; tfail=1; fi
    done
    [ $nposix = 0 ] || echo "  $nposix check(s) cannot pass on this host (POSIX/WIN above): CI runs them"
    [ $tfail = 0 ] || exit 1
fi
echo "PASS"
