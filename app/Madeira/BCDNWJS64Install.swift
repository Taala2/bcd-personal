// SPDX-License-Identifier: GPL-3.0-only
//
// madeira-bcd personal (2026-11-03): BCD's 64-bit NW.js for 32-bit NW.js games (BCDNWJS64.swift says why).
//
// The first start of such a game downloads NW.js 0.48.4 win-x64 once, with BCD's own downloader, from
// dl.nwjs.io only, checks its size and SHA-256 against the pins, unpacks it into C:\windows\bcd-nwjs and
// then starts the game. Every start after that runs nw.exe from there with the game's folder as the app.
// A download that fails leaves the game as it was: the next start runs its own 32-bit NW.js, as before.
// ContentView.startLibraryEntry asks BCDNWJS64Launch.plan before configureLaunch and calls export after it.

import Foundation

@MainActor final class BCDNWJS64Installer {
    static let shared = BCDNWJS64Installer()

    private var current: Task<Void, Error>?
    /// A download or unpacking that failed in this run of BCD: further starts run the game's own NW.js.
    private(set) var failedThisRun = false
    /// True from the Play that starts the download (set before that Play returns) until it ends.
    var busy: Bool { current != nil }
    /// Where the run is, for the waiting message: bytes received so far, or nil while it unpacks.
    private var stage: (Int64?) -> Void = { _ in }

    private func log(_ line: String) { LogStore.shared.log(line) }

    /// Starts making the NW.js folder complete (download, check, unpack) and calls `done` on the main actor
    /// with the outcome. `busy` is true before it returns; a call while a run is in progress does nothing.
    /// `progress` gets the bytes received, then nil while the archive is unpacked.
    func start(progress: @escaping (Int64?) -> Void, done: @escaping (Error?) -> Void) {
        guard current == nil else { return }
        let drive = LibraryModel.drive
        stage = progress
        let task = Task { try await self.run(drive: drive) }
        current = task
        Task {
            var failure: Error?
            do { try await task.value }
            catch {
                failure = error
                failedThisRun = true
                let reason = (error as? BCDLauncherError)?.logReason ?? (error is CancellationError ? "cancelled" : "other")
                log("[nwjs64] download failed reason=\(reason); this run of BCD starts 32-bit NW.js games on their own NW.js")
            }
            current = nil
            stage = { _ in }
            done(failure)
        }
    }

    private func run(drive: URL) async throws {
        if BCDNWJS64.ready(drive: drive) { return }
        let fm = FileManager.default
        let parent = BCDNWJS64.folder(drive: drive).deletingLastPathComponent()
        do { try fm.createDirectory(at: parent, withIntermediateDirectories: true) }
        catch { throw BCDLauncherError.saving(error.localizedDescription) }
        if let free = (try? URL(fileURLWithPath: NSHomeDirectory()).resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage, free < BCDNWJS64.spaceNeeded {
            log("[nwjs64] refused: free=\(free / (1 << 20))MB")
            throw BCDLauncherError.saving("not enough free space (about \(BCDNWJS64.spaceNeeded / 1_000_000) MB are needed)")
        }
        let zip = parent.appendingPathComponent(BCDNWJS64.folderName + ".zip.part")
        try? fm.removeItem(at: zip)
        defer { try? fm.removeItem(at: zip) }
        log("[nwjs64] download NW.js \(BCDNWJS64.version) win-x64 (\(BCDNWJS64.bytes / 1_000_000) MB) from \(BCDLauncherNet.origin(BCDNWJS64.url))")
        try await fetch(into: zip)
        let size = (try? fm.attributesOfItem(atPath: zip.path)[.size] as? NSNumber)?.int64Value ?? 0
        guard size == BCDNWJS64.bytes else { throw BCDLauncherError.checksum(what: "NW.js") }
        guard try BCDGeckoFiles.sha256(of: zip) == BCDNWJS64.sha256 else { throw BCDLauncherError.checksum(what: "NW.js") }
        log("[nwjs64] the archive matches its pinned size and SHA-256; unpacking")
        stage(nil)
        let files = try await Task.detached(priority: .utility) { try BCDNWJS64Installer.unpack(zip: zip, drive: drive) }.value
        log("[nwjs64] ready: \(files) files in \(BCDNWJS64.windowsFolder)")
    }

    private func fetch(into part: URL) async throws {
        guard BCDLauncherNet.isAllowed(BCDNWJS64.url, hosts: BCDNWJS64.hosts) else {
            throw BCDLauncherError.redirected(BCDLauncherNet.origin(BCDNWJS64.url))
        }
        let control = BCDLauncherDownloadControl()
        let box = BCDNWJS64ProgressBox()
        let reached: URL = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
                let downloader = BCDLauncherDownloader(url: BCDNWJS64.url, hosts: BCDNWJS64.hosts, maxBytes: BCDNWJS64.bytes + (1 << 20),
                                                       company: "NW.js", destination: part, progress: { received, _ in
                    // At most one message update per megabyte, on the main actor.
                    guard box.accept(received) else { return }
                    Task { @MainActor [weak self] in self?.stage(received) }
                }) { result in
                    control.clear()
                    continuation.resume(with: result)
                }
                control.set(downloader)
                downloader.start()
            }
        } onCancel: {
            control.cancel()
        }
        log("[nwjs64] answered from \(BCDLauncherNet.origin(reached))")
    }

    /// Unpacks every file of the archive's top folder into a staging folder, then puts it in place and writes
    /// the marker last. Returns the number of files.
    nonisolated static func unpack(zip: URL, drive: URL) throws -> Int {
        let fm = FileManager.default
        let final = BCDNWJS64.folder(drive: drive)
        let staging = final.deletingLastPathComponent().appendingPathComponent(BCDNWJS64.folderName + ".part", isDirectory: true)
        try? fm.removeItem(at: staging)
        do {
            try fm.createDirectory(at: staging, withIntermediateDirectories: true)
            let reader = try SaveZipReader(url: zip)
            var files = 0
            for entry in reader.entries {
                guard let path = BCDNWJS64.unpackedPath(entry.name) else { continue }
                let target = staging.appendingPathComponent(path)
                try fm.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
                try reader.extract(entry).write(to: target)
                files += 1
            }
            guard fm.fileExists(atPath: staging.appendingPathComponent("nw.exe").path),
                  fm.fileExists(atPath: staging.appendingPathComponent("nw.dll").path) else {
                throw BCDLauncherError.checksum(what: "NW.js")
            }
            try? fm.removeItem(at: final)
            try fm.moveItem(at: staging, to: final)
            try BCDNWJS64.sha256.write(to: final.appendingPathComponent(BCDNWJS64.marker), atomically: true, encoding: .utf8)
            return files
        } catch let error as BCDLauncherError {
            try? fm.removeItem(at: staging)
            throw error
        } catch {
            try? fm.removeItem(at: staging)
            throw BCDLauncherError.saving(error.localizedDescription)
        }
    }
}

/// What a library start does for a 32-bit NW.js game.
enum BCDNWJS64Launch {
    enum Plan: Equatable { case none, download, run }

    /// The waiting message's first words, so a progress update only replaces the message it put up.
    static let waitPrefix = "This game uses a 32-bit NW.js, which doesn't run reliably in BCD."

    /// The waiting message: `received` bytes so far, or nil while the archive is unpacked.
    static func waitMessage(received: Int64?) -> String {
        let total = BCDNWJS64.bytes
        let where_: String
        if let received {
            let percent = total > 0 ? min(100, Int(received * 100 / total)) : 0
            where_ = "Downloading BCD's 64-bit NW.js (once): \(percent)% (\(received / 1_000_000) of \(total / 1_000_000) MB)."
        } else {
            where_ = "Unpacking BCD's 64-bit NW.js (once)."
        }
        return waitPrefix + " " + where_ + " The game starts by itself when it is ready."
    }

    static func failedMessage(_ error: Error) -> String {
        let why = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        return "BCD couldn't download its 64-bit NW.js (\(why)). Press Play again to start the game with its own NW.js."
    }

    /// .run when the game is a 32-bit NW.js program and BCD's NW.js is there; .download when it is not yet;
    /// .none for every other start, when env.MADEIRA_NWJS64 = 0, or after a failed download in this run.
    @MainActor static func plan(_ entry: LibraryEntry) -> Plan {
        guard entry.desktop != true, entry.steamAppID == nil, !entry.runsInDesktop, !entry.isBatch,
              entry.servicesScript == nil else { return .none }
        let program = LibraryModel.drive.appendingPathComponent(entry.launchRelativePath)
        guard BCDNWJS64.isNWJS32(program: program) else { return .none }
        if !MadeiraConfig.flag("MADEIRA_NWJS64") || BCDNWJS64.turnedOff(entry.config ?? "")   // 0: a 32-bit NW.js game (RPG Maker MV) starts on its own NW.js, not on BCD's 64-bit NW.js 0.48.4
            || BCDNWJS64.turnedOff(GameProfile(windowsPath: entry.windowsPath).text) {
            LogStore.shared.log("[nwjs64] \(program.lastPathComponent) is a 32-bit NW.js program; env.MADEIRA_NWJS64 = 0: it starts on its own NW.js")
            return .none
        }
        if BCDNWJS64.ready(drive: LibraryModel.drive) { return .run }
        return BCDNWJS64Installer.shared.failedThisRun ? .none : .download
    }

    /// The entry whose start runs on BCD's NW.js, between export and the launch worker's reassert.
    private static let lock = NSLock()
    nonisolated(unsafe) private static var armed: UUID?

    /// A start that is not .run: nothing to reassert on the launch worker.
    static func disarm() { lock.lock(); armed = nil; lock.unlock() }

    /// After configureLaunch: nw.exe with the game's folder as the app, in the game's folder.
    @MainActor static func export(_ entry: LibraryEntry) {
        lock.lock(); armed = entry.id; lock.unlock()
        apply(entry)
        let name = entry.launchWindowsPath.split(separator: "\\").last.map(String.init) ?? entry.launchWindowsPath
        LogStore.shared.log("[nwjs64] \(name) is a 32-bit NW.js program: it starts on BCD's NW.js \(BCDNWJS64.version) (64-bit), "
                            + "\(BCDNWJS64.windowsExe), with its folder as the app; env.MADEIRA_NWJS64 = 0 turns this off")
    }

    /// On the launch worker, after LibraryEntry.applyEnvironment, which runs configureLaunch again and puts the
    /// game's own program back: the same command once more for the entry export armed (a build-627 run started
    /// Game.exe after the [nwjs64] line, 2026-11-03).
    static func reassert(_ entry: LibraryEntry) {
        lock.lock(); let mine = armed == entry.id; armed = nil; lock.unlock()
        guard mine else { return }
        apply(entry)
        LogStore.shared.log("[nwjs64] kept after the launch profile was applied: \(BCDNWJS64.windowsExe)")
    }

    private static func apply(_ entry: LibraryEntry) {
        let command = BCDNWJS64.command(gameFolder: LibraryEntry.folder(of: entry.launchWindowsPath), arguments: entry.programArguments)
        setenv("MADEIRA_EXE", command.exe, 1)
        setenv("MADEIRA_ARGS", command.args, 1)
        setenv("MADEIRA_WORKDIR", entry.launchDirectory, 1)
    }
}

/// Lets the waiting message follow a download without an update for every 64 KB: about one a megabyte.
final class BCDNWJS64ProgressBox: @unchecked Sendable {
    private let lock = NSLock()
    private var last: Int64 = -1
    func accept(_ received: Int64) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard last < 0 || received - last >= 1 << 20 else { return false }
        last = received
        return true
    }
}
