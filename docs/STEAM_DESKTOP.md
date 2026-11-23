# Steam for Windows

madeira-bcd personal: upstream's own docs/STEAM_DESKTOP.md is not in the public bcd-games tree, although tests/host/check-steam-desktop.py
reads it. This short copy is written in the fork from the headers of app/Madeira/SteamDesktop.swift and BCDSteamDesktopLive.swift. If upstream
publishes its file, take upstream's.

Steam for Windows is experimental (branch steam-client, milestone M1 "bootstrap"). It is Valve's own Steam client, steam.exe with its window,
store and library, in a Wine desktop. It is a separate choice beside BCD Dock, off unless **Settings › BCD Dock › Steam for Windows** turns it on.
It is meant for the store, the library, Friends, the Workshop and Steam's own settings, not for playing games: a game started from it runs inside
Steam's session, without the settings BCD gives that game.

## Where it lives

- The client's own folder is `Program Files (x86)/Steam Desktop`, never BCD Dock's `Steam` folder. Valve's client updates itself and would
  replace the pinned build the Dock host checks.
- The install reads Valve's public client manifest `steam_client_win64` (platform win64) from the one HTTPS update host and checks every
  package against its size and SHA-256. steam.exe is unpacked with the app's own ZIP reader.
- No Valve file is in BCD. The client is Valve's unchanged program, signed in by the player in Valve's own window. BCD hands it no sign-in,
  no licence and no ticket.

## The session

- The app sets `MADEIRA_STEAM_DESKTOP_SESSION=1` for a Steam for Windows session and unsets it for every other launch. WineProcessBridge.m
  then publishes no fixed Steam game identity.
- Before the start, BCD's own Steam connection logs off and stays off until the session has ended, and the registry values are written. This
  step is last before the launch is set up (ContentView's `BCDSteamDesktopLive.preLaunch`).
- The session's own settings include `pool = 1152, pool-split = 1, pool-low = 1` (the JIT pool at its largest: Steam's processes filled a
  592 MB pool and its web helper then restarted in a loop), the swap file, `avail-phys` with at most 900 MB for Steam's web helper, and
  pure-x64 images without a copy in the JIT pool.
- It needs a lot of memory: a device with a large address space and a memory limit of 6 GB or more.

## Log

Its lines carry the tag `[steam-desktop]`: file names, sizes, counts and short reasons. They never carry an account, a path with a user
name or an address.

Check: `tests/host/check-steam-desktop.py`.
