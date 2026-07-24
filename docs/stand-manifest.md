# Манифест стенда

Это руководство описывает только декларативный контракт `stand.yml`: какие
серверы нужны, какие приложения и инстансы существуют и где они размещаются.

Создание нового `app.yml` рассматривается в
[руководстве по приложениям](application-manifest.md), а provider credentials,
cloud-init и команды запуска — в
[руководстве по эксплуатации](operations.md).

## 1. Модель стенда

```text
stand
├── node_profiles ──> nodes
├── registries ─────> apps ──> instances
├── agents ─────────> instances на каждой node
└── nodes.apps ─────> размещённые instances
```

Манифест описывает желаемое состояние целиком. Имена образуют связи:

- приложение ссылается на registry;
- инстанс ссылается на роль своего приложения;
- node ссылается на profile и глобальные имена инстансов;
- agent ссылается на инстанс, который нужно размножить на все nodes.

## 2. Минимальный манифест

```yaml
version: 1

stand:
  project: demo
  env: test
  users:
    sudo: admin
    app: userapp
  ssh:
    key_name_admin: admin-key

node_profiles:
  default:
    location: hel1
    type_serv: cpx32
    image: rocky-10
    network: demo-network
    cloud-init: ./cloud-init.yaml.mako

registries:
  docker:
    url: docker.io

apps:
  hello:
    from_dep_manifest: ./app-registry/hello/app.yml
    instances:
      hello-main:
        role: web
        cpu: 250
        ram: 128

nodes:
  app-server:
    profile: default
    apps:
      - hello-main
```

Обязательные корневые mappings: `stand`, `node_profiles`, `registries`, `apps`,
`nodes`. Все они должны быть непустыми. `agents` необязателен.

`version: 1` используется во всех примерах, но текущий parser его не валидирует.

## 3. `stand`

```yaml
stand:
  project: payments
  env: test
  users:
    sudo: stand-admin
    app: userapp
  ssh:
    key_name_admin: company-admin-key
```

| Поле | Назначение |
|---|---|
| `project` | Имя Pulumi project и часть имени локальных результатов |
| `env` | Имя Pulumi stack/окружения |
| `users.sudo` | Административный пользователь создаваемых серверов |
| `users.app` | Пользователь, от которого запускаются приложения |
| `ssh.key_name_admin` | Имя уже существующего SSH key в Hetzner |

Все значения обязательны и должны быть непустыми строками.

`stand.project` и `stand.env` вместе с внешним `STAND__USER` идентифицируют
состояние и локальные артефакты. Не меняйте их у существующего стенда без
понимания, что движок выберет другой Pulumi project/stack.

Создание пользователей и SSH key описано в
[operations guide](operations.md#3-ssh-key-и-пользователи).

## 4. `node_profiles`

Profile содержит повторяющуюся конфигурацию сервера:

```yaml
node_profiles:
  default:
    location: hel1
    type_serv: cpx32
    image: rocky-10
    network: demo-network
    cloud-init: ./cloud-init.yaml.mako
    app_runtime: podman

  large:
    location: fsn1
    type_serv: cpx52
    image: rocky-10
    network: demo-network
    cloud-init: ./cloud-init.yaml.mako
    app_runtime: podman
```

| Поле | Требование |
|---|---|
| `location` | Обязательная локация Hetzner |
| `type_serv` | Обязательный тип Hetzner Server |
| `image` | Обязательное имя поддерживаемого Hetzner image |
| `network` | Имя уже существующей Hetzner network |
| `cloud-init` | Путь к обязательному Mako-шаблону |
| `app_runtime` | Необязательно; по умолчанию `podman` |

Если node не содержит `profile`, используется profile с именем `default`. Поэтому
либо объявите `default`, либо задавайте `profile` каждой node.

Все nodes стенда должны после применения overrides иметь один и тот же
`app_runtime`; несколько runtimes в одном стенде пока не поддерживаются.

Путь `cloud-init` разрешается относительно файла, где он объявлен. Контекст и
требования к шаблону описаны в [operations](operations.md#4-cloud-init).

## 5. `registries`

```yaml
registries:
  docker:
    url: docker.io

  local:
    url: registry.example.test
    username: robot
    password: !secret registry-password
    insecure: false
```

Registry key должен быть непустым и используется в `apps.*.image.registry`.

| Поле | Требование |
|---|---|
| `url` | Обязательная непустая строка без схемы, если её не принимает Podman |
| `username` | Необязательно, но только вместе с `password` |
| `password` | Необязательно, но только вместе с `username` |
| `insecure` | Необязательный boolean, по умолчанию `false` |

Registries удобно вынести:

```yaml
from_dep_manifest: ./app-registry/registries.yml
```

Правила безопасности и выполнение login/pull/logout описаны в
[operations](operations.md#5-registries-и-secrets).

## 6. `apps` и `instances`

Стенд подключает переиспользуемое приложение и добавляет конкретные параметры:

```yaml
apps:
  redis:
    from_dep_manifest: ./app-registry/redis/app.yml
    connection_instance: redis-main
    preferences:
      admin_user: stand-admin
      admin_pass: !secret redis-admin-password
    instances:
      redis-main:
        role: master-redis
        cpu: 1000
        ram: 2048
        oom_priority: -200
        preferences:
          shard: primary
        hooks: ./app-registry/redis/bootstrap
```

После раскрытия dependency приложение обязано иметь `name`, `image`, `roles`,
`templates` и непустой `instances`.

### App preferences

`preferences` — произвольный mapping общих параметров приложения. Его схема
определяется шаблонами конкретного приложения. Секретные значения задавайте через
`!secret`.

Механизм dependency не поддерживает overrides конфликтующих ключей. Поэтому
stand-specific preferences не следует одновременно объявлять в `app.yml`.

### Instance

| Поле | Требование |
|---|---|
| `role` | Обязательное имя роли этого приложения |
| `cpu` | Обязательный положительный integer в millicpu |
| `ram` | Обязательный положительный integer в десятичных MB |
| `oom_priority` | Необязательный integer `-1000..1000` |
| `preferences` | Необязательный mapping параметров инстанса |
| `hooks` | Необязательный путь к каталогу hook |

Имена инстансов глобальны для всего стенда, включая разные приложения. Повтор
запрещён.

Относительный `hooks` разрешается от манифеста, в котором поле объявлено. Если
поле находится в `stand.yml`, путь указывайте относительно `stand.yml`.

### Connection instance

Если подключённый `app.yml` объявляет `connection`, стенд обязан выбрать
`connection_instance` из инстансов того же приложения.

Без `connection` поле запрещено. Agent-инстанс выбрать нельзя. Формат
connection template описан в
[application guide](application-manifest.md#6-connection-template).

## 7. `nodes`

```yaml
nodes:
  api-server:
    profile: default
    apps:
      - api-main
      - redis-main

  worker-server:
    profile: large
    location: nbg1
    apps:
      - worker-1
```

Каждый node key — имя создаваемого Hetzner Server. Node требует список `apps`,
который может быть пустым.

`profile` необязателен и по умолчанию равен `default`. Поддерживаемые overrides:

```yaml
nodes:
  special-server:
    profile: default
    location: fsn1
    type_serv: cpx42
    image: rocky-10
    network: other-network
    cloud-init: ./special-cloud-init.yaml.mako
    app_runtime: podman
    apps: []
```

Overrides заменяют соответствующие значения выбранного profile. Текущий
валидатор подробно проверяет profile, но не типы всех override-полей; используйте
те же непустые строковые значения, что и в profile.

### Правила размещения

- каждый обычный инстанс обязан находиться ровно на одной node;
- неизвестное имя инстанса запрещено;
- повтор на той же или другой node запрещён;
- agent-инстанс нельзя размещать вручную;
- node должна ссылаться на существующий profile.

Порядок `nodes` и `apps` не является dependency graph приложений.

## 8. `agents`

Agent — объявленный инстанс, который автоматически запускается на каждой node:

```yaml
apps:
  dozzle:
    from_dep_manifest: ./app-registry/dozzle/app.yml
    instances:
      dozzle:
        role: logs-viewer
        cpu: 500
        ram: 512

agents:
  apps:
    - dozzle
```

Для nodes `master` и `worker` движок создаст:

```text
dozzle--master
dozzle--worker
```

Эти имена становятся фактическими `instance.name`, ключами Mako `apps`,
service/container names и частями configset.

Ограничения:

- список содержит существующие уникальные имена инстансов;
- agent не указывается в `nodes.*.apps`;
- agent не может быть `connection_instance`;
- генерируемое имя не должно конфликтовать с явным инстансом.

`agents` можно не указывать. `agents.apps: []` допустим.

## 9. `from_dep_manifest`

Ключ можно использовать внутри любого YAML mapping:

```yaml
apps:
  redis:
    from_dep_manifest: ./app-registry/redis/app.yml
    instances:
      redis-main:
        role: master-redis
        cpu: 500
        ram: 512
```

Правила:

1. Путь вычисляется относительно текущего манифеста.
2. Dependencies раскрываются рекурсивно.
3. Загруженный mapping объединяется с локальными ключами на текущем уровне.
4. Одинаковый ключ с разными значениями вызывает ошибку.
5. Локальное значение не переопределяет dependency.
6. Пути templates, connection, hooks и cloud-init нормализуются относительно
   соответствующих объявлений.

Файл должен иметь расширение `.yml` или `.yaml` и содержать YAML mapping.

## 10. `!secret`

```yaml
preferences:
  password: !secret service-password
```

Имя преобразуется в env variable:

```text
service-password -> SECRET_SERVICE_PASSWORD
```

Допустимый шаблон имени: `[A-Za-z][A-Za-z0-9_-]*`.

`!secret` применяется только к scalar value. Его нельзя использовать как mapping
key или для list/mapping. Подставленное значение всегда является строкой.

Перед `create` все ссылки должны иметь непустые значения. При `destroy` можно не
передавать секреты из `preferences` и registry credentials; структурные значения
остаются обязательными.

Подробнее о загрузке окружения и защите результатов:
[operations](operations.md#5-registries-и-secrets).

## 11. Полный пример

Полный актуальный пример находится в [`demo/stand.yml`](../demo/stand.yml). Он
показывает:

- общий node profile;
- внешний registry manifest;
- несколько приложений и ролей;
- cluster preferences и secrets;
- ресурсы и hooks;
- connection instances;
- agent на каждой node;
- распределение обычных инстансов по трём nodes.

Используйте demo как структуру, но замените project, env, users, provider resource
names, credentials и размеры серверов.

## Проверка манифеста

Отдельной CLI-команды `validate` пока нет. Выполните parser напрямую:

```bash
set -a
source dev.env
set +a

uv run python -c \
  'from pathlib import Path; from ManifestParser import parse_manifest; parse_manifest(Path("demo/stand.yml")); print("manifest: OK")'
```

Это раскрывает dependencies, разрешает secrets, нормализует пути и проверяет
связи, но не создаёт облачные ресурсы.

Валидатор проверяет:

- обязательные mappings и строки;
- registry credentials и ссылки image;
- совпадение app key/name;
- roles, resources и OOM range;
- connection ownership;
- глобальную уникальность инстансов;
- profiles и размещение;
- agents и конфликты генерируемых имён;
- обязательные secrets.

После проверки YAML отдельно убедитесь, что provider resources реально
существуют. Это относится к эксплуатационной проверке, а не к parser.

## Типичные ошибки

- **Missing section** — отсутствует обязательный корневой mapping.
- **`app.name must match app key`** — ключ app отличается от подключённого name.
- **Unknown registry/role/profile/instance** — нарушена именованная ссылка.
- **`from_dep_manifest conflicts`** — локальный ключ пытается переопределить
  dependency.
- **Instance is not assigned** — обычный инстанс отсутствует во всех nodes.
- **Instance already assigned** — инстанс указан более одного раза.
- **Agent cannot be assigned** — agent вручную добавлен на node.
- **Connection outside app** — выбран инстанс другого приложения.
- **Only one app runtime** — profiles/overrides дают разные runtimes.
- **Missing manifest secret** — соответствующая `SECRET_*` отсутствует или пуста.

## Checklist автора стенда

- [ ] `project`, `env`, users и SSH key name заданы осознанно.
- [ ] Каждый profile содержит все обязательные provider-поля.
- [ ] Hetzner network, images, server types и locations существуют.
- [ ] Все nodes используют один app runtime.
- [ ] Registries объявлены, credentials заданы парами.
- [ ] Каждый app key совпадает с `app.yml` name.
- [ ] Все обязательные app preferences заполнены.
- [ ] Имена инстансов глобально уникальны.
- [ ] CPU/RAM положительны, OOM priority находится в диапазоне.
- [ ] Каждый обычный инстанс размещён ровно на одной node.
- [ ] Agents не размещены вручную.
- [ ] Connection instance принадлежит нужному приложению.
- [ ] Относительные пути вычисляются от правильного файла.
- [ ] Все `SECRET_*` переданы процессу.
- [ ] Статическая проверка проходит без облачных операций.
- [ ] Перед `create` выполнен operational checklist из
      [operations](operations.md#checklist-перед-create).
