# polygon-orchestrator

Оркестратор подготовки олимпиадных задач для [Polygon](https://polygon.codeforces.com).
По структурированному спеку задачи (`authoring/spec.yaml`) он прогоняет цепочку
генеративных шагов — каждый шаг это вызов модели Claude плюс механическая
проверка результата — и получает черновики артефактов задачи:

- условие в LaTeX (`statement/*.tex`) и примеры;
- ограничения, TL/ML, тестовые группы и валидатор;
- генераторы (C++/testlib) и FreeMarker test-script;
- черновики решений (ok / wa / tl);
- кастомный чекер и интерактор — если они нужны задаче.

Потом всё это загружается на Polygon через Polygon API (`polygon push`) или
скачивается оттуда (`polygon pull`). Результат — **черновик**: автор
проверяет и докручивает его руками.

Подробные описания: [CLAUDE.md](CLAUDE.md) (архитектура и поведение команд),
[docs/SPEC_FORMAT.md](docs/SPEC_FORMAT.md) (формат спека),
[docs/SETUP.md](docs/SETUP.md) (детали настройки).

---

## Содержание

1. [Как запускать на Windows](#как-запускать-на-windows)
2. [Установка (WSL2 + Ubuntu)](#установка-wsl2--ubuntu)
3. [Первая задача: от черновика до Polygon](#первая-задача-от-черновика-до-polygon)
4. [Справочник команд](#справочник-команд)
5. [Что где лежит](#что-где-лежит)
6. [Совместная работа над задачами](#совместная-работа-над-задачами)
7. [Частые проблемы](#частые-проблемы)

---

## Как запускать на Windows

Оркестратор разрабатывается и проверяется на Linux. На Windows рекомендуемый
путь — **WSL2 с Ubuntu**: это обычный Linux внутри Windows, все инструкции
ниже — для него.

| Вариант | Плюсы | Минусы |
|---|---|---|
| **WSL2 + Ubuntu** (рекомендуется) | Всё работает как у автора, без изменений кода; VS Code открывает проект через расширение WSL; Claude Code ставится прямо в WSL | Нужно один раз включить WSL |
| Docker | Одинаковое окружение у всех | На Windows Docker Desktop всё равно работает поверх WSL2; логин Claude Code нужно пробрасывать в контейнер (`~/.claude`); Tab-дополнение и git-хуки неудобны; образа в репозитории пока нет |
| Нативный Windows | Без WSL | Не проверялся: нужны `g++` (MSYS2), `bash` для git-хука и дополнения; запуск процессов с таймаутами и пути могут вести себя иначе |

---

## Установка (WSL2 + Ubuntu)

### 1. WSL и Ubuntu

В PowerShell **от администратора**:

```powershell
wsl --install -d Ubuntu-24.04
```

Перезагрузить компьютер, открыть «Ubuntu» из меню Пуск, задать имя
пользователя и пароль. Дальше все команды — **в терминале Ubuntu**.

> Работайте в домашнем каталоге Linux (`~/...`), а не в `/mnt/c/...`:
> файлы на диске Windows из WSL читаются в разы медленнее, плюс бывают
> проблемы с правами и концами строк.

Если пользуетесь VS Code: поставьте в Windows расширение **WSL**
(`ms-vscode-remote.remote-wsl`), тогда `code .` в терминале Ubuntu открывает
проект в VS Code, а расширение Claude Code работает внутри WSL.

### 2. Системные пакеты

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip g++ default-jdk-headless bash-completion curl
```

Зачем что нужно:

- `python3` (≥ 3.10) — сам оркестратор;
- `g++` — compile-check сгенерированных C++-файлов (валидатор, генераторы,
  решения, чекер);
- `default-jdk-headless` (JDK 11+) — проверка test-script'а настоящим
  FreeMarker в `local check` и перед `polygon push` (подробнее — в п. 5);
- `bash-completion` — Tab-дополнение команд и адресов задач.

### 3. Claude Code CLI (подписка Pro/Max)

Оркестратор по умолчанию вызывает модели через Claude Code CLI (`claude -p`),
поэтому расход идёт из лимитов вашей подписки, а не с API-баланса.

```bash
curl -fsSL https://claude.ai/install.sh | bash
# или: npm install -g @anthropic-ai/claude-code
```

Затем один раз залогиниться **подпиской** (не API-ключом):

```bash
claude          # откроется ссылка для входа в браузере
# внутри Claude Code: /status — должен показать вашу подписку, /exit — выйти
```

Проверить, что CLI в `PATH` и достаточно свежий:

```bash
claude --version
claude update
```

Нужна версия с флагами `--system-prompt-file` и `--safe-mode`; со старой
оркестратор упадёт с подсказкой обновиться. Если `claude` лежит не в
`PATH` — укажите путь в `CLAUDE_CODE_BIN` в `.env`.

### 4. Репозиторий и Python-окружение

```bash
cd ~
git clone https://github.com/Slamur/polygon-orchestrator.git
cd polygon-orchestrator

python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

После этого в активированном окружении доступна команда `orchestrator`.
Окружение нужно активировать в каждом новом терминале
(`source .venv/bin/activate`), либо добавить эту строку в `~/.bashrc`.

Проверка:

```bash
orchestrator --help
pytest               # unit-тесты; к модели и Polygon не обращаются
```

### 5. Java и FreeMarker (проверка test-script'а)

`local check` и `polygon push` (перед загрузкой test-script'а) раскрывают
скрипт **настоящим FreeMarker** — тем же движком, что и Polygon, — и ищут
одинаковые строки (с ними Polygon отказывается генерировать тесты) и строки,
которые Polygon не примет (кавычки или лишний `>` в параметрах генератора). Для этого
нужны Java и jar FreeMarker.

По раскрытому скрипту `polygon push` ещё и ждёт, пока Polygon сгенерирует
тесты: test-script загружается последним шагом, и коммит делается только
после того, как тесты на Polygon совпали со строками скрипта (до 30 минут).
Без Java push полагается только на ответ Polygon на сохранение скрипта: не
дождался его — шаг падает, и закоммитить нужно вручную на Polygon, а потом
сделать `polygon pull`.

**Java** (JDK 11+; `.java`-файл запускается без сборки, поэтому нужен
именно JDK, а не только JRE) ставится вместе с системными пакетами из п. 2
(`default-jdk-headless`). Проверить:

```bash
java -version        # должна быть 11 или новее
```

Если пакет не ставили — `sudo apt install -y default-jdk-headless` (подойдёт
и любой другой JDK 11+, лишь бы `java` была в `PATH`).

**FreeMarker** отдельно ставить не нужно: при первой проверке оркестратор
сам скачивает `freemarker-2.3.33.jar` с Maven Central (с проверкой sha1) в
`~/.cache/polygon-orchestrator/`. Чтобы скачать его сразу и убедиться, что
всё работает:

```bash
python -c "from orchestrator.checks.test_script_check import freemarker_jar; print(freemarker_jar())"
```

Без доступа к Maven Central (прокси, офлайн) — скачайте jar вручную
(`https://repo1.maven.org/maven2/org/freemarker/freemarker/2.3.33/freemarker-2.3.33.jar`)
и либо положите его в `~/.cache/polygon-orchestrator/freemarker-2.3.33.jar`,
либо укажите путь в `.env`: `ORCHESTRATOR_FREEMARKER_JAR=/путь/к/freemarker-2.3.33.jar`.

Без Java или jar `local check` сообщает, что проверку выполнить нельзя, а
`polygon push` загружает скрипт без проверки, с предупреждением.

### 6. Git-хук (если будете присылать изменения в код)

```bash
git config core.hooksPath githooks
```

Хук `pre-push` не даёт запушить ветку или коммит, в имени/сообщении которого
есть `problem_id` вашей задачи из `problems/` или имя черновика из
`drafts/` — репозиторий публичный, задачи нет. Подробнее — в
[docs/SETUP.md](docs/SETUP.md) и в разделе «Конфиденциальность problems/»
[CLAUDE.md](CLAUDE.md).

### 7. Ключи Polygon и `.env`

```bash
cp .env.example .env
```

В Polygon: **Settings → API keys → Create** — получите `key` и `secret` и
впишите их в `.env`:

```dotenv
ORCHESTRATOR_LLM_BACKEND=claude-code
POLYGON_API_KEY=ваш_key
POLYGON_API_SECRET=ваш_secret
POLYGON_API_BASE_URL=https://polygon.codeforces.com/api
```

`.env` в `.gitignore` и никуда не коммитится. `ANTHROPIC_API_KEY` нужен
только при `ORCHESTRATOR_LLM_BACKEND=anthropic-api` (те же модели через
Anthropic API, деньги с API-баланса вместо подписки).

Задачи, созданные через `polygon push`, появятся в **вашем** аккаунте
Polygon.

### 8. Tab-дополнение (по желанию)

Из активированного окружения, один раз:

```bash
mkdir -p ~/.local/share/bash-completion/completions
register-python-argcomplete orchestrator \
    > ~/.local/share/bash-completion/completions/orchestrator
```

В новом терминале `orchestrator examples/<Tab>` дополняет адреса задач,
`orchestrator <задача> polygon pu<Tab>` — команды. Дополнять нужно из корня
репозитория.

---

## Первая задача: от черновика до Polygon

> ⚠️ Команды `init --draft` и `llm generate` вызывают модель и тратят лимиты
> подписки (общие с вашей обычной работой в Claude Code и claude.ai).
> Повторный прогон без изменений модель **не** вызывает — результаты
> кэшируются по хешу входа.

Все команды — из корня репозитория, с активированным `.venv`.

**1. Черновик.** Опишите задачу в свободной форме в файле `drafts/`, например
`drafts/my-task.md` (можно и в подкаталоге, `drafts/icpc/my-task.md`): суть, ввод-вывод, ограничения, идея решения, типичные
ошибки — что знаете. Чтобы понять, во что черновик превратится, посмотрите
спеки учебных задач: `examples/*/*/authoring/spec.yaml`.

**2. Спек по черновику.**

```bash
orchestrator problems/my-task init --draft my-task
# с учебной задачей как образцом формата и файлов:
orchestrator problems/my-task init --draft my-task --ref examples/array/prefix-sums
```

Модель пишет `problems/my-task/authoring/spec.yaml`. Всё, что она дописала
сама, помечено `# PROPOSED, REVIEW ME: ...` или `# SUGGESTION: ...` —
**прочитайте спек**, поправьте и удалите пометки. Отчёт — в
`problems/my-task/llm_reports/spec_from_draft.md`. Поменяли черновик — та же
команда доработает спек, а не напишет его заново: поправки в спеке
останутся.

Без `--draft` создаётся пустой спек из шаблона (или копия спека `--ref`),
который заполняется руками по [docs/SPEC_FORMAT.md](docs/SPEC_FORMAT.md).

**3. Генерация.**

```bash
orchestrator problems/my-task llm status     # что будет запущено (бесплатно)
orchestrator problems/my-task llm generate   # все шаги по порядку
```

После каждого шага смотрите его отчёт `problems/my-task/llm_reports/<шаг>.md`:
там заметки модели (`uncertain` — чего не хватает в спеке, `proposed` —
предположения, которые надо проверить, `suggestion` — идеи сверх спека),
ошибки компиляции и диффы.

Если шаг вернул `uncertain` — пайплайн останавливается: допишите в спек
то, что просит модель, и запустите `llm generate` снова (готовые шаги
пропустятся по кэшу). Поправили спек — перезапускаются только те шаги, на
которые правка влияет; текущие файлы при этом не переписываются с нуля, а
дорабатываются (в том числе с вашими ручными правками).

**4. Проверка и загрузка на Polygon.**

```bash
orchestrator problems/my-task local check    # оформление условия, дубли и кавычки в скрипте
orchestrator problems/my-task local check --full  # ещё и сгенерировать тесты: валидатор, одинаковые тесты
orchestrator problems/my-task local run      # валидатор и решения на примерах
orchestrator problems/my-task local run --full  # ещё и на всех тестах по скрипту
orchestrator problems/my-task local fix      # автоматически исправить то, что можно
orchestrator problems/my-task polygon push   # создать задачу на Polygon и загрузить
```

Дальше — тесты, пакет и проверка решений в Polygon руками. Если правили
задачу в Polygon — `polygon pull` перед следующим `push`.

---

## Справочник команд

Общий вид:

```
orchestrator [--base-dir DIR] <задача> <init|llm|local|polygon> [команда] [флаги]
```

`<задача>` — путь каталога задачи **вместе с корнем**: `problems/<путь>`
(ваши задачи, на любой глубине: `problems/contest-2026/round-1/my-task`) или
`examples/<путь>` (учебные). `problem_id` (имя задачи на Polygon) выводится
из пути: для `problems/` — имя последнего каталога (`my-task`), для
`examples/` — путь через `--` (`array--prefix-sums`).

`--base-dir` — каталог, в котором лежат `problems/` и `examples/` (по
умолчанию текущий).

### `init` — создать задачу

| Команда | Что делает | Модель |
|---|---|---|
| `init` | Каталог задачи + `authoring/spec.yaml` из шаблона с комментариями | нет |
| `init --ref <задача>` | Спек — копия спека другой задачи; она же добавляется в `references` как образец для шагов | нет |
| `init --draft NAME` | Спек пишет модель по черновику `drafts/NAME` (`NAME` — путь в `drafts/`, с расширением или без; имя без подкаталогов ищется и в них; с `--ref` — на его основе); копия черновика — в `authoring/` | **да** |

| `init --draft NAME` (спек уже есть) | Модель дорабатывает текущий спек (ручные правки сохраняются) по тому, что изменилось в черновике с его копии в `authoring/`; черновик не менялся — модель не вызывается (`--force` — вызвать всё равно); прежние версии — в `.llm_backup/` | **да** |

Без `--draft` существующий `spec.yaml` не перезаписывается.

### `llm` — генеративные шаги

Шаги по порядку: `statement_draft` → `constraints_pick` →
`limits_from_constraints` (механический, без модели) → `tests_plan` (план
тестов) → `generators_draft` (генераторы по плану) → `test_script_draft`
(test-script по плану) → `checker_draft` (только при
`checker.custom_needed: true`) → `interactor_draft` (только у интерактивных)
→ `solutions_draft`.

| Команда | Что делает | Модель |
|---|---|---|
| `llm status` | Таблица «шаг → cache hit / stale / not run / uncertain / conflict»; у `stale` — что именно изменилось | нет |
| `llm generate` | Прогон всех шагов; валидные по кэшу пропускаются | **да** |
| `llm generate --step NAME` | Только один шаг | **да** |
| `llm generate --force` | Игнорировать кэш, доработать текущие файлы заново | **да** |
| `llm generate --fresh` | С нуля, не показывая модели текущие файлы | **да** |
| `llm apply [--step NAME]` | Применить сохранённый, но не применённый ответ модели (например, после конфликта или ошибки) | нет |
| `llm accept [--step NAME]` | Пометить текущие файлы `stale`-шагов актуальными (если правка не должна менять результат, или вы уже поправили файлы руками); что именно изменилось у каждого шага — в `llm_reports/accept.md` | нет |

Что важно знать:

- **Кэш.** Шаг не вызывает модель, если не изменились его часть спека,
  результаты предыдущих шагов, промпты, образцы и модель.
- **Доработка.** Если файлы шага уже есть, модель получает их (с вашими
  ручными правками) и «что изменилось во входе» и дорабатывает их.
  Прежние версии — в `.llm_backup/<время>/`. Файлы, которые модель
  предлагает удалить, становятся `<имя>.removed` — удаляете вы сами.
- **Конфликты.** То, что нельзя применить, откладывается в
  `.llm_conflicts/<шаг>-<время>/` с `CONFLICTS.md`. Разберите, удалите
  каталог и сделайте `llm accept` (или `llm generate --force`).
- **Лимит подписки исчерпан** — `llm generate` останавливается на текущем
  шаге; после сброса лимита просто запустите снова.

### `local` — проверки без модели и сети

| Команда | Что делает |
|---|---|
| `local check` | Проверка условия (Markdown-списки, кавычки не `<<…>>`), дублей строк и параметров с кавычками/`>` в раскрытом test-script'е (нужна `java`) |
| `local check --full` | То же плюс локальная генерация тестов по скрипту: валидатор на каждом тесте и поиск одинаковых по содержимому (в том числе с примерами) |
| `local run` | Собрать валидатор и решения и прогнать на примерах (дольше, чем `check`) |
| `local run --full` | То же плюс все тесты по скрипту, с ответами от главного `ok`-решения: `wa`, `re`, `il` обязаны получить свой вердикт хотя бы раз |
| `local fix` | Исправить найденное `check`: списки → `itemize`/`enumerate`, кавычки → `<<…>>`; бэкап в `.fix_backup/` |

`local check --full`: одинаковые тесты только показываются, с командами и строками
скрипта, ошибкой не считаются. Тесты и сборки генераторов лежат в `.tests_cache/`, их
можно удалять; номер, файл и команда каждого теста — в `.tests_cache/manifest.txt`.
Заново генерируются только новые строки скрипта и тесты изменившихся генераторов.
Тесты сгенерированы другим компилятором, чем на Polygon, поэтому в редких случаях
(`long`, `std::shuffle`, порядок `unordered_*`) могут отличаться от тестов Polygon.

`local run --full`: `wa`, `re`, `il`, ни разу не получившие свой вердикт, — ошибка:
тесты не ловят заявленную ошибку (или решение на самом деле верное). `tl` без
TL — только предупреждение, потому что время локальное. Такие решения
останавливаются на первом заявленном вердикте, `ok` и `tl_ok` идут до конца. На
задаче с большим числом решений и большими тестами прогон может занять много минут.

`local run`: каждое решение из `solutions/` собирается и
запускается на `statement/examples/example_<N>.txt`, ответ проверяет чекер
задачи (свой `checker.cpp` или стандартный из `checker.standard`), ответы —
`output` примеров в спеке (если его нет — вывод главного `ok`-решения).
Решение с вердиктом X в имени может получить на примере OK или X (`tl_ok` —
OK или TL); всё остальное — ошибка. TL — из `limits.yaml`, время меряется
локально и с Polygon может не совпадать; решение не с вердиктом `tl`, которое
работало дольше половины TL, попадает в предупреждения. Для Java-решений нужен JDK
(`default-jdk-headless`). У интерактивной задачи решение запускается вместе
с `interactor.cpp`, чекер проверяет то, что интерактор записал в `tout`, а
ответом служит `tout` главного `ok`-решения. Решение, которое не сбрасывает
вывод и зависает в ожидании ответа, получает IL — допустимо только для `il`.
Если у примера задан свой ввод/вывод для условия
(`example_<N>.input_for_statement.txt` / `.output_for_statement.txt`, их
скачивает `polygon pull`), он тоже проверяется: ввод — валидатором, вывод —
чекером; у интерактивной задачи диалог из условия проигрывается с
интерактором. Флаг Polygon «verify input/output for statements» хранится в
`example_<N>.verify_for_statement.txt` (файл есть — флаг включён).

### `polygon` — синхронизация с Polygon

| Команда | Что делает | Сеть |
|---|---|---|
| `polygon status` | Какие polygon-шаги сделаны / устарели — по локальным файлам | нет |
| `polygon push` | Создать задачу (если нет) и загрузить только изменившееся с последней синхронизации; после каждого шага — коммит на Polygon | **да** |
| `polygon push --step NAME` | Только один шаг (`create_problem`, `upload_statement`, `upload_solutions`, …) | **да** |
| `polygon push --force` | Загрузить всё, даже если задачу меняли на Polygon (затирает те правки) | **да** |
| `polygon push --no-commit` | Загрузить без коммитов (все шаги или один, с `--step`; можно несколько раз подряд): всё остаётся в рабочей копии на Polygon. Потом закоммитьте там одной пачкой и сделайте `polygon pull` | **да** |
| `polygon pull` | Привязаться к существующей задаче на Polygon (по имени = `problem_id`), сравнить и скачать изменения; спек не нужен | **да**, только чтение |
| `polygon pull --polygon-id N` | Уточнить задачу по числовому id, если с таким именем их несколько | **да** |
| `polygon pull --overwrite` | Заменить и локальные файлы, которые менялись с обеих сторон; бэкап в `.pull_backup/` | **да** |

`push` останавливается, если задачу на Polygon меняли в обход оркестратора
(или там есть незакоммиченные правки), и предлагает сначала сделать `pull`.

### Переменные окружения (`.env`)

| Переменная | Значение |
|---|---|
| `ORCHESTRATOR_LLM_BACKEND` | `claude-code` (по умолчанию, подписка) или `anthropic-api` |
| `CLAUDE_CODE_BIN` | путь к `claude`, если он не в `PATH` |
| `ORCHESTRATOR_LLM_TIMEOUT` | предел одного вызова `claude -p` в секундах (по умолчанию 1800); не уложился — ошибка шага, на диск ничего не пишется |
| `ANTHROPIC_API_KEY` | только для `anthropic-api` |
| `POLYGON_API_KEY`, `POLYGON_API_SECRET` | ключи Polygon API |
| `POLYGON_API_BASE_URL` | `https://polygon.codeforces.com/api` |
| `ORCHESTRATOR_FREEMARKER_JAR` | путь к jar FreeMarker, если нет сети (иначе скачивается сам в `~/.cache/polygon-orchestrator/`) |
| `ORCHESTRATOR_SKIP_SCRIPT_CHECK=1` | отключить проверку test-script'а перед `push` (и ожидание генерации тестов по нему) |

---

## Что где лежит

```
drafts/                    черновики задач для init --draft (не в git)
problems/<путь задачи>/    ваши задачи (не в git)
  authoring/spec.yaml        спек — вход всех шагов
  authoring/constraints.yaml ограничения, группы, что проверяет валидатор
  authoring/tests_plan.yaml  план тестов: генераторы и тесты скрипта (можно править руками)
  statement/                 условие (.tex) и примеры
  limits.yaml                актуальные TL/ML (можно править руками)
  validator.cpp, checker.cpp, interactor.cpp
  generators/*.cpp, test_script, test_script_groups
  solutions/*
  llm_reports/<шаг>.md       отчёт последнего прогона шага — читать в первую очередь
  llm_reports/accept.md      что принял последний llm accept
  llm_generation.log         полный лог llm-прогонов
  polygon.log                полный лог push/pull
  polygon_state.json         привязка к задаче на Polygon
examples/<вид>/<суть>/     учебные задачи — образцы для --ref и references
templates/                 общие шаблоны: problem_lib.h, testlib.h, правила (tutorials/*.md)
prompts/                   промпты шагов
orchestrator/              код
```

`problems/` и `drafts/` в `.gitignore` специально: репозиторий публичный, а
задачи — нет, пока не прошёл контест.

---

## Совместная работа над задачами

Задачи не хранятся в git, поэтому обмениваться ими удобнее всего через
Polygon:

1. Владелец задачи даёт доступ на Polygon (**Manage access**, право
   `WRITE`).
2. Второй участник: `orchestrator problems/<путь>/<problem_id> polygon pull` —
   задача найдётся по имени, скачаются её файлы, а из Attachments — спек,
   `constraints.yaml`, план тестов и черновик.
3. Дальше обычный цикл: правки → `llm generate` / руками → `polygon push`.
   Если другой человек успел что-то запушить, `push` остановится и попросит
   сначала `pull`.

Кэш шагов (`.cache/`) через Polygon не переносится: после `pull` шаги будут
`not run`. Если файлы уже в порядке и перегенерировать их не нужно —
`llm accept` вместо `llm generate`.

---

## Частые проблемы

- **`orchestrator: command not found`** — не активировано окружение:
  `source .venv/bin/activate`.
- **`claude` не найден / требует обновления** — `claude --version`,
  `claude update`; или путь в `CLAUDE_CODE_BIN`. Плагин VS Code несёт свою
  копию бинаря, но оркестратор использует ту, что в `PATH`.
- **Модель тратит API-деньги вместо подписки** — проверьте
  `ORCHESTRATOR_LLM_BACKEND=claude-code` и что `claude` залогинен подпиской
  (`/status`). `ANTHROPIC_API_KEY` в `.env` при этом не мешает — оркестратор
  его не передаёт в `claude`.
- **`local check` пишет, что нет java** — `sudo apt install
  default-jdk-headless`.
- **`local check` не может скачать jar FreeMarker** — нет доступа к Maven
  Central; скачайте jar вручную и укажите `ORCHESTRATOR_FREEMARKER_JAR`
  (см. п. 5 установки).
- **Всё очень медленно в WSL** — проект лежит в `/mnt/c/...`; перенесите в
  `~/`.
- **`push` отказывается: задача изменена на Polygon** — `polygon pull`,
  разобрать конфликты, затем снова `push`. `--force` — только если точно
  нужно затереть правки на Polygon.
- **Шаг постоянно `uncertain`** — в спеке не хватает того, что модель не
  имеет права придумывать сама (асимптотика, точность ответа, правило
  сравнения в чекере…). Что именно — в `llm_reports/<шаг>.md`.

### Если работаете в репозитории через Claude Code

[CLAUDE.md](CLAUDE.md) подхватывается автоматически. Claude Code в этом
проекте пишет код оркестратора, а не содержимое задач, и **не запускает**
`llm generate` / `init --draft` без явной команды — они тратят лимиты
подписки.
