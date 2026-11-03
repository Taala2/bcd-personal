// SPDX-License-Identifier: GPL-3.0-only
//
// madeira-bcd personal (2026-11-03): a 32-bit NW.js game runs on BCD's own 64-bit NW.js.
//
// RPG Maker MV ships its games on a 32-bit NW.js. Under BCD's 32-bit emulation their V8 renderer dies
// layer after layer (docs/personal/FINDINGS.md, section 7: anonymous-alias stores, unordered vector
// copies, the deoptimizer's frame check, then data corruption no switch removed), while a 64-bit
// NW.js runs (Internship [demo], RPG Maker MZ). On the device, Way of Corruption 0.33 A ran
// with its NW.js files replaced by NW.js 0.48.4 win-x64 and nothing else changed (2026-11-03 16:50:
// Machine=0x8664, no renderer crash). BCD does the same without touching the game: it keeps that
// NW.js under C:\windows\bcd-nwjs (the library scan skips windows\) and starts its nw.exe with the
// game's folder, where package.json and the game's files are, as the app.
//
// Foundation only: tests/host/check-nwjs64.py compiles this file alone. The download and the launch
// glue are in BCDNWJS64Install.swift. Log tag: [nwjs64]. Off: env.MADEIRA_NWJS64 = 0 in bcd.cfg or in
// the game's own lines.

import Foundation

enum BCDNWJS64 {
    // MARK: What is downloaded (dl.nwjs.io/v0.48.4/SHASUMS256.txt)

    static let version = "0.48.4"
    static let folderName = "nwjs-v\(version)-win-x64"
    static let url = URL(string: "https://dl.nwjs.io/v\(version)/\(folderName).zip")!
    static let sha256 = "1731a5ed81139a63a5d1d7e7c521b5f08add023ffe4b719d06796a1ba77dc8a3"
    static let bytes: Int64 = 94_821_458
    /// NW.js's download host and the one it redirects to; no other host may answer.
    static let hosts: Set<String> = ["dl.nwjs.io", "dl.node-webkit.org"]
    /// The zip, the unpacked files and room to spare.
    static let spaceNeeded: Int64 = 700_000_000

    // MARK: Where it lives

    /// Relative to drive_c, under windows\ so GameLibrary.scan never lists nw.exe as a game.
    static let relativeFolder = "windows/bcd-nwjs/" + folderName
    static var windowsFolder: String { "C:\\" + relativeFolder.replacingOccurrences(of: "/", with: "\\") }
    static var windowsExe: String { windowsFolder + "\\nw.exe" }
    /// Written last, after every file is in place: its text is the archive's SHA-256.
    static let marker = ".bcd-nwjs-complete"

    static func folder(drive: URL) -> URL { drive.appendingPathComponent(relativeFolder, isDirectory: true) }

    static func ready(drive: URL) -> Bool {
        let dir = folder(drive: drive), fm = FileManager.default
        guard fm.fileExists(atPath: dir.appendingPathComponent("nw.exe").path),
              fm.fileExists(atPath: dir.appendingPathComponent("nw.dll").path),
              let text = try? String(contentsOf: dir.appendingPathComponent(marker), encoding: .utf8) else { return false }
        return text.trimmingCharacters(in: .whitespacesAndNewlines) == sha256
    }

    // MARK: Which games

    /// The PE header's Machine (0x14c for 32-bit x86, 0x8664 for x64), or nil when the file is not a PE program.
    static func peMachine(_ file: URL) -> UInt16? {
        guard let handle = try? FileHandle(forReadingFrom: file) else { return nil }
        defer { try? handle.close() }
        guard let head = try? handle.read(upToCount: 4096), head.count >= 0x40 else { return nil }
        let b = [UInt8](head)
        guard b[0] == 0x4d, b[1] == 0x5a else { return nil }   // "MZ"
        let pe = Int(b[0x3c]) | Int(b[0x3d]) << 8 | Int(b[0x3e]) << 16 | Int(b[0x3f]) << 24
        guard pe >= 0x40, pe + 6 <= b.count, b[pe] == 0x50, b[pe + 1] == 0x45, b[pe + 2] == 0, b[pe + 3] == 0 else { return nil }
        return UInt16(b[pe + 4]) | UInt16(b[pe + 5]) << 8
    }

    /// A 32-bit NW.js game: the program is 32-bit x86, and nw.dll and package.json lie beside it.
    static func isNWJS32(program: URL) -> Bool {
        let dir = program.deletingLastPathComponent(), fm = FileManager.default
        guard fm.fileExists(atPath: dir.appendingPathComponent("nw.dll").path),
              fm.fileExists(atPath: dir.appendingPathComponent("package.json").path) else { return false }
        return peMachine(program) == 0x14c
    }

    /// env.MADEIRA_NWJS64 = 0 in text in bcd.cfg's syntax (the last line for the key wins).
    static func turnedOff(_ text: String) -> Bool {
        var off = false
        for raw in text.split(whereSeparator: { $0 == "\n" || $0 == "\r" }) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            guard line[..<eq].trimmingCharacters(in: .whitespaces) == "env.MADEIRA_NWJS64" else { continue }
            off = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces) == "0"
        }
        return off
    }

    // MARK: What starts

    /// nw.exe with the game's folder as the app, then the game's own arguments. A folder never ends in
    /// a backslash here: before the closing quote it would escape it.
    static func command(gameFolder: String, arguments: String) -> (exe: String, args: String) {
        var folder = gameFolder
        while folder.count > 3, folder.hasSuffix("\\") { folder.removeLast() }
        let extra = arguments.trimmingCharacters(in: .whitespaces)
        return (windowsExe, "\"\(folder)\"" + (extra.isEmpty ? "" : " " + extra))
    }

    // MARK: Unpacking

    /// Where a zip entry goes inside the NW.js folder: the name without the archive's top folder, or nil
    /// for the top folder itself, a folder entry, or a name that would leave the folder.
    static func unpackedPath(_ name: String) -> String? {
        let parts = name.replacingOccurrences(of: "\\", with: "/").split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        guard parts.count >= 2, parts[0] == folderName, let last = parts.last, !last.isEmpty else { return nil }
        let rest = Array(parts.dropFirst())
        guard rest.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." && !$0.contains(":") }) else { return nil }
        return rest.joined(separator: "/")
    }
}
