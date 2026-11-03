# Taala2/bcd-personal: выводы, проверенные гипотезы и порядок проверки

Документ ведётся по правилам «BCD Personal — Engineering Workflow». Факты, гипотезы и непроверенное разделены. «Проверено» означает одно из трёх, и каждый раз указано, что именно:
- **CI** — CI зелёный;
- **host** — проверка на хосте (Windows, без компилятора C и Swift: только Python-части);
- **устройство** — проверено на телефоне.

Устройство: iPhone16,1 (8 ГБ, лимит iOS 6143 МБ), iOS 27.0.1, приложение «BCD 2» (Madeira2 IPA), сборка 627.

## 1. Порядок проверки на устройстве

1. Сборка: `gh workflow run build-ipa.yml --repo Taala2/bcd-personal --ref sync/0.1.626`. Сборка занимает около 55 минут.
2. Репозиторий публичный, поэтому артефакт `madeira-unsigned-ipas-0.1.<N>-encrypted` содержит только `.ipa.enc` и хранится 3 дня. Расшифровка:
   `openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -md sha256 -in Madeira2-0.1.<N>-unsigned.ipa.enc -out Madeira2-0.1.<N>-unsigned.ipa`
3. Подписать и установить поверх «BCD 2».
4. Правило из списка рекомендаций попадает в игру при первом появлении игры или если её конфиг не меняли. Если конфиг менялся, нужно нажать «Reset to Recommended».
5. Присылать нужно обычный `bcd-log*.txt`. Файл `.prev` нужен только тогда, когда приложение закрылось и обычный лог той игры не сохранился.
6. Какие строки в логе подтверждают, что сработало нужное:
   - `[recs] personal list v<N> ...` — загружен личный список правил;
   - `[game-cfg] ...` — с каким конфигом шла игра;
   - `[personal-default] NAME=1` — включён общий ключ;
   - `[wasm-guard-trim]`, `[anon-ldadd] personal ... width=8`, `[tls-cross-clear] ... SPARED` — сработал ключ;
   - `[reloc-bounds]` — сработала проверка границ таблицы релокаций;
   - `[bcd] library launch <exe>` — какой exe запущен.

## 2. Подтверждено на устройстве

### Internship [demo] (NW.js 0.48, RPG Maker MZ, x64)

Игра работает около 6 минут на 57–60 fps (2026-11-02). Каждая причина ниже подтверждена своим запуском, а каждый ключ проверялся отдельно.

| Симптом в логе | Причина | Ключ |
|---|---|---|
| Chromium `Check failed: current` (sequenced_task_runner_handle, thread_task_runner_handle) | `TlsFree` в одном псевдопроцессе обнуляет TLS-слоты 4 и 5 во всех потоках всех процессов | `env.MADEIRA_TLS_CLEAR_OWN_PROCESS = 1` (запуск 7: 170 `SPARED`, 0 FATAL) |
| `[jit-pool] EXHAUSTED ... nw.dll`, `Failed to load NW DLL` | Каждому дочернему процессу Chromium нужна своя копия nw.dll (138 МБ) в JIT-пуле | `env.MADEIRA_X64_IMAGE_NOCOPY = 1` (запуск 4) |
| `[store-undecoded] insn=0xf8e800c6 ... alias OK`, `[mach_exc] UNHANDLED` | 64-битный LDADDAL на анонимном RX-алиасе кучи V8 | `env.MADEIRA_ANON_LDADDAL64 = 1` (запуск 12: 16 строк `width=8`, 0 UNHANDLED) |
| 64 отказа на резервирование `size=0x280000000` | V8 8.5 резервирует 10 ГБ под каждый `WebAssembly.Memory` (`kFullGuardSize = 10 GiB`), и флагом это не отключить | `env.MADEIRA_WASM_GUARD_TRIM = 1` (запуск 15: 6 ГБ выдано, 0 отказов) |
| `[jit-pool] EXHAUSTED (anon RWX)`, `Fatal process out of memory: wasm code commit` | Пул в 624 МБ мал для wasm-кода V8 | `pool-split = 1`, `pool-low = 1` (запуск 16: нет EXHAUSTED и Fatal) |
| Тёмный экран, AudioService не может загрузить nw.dll | Урезанные 4 ГБ попали внутрь окна образов | Исправлено размещением только выше `0x73ffff0000` (e3b26c7) |

Не доказано, что нужны `MADEIRA_X64_NOCOPY_DATAIMP` и `MADEIRA_THREAD_STACK_SPILL`: оба были в рабочем конфиге, но отдельной проверки не было.

### Прочее на устройстве

- Правило upstream `nwjs-game-exe` (`MADEIRA_CHILD_ARGS`) передаёт аргументы и в `--type=crashpad-handler`. Тот завершается с ошибкой `invalid option -- 'd'`, и игра не стартует (запуски 1–2).
- Закономерности первой серии из 14 игр (2026-11-02/03, сборка b3b1643) описаны в разделе 4.

## 3. Опровергнутые гипотезы

- **Флаги отключения trap-handler для wasm** (`--disable-features=WebAssemblyTrapHandler --js-flags=--no-wasm-trap-handler`) убирают 10-гигабайтные резервирования. Опровергнуто запуском 13: флаги дошли до рендерера, а 65 запросов `0x280000000` остались.
- **`MADEIRA_CAGE_RELEASE = 1`** помогает Internship. Опровергнуто запуском 10: стало хуже, рендерер умер.
- **Видеофлаги в конфиге одной строкой** доходят до Chromium. Опровергнуто запуском 6: в `argv` их нет. Аргументы задаются только полем «Launch arguments».

## 4. Первая серия игр (сборка b3b1643): факты и гипотезы

| Игра | Движок | Факт из лога | Статус |
|---|---|---|---|
| ViV | ? | 614 кадров, ~21 fps | работает (лог неполный) |
| V.0.7, Succum | NW.js x64 | картинка есть; Succum доходит до 6085 МБ | работает до лимита памяти |
| 8th Heaven, CelSector | Unity | ~60 fps, затем скачок памяти до 5,6–6,1 ГБ, лог обрывается | вероятно, закрыта iOS по памяти (jetsam) |
| LogoutDenied, Secret.Glory.Hole | Ren'Py 8 | `renpy.pygame.error.error: No matching GL pixel format available` в `presplash.start` | нет OpenGL |
| SleeveShock, TheGrandButtSexHotel | Ren'Py 8 | `[bcd] library launch python.exe`, выход с кодом 0 | выбран не тот exe |
| lucieneslife | Ren'Py 8 | `[early-fault] ... phase=relocations`, `wineserver crashed`; таблица релокаций указывает в `.rsrc` | падает всё приложение |
| Minami | NW.js x86 | `CrRendererMain` в цикле `NtDelayExecution`, у рендерера 9 модулей | зависание, причина не найдена |
| Way of Corruption | NW.js x86 | `Fatal error in ../../v8/src/deoptimizer.cc, line 1804` | падение V8 |
| Metamorph | Godot 3 | окно «Unable to initialize video driver» | только OpenGL |
| the-quiet-song-of-plants | ? | INT3 сразу после CPUID при `avx=0`; `fex-avx-on` не срабатывает (исключение приходит как 80000003) | гипотеза: нужен AVX |

Подтверждено исходниками или кодом, но не на устройстве:
- `opengl32.dll` в BCD — заглушка (`[unixlib] opengl32 (GL-absent stub table)`). Upstream-правило `opengl-only-no-context` помечено как неисправимое (noFix).
- Ren'Py (`renpy/display/presplash.py`, `core.py`):
  - если задан `RENPY_LESS_UPDATES`, заставка не показывается, а переходы между сценами отключаются;
  - `RENPY_RENDERER` выбирает отрисовщик; на Windows порядок такой: `gl2 → angle2 → gles2 → sw`.
- Внешние переменные окружения передаются Windows-программе без фильтра (`build/ntdll-unix/env_ios.c`, `get_initial_environment`).
- Нехватку памяти обрабатывает upstream auto-tune (`swap-8gb-memory-kill*`). Подкачка включается только после вердикта «killed», то есть со второго запуска.
- Python в Ren'Py выполняет `cmd /c ver`. ARM64-процесс `cmd.exe` падает в хуке FEX (`libarm64ecfex.dll+0x1130dc`, адрес 0x38, cpuarea=0). Игре это не мешает: процесс убирается.

Изменения после этой серии (10db375, CI 41500754292 зелёный, **на устройстве не проверены**):
- `virtual_ios.c` `[reloc-bounds]`: таблица релокаций и записи за пределами образа больше не применяются. Это проверяет гипотезу для lucieneslife.
- `GameLibrary.swift`: `python.exe` и `pythonw.exe` больше не выбираются как программа игры.
- `personal-renpy`: `RENPY_LESS_UPDATES = 1` и `RENPY_RENDERER = angle2`. **Здесь две переменные в одной итерации.** Их можно различить по логу (`[guest-log]` дублирует `log.txt` Ren'Py, где виден выбранный отрисовщик), но если ANGLE не заработает, следующая итерация — оставить только `RENPY_LESS_UPDATES`.

Открытые гипотезы, ещё не проверявшиеся:
- Way of Corruption: деоптимизатор V8 32-бит под FEX. Эксперимент: «Launch arguments» `--js-flags=--no-opt`.
- the-quiet-song-of-plants: включить AVX в настройках игры.
- Minami: данных нет. Нужен лог с `env.MADEIRA_GUEST_LOG = all`, он уже включён правилом.

## 5. Общие ключи для всех игр (b3b1643)

`WineProcessBridge.m` выставляет `MADEIRA_TLS_CLEAR_OWN_PROCESS`, `MADEIRA_ANON_LDADDAL64` и `MADEIRA_WASM_GUARD_TRIM` в 1, если ни `bcd.cfg`, ни конфиг игры их не задают. Каждый ключ срабатывает только там, где без него ошибка. Риск регрессий у игр, которые уже работали, на устройстве не измерялся. Если такая игра сломается, ключи проверяются по одному через `env.NAME = 0`.

Каталог настроек для этих ключей по-прежнему показывает «Default off». Это известное расхождение.

## 6. Особенности CI (macos-15 arm64)

- Диапазон `[0x1000000000, 0x7000000000)` уже занят в каждом процессе, поэтому тесты с фиксированными адресами там падают. Окно для таких тестов выбирается при запуске.
- Размер страницы 16 КБ. Тесты, рассчитанные на 4 КБ, идут как x86_64 под Rosetta.
- Встроенному C, который подключает `windef.h` Wine, сначала нужен `<stdarg.h>`.
- Тесты, которые вырезают код из `virtual_ios.c` по маркерам, ломаются, если новый код попадает в вырезаемый участок. Так было с `check-soft-pool-decommit.py`, исправлено в 81b77ff.
- Минуты macOS в приватном репозитории считаются с множителем 10. В публичном репозитории сборки бесплатные.
