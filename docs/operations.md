# Настройка и эксплуатация Stands Engine

Это руководство предназначено для оператора движка: подготовка provider
credentials, cloud-init и SSH, запуск `create`/`destroy`, работа с результатами и
диагностика.

Формат `stand.yml` описан в [руководстве по манифесту](stand-manifest.md), а
формат пакета приложения — в
[руководстве по приложениям](application-manifest.md).

> `create` выполняет `pulumi up` и создаёт реальные ресурсы Hetzner. Проверьте
> выбранные server types, количество nodes и стоимость до запуска.

## 1. Требования

- Python `>=3.14` и `uv` для локального запуска;
- Pulumi CLI в `PATH`;
- аккаунт Hetzner Cloud и API token;
- существующие Hetzner network и административный SSH key;
- S3-совместимое хранилище для Pulumi state;
- доступ к registries приложений;
- на целевом image — рабочий cloud-init;
- Docker или Podman, если движок запускается через container launcher.

Python-зависимости:

```bash
uv sync
```

Проверка CLI:

```bash
uv run stands-engine --version
uv run stands-engine --help
pulumi version
```

## 2. Конфигурация окружения

Настройки читаются из environment или `.env`. Вложенность задаётся через `__`,
имена регистронезависимы, лишние переменные игнорируются.

```env
HCLOUD__TOKEN=

S3__ACCESS_KEY=
S3__SECRET_KEY=
S3__REGION=
S3__ENDPOINT=
S3__BUCKET=

STAND__USER=
STAND__PASSPHRASE=
STAND__PATH_TO_KEY=
STAND__PATH_TO_CONFIGSET=

OUTPUT__CONSOLE=true
OUTPUT__CONSOLE_SECRETS=false
OUTPUT__FILE=false
OUTPUT__FILE_PATH=
```

### Provider и state

| Переменная | Назначение |
|---|---|
| `HCLOUD__TOKEN` | Token Hetzner Cloud API |
| `S3__ACCESS_KEY` | S3 access key для Pulumi backend |
| `S3__SECRET_KEY` | S3 secret key |
| `S3__REGION` | Регион S3 |
| `S3__ENDPOINT` | Endpoint с `https://` или без схемы |
| `S3__BUCKET` | Bucket состояния |

Backend формируется как:

```text
s3://<bucket>/<STAND__USER>?region=<region>&endpoint=<endpoint>&s3ForcePathStyle=true
```

Pulumi project берётся из `stand.project`, stack — из `stand.env`.
`STAND__PASSPHRASE` используется provider `passphrase` для Pulumi secrets.

Для повторного `create` и последующего `destroy` используйте те же:

- `STAND__USER`;
- `STAND__PASSPHRASE`;
- `stand.project`;
- `stand.env`;
- S3 backend.

Иначе будет выбран другой backend prefix, project или stack либо станет
невозможно расшифровать state.

### Локальные пути

| Переменная | Назначение |
|---|---|
| `STAND__PATH_TO_KEY` | Файл приватного SSH key стенда |
| `STAND__PATH_TO_CONFIGSET` | Корень локальных отрендерированных файлов |
| `OUTPUT__FILE_PATH` | Каталог connection output |

Если `OUTPUT__FILE=true`, `OUTPUT__FILE_PATH` обязателен и должен быть каталогом
либо ещё не существовать.

### Загрузка env-файла

Для локального процесса:

```bash
set -a
source dev.env
set +a
```

Не добавляйте env-файл с credentials в Git. В CI используйте protected/secret
variables и ограничивайте вывод окружения.

## 3. SSH key и пользователи

`stand.users.sudo` и `stand.users.app` передаются cloud-init:

- sudo user получает публичный ключ и административный доступ;
- app user используется для rootless Podman и user systemd.

`stand.ssh.key_name_admin` — имя существующего SSH key в Hetzner, которое provider
передаёт создаваемому серверу. Оно не является путём к локальному ключу.

Локальная пара управляется через `STAND__PATH_TO_KEY`:

- если файл существует, движок читает private key и вычисляет public key;
- если файла нет при `create`, движок генерирует ключ, использует public part в
  cloud-init и записывает private key по указанному пути;
- при `destroy` существующий ключ используется только для построения модели,
  подключение к серверу не требуется.

Каталог для нового ключа должен существовать и быть доступен на запись. Защитите
private key правами файловой системы и резервной копией, если стенд нужно
диагностировать по SSH.

Hetzner SSH key из `key_name_admin` и сгенерированный ключ выполняют разные роли:
первый передаётся provider как ресурс Hetzner, второй добавляется cloud-init
административному пользователю.

## 4. Cloud-init

Каждый node profile должен ссылаться на Mako-файл:

```yaml
node_profiles:
  default:
    location: hel1
    type_serv: cpx32
    image: rocky-10
    network: demo-network
    cloud-init: ./cloud-init.yaml.mako
```

Относительный путь вычисляется от манифеста, где поле объявлено.

### Mako-контекст

| Переменная | Значение |
|---|---|
| `user_admin` | `stand.users.sudo` |
| `user_app` | `stand.users.app` |
| `ssh_public_key` | Public part локального ключа стенда |
| `network_ip_range` | CIDR найденной Hetzner network |

Минимальные фрагменты:

```yaml
#cloud-config
users:
  - name: ${user_admin}
    shell: /bin/bash
    ssh_authorized_keys:
      - ${ssh_public_key}
  - name: ${user_app}
    shell: /bin/bash
```

Текущий runtime ожидает, что cloud-init подготовит как минимум:

- административного и app users;
- SSH-доступ admin user;
- Podman;
- firewalld и зоны, используемые app roles;
- Podlet в `PATH`.

После provision движок ожидает `cloud-init status --wait`, затем самостоятельно
настраивает user systemd, linger, Podman socket и сеть `app-net`.

Готовый поддерживаемый шаблон:
[`demo/cloud-init.yaml.mako`](../demo/cloud-init.yaml.mako).

### Изменения cloud-init

Pulumi resource настроен с `ignore_changes=["user_data"]`. Изменение шаблона у
уже созданного server не применяет новый user data автоматически. Для существующей
ноды выполните настройку отдельно либо осознанно пересоздайте ресурс.

Перед использованием кастомного шаблона:

1. Отрендерите его тестовыми значениями.
2. Проверьте `cloud-init schema`.
3. Убедитесь, что выбранный OS image содержит нужные systemd/firewalld механизмы.
4. Проверьте установку Podman и Podlet без интерактивных действий.

## 5. Registries и secrets

Registry credentials находятся в итоговом манифесте, но значения рекомендуется
передавать через `!secret`:

```yaml
registries:
  local:
    url: registry.example.test
    username: robot
    password: !secret registry-password
```

```bash
export SECRET_REGISTRY_PASSWORD='change-me'
```

При `create` движок:

1. Проверяет наличие всех manifest secrets до cloud operations.
2. Выполняет `podman login` от app user.
3. Скачивает отсутствующие images.
4. Выполняет logout.

`insecure: true` добавляет `--tls-verify=false` к login/pull. Используйте только
для доверенной внутренней сети.

При `destroy` разрешено не передавать application preferences и registry
credentials. Структурные secrets остаются обязательными.

`!secret` не шифрует configsets и connection files. Не публикуйте их в Git,
логи или незащищённые CI artifacts.

## 6. Статическая проверка

До provision выполните полный parser:

```bash
set -a
source dev.env
set +a

uv run python -c \
  'from pathlib import Path; from ManifestParser import parse_manifest; parse_manifest(Path("demo/stand.yml")); print("manifest: OK")'
```

Команда не создаёт ресурсы. Она проверяет YAML, dependencies, secrets, связи и
нормализует пути.

Дополнительно вручную проверьте:

- Hetzner token и права;
- существование network и административного SSH key;
- доступность server type/image в выбранной location;
- S3 bucket, endpoint и credentials;
- доступность registries;
- возможность записи key/configset/output paths.

Проект пока не предоставляет `preview` или `validate` через CLI, хотя внутренний
provision layer содержит Pulumi preview.

## 7. Запуск

### Локально

```bash
uv run stands-engine create demo/stand.yml
uv run stands-engine destroy demo/stand.yml
```

Совместимый вариант:

```bash
python main.py create demo/stand.yml
```

CLI принимает только:

```text
stands-engine <create|destroy> <manifest>
```

### Через container launcher

```bash
./stands-engine --env-file dev.env create demo/stand.yml
./stands-engine --env-file dev.env destroy demo/stand.yml
```

Явный runtime/image:

```bash
./stands-engine \
  --runtime docker \
  --image registry.example.test/stands-engine:0.1.0 \
  --env-file dev.env \
  create demo/stand.yml
```

Launcher:

- выбирает Podman или Docker;
- монтирует workspace read-only в `/workspace`;
- создаёт `.stands-engine/keys`, `configsets`, `output`;
- переопределяет пути на `/data/...`;
- удаляет временный container после команды.

На Linux launcher использует SELinux volume labels для Podman. Для
воспроизводимости используйте immutable version tag или digest движка.

PowerShell:

```powershell
.\stands-engine.ps1 create .\demo\stand.yml -EnvFile dev.env
.\stands-engine.ps1 destroy .\demo\stand.yml -EnvFile dev.env
```

## 8. Lifecycle `create`

Фактическая последовательность:

1. Загрузка внешней конфигурации.
2. Parsing manifest, dependencies и secrets.
3. Validation и сборка модели; разворачивание agents.
4. Выбор/создание Pulumi stack в S3 backend.
5. Создание Hetzner servers, attachment к network, cloud-init и labels.
6. Получение public/private IP и подготовка SSH inventory.
7. Локальный рендеринг templates и hook assets.
8. Ожидание cloud-init на nodes.
9. Настройка Podman, firewalld, app user systemd, socket и `app-net`.
10. Registry login, параллельный pull images и logout.
11. Загрузка templates.
12. Генерация Podlet units и запуск user services.
13. Ожидание active service и каждого role port: до 30 попыток с интервалом
    2 секунды.
14. Выполнение post-start hooks.
15. Рендеринг и публикация connection output.

Движок проверяет service/listen socket, но не HTTP readiness и не dependency
graph. Hooks сложных кластеров должны иметь собственный retry/timeout.

### Pulumi state и события

Состояние хранится в S3 prefix `STAND__USER`; credentials передаются Pulumi через
AWS-compatible variables. Hetzner token записывается в stack config как secret.

Во время работы движок печатает resource operations, warnings/errors и итоговую
сводку Pulumi, но подавляет обычный progress и outputs.

## 9. Lifecycle `destroy`

```bash
uv run stands-engine destroy demo/stand.yml
```

`destroy`:

1. Загружает тот же manifest и backend identity.
2. Допускает неразрешённые app/registry secrets.
3. Выбирает существующий Pulumi stack.
4. Выполняет `pulumi destroy`.

Команда не удаляет локальные SSH keys, configsets, connection files, S3 stack
metadata или bucket.

Для гарантированного выбора нужного stack не изменяйте `STAND__USER`,
`stand.project`, `stand.env`, S3 settings и passphrase между `create` и
`destroy`.

## 10. Configsets

Результаты Mako сохраняются в:

```text
<STAND__PATH_TO_CONFIGSET>/<owner>_<project>_<env>/
└── <app>--<instance>/
    ├── <template-without-.mako>
    └── hook/
```

Configset полезен для диагностики фактически переданного server config. Он может
содержать passwords, tokens, keys и application data в открытом виде.

Рекомендации:

- исключить каталог из Git;
- ограничить filesystem access;
- не отправлять целиком в issue/логи;
- очищать устаревшие configsets по внутренней политике;
- учитывать, что текущий код перезаписывает файлы при следующем рендеринге.

## 11. Connection output

Connection templates определяются приложениями, но публикация настраивается
оператором:

| Переменная | Default | Поведение |
|---|---|---|
| `OUTPUT__CONSOLE` | `true` | Печатает общий JSON после успешного create |
| `OUTPUT__CONSOLE_SECRETS` | `false` | Показывает настоящие password и URL |
| `OUTPUT__FILE` | `false` | Сохраняет полный JSON |
| `OUTPUT__FILE_PATH` | — | Каталог, обязательный при file output |

Консоль по умолчанию заменяет `credentials.password` и `url` на `***`.
Дополнительные secret-подобные поля внутри `credentials` автоматически не
маскируются.

Файл:

```text
<OUTPUT__FILE_PATH>/<STAND__USER>_<project>_<env>.json
```

содержит реальные значения и создаётся с mode `0600`. Рассматривайте его как
секрет. File output выполняется только после успешных приложений и hooks.

Формат самого connection template описан в
[application guide](application-manifest.md#6-connection-template).

## 12. Диагностика

### Ошибка до Pulumi

Проверьте:

- Pydantic message об отсутствующей env variable;
- путь/расширение manifest;
- `from_dep_manifest` и локальные ресурсы;
- список отсутствующих `SECRET_*`;
- статический parser.

### Pulumi/S3

- убедитесь, что Pulumi CLI доступен процессу;
- проверьте endpoint, bucket, region и credentials;
- подтвердите прежние project/stack/passphrase;
- изучите `[pulumi:error]`, `[pulumi:warning]` и resource events.

### Hetzner

- token имеет нужные права;
- network и SSH key существуют;
- location поддерживает server type;
- account quota и billing позволяют создать nodes.

### SSH/cloud-init

- подключитесь private key из `STAND__PATH_TO_KEY`;
- проверьте `/var/log/cloud-init.log` и `cloud-init status --long`;
- убедитесь, что admin/app users созданы;
- проверьте firewalld, Podman, Podlet и user systemd.

### Приложение

На node:

```bash
systemctl --user --machine=userapp@.host status <instance>.service --no-pager
ss -ltn
```

Проверьте отрендерированный configset, image pull, host ports, Podman logs и
ошибки hook. Не вставляйте секретный configset в публичную диагностику.

## Checklist перед `create`

- [ ] Manifest прошёл статический parser.
- [ ] Проверено количество и стоимость Hetzner servers.
- [ ] Token, network, SSH key, locations, images и server types существуют.
- [ ] S3 backend доступен и сохранены identity/passphrase.
- [ ] Локальный key path защищён и доступен на запись.
- [ ] Cloud-init отрендерирован и проверен.
- [ ] Registries доступны, все `SECRET_*` переданы.
- [ ] Configset/output directories защищены и исключены из Git.
- [ ] App templates и hooks протестированы.
- [ ] Connection output настроен согласно политике секретов.
- [ ] Для первого запуска используется отдельный test stand.
- [ ] Известна команда `destroy` с теми же backend parameters.
