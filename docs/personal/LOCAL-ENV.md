# Локальная среда без Mac (Windows, 2026-11-04)

## Что можно проверить здесь, а что только в CI

| Часть | Здесь (Windows) | Только CI (macOS arm64) |
|---|---|---|
| Патчи FEX ARM64EC (`tools/patch-fex-ios-*.py`) и компиляция `xtajit64.dll` | да: `tools/local-fex-check.sh` | — |
| Host-тесты `tests/host/check-*.py` | да (кроме тех, что требуют macOS или подмодулей Wine/DXMT) | — |
| Wine для iOS (`build/ntdll-unix/*.c`), DXMT, приложение Swift/SwiftUI, IPA | — | нужен SDK Apple |
| Проверка на iPhone | — | только устройство |

## Установлено (всё в `toolchains/`, папка в `.gitignore`)

| Что | Версия | Источник | SHA-256 архива |
|---|---|---|---|
| llvm-mingw (тот же выпуск, что `toolchains/llvm-mingw-20260421-ucrt-macos-universal` в CI) | 20260421, clang 22.1.4 | github.com/mstorsjo/llvm-mingw | `0c47b9fc1043b68a8d7e8e022b878f11306ed35beacf858864b36b096a0acd95` |
| CMake | 4.4.5 | github.com/Kitware/CMake | `326c826486d46a23cf9d5a1c9ee3a567d427bdd02c4a34071937a2a675cff004` |
| Ninja | 1.13.2 | github.com/ninja-build/ninja | `07fc8261b42b20e71d1720b39068c2e14ffcee6396b76fb7a795fb460b78dc65` |
| `toolchains/bin/python3.cmd` | обёртка `@python %*` | — | CMake FEX вызывает `python3`, в Windows есть только `python.exe` |
| pyyaml, unicorn (pip `--user`) | 6.0.3, 2.1.4 | PyPI | — |

Архивы лежат в `toolchains/_dl/`, хеши сверены с теми, что GitHub публикует для релизов.

Подмодуль `FEX` (thebcd69/bcd-fex на коммите пина) загружен без истории (`--depth 1`). Из вложенных подмодулей взяты только нужные для сборки модуля: vixl, cpp-optparse, fmt, drm-headers, xxhash, Vulkan-Headers, jemalloc_glibc, tracy, range-v3, zydis, unordered_dense, rpmalloc. Не загружены: Catch2 и три набора тестовых бинарников. Сборка: `FEX/build-arm64ec` (внутри подмодуля, git его не видит).

## Как пользоваться

```bash
bash tools/local-fex-check.sh            # патчи FEX, как в CI, и сборка arm64ecfex
bash tools/local-fex-check.sh --tests    # то же, затем host-тесты патчей FEX на чистом дереве
bash tools/local-fex-check.sh --keep     # оставить патчи наложенными, чтобы читать итоговый код
```

- Первый прогон (2026-11-04, FEX 959ac06, 8 патчей, включая renderer-tso): все патчи наложились, `arm64ecfex` собран за 207 с с нуля, после правки патча — за 14–20 с.
- Host-тесты `--tests`: 14 проходят. 13 здесь пройти не могут, и CI запускает их сам:
  - `POSIX` — фикстура на C требует POSIX (`setenv`, `sys/mman.h`, `dprintf`, ASan) или `tar` из stdin;
  - `WIN` — `check-fex-arena-census` (фикстура печатает адрес как `long`, а в Windows он 32-битный) и `check-fex-sha-off` (сравнивает пути с `/`, а Windows даёт `\`).
- Чтобы эти 13 шли и здесь, нужен WSL с Ubuntu. Его включает пользователь сам: это системный компонент Windows (2026-11-06 на этой машине `wsl --status` отвечает `WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED`, то есть он не включён).
  1. PowerShell от имени администратора: `wsl --install -d Ubuntu`, затем перезагрузка; при первом запуске Ubuntu спросит имя и пароль пользователя Linux.
  2. В Ubuntu: `sudo apt update && sudo apt install -y build-essential git python3 python3-yaml`.
  3. Там же: `git config --global --add safe.directory '*'`. Иначе git откажется работать с репозиторием на `/mnt/c`, владелец которого — пользователь Windows.
- С WSL `--tests` сам находит его (нужны `python3`, `cc` и `git`), запускает проверки `POSIX`/`WIN` ещё раз там, на том же checkout через `/mnt/c`, и считает их результат как обычный (`ok … (WSL)` или `FAIL … (WSL, log ….wsl)`). Без WSL всё как раньше.
  - `cc` в WSL идёт через обёртку с `-D_DEFAULT_SOURCE`: фикстуры написаны под macOS, где `-std=c11` не прячет `setenv`/`dprintf`, а glibc прячет. Сами проверки (они из апстрима BCD) не меняются.
  - `CI` — проверка компилирует с `compile_commands.json` локальной сборки, а в нём пути Windows (`check-fex-mono-bp-unlocked-miss`); её реальную компиляцию делает только CI.
  - `DEP` — в WSL нет модуля Python. `check-launcher-smc` нужен `unicorn`: `sudo apt install -y python3-unicorn`. CI эту проверку не запускает (`build-ipa.yml`, «Not in the workflow»).
- Первый прогон с WSL (2026-11-06, Ubuntu 24.04, WSL2): из 13 проверок 11 прошли, 1 `CI`, 1 `DEP`; весь `--tests` около 3,5 мин.
- Скрипт задаёт `PYTHONUTF8=1`: без этого часть тестов падает на кодировке cp1251 при чтении исходников.
- Список патчей скрипт каждый раз читает из `tools/build-xtajit64.sh` (ветку для текущего пина и строки «для любого пина»), так что новый патч, добавленный туда, подхватывается сам.
- Готовая DLL — `FEX/build-arm64ec/Bin/libarm64ecfex.dll`. В `app/Madeira` она **не** копируется.
- Если пин FEX в репозитории сменился, обнови подмодуль и вложенные, затем удали `FEX/build-arm64ec`, чтобы CMake настроился заново:

```bash
git -c core.longpaths=true submodule update --init --depth 1 FEX
git -C FEX -c core.longpaths=true submodule update --init --depth 1 -- External/vixl Source/Common/cpp-optparse External/fmt External/drm-headers External/xxhash External/Vulkan-Headers External/jemalloc_glibc External/tracy External/range-v3 External/zydis External/unordered_dense External/rpmalloc
```
