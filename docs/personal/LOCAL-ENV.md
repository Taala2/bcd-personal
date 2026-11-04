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
- Чтобы эти 13 шли и здесь, нужен WSL с Ubuntu. Его включает пользователь сам (`wsl --install`, это системный компонент Windows, после установки нужна перезагрузка). Затем в Ubuntu: `sudo apt install clang python3 python3-yaml`.
- Скрипт задаёт `PYTHONUTF8=1`: без этого часть тестов падает на кодировке cp1251 при чтении исходников.
- Список патчей скрипт каждый раз читает из `tools/build-xtajit64.sh` (ветку для текущего пина и строки «для любого пина»), так что новый патч, добавленный туда, подхватывается сам.
- Готовая DLL — `FEX/build-arm64ec/Bin/libarm64ecfex.dll`. В `app/Madeira` она **не** копируется.
- Если пин FEX в репозитории сменился, обнови подмодуль и вложенные, затем удали `FEX/build-arm64ec`, чтобы CMake настроился заново:

```bash
git -c core.longpaths=true submodule update --init --depth 1 FEX
git -C FEX -c core.longpaths=true submodule update --init --depth 1 -- External/vixl Source/Common/cpp-optparse External/fmt External/drm-headers External/xxhash External/Vulkan-Headers External/jemalloc_glibc External/tracy External/range-v3 External/zydis External/unordered_dense External/rpmalloc
```
