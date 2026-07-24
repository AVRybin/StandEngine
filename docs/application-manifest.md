# Описание приложений

Это руководство предназначено для автора переиспользуемого приложения Stands
Engine. Оно описывает каталог приложения, контракт `app.yml`, Mako-шаблоны,
connection template и post-start hooks.

Размещение инстансов по серверам и остальные поля `stand.yml` описаны отдельно в
[руководстве по манифесту стенда](stand-manifest.md).

## 1. Модель и структура каталога

Описание приложения хранит неизменную между стендами часть:

- container image;
- доступные роли;
- открываемые ролью порты;
- шаблоны файлов;
- опциональный шаблон данных подключения.

Параметры окружения, credentials, инстансы и их ресурсы задаются в `stand.yml`.

```text
app-registry/
└── hello/
    ├── app.yml
    ├── hello.yml.mako
    ├── connection.json.mako       # необязательно
    └── bootstrap/                 # необязательно
        ├── hook.sh.mako
        └── data.json.mako
```

Обязательны `app.yml` и хотя бы один шаблон. Для запуска должен существовать
template с ключом `pod`.

## 2. Минимальное приложение

`app-registry/hello/app.yml`:

```yaml
version: 1
name: hello

image:
  registry: docker
  path: library/nginx
  version: "1.27-alpine"

roles:
  web:
    ports:
      - number: 8080
        protocol: tcp
        zone: internal

templates:
  pod:
    path: hello.yml.mako
    dest: /home/userapp/hello.yml
    owner: userapp
    mode: "644"
```

`app-registry/hello/hello.yml.mako`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: ${instance.name}
  labels:
    stands-engine.io/managed: "true"
% if instance.oom_priority is not None:
  annotations:
    io.podman.annotations.oom_score_adj: "${instance.oom_priority}"
% endif
spec:
  restartPolicy: Always
  containers:
    - name: ${instance.name}
      image: ${cluster.image.full_name}
      resources:
        requests:
          cpu: "${instance.cpu}m"
          memory: "${instance.ram}M"
        limits:
          cpu: "${instance.cpu}m"
          memory: "${instance.ram}M"
      ports:
        - containerPort: 80
          hostPort: 8080
          hostIP: ${node.private_ip}
```

Минимальное подключение в стенде:

```yaml
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

Ключ `apps.hello` должен совпадать с `name: hello`.

## 3. Контракт `app.yml`

Полная переиспользуемая часть:

```yaml
version: 1
name: example

image:
  registry: docker
  path: organization/example
  version: "2.4.1"

roles:
  api:
    preferences:
      container_port: 8080
    ports:
      - number: 8080
        protocol: tcp
        zone: internal
  worker: {}

templates:
  pod:
    path: example.yml.mako
    dest: /home/userapp/example.yml
    owner: userapp
    mode: "644"
  app-config:
    path: application.conf.mako
    dest: /home/userapp/application.conf
    owner: userapp
    mode: "600"

connection: connection.json.mako
```

| Поле | Требование |
|---|---|
| `version` | Рекомендуется `1`; текущий parser поле не валидирует |
| `name` | Обязательная непустая строка, совпадающая с ключом приложения в стенде |
| `image` | Обязательный mapping с `registry`, `path`, `version` |
| `roles` | Обязательный непустой mapping |
| `templates` | Обязательный непустой mapping; runtime ожидает ключ `pod` |
| `connection` | Необязательный путь к Mako-шаблону connection JSON |

### Image

```yaml
image:
  registry: local
  path: team/service
  version: "2026.07"
```

- `registry` — ключ из `registries` стенда, а не URL;
- `path` — путь образа без registry и tag;
- `version` — непустая строка; числовой tag нужно заключать в кавычки.

Полное имя формируется как
`<registry.url>/<image.path>:<image.version>`.

### Roles и ports

```yaml
roles:
  leader:
    preferences:
      quorum_port: 7000
    ports:
      - number: 7000
        protocol: tcp
        zone: internal
  follower: {}
```

Роль задаёт логическую функцию инстанса, постоянные role preferences и порты.
Для порта обязательны:

- `number` — integer; используйте допустимый диапазон `1..65535`;
- `protocol` — только `tcp` или `udp`;
- `zone` — непустое имя firewalld zone.

Role port открывает firewall и добавляет ожидание listen socket. Он не создаёт
`containerPort`/`hostPort` в pod и не выполняет прикладной health check. Порты в
роли и шаблоне необходимо согласовать вручную.

### Templates

```yaml
templates:
  pod:
    path: service.yml.mako
    dest: /home/userapp/service.yml
    owner: userapp
    mode: "644"
```

Каждый template требует непустые `path`, `dest`, `owner` и `mode`. Относительный
`path` вычисляется от файла, в котором объявлен.

Движок рендерит и загружает все templates для каждого инстанса. Дополнительный
template сам по себе не используется приложением: pod или hook должен обратиться
к его `dest`.

Ключ `pod` специальный. Из его `dest` движок создаёт unit:

```text
podlet --overwrite --unit-directory --name <instance> \
  podman kube play --network app-net --no-pod-prefix <dest>
```

Содержимое должно поддерживаться `podman kube play` и Podlet. Валидатор пока не
проверяет наличие ключа `pod`.

`owner` и домашний каталог в `dest` должны соответствовать `stand.users.app`.

## 4. Mako-контекст

Все app templates, connection template и файлы hook получают:

| Переменная | Основные поля |
|---|---|
| `node` | `private_ip`, `public_ip`, `app_runtime` |
| `instance` | `name`, `cpu`, `ram`, `oom_priority`, `preferences`, `role` |
| `role` | `name`, `ports`, `preferences` |
| `cluster` | `name`, `image.full_name`, `preferences`, `instances_app` |
| `apps` | Все инстансы стенда по глобальному имени |

Текущий инстанс:

```mako
${instance.name}
${instance.cpu}
${instance.ram}
${role.name}
${node.private_ip}
${cluster.image.full_name}
${cluster.preferences.log_level}
${instance.preferences['shard']}
${role.preferences['container_port']}
```

`cluster.preferences` поддерживает точечный доступ. Для role/instance preferences
предпочтителен доступ как к обычному mapping.

Другой инстанс представлен wrapper с `node`, `app` и `cluster`:

```mako
${apps['database-main'].node.private_ip}
${apps['database-main'].app.preferences['shard']}
${apps['database-main'].cluster.preferences['password']}
```

Ключ — глобальное имя инстанса, а не имя приложения. Такая ссылка создаёт контракт
на имя, который нужно обновлять при переименовании.

### Условия и экранирование

```mako
% if role.name == "leader":
        - name: MODE
          value: leader
% endif
```

Для строк в JSON и JSON-совместимом YAML используйте encoder:

```mako
<%!
import json
%>
value: ${json.dumps(cluster.preferences.message)}
```

Литеральные shell `${VARIABLE}` Mako воспринимает как выражение. Защитите блок:

```mako
<%text>
echo "${SHELL_VARIABLE}"
</%text>
```

### Ресурсы

`cpu`, `ram` и `oom_priority` только передаются в контекст. Чтобы они применялись,
шаблон должен использовать:

```yaml
resources:
  requests:
    cpu: "${instance.cpu}m"
    memory: "${instance.ram}M"
  limits:
    cpu: "${instance.cpu}m"
    memory: "${instance.ram}M"
```

Для OOM priority используется annotation
`io.podman.annotations.oom_score_adj`. Значение задаётся инстансом в диапазоне
`-1000..1000`.

## 5. Preferences и зависимости

Существуют три независимых уровня:

| Уровень | Доступ |
|---|---|
| Приложение `apps.<app>.preferences` | `cluster.preferences` |
| Роль `roles.<role>.preferences` | `role.preferences` |
| Инстанс `instances.<name>.preferences` | `instance.preferences` |

Mappings автоматически не сливаются. Схема пользовательских preferences не
валидируется, поэтому приложение должно документировать обязательные ключи.

Для связи приложений используйте `apps`:

```mako
bootstrapServers: ${apps['redpanda-master'].node.private_ip}:19092
```

Движок ожидает active service и role ports, но не строит dependency graph.
Приложение или hook должны самостоятельно дожидаться прикладной готовности
зависимости с ограниченным timeout.

## 6. Connection template

В `app.yml`:

```yaml
connection: connection.json.mako
```

Стенд дополнительно выбирает обычный инстанс этого приложения через
`connection_instance`.

```mako
<%!
import json
from urllib.parse import quote
%><%
user = cluster.preferences.admin_user
password = cluster.preferences.admin_pass
endpoint = node.private_ip
url = f"redis://{quote(user, safe='')}:{quote(password, safe='')}@{endpoint}:6379"
%>{
  "endpoint": ${json.dumps(endpoint)},
  "port": 6379,
  "credentials": {
    "user": ${json.dumps(user)},
    "password": ${json.dumps(password)}
  },
  "url": ${json.dumps(url)}
}
```

Результат — один JSON object:

| Поле | Контракт |
|---|---|
| `endpoint` | Обязательная непустая строка |
| `port` | Обязательный integer `1..65535` |
| `credentials` | Обязательный object |
| `credentials.user/password` | Обязательные непустые строки |
| `url` | Необязательная непустая строка |

В `credentials` разрешены дополнительные поля. Другие поля верхнего уровня
запрещены. Значения обязательно сериализуйте через `json.dumps`; URL-компоненты
также кодируйте через `urllib.parse.quote`.

Настройки публикации и защиты connection output описаны в
[руководстве по эксплуатации](operations.md#11-connection-output).

## 7. Post-start hooks

Каталог hook:

```text
bootstrap/
├── hook.sh.mako
└── migration/
    └── initial.json.mako
```

Путь подключается к конкретному инстансу через `instances.<name>.hooks`.
Каталог обязан содержать `hook.sh.mako`.

Движок:

1. Рекурсивно рендерит каждый файл как Mako-текст.
2. Удаляет суффикс `.mako`.
3. Загружает дерево в `/home/<app-user>/hook/<instance>/` с mode `644`.
4. Выполняет `hook.sh` из корня дерева.
5. После успеха удаляет remote-каталог.

Не помещайте бинарные файлы: все ресурсы читаются как текст. Даже файл без
`.mako` проходит renderer.

Надёжный hook должен:

- завершаться при ошибке и возвращать ненулевой exit code;
- быть идемпотентным;
- иметь retry с общим timeout для зависимостей;
- не печатать секреты;
- использовать относительные пути от корня hook.

Примеры: [`mongo/hook`](../demo/app-registry/mongo/hook) и
[`redpanda/migration`](../demo/app-registry/redpanda/migration).

## 8. Проверка приложения

До реального стенда:

1. Отрендерите каждый Mako-шаблон с тестовыми `node`, `instance`, `role`,
   `cluster`, `apps`.
2. Разберите pod через `yaml.safe_load`.
3. Проверьте image, имена, resources, volumes и ports.
4. Разберите connection через `json.loads` и проверьте контракт.
5. Проверьте hook повторным выполнением.
6. Подключите приложение к минимальному тестовому `stand.yml` и выполните
   статическую проверку из [stand guide](stand-manifest.md#проверка-манифеста).

Подход к unit-тесту рендеринга показан в
[`tests/test_app_resources.py`](../tests/test_app_resources.py).

## 9. Типичные ошибки

- **`app.name must match app key`** — `name` не совпадает с ключом в `apps`.
- **Unknown registry** — `image.registry` содержит URL вместо ключа registry.
- **Image version is not a string** — заключите числовой tag в кавычки.
- **Ошибка `templates["pod"]`** — templates непустой, но ключ `pod` отсутствует.
- **Порт не слушается** — role port не согласован с `hostPort` или listen address.
- **Ресурсы не применились** — template не использует `instance.cpu/ram`.
- **Другой app не найден** — в `apps[...]` указано имя приложения вместо инстанса.
- **Hook падает на `${VAR}`** — shell expression не защищён от Mako.
- **Connection не создаётся** — отсутствует `connection` или
  `connection_instance`.

## Checklist автора приложения

- [ ] `name` стабилен и совпадёт с ключом приложения.
- [ ] Registry указан ключом, image tag является строкой.
- [ ] Роли и порты соответствуют реальному поведению контейнера.
- [ ] В `templates` есть `pod`.
- [ ] Пути, owner и mode подходят app user.
- [ ] Pod использует `instance.name` и `cluster.image.full_name`.
- [ ] CPU, RAM и OOM priority отражены в шаблоне.
- [ ] Контракт preferences понятен автору стенда.
- [ ] Все строки безопасно экранируются.
- [ ] Зависимости имеют retry/timeout.
- [ ] Connection выдаёт допустимый JSON.
- [ ] Hook текстовый, идемпотентный и не раскрывает секреты.
- [ ] Все templates успешно рендерятся и разбираются.

Готовые эталоны находятся в
[`demo/app-registry`](../demo/app-registry): Redis — простой stateful service,
Redpanda — несколько ролей, Kafka UI — зависимость, MongoDB — hook и connection,
Dozzle — node agent.
