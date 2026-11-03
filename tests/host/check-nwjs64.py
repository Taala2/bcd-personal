#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""madeira-bcd personal: a 32-bit NW.js game starts on BCD's 64-bit NW.js (app/Madeira/BCDNWJS64.swift).

RPG Maker MV games on a 32-bit NW.js crash under BCD's 32-bit emulation; the same game ran on the device
with NW.js 0.48.4 win-x64 (docs/personal/FINDINGS.md, section 7). BCD keeps that NW.js under
C:\\windows\\bcd-nwjs and starts its nw.exe with the game's folder as the app.

Checks:
  - the pins: NW.js 0.48.4 win-x64 from dl.nwjs.io, its SHA-256 and size; the folder under windows\\,
    which GameLibrary.scan skips;
  - the glue: ContentView.startLibraryEntry asks BCDNWJS64Launch.plan before configureLaunch, starts the
    download and the game after it for .download, exports the command right after configureLaunch for
    .run; the installer checks size and SHA-256 before unpacking and writes the marker last; the Xcode
    project builds both files; CI runs this check;
  - the Swift fixture (macOS CI, --require-swift): BCDNWJS64.swift alone: the PE check (32-bit x86 beside
    nw.dll and package.json only), the command and its quoting, env.MADEIRA_NWJS64 = 0, the zip entry
    names it unpacks, ready() and its marker.
No device is run.
"""
from pathlib import Path
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
APP = R / 'app' / 'Madeira'

parser = argparse.ArgumentParser()
parser.add_argument('--require-swift', action='store_true')
args = parser.parse_args()

ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('  [' + detail + ']') if detail and not cond else ''))
    ok = ok and bool(cond)


pure = (APP / 'BCDNWJS64.swift').read_text(encoding='utf-8')
install = (APP / 'BCDNWJS64Install.swift').read_text(encoding='utf-8')
content = (APP / 'ContentView.swift').read_text(encoding='utf-8')
library = (APP / 'GameLibrary.swift').read_text(encoding='utf-8')
project = (R / 'app' / 'Madeira.xcodeproj' / 'project.pbxproj').read_text(encoding='utf-8')
wf = (R / '.github' / 'workflows' / 'build-ipa.yml').read_text(encoding='utf-8')

# ---- the pins -----------------------------------------------------------------------------------
check('NW.js 0.48.4', 'static let version = "0.48.4"' in pure)
check('the win-x64 archive from dl.nwjs.io',
      'static let url = URL(string: "https://dl.nwjs.io/v\\(version)/\\(folderName).zip")!' in pure
      and 'static let folderName = "nwjs-v\\(version)-win-x64"' in pure)
check('its SHA-256 (dl.nwjs.io/v0.48.4/SHASUMS256.txt) and size',
      'static let sha256 = "1731a5ed81139a63a5d1d7e7c521b5f08add023ffe4b719d06796a1ba77dc8a3"' in pure
      and 'static let bytes: Int64 = 94_821_458' in pure)
check('only NW.js\'s hosts may answer', 'static let hosts: Set<String> = ["dl.nwjs.io", "dl.node-webkit.org"]' in pure)
check('the folder is under windows\\, which the library scan skips',
      'static let relativeFolder = "windows/bcd-nwjs/" + folderName' in pure
      and re.search(r'skippedRoots: Set<String> = \[\s*"windows"', library) is not None)
check('the pure file imports Foundation only', re.findall(r'^import (\w+)', pure, re.M) == ['Foundation'])

# ---- the glue -----------------------------------------------------------------------------------
start = content.find('private func startLibraryEntry(')
body = content[start:content.find('\n    }\n', start)] if start >= 0 else ''
i_plan = body.find('let nwjs64 = BCDNWJS64Launch.plan(entry)')
i_configure = body.find('entry.configureLaunch()')
i_export = body.find('if nwjs64 == .run { BCDNWJS64Launch.export(entry) }')
check('startLibraryEntry asks the plan before configureLaunch', 0 <= i_plan < i_configure, f'{i_plan} {i_configure}')
check('the command is exported right after configureLaunch',
      i_export > i_configure and body[i_configure:i_export].count('\n') == 1)
download = body[i_plan:i_configure]
check('.download: a message, the download, then the same start again',
      'if nwjs64 == .download {' in download and 'library.error = BCDNWJS64Launch.waitMessage(received: 0)' in download
      and 'BCDNWJS64Installer.shared.start(progress: { received in' in download and 'startLibraryEntry(pending)' in download
      and re.search(r'\n            return\n        \}\n\s*$', download) is not None)
check('the message follows the download (percent and megabytes, then unpacking), only while it is the one it put up',
      'library.error = BCDNWJS64Launch.waitMessage(received: received)' in download
      and 'if library.error?.hasPrefix(BCDNWJS64Launch.waitPrefix) == true {' in download
      and 'Downloading BCD\'s 64-bit NW.js (once): \\(percent)% (\\(received / 1_000_000) of \\(total / 1_000_000) MB).' in install
      and 'Unpacking BCD\'s 64-bit NW.js (once).' in install and 'stage(nil)' in install
      and 'Task { @MainActor [weak self] in self?.stage(received) }' in install)
check('a second Play while it downloads only shows the message', 'guard !BCDNWJS64Installer.shared.busy else { return }' in download)
starter = install[install.find('func start(progress:'):install.find('private func run(drive: URL)')]
i_guard = starter.find('guard current == nil else { return }')
i_current = starter.find('current = task')
i_wait = starter.find('try await task.value')
check('the download counts as busy before the first Play returns (set synchronously, not in the task)',
      0 <= i_guard < i_current < i_wait and starter.count('Task {') == 2)
check('a failed download says so and starts nothing', 'if let failure { library.error = BCDNWJS64Launch.failedMessage(failure); return }' in download)
worker = content[content.find('DispatchQueue.global(qos: .userInitiated).async {', content.find('private func runWineFullSequence(')):]
i_env = worker.find('profile.applyEnvironment()')
i_re = worker.find('BCDNWJS64Launch.reassert(profile)')
check('the launch worker puts BCD\'s NW.js back right after applyEnvironment (which runs configureLaunch again)',
      0 <= i_env < i_re and worker[i_env:i_re].count('\n') <= 3)
check('only the start export armed is reasserted, once; other starts disarm',
      'lock.lock(); let mine = armed == entry.id; armed = nil; lock.unlock()' in install
      and 'if nwjs64 == .run { BCDNWJS64Launch.export(entry) } else { BCDNWJS64Launch.disarm() }' in body)
check('export sets the program, its arguments and the game\'s folder',
      all(s in install for s in ('setenv("MADEIRA_EXE", command.exe, 1)', 'setenv("MADEIRA_ARGS", command.args, 1)',
                                 'setenv("MADEIRA_WORKDIR", entry.launchDirectory, 1)')))
check('the plan leaves the Desktop, Steam, desktop mode, batches and services launches alone',
      'guard entry.desktop != true, entry.steamAppID == nil, !entry.runsInDesktop, !entry.isBatch,' in install
      and 'entry.servicesScript == nil else { return .none }' in install)
check('env.MADEIRA_NWJS64 = 0 (bcd.cfg, the entry\'s lines, the game\'s file) turns it off',
      '!MadeiraConfig.flag("MADEIRA_NWJS64")' in install and 'BCDNWJS64.turnedOff(entry.config ?? "")' in install
      and 'BCDNWJS64.turnedOff(GameProfile(windowsPath: entry.windowsPath).text)' in install)
check('a failed download leaves this run on the games\' own NW.js',
      'failedThisRun = true' in install and 'return BCDNWJS64Installer.shared.failedThisRun ? .none : .download' in install)
i_size = install.find('guard size == BCDNWJS64.bytes')
i_sha = install.find('BCDGeckoFiles.sha256(of: zip) == BCDNWJS64.sha256')
i_unpack = install.find('BCDNWJS64Installer.unpack(zip: zip, drive: drive)')
check('size and SHA-256 are checked before anything is unpacked', 0 <= i_size < i_sha < i_unpack)
check('the download goes through BCD\'s downloader with the pinned hosts',
      'BCDLauncherDownloader(url: BCDNWJS64.url, hosts: BCDNWJS64.hosts' in install
      and 'BCDLauncherNet.isAllowed(BCDNWJS64.url, hosts: BCDNWJS64.hosts)' in install)
i_move = install.find('try fm.moveItem(at: staging, to: final)')
i_marker = install.find('try BCDNWJS64.sha256.write(to: final.appendingPathComponent(BCDNWJS64.marker)')
check('files go to a staging folder, then in place, the marker last', 0 <= i_move < i_marker
      and 'guard let path = BCDNWJS64.unpackedPath(entry.name) else { continue }' in install)

# ---- the project and CI -------------------------------------------------------------------------
for name in ('BCDNWJS64.swift', 'BCDNWJS64Install.swift'):
    check(f'the Xcode project builds {name}',
          project.count(f'/* {name} in Sources */') == 2 and project.count(f'/* {name} */ = {{isa = PBXFileReference;') == 1)
check('CI runs this check with the Swift fixture', 'run: python3 tests/host/check-nwjs64.py --require-swift' in wf)

# ---- the Swift fixture --------------------------------------------------------------------------
MAIN = r'''
import Foundation

var failures = 0
func check(_ cond: Bool, _ what: String) {
    print((cond ? "ok   " : "FAIL ") + what)
    if !cond { failures += 1 }
}

let work = URL(fileURLWithPath: ProcessInfo.processInfo.environment["BCD_NWJS_WORK"]!)
let fm = FileManager.default

func pe(_ machine: UInt16) -> Data {
    var b = [UInt8](repeating: 0, count: 0x200)
    b[0] = 0x4d; b[1] = 0x5a; b[0x3c] = 0x80
    b[0x80] = 0x50; b[0x81] = 0x45
    b[0x84] = UInt8(machine & 0xff); b[0x85] = UInt8(machine >> 8)
    return Data(b)
}
func game(_ name: String, machine: UInt16?, nwdll: Bool = true, package: Bool = true) -> URL {
    let dir = work.appendingPathComponent(name, isDirectory: true)
    try! fm.createDirectory(at: dir, withIntermediateDirectories: true)
    let exe = dir.appendingPathComponent("Game.exe")
    try! (machine.map(pe) ?? Data("not a program".utf8)).write(to: exe)
    if nwdll { try! Data().write(to: dir.appendingPathComponent("nw.dll")) }
    if package { try! Data("{}".utf8).write(to: dir.appendingPathComponent("package.json")) }
    return exe
}

check(BCDNWJS64.peMachine(game("a", machine: 0x14c)) == 0x14c, "the PE check reads Machine 0x14c")
check(BCDNWJS64.isNWJS32(program: game("mv", machine: 0x14c)), "a 32-bit Game.exe beside nw.dll and package.json is one")
check(!BCDNWJS64.isNWJS32(program: game("mz", machine: 0x8664)), "a 64-bit one is not (it already runs)")
check(!BCDNWJS64.isNWJS32(program: game("nodll", machine: 0x14c, nwdll: false)), "without nw.dll it is not")
check(!BCDNWJS64.isNWJS32(program: game("nopkg", machine: 0x14c, package: false)), "without package.json it is not")
check(!BCDNWJS64.isNWJS32(program: game("text", machine: nil)), "a file that is not a program is not")
check(!BCDNWJS64.isNWJS32(program: work.appendingPathComponent("missing/Game.exe")), "a missing program is not")

let c = BCDNWJS64.command(gameFolder: "C:\\Games\\Way of Corruption 0.33 A\\Way of Corruption 0.33 A", arguments: "")
check(c.exe == "C:\\windows\\bcd-nwjs\\nwjs-v0.48.4-win-x64\\nw.exe", "nw.exe from BCD's folder starts")
check(c.args == "\"C:\\Games\\Way of Corruption 0.33 A\\Way of Corruption 0.33 A\"", "the game's folder, quoted, is the app")
check(BCDNWJS64.command(gameFolder: "C:\\Games\\X\\", arguments: " --enable-logging ").args == "\"C:\\Games\\X\" --enable-logging",
      "no backslash before the closing quote; the game's own arguments follow")

check(BCDNWJS64.turnedOff("env.MADEIRA_NWJS64 = 0"), "env.MADEIRA_NWJS64 = 0 turns it off")
check(!BCDNWJS64.turnedOff("env.MADEIRA_NWJS64 = 1\npool-low = 1"), "= 1 does not")
check(!BCDNWJS64.turnedOff("# env.MADEIRA_NWJS64 = 0"), "a comment does not")
check(!BCDNWJS64.turnedOff("env.MADEIRA_NWJS64 = 0\nenv.MADEIRA_NWJS64 = 1"), "the last line wins")
check(!BCDNWJS64.turnedOff(""), "nothing set: on")

check(BCDNWJS64.unpackedPath("nwjs-v0.48.4-win-x64/nw.exe") == "nw.exe", "a top-level file")
check(BCDNWJS64.unpackedPath("nwjs-v0.48.4-win-x64/locales/en-US.pak") == "locales/en-US.pak", "a file in a subfolder")
check(BCDNWJS64.unpackedPath("nwjs-v0.48.4-win-x64/") == nil, "the top folder entry is skipped")
check(BCDNWJS64.unpackedPath("nwjs-v0.48.4-win-x64/locales/") == nil, "a folder entry is skipped")
check(BCDNWJS64.unpackedPath("nwjs-v0.48.4-win-x64/../evil.dll") == nil, "a name that leaves the folder is refused")
check(BCDNWJS64.unpackedPath("other/nw.exe") == nil, "a file outside the top folder is refused")
check(BCDNWJS64.unpackedPath("nwjs-v0.48.4-win-x64/C:/x.dll") == nil, "a drive letter is refused")

let drive = work.appendingPathComponent("drive_c", isDirectory: true)
let folder = BCDNWJS64.folder(drive: drive)
check(folder.path.hasSuffix("/drive_c/windows/bcd-nwjs/nwjs-v0.48.4-win-x64"), "the folder is drive_c/windows/bcd-nwjs/...")
check(!BCDNWJS64.ready(drive: drive), "nothing there: not ready")
try! fm.createDirectory(at: folder, withIntermediateDirectories: true)
try! Data().write(to: folder.appendingPathComponent("nw.exe"))
try! Data().write(to: folder.appendingPathComponent("nw.dll"))
check(!BCDNWJS64.ready(drive: drive), "files without the marker: not ready (an unpacking that stopped)")
try! "0000".write(to: folder.appendingPathComponent(BCDNWJS64.marker), atomically: true, encoding: .utf8)
check(!BCDNWJS64.ready(drive: drive), "a marker with another archive's SHA-256: not ready")
try! BCDNWJS64.sha256.write(to: folder.appendingPathComponent(BCDNWJS64.marker), atomically: true, encoding: .utf8)
check(BCDNWJS64.ready(drive: drive), "the files and this archive's marker: ready")

if failures > 0 { exit(1) }
print("PASS: 32-bit NW.js games are told apart, nw.exe gets the game's folder, the archive is unpacked inside its folder")
'''

with tempfile.TemporaryDirectory(prefix='bcd-nwjs64-') as directory:
    tmp = Path(directory)
    compiler = shutil.which('swiftc')
    if not compiler:
        if args.require_swift:
            raise SystemExit('Swift compiler required for the NW.js 64-bit fixture')
        print('SKIP: Swift compiler unavailable; macOS CI runs this check with --require-swift')
    else:
        (tmp / 'main.swift').write_text(MAIN)
        (tmp / 'work').mkdir()
        binary = tmp / 'bcd-nwjs64-check'
        built = subprocess.run([compiler, str(APP / 'BCDNWJS64.swift'), str(tmp / 'main.swift'), '-o', str(binary)],
                               capture_output=True, text=True)
        check('the fixture compiles (BCDNWJS64.swift alone)', built.returncode == 0, built.stderr[-4000:])
        if built.returncode == 0:
            ran = subprocess.run([str(binary)], capture_output=True, text=True,
                                 env=dict(os.environ, BCD_NWJS_WORK=str(tmp / 'work')))
            print(ran.stdout.rstrip())
            check('the fixture passes', ran.returncode == 0, ran.stderr[-4000:])

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
