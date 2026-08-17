# Narezka OS на голом Ubuntu — альфа-тест

Ubuntu 24.04 (в ней Python 3.12 из коробки). На 22.04 Python придётся ставить
отдельно.

Требования к машине: 4+ ядра, 8+ ГБ памяти, 100+ ГБ диска. Считайте
**1.8 ГБ диска и 45 минут процессора на каждый час обрабатываемой записи**.

---

## 1. Установка

```bash
sudo apt update
sudo apt install -y ffmpeg python3.12 python3.12-venv git curl

# Node нужен только чтобы собрать интерфейс
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

sudo useradd -m -s /bin/bash narezka
sudo -iu narezka
```

Дальше от пользователя `narezka`:

```bash
git clone <адрес-репозитория> ~/narezka
cd ~/narezka

python3.12 -m venv .venv
.venv/bin/pip install -e ".[pipeline]"

cd frontend && npm ci && npm run build && cd ..

.venv/bin/narezka doctor      # должно быть «Окружение готово»
```

---

## 2. Ключ модели

```bash
cp .env.example .env
nano .env      # OPENROUTER_API_KEY=sk-or-v1-...
```

Проверка: `.venv/bin/narezka models` — покажет доступные модели и работает ли
ключ.

---

## 3. Конфиг сервера

`configs/config.yaml`, добавить или поправить:

```yaml
profile: batch          # dev = small (быстро, хуже текст), batch = large-v3

auth:
  enabled: true         # без него сервис открыт всем, кто знает адрес
  secure_cookie: true   # только по https

billing:
  enabled: true
  version: 1
  per_video_hour: {analysis: 20, shorts: 8, long: 12}
  minimum: 2
  signup_bonus: 60

queue:
  parallel: 1           # поднимать, только если ядер сильно больше шести

sources:
  allowed_hosts: [youtube.com, twitch.tv]
  max_upload_gb: 10

retention:
  source_days: 14       # 0 = не удалять исходники
  check_hours: 6
```

---

## 4. Автозапуск

`/etc/systemd/system/narezka.service`:

```ini
[Unit]
Description=Narezka OS
After=network.target

[Service]
Type=simple
User=narezka
WorkingDirectory=/home/narezka/narezka
ExecStart=/home/narezka/narezka/.venv/bin/narezka serve --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
# Обработка съедает все ядра; оставляем одно системе
CPUQuota=500%
MemoryMax=10G

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now narezka
sudo systemctl status narezka
journalctl -u narezka -f        # логи
```

Слушает только `127.0.0.1` — наружу открывает туннель или прокси.

---

## 5. Доступ снаружи

**Вариант А, для своих** — Tailscale, ничего не публикуется в интернет:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# в systemd поменять --host 127.0.0.1 на --host 0.0.0.0
```

Адрес вида `http://100.x.x.x:8000` внутри своей сети Tailscale.
`secure_cookie: false`, если без https.

**Вариант Б, публичный адрес** — Caddy, сертификат сам:

```bash
sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```
narezka.example.com {
    reverse_proxy 127.0.0.1:8000
    request_body {
        max_size 10GB          # загрузка записей
    }
}
```

```bash
sudo systemctl reload caddy
```

---

## 6. Команды админа

Все — из `~/narezka`, через `.venv/bin/narezka`.

### Люди и доступ

```bash
narezka invite new --credits 100 --note "Вася"   # выдать код приглашения
narezka invite list                              # кто вошёл, кто ещё нет
narezka user add --login ivan --password ...     # завести напрямую
narezka user list
```

### Деньги

```bash
narezka credits show --workspace ivan            # счёт, цены, движения
narezka credits add --workspace ivan --amount 500 --note "оплата"
narezka usage --days 30                          # часы записи и машинное время
```

Пространство (`--workspace`) видно в `narezka user list`.

### Записи

```bash
narezka status                                   # все записи проекта
narezka status --video-id abc123                 # стадии одной записи
narezka add --url https://twitch.tv/videos/1 --title "Стрим" --project ivan
narezka rename --video-id abc123 "Новое имя" --project ivan
narezka pipeline --video-id abc123 --group analysis   # analysis|shorts|long
narezka run transcribe --video-id abc123 --force      # одна стадия заново
narezka stop --video-id abc123                        # остановить после стадии
```

### Место на диске

```bash
narezka prune                        # что можно освободить и чем восстановимо
narezka prune --apply                # удалить всё, кроме исходников
narezka prune --apply --source       # и исходники тоже
narezka clean                        # мусор от прерванных запусков
du -sh storage/projects/*/videos/*   # кто сколько занимает
```

### Проверка

```bash
narezka doctor                       # ffmpeg, место, устройство, шрифты
narezka stages                       # стадии и куски работы
systemctl status narezka
```

---

## 7. Проверки после запуска

```bash
curl -s localhost:8000/api/auth/me            # auth_required: true
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/api/videos   # 401
```

В браузере:

- открывается форма входа, а не каталог;
- неверный пароль и несуществующий логин дают одинаковую ошибку;
- после пяти неверных попыток — отказ на 15 минут;
- вкладка «Исходник» не начинает качать запись сама;
- ссылка `http://127.0.0.1/x` при добавлении отклоняется;
- запись не принимается без галочки о правах.

---

## 8. Обновление

```bash
sudo -iu narezka
cd ~/narezka
git pull
.venv/bin/pip install -e ".[pipeline]"
cd frontend && npm ci && npm run build && cd ..
sudo systemctl restart narezka
```

Незаконченные задачи после перезапуска сами возвращаются в очередь.
Пересчитывается только та стадия, что не успела завершиться.

---

## 9. Резервные копии

Копировать `~/narezka/storage` — там записи, ролики, база с учётками,
счетами и очередью. Плюс `configs/config.yaml` и `.env`.

```bash
# без исходников — они самые тяжёлые и восстановимы скачиванием
tar --exclude='*/source/*' -czf narezka-$(date +%F).tar.gz storage configs .env
```

База: `storage/narezka.db` (SQLite). Копировать при остановленном сервисе
или командой `sqlite3 storage/narezka.db ".backup копия.db"`.

---

## 10. Что где лежит

```
storage/
  narezka.db                        учётки, сессии, приглашения, счета, очередь
  uploads/<пространство>/           временные файлы загрузки
  projects/<пространство>/videos/<id>/
    source/     исходник (79% места, чистится retention)
    audio/      дорожка для распознавания
    transcript/ расшифровка
    analysis/   моменты, отбор, эпизоды, разметка человеком
    shorts/     готовые вертикальные ролики
    meta/       метаданные, кэш стадий, кадры и куски предпросмотра
```

---

## 11. Типичные проблемы

**Первый запуск обработки идёт очень долго.** Качаются веса модели
распознавания (`large-v3` — около 3 ГБ). Один раз; лежат в
`~/.cache/huggingface`.

**«Больше одного медиафайла».** В `source/` попало два файла. Оставить один.

**Модель отвечает 429.** У бесплатных моделей общий пул. Запасные уже
настроены; если стабильно — переключить `llm.provider` на платный в
`configs/config.yaml`.

**Память кончается.** `queue.parallel: 1` и `MemoryMax` в systemd. Пятичасовая
запись требует около 1 ГБ на чтение звука плюс модель.

**Диск кончается.** `narezka prune --apply --source`, затем
`retention.source_days` поменьше.

**Соединение с моделью падает через SOCKS-прокси.** Схема `socks://` не
понимается — нужна `socks5://`. Программа нормализует её сама, но если
прокси задан у systemd — проверьте `Environment=` в юните.

**Порт занят после перезапуска.** `ss -ltnp | grep 8000`, убить старый
процесс по pid. `systemctl restart` делает это сам.
