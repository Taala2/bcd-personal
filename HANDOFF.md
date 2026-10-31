# HANDOFF: перенос работы в Claude Code (репозиторий Taala2/bcd-personal)

Скопируйте этот файл в корень клона как `HANDOFF.md` (или в `CLAUDE.md`) и начните сессию словами:
«Прочитай HANDOFF.md и продолжи с раздела "Текущая задача". Ничего не коммить, не пушь и не запускай Actions без моего отдельного согласия».

## 1. Правила (действуют всегда)
- Отвечать по-русски. Минимальные патчи, без общего аудита.
- Отделять факты от гипотез. Не утверждать, что тест прошёл, если он не запускался.
- Не менять диагностический патч TLS (`tls_diag_v2.patch`, файлы `build/ntdll-unix/ios_tls_clear.h`, `virtual_ios.c`, `signal_arm64_ios.c`, `ios_tls_diag_util.h`, `ios_nw_tls_diag.h`) и его логику.
- Не менять цели развёртывания, не убирать этапы сборки, не ослаблять проверки хешей и безопасности.
- Не добавлять `libmetalirconverter.dylib` и не обходить проверку на него. Вопрос лицензии Apple Metal Shader Converter открыт и блокирует D3D12 и любую передачу IPA.
- Не утверждать, что интеграция с iOS проверена полностью (iOS SDK на проверках не было).
- Юридических выводов без подтверждения не делать.
- Не коммитить, не пушить, не открывать PR, не запускать Actions без отдельного согласия. Ветки `personal`, `tls-diagnostic` и публичный репозиторий не менять.
- DCO: подпись только с реальными именем и e-mail пользователя (`git commit -s`), подписи от имени Claude не ставить.
- Тесты не отключать, assertions не удалять, ошибку не подменять успехом.

## 2. Что за проект и задача
BCD (Madeira): Windows-игры на iOS (Wine + FEX ARM64EC). Симптом: игра NW.js «Internship [demo]» на iPhone 15 Pro падает с AV в `nw.dll+0xEDD661` (tid 0x24) после `nw.dll+0xFFF540`, который вернул NULL, при `MADEIRA_X64_IMAGE_NOCOPY=1` (сборка 623). Гипотеза (НЕ доказана): `TlsFree` в одном псевдопроцессе через `virtual_clear_tls_index` обнуляет ячейку TLS у потоков всех процессов. Добавлена только диагностика (логи), не исправление.

Исходный репозиторий: `thebcd69/bcd-games`, тег `v0.1.623` = `05e9f6fe56972d64ecf79c7a6b41f2d9be107072`. Приватный источник `thebcd69/madeira-bcd` недоступен.

## 3. Состояние репозитория
- Локальный клон: `bcd-personal`, remote `upstream` = bcd-games, `origin` = `Taala2/bcd-personal` (Private). Рабочая ветка `diag-623` (она же default на GitHub), `main` = зеркало тега.
- Коммиты на `diag-623` (патчи в `~/Desktop/bcd-patches`, контрольные суммы в `SHA256SUMS`):
  1. 0001 `Add TLS cross-process diagnostics` (6 файлов; только он идёт в PR в bcd-games; описание PR в `pr_description.md`, подпись DCO в нём пустая)
  2. 0002 `Add diagnostic build workflow` (`build-ipa.yml`, `BUILD-NUMBER`=624; условия на репозиторий `Taala2/bcd-personal`)
  3. 0003 `Build without Metal Shader Converter in the diagnostic repo` (`MADEIRA_ALLOW_NO_D3D12=1`, orphan `madeira_ir_stub.c`)
  4. 0004 пропуск `check-log-upload` в bcd-personal (`if: github.repository != 'Taala2/bcd-personal'`)
  5. 0006 исправлен порядок аргументов `check()` в `check-bcd-menu-items.py`
  6. 0007 `#undef` трёх макросов SDK в harness `check-fault-guard-guest-rip.py`
- Необязательные патчи (применять только по решению пользователя): 0005 (пропуск ещё 9 шагов, которым нужны приватные файлы), 0008 (тот же `#undef` в `check-fault-loop-guest-progress.py`).
- Пользователь сообщил, что 0007 и 0008 применены, HEAD `2680b1a`. Что именно из 0004/0005/0006 применено, проверить командой `git log --oneline v0.1.623..HEAD`.

## 4. Что выяснили по CI (run на macos-15-arm64, Xcode 26.3)
- Workflow зарегистрирован и запускается. Сабмодули (FEX, wine, dxmt, madeira-dock и вложенные) публичны, отдельный доступ не нужен.
- Причина класса сбоев: публичный экспорт (`tools/export-public.py`, списки `PRIVATE`/`MARKDOWN_PUBLIC`) не содержит `bcd-logs/`, `docs/` (кроме `docs/local-build/BUILD-NUMBER`), `research/`, `tools/pull-logs.py`, `tools/log-facts.py` и любых `.md`, кроме шести разрешённых. Тесты и скрипты сборки, читающие эти файлы, падают.
- Подтверждённо или по коду падают: `check-bcd-help`, `check-bcd-screenshot`, `check-feedback` (bcd-logs), `check-bcd-perftrace`, `check-bcd-session-record` (tools/log-facts.py), `check-external-storage` (docs/EXTERNAL-STORAGE.md), `check-d3d8-to-d3d9` (LICENSE.md), `check-vcruntime-mfc`, `check-vcruntime-sources` (tools/fetch-vcruntime.md). Для них патч 0005.
- `third-party/d3d8to9` и `third-party/cnc-ddraw`: «differs from SHA256SUMS» из-за отсутствующих `LICENSE.md`/`README.md` (хеши не расходятся; недостающие файлы можно взять из upstream и сверить по `SHA256SUMS`). Пока не менялось, DLL не собираются.
- LLVM: шаг `cmake --build toolchains/llvm-ios-build` завершился кодом 1 (шаг с `continue-on-error`), в `lib` 73 `.a`. Какие цели упали, неизвестно: нужен файл `llvm-ios-build.log` из артефакта `build-logs`; `grep -n "FAILED:"`. DXMT (airconv) нужны 34 библиотеки из `dxmt/src/airconv/meson.build`; проверки их наличия нет. КЭШ `llvm-15.0.7-ios-v2` сохранён после неудачной сборки (шаг Save не проверяет успех): до выяснения лучше удалить (`gh cache delete llvm-15.0.7-ios-v2`) по решению пользователя.
- Давно не гонялся CI (в workflow пометка «builds run on the owner's Mac, 2026-10-13»), поэтому новые тесты на macOS ранее не проверялись; ожидаются ещё падения.
- Шаги с несколькими тестами идут под `bash -e`: после первого падения остальные тесты шага не запускаются.

## 5. Текущая задача (не завершена)
Падает `tests/host/check-x87-int64-diag.py` на Actions: `FAIL` у всех сценариев с компиляцией, в выводе `alloc-failed`; следующий `check-fault-neighbour-diag.py` не запускался (`bash -e`).

Установлено:
- `alloc-failed` печатает функция `alloc_at` в строках ~146-150 теста: `mach_vm_allocate(mach_task_self(), &m, n, VM_FLAGS_FIXED)` на заданный адрес, при неудаче `exit(3)`. Тот же шаблон есть в `check-int3-x86-diag.py` (201), `check-ill-guest-diag.py` (105), `check-wow-null-diag.py` (151), `check-x86-fault-diag.py` (198).
- Первая аллокация в сценариях: пул JIT `alloc_at(0x300000000 + 0x1000000, 0x10000)` (адрес ≈ 0x301000000), потом окно гостя `WIN = 0x5000000000`, страницы стека/головы и «живой блок» на `WIN + 0xf070000`; сценарий `pair` берёт `0x300000000 + 0x2000000`. Компиляция без санитайзеров: `cc -std=gnu11 -Wall -Wextra -Werror`.
- Сценарии `roundtrip` (не аллоцирует) прошёл, остальные упали каскадом: каждый запуск это отдельный процесс, и любой неудачный `alloc_at` завершает его с кодом 3, поэтому проверки по `lines`/`stdout` падают как следствие.

НЕ установлено (гипотезы, не доказаны): какой именно вызов `alloc_at` возвращает ошибку; вероятнее всего фиксированный адрес занят на macOS 15 arm64 (например, 0x301000000 попадает в уже занятую область процесса или адрес не выровнен под размер страницы 16 KB: на arm64 macOS страница 0x4000, а в тесте используются размеры 0x1000-подобные адреса и `+0x1000`). Запустить на macOS нечем; из Linux проверить нельзя (тест там пропускается).

План (пока ничего не менять):
1. Добавить в harness (только в тестовом C-коде) вывод адреса, размера и кода возврата `kern_return_t` при неудаче, либо получить `vmmap`/`vm_region` процесса. Эта диагностика не должна превращать ошибку в успех.
2. Если причина в занятом адресе, выбрать другой диапазон для пула; если в выравнивании по 16 KB, выровнять адреса и размеры. Производственный код (`ios_x87_int64_diag.h`) не трогать без доказательства.
3. Подтверждение: `check-x87-int64-diag.py` на macOS-раннере, затем `check-fault-neighbour-diag.py` и четыре теста с таким же шаблоном `alloc_at`.
4. Результат оформить патчем `0009-Fix-x87-int64-diagnostic-host-test.patch`, не применять без решения пользователя.

## 6. Как проверять локально и что нельзя
- На Windows host-тесты, компилирующие Mach-код, не запустятся (macOS нужен). Можно статически читать код и проверять `python -m py_compile`. Для сабмодулей нужен `git submodule update --init --recursive`.
- Реальных сборок под iOS не было. GitHub-выражения проверены только по структуре YAML.

## 7. Открытые вопросы к пользователю
- Лицензия/дylib Apple (владелец), распространение IPA.
- Применять ли 0005 и 0008; удалять ли кэш LLVM; восстанавливать ли `LICENSE.md`/`README.md` для d3d8to9 и cnc-ddraw.
- Реальные имя и e-mail для DCO; PR в bcd-games только после отдельного решения.
- Номер сборки 624 может совпасть с официальным.
