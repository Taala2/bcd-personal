#!/usr/bin/env python3
# madeira-bcd personal (2026-11-18, the owner's call): a measurement only. tests/host/vt_interp_harness.m paces its frames with
# usleep and CLOCK_MONOTONIC (sleep_until, frames()), and its cadence lines (80 FPS on a 60 Hz screen, 50 FPS) failed on all three
# tries on the macos-15 runner. This prints how much later than asked the runner's usleep wakes, alone and on the harness's
# own schedule, so that the cause can be told from the numbers. It checks nothing and always exits 0.
import ctypes
import ctypes.util
import os
import statistics
import time

libc = ctypes.CDLL(ctypes.util.find_library("c"))
libc.usleep.argtypes = [ctypes.c_uint]

REPS = 100
SLEEPS_US = (1000, 5000, 12000, 33000)   # 12 ms is the harness's 80 FPS frame, 33 ms its 30 FPS one
# frames(layer, q, n, ms, 0) at 80 FPS (phase 2, a 60 Hz screen) and 50 FPS (phase 3, 120 Hz), each with the interval past
# which the frame no longer fits its expectation: one 60 Hz frame, and three 120 Hz frames
SCHEDULES = ((12, 1000.0 / 60, "one 60 Hz frame"), (20, 3000.0 / 120, "three 120 Hz frames"))
SCHEDULE_FRAMES = 18                     # phase 2 runs 10 + 8 frames at 12 ms


def now_s() -> float:
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def summary(values_ms: list) -> str:
    q = statistics.quantiles(values_ms, n=10)
    return (f"median {statistics.median(values_ms):.2f}  p90 {q[-1]:.2f}  max {max(values_ms):.2f}  "
            f"min {min(values_ms):.2f} ms")


def single_sleeps() -> None:
    for us in SLEEPS_US:
        over = []
        for _ in range(REPS):
            t = now_s()
            libc.usleep(us)
            over.append((now_s() - t) * 1000 - us / 1000)
        print(f"usleep({us:>5}) x{REPS}: overshoot {summary(over)}")


def schedule(ms: int) -> list:
    # the harness's frames(): sleep until t0 + (i + 1) * ms, then the frame
    t0 = now_s()
    stamps = []
    for i in range(SCHEDULE_FRAMES):
        d = t0 + (i + 1) * ms / 1000.0 - now_s()
        if d > 0:
            libc.usleep(int(d * 1e6))
        stamps.append(now_s())
    return [(b - a) * 1000 for a, b in zip(stamps, stamps[1:])]


def main() -> None:
    single_sleeps()
    for ms, limit_ms, what in SCHEDULES:
        for run in range(1, 4):
            iv = schedule(ms)
            slow = sum(1 for v in iv if v > limit_ms)
            print(f"schedule {ms} ms, run {run}: intervals {summary(iv)}; {slow} of {len(iv)} longer than {what} "
                  f"({limit_ms:.1f} ms)")
    try:
        print(f"cpus {os.cpu_count()}, load {os.getloadavg()}")
    except OSError as e:
        print(f"load average not readable: {e}")


if __name__ == "__main__":
    main()
