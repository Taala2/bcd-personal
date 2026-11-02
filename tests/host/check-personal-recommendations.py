#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""madeira-bcd personal: recommended/personal-recommendations.json, the fork's own rules.

GameRecommendations.match looks at this list before the list in use (the built-in one or BCD's
signed download), so a game family this fork fixed gets its lines without a setting typed by
hand, while the downloads keep coming in. The first rule: NW.js games (Game.exe beside nw.dll),
with the lines Internship [demo] ran with on build 627 (iPhone16,1, 2026-11-02), and without
upstream's MADEIRA_CHILD_ARGS, which reaches --type=crashpad-handler children.

Checks:
  - the file: strict JSON (no key twice), format 1, a version, a date, rule ids "personal-...",
    no standard rule, every rule with a match; each line a comment or "key = value" whose key is
    a game setting of the settings catalog (env.* lines too), never MADEIRA_CHILD_ARGS;
  - the Swift: match asks the personal list first, reload forgets it, the main list's reader still
    requires its standard rule, the Xcode project bundles the file, CI runs this check;
  - the Swift fixture (macOS CI, --require-swift): the app's reader and match, compiled with stubs:
    without the file a Game.exe beside nw.dll gets upstream's nwjs-game-exe; with it the personal
    rule, and only beside nw.dll; other games get what the list in use gives; a personal file with
    a standard rule, or one that does not read, is not used.
No device is run.
"""
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
APP = R / 'app' / 'Madeira'
PERSONAL = R / 'recommended' / 'personal-recommendations.json'
MAIN_LIST = R / 'recommended' / 'recommendations.json'

parser = argparse.ArgumentParser()
parser.add_argument('--require-swift', action='store_true')
args = parser.parse_args()

ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('  [' + detail + ']') if detail and not cond else ''))
    ok = ok and bool(cond)


def strict(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('a key twice in an object: %r' % keys)
    return dict(pairs)


# ---- the file -----------------------------------------------------------------------------------
doc = json.loads(PERSONAL.read_text(encoding='utf-8'), object_pairs_hook=strict)
check('format 1, a version, a date, rules', doc.get('format') == 1 and isinstance(doc.get('version'), int)
      and doc['version'] >= 1 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', doc.get('generated', '')) is not None
      and isinstance(doc.get('rules'), list) and len(doc['rules']) >= 1)
check('no standard rule (every other game keeps the list in use)', all(r['id'] != 'standard' for r in doc['rules']))
check('rule ids are "personal-..." and come once', all(r['id'].startswith('personal-') for r in doc['rules'])
      and len({r['id'] for r in doc['rules']}) == len(doc['rules']))
check('every rule has a match', all(r.get('match') for r in doc['rules']))
main_ids = {r['id'] for r in json.loads(MAIN_LIST.read_text(encoding='utf-8'))['rules']}
check('no personal id is an id of the main list', not ({r['id'] for r in doc['rules']} & main_ids))

catalog_src = (APP / 'ConfigCatalog.generated.swift').read_text(encoding='utf-8')
catalog = dict(re.findall(r'ConfigOption\(key: "([^"]+)", title: "(?:[^"\\]|\\.)*", kind: \.\w+, '
                          r'defaultValue: "(?:[^"\\]|\\.)*", category: "([^"]+)"', catalog_src))
check('the settings catalog reads (%d keys)' % len(catalog), len(catalog) > 500)
lines = []
for r in doc['rules']:
    for line in r['config'] + r.get('smallRAMConfig', []):
        lines.append((r['id'], line['line'] if isinstance(line, dict) else line))
bad = []
for rid, text in lines:
    if text.startswith('#'):
        continue
    m = re.fullmatch(r'([A-Za-z0-9_.-]+) = (.+)', text)
    if not m or text.strip() != text or len(text) > 1024:
        bad.append('%s: %r is not "key = value"' % (rid, text))
    elif m.group(1).startswith('env.RENPY_'):
        continue   # Ren'Py's own variables (the game reads them, BCD does not)
    elif m.group(1) not in catalog or catalog[m.group(1)] == 'App & front end':
        bad.append('%s: %s is not a game setting of the catalog' % (rid, m.group(1)))
check('every line is a comment, a catalog game setting or a Ren\'Py variable', not bad, '; '.join(bad))
renpy = next((r for r in doc['rules'] if r['id'] == 'personal-renpy'), None)
check('personal-renpy: any program beside the renpy folder skips the presplash and draws through ANGLE',
      renpy is not None and renpy['match'] == {'programs': [{'name': '*.exe', 'beside': 'renpy'}]}
      and 'env.RENPY_LESS_UPDATES = 1' in renpy['config'] and 'env.RENPY_RENDERER = angle2' in renpy['config'])
check('no rule sets MADEIRA_CHILD_ARGS (it reaches --type=crashpad-handler children)',
      not any('MADEIRA_CHILD_ARGS' in text for _, text in lines))
nw = next((r for r in doc['rules'] if r['id'] == 'personal-nwjs-game-exe'), None)
want = ['env.MADEIRA_TLS_CLEAR_OWN_PROCESS = 1', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'env.MADEIRA_X64_NOCOPY_DATAIMP = 1',
        'env.MADEIRA_ANON_LDADDAL64 = 1', 'env.MADEIRA_WASM_GUARD_TRIM = 1', 'env.MADEIRA_THREAD_STACK_SPILL = 1',
        'pool-split = 1', 'pool-low = 1']
check('personal-nwjs-game-exe: Game.exe beside nw.dll or nw_elf.dll, with the lines Internship ran with',
      nw is not None and nw['match'] == {'programs': [{'name': 'game.exe', 'beside': 'nw_elf.dll'},
                                                      {'name': 'game.exe', 'beside': 'nw.dll'}]}
      and all(w in nw['config'] for w in want))
anynw = next((r for r in doc['rules'] if r['id'] == 'personal-nwjs-any'), None)
check('personal-nwjs-any: any program beside nw.dll or nw_elf.dll, the same lines',
      anynw is not None and nw is not None and anynw['config'][2:] == nw['config'][2:]
      and anynw['match'] == {'programs': [{'name': '*.exe', 'beside': 'nw_elf.dll'}, {'name': '*.exe', 'beside': 'nw.dll'}]})
engine = ['env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'env.MADEIRA_X64_NOCOPY_DATAIMP = 1', 'pool-split = 1', 'pool-low = 1']
for rid, beside in (('personal-cef', 'libcef.dll'), ('personal-electron', 'chrome_100_percent.pak')):
    r = next((r for r in doc['rules'] if r['id'] == rid), None)
    check('%s: any program beside %s, the pool lines' % (rid, beside),
          r is not None and [p['beside'] for p in r['match']['programs']] == [beside] and all(w in r['config'] for w in engine))
wild = [p for r in doc['rules'] for p in r['match'].get('programs', []) if p['name'] == '*.exe']
check('every "*.exe" program names its engine file (beside)', bool(wild) and all(p.get('beside') for p in wild))
order = [r['id'] for r in doc['rules']]
check('NW.js and CEF come before Electron (their folders may hold chrome_100_percent.pak too)',
      order.index('personal-nwjs-any') < order.index('personal-electron') and order.index('personal-cef') < order.index('personal-electron'))

# ---- the Swift ----------------------------------------------------------------------------------
lists = (APP / 'GameRecommendations.swift').read_text(encoding='utf-8')
listing = (APP / 'GameRecommendationsList.swift').read_text(encoding='utf-8')
check('match asks the personal list first, then the list in use, then the personal engine rules',
      'personal.match(windowsPath: windowsPath, steamAppID: steamAppID, fileExists: fileExists)\n'
      '            ?? active.match(windowsPath: windowsPath, steamAppID: steamAppID, fileExists: fileExists)\n'
      '            ?? personalEngineMatch(windowsPath: windowsPath, fileExists: fileExists)' in lists
      and 'for program in match.programs where program.name == "*.exe" {' in lists
      and r'guard let beside = program.beside, fileExists(folder + "\\" + beside) else { continue }' in lists)
bridge = (APP / 'WineProcessBridge.m').read_text(encoding='utf-8')
at = bridge.index('static const char *const personal_defaults[] = {')
check('the bridge turns the three switches on for every game after its own lines, never over a line or a lock',
      bridge.index('LOG("game config env: %{public}s=%{public}s"') < at < bridge.index('MADEIRA_STEAM_IDENTITY = none from the')
      and '"MADEIRA_TLS_CLEAR_OWN_PROCESS", "MADEIRA_ANON_LDADDAL64", "MADEIRA_WASM_GUARD_TRIM",\n                    };' in bridge
      and 'if (getenv(name) || madeira_env_is_locked(name)) continue;\n                        setenv(name, "1", 1);' in bridge
      and '[personal-default] %s=1' in bridge)
check('the personal list is the bundle resource, read without a standard rule and refused with one',
      'Bundle.main.url(forResource: "personal-recommendations", withExtension: "json")' in lists
      and 'RecommendationsFile.parse(data, requireStandard: false)' in lists
      and 'file.rules.contains(where: { $0.id == standardID })' in lists)
reload = lists[lists.index('    static func reload() {'):]
reload = reload[:reload.index('\n    }\n')]
check('reload forgets the personal list too', 'personalLoaded = nil' in reload and 'loaded = nil' in reload)
check('the main list still needs its standard rule (parse and checkLayout default to it)',
      'static func parse(_ data: Data, requireStandard: Bool = true)' in listing
      and 'func checkLayout(requireStandard: Bool = true) throws' in listing
      and 'try file.checkLayout(requireStandard: requireStandard)' in listing
      and 'guard !requireStandard || ids.contains(GameRecommendations.standardID)' in listing)
pbx = (R / 'app' / 'Madeira.xcodeproj' / 'project.pbxproj').read_text(encoding='utf-8')
check('the Xcode project bundles the file (A1000BA3/A2000BA3, ../recommended from app/)',
      pbx.count('A1000BA3 /* personal-recommendations.json in Resources */') == 2
      and pbx.count('A2000BA3 /* personal-recommendations.json */') == 3
      and 'path = ../recommended/personal-recommendations.json; sourceTree = SOURCE_ROOT;' in pbx)
wf = (R / '.github' / 'workflows' / 'build-ipa.yml').read_text(encoding='utf-8')
check('CI runs this check with the Swift fixture', 'run: python3 tests/host/check-personal-recommendations.py --require-swift' in wf)

# ---- the Swift fixture --------------------------------------------------------------------------
STUBS = r'''
import Foundation

final class LogStore {
    static let shared = LogStore()
    enum Level { case info, error }
    var lines: [(String, Level)] = []
    func log(_ message: String, level: Level = .info) { lines.append((message, level)) }
}

struct ConfigOption {
    enum Kind { case bool, int, text, choice }
    let key: String
    let category: String
    init(key: String, title: String, kind: Kind, defaultValue: String, category: String, note: String,
         choices: [(String, String)], sources: [String]) {
        self.key = key
        self.category = category
    }
}

enum ConfigCatalog {}
'''

MAIN = r'''
import Foundation

var failures = 0
func check(_ ok: Bool, _ what: String, line: Int = #line) {
    if !ok { print("FAILED (main.swift line \(line)): \(what)"); failures += 1 }
}
let env = ProcessInfo.processInfo.environment
let work = URL(fileURLWithPath: env["BCD_RECS_WORK"]!, isDirectory: true)
RecommendationsStore.builtInURL = URL(fileURLWithPath: env["BCD_RECS_JSON"]!)
RecommendationsStore.directory = work.appendingPathComponent("support", isDirectory: true)
RecommendationsStore.build = 627
let G = GameRecommendations.self
let personalURL = URL(fileURLWithPath: env["BCD_PERSONAL_JSON"]!)
let nwjs = #"C:\Games\Internship[demo]\Game.exe"#
let others = [#"C:\Games\RDR2\RDR2.exe"#, #"C:\Games\Foo\Game.exe"#, #"C:\Games\CrossCode\crosscode.exe"#, #"C:\Games\Foo\foo.exe"#]
func besideNW(_ path: String) -> Bool { path.lowercased().hasSuffix(#"\nw.dll"#) }
func nothing(_ path: String) -> Bool { false }
let crosscode = #"C:\Games\CrossCode\crosscode.exe"#
let foo = #"C:\Games\Foo\foo.exe"#
func besideCEF(_ path: String) -> Bool { path.lowercased().hasSuffix(#"\libcef.dll"#) }
func besideElectron(_ path: String) -> Bool { path.lowercased().hasSuffix(#"\chrome_100_percent.pak"#) }

// Without the file (an app built without it, as every host test): upstream's rule.
GameRecommendations.personalURL = nil
G.reload()
check(G.personal.rules.isEmpty, "no file, no personal rules")
check(G.match(windowsPath: nwjs, steamAppID: nil, fileExists: besideNW)?.id == "nwjs-game-exe", "without it: upstream's nwjs-game-exe")
let before = others.map { G.match(windowsPath: $0, steamAppID: nil, fileExists: nothing) }
let crosscodeBefore = G.match(windowsPath: crosscode, steamAppID: nil, fileExists: besideNW)
check(crosscodeBefore?.id == "crosscode", "without it: CrossCode's own rule")
check(G.match(windowsPath: foo, steamAppID: nil, fileExists: besideCEF) == nil, "without it: nothing for a CEF program")

// With it.
GameRecommendations.personalURL = personalURL
G.reload()
check(G.personal.rules.count >= 1, "the personal list reads")
let rec = G.match(windowsPath: nwjs, steamAppID: nil, fileExists: besideNW)
check(rec?.id == "personal-nwjs-game-exe", "a Game.exe beside nw.dll gets the personal rule")
check(rec?.config.contains("env.MADEIRA_WASM_GUARD_TRIM = 1") == true && rec?.config.contains("pool-low = 1") == true
      && rec?.config.contains("MADEIRA_CHILD_ARGS") == false, "its lines, without CHILD_ARGS")
check(rec?.config.contains("env.MADEIRA_GUEST_LOG = all") == true, "a line from build 598 on reaches build 627")
check(G.match(windowsPath: nwjs, steamAppID: nil, fileExists: nothing)?.id != "personal-nwjs-game-exe", "not without nw.dll")
let after = others.map { G.match(windowsPath: $0, steamAppID: nil, fileExists: nothing) }
check(before == after, "programs without an engine file get what the list in use gives")
check(G.match(windowsPath: crosscode, steamAppID: nil, fileExists: besideNW) == crosscodeBefore, "a game with a rule in the list in use keeps it")
check(G.match(windowsPath: foo, steamAppID: nil, fileExists: besideNW)?.id == "personal-nwjs-any", "another NW.js program")
check(G.match(windowsPath: foo, steamAppID: nil, fileExists: besideCEF)?.id == "personal-cef", "a CEF program")
check(G.match(windowsPath: foo, steamAppID: nil, fileExists: besideElectron)?.id == "personal-electron", "an Electron program")
check(G.match(windowsPath: foo, steamAppID: nil, fileExists: { besideCEF($0) || besideElectron($0) })?.id == "personal-cef",
      "a CEF folder with chrome_100_percent.pak stays CEF")
check(G.match(windowsPath: #"C:\Games\Foo"#, steamAppID: nil, fileExists: besideNW)?.id.hasPrefix("personal-") != true, "a folder is not a program")
check(G.match(windowsPath: #"C:\Games\LogoutDenied-v0.6.Public-pc\LogoutDenied.exe"#, steamAppID: nil,
              fileExists: { $0.lowercased().hasSuffix(#"\renpy"#) })?.id == "personal-renpy", "a Ren'Py program beside its renpy folder")
check(G.active.rules.contains { $0.recommendation.id == "nwjs-game-exe" }, "the list in use keeps upstream's rule")
check(LogStore.shared.lines.contains { $0.0.hasPrefix("[recs] personal list v") }, "one log line says the personal list is used")

// The main list's reader still needs its standard rule.
let personalData = try! Data(contentsOf: personalURL)
check((try? RecommendationsFile.parse(personalData)) == nil, "parse without requireStandard: false refuses a list with no standard rule")

// A personal file with a standard rule, or one that does not read, is not used.
var o = try! JSONSerialization.jsonObject(with: personalData) as! [String: Any]
var rules = o["rules"] as! [[String: Any]]
rules.append(["id": "standard", "title": "Standard", "version": 1, "config": ["# nothing"]])
o["rules"] = rules
let withStandard = work.appendingPathComponent("with-standard.json")
try! JSONSerialization.data(withJSONObject: o).write(to: withStandard)
GameRecommendations.personalURL = withStandard
G.reload()
check(G.personal.rules.isEmpty && LogStore.shared.lines.contains { $0.0 == "[recs] personal list not used: it has a standard rule" && $0.1 == .error },
      "a personal standard rule is refused")
check(G.match(windowsPath: nwjs, steamAppID: nil, fileExists: besideNW)?.id == "nwjs-game-exe", "then upstream's rule again")
let broken = work.appendingPathComponent("broken.json")
try! Data("{}".utf8).write(to: broken)
GameRecommendations.personalURL = broken
G.reload()
check(G.personal.rules.isEmpty && LogStore.shared.lines.contains { $0.0.hasPrefix("[recs] personal list not used: ") && $0.1 == .error },
      "a file that does not read is not used")

if failures > 0 { exit(1) }
print("PASS: the personal list is asked first, only for its games; a bad one is not used")
'''

with tempfile.TemporaryDirectory(prefix='bcd-personal-recs-') as directory:
    tmp = Path(directory)
    compiler = shutil.which('swiftc')
    if not compiler:
        if args.require_swift:
            raise SystemExit('Swift compiler required for the personal recommendations fixture')
        print('SKIP: Swift compiler unavailable; macOS CI runs this check with --require-swift')
    else:
        (tmp / 'Stubs.swift').write_text(STUBS)
        (tmp / 'main.swift').write_text(MAIN)
        (tmp / 'work').mkdir()
        binary = tmp / 'bcd-personal-recs-check'
        built = subprocess.run([compiler] + [str(APP / n) for n in ('GameRecommendations.swift', 'GameRecommendationsList.swift',
                                                                    'GameRecommendationsSource.swift', 'ConfigCatalog.generated.swift')]
                               + [str(tmp / 'Stubs.swift'), str(tmp / 'main.swift'), '-o', str(binary)],
                               capture_output=True, text=True)
        check('the fixture compiles (the app\'s reader and match)', built.returncode == 0, built.stderr[-4000:])
        if built.returncode == 0:
            ran = subprocess.run([str(binary)], capture_output=True, text=True,
                                 env=dict(os.environ, BCD_RECS_JSON=str(MAIN_LIST), BCD_PERSONAL_JSON=str(PERSONAL),
                                          BCD_RECS_WORK=str(tmp / 'work')))
            print(ran.stdout.rstrip())
            check('the fixture passes', ran.returncode == 0, ran.stderr[-4000:])

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
