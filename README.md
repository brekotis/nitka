# Нитка

Веб-сервис турклуба «Восхождение»: из трека похода — обзорная карта, рабочие листы, карты дней с профилем и общий высотный профиль (PNG 300 dpi, JPG, PDF).

## Как устроено

| Папка | Что там |
|---|---|
| `engine/` | Движок. `importers.py` — GPX, KML/KMZ, GeoJSON, ZIP, ссылки nakarte (nktk v1–4, nktl). `classify.py` — дни по названиям треков и времени GPS, типы точек по названиям. `model.py` — поход как данные (дни, радиалки, ночёвки, профили, километраж). `sheets.py` — листы. `mapkit.py`, `profile.py`, `dem.py` — рисование, профиль, высоты. `suggest.py` — подсказки: ночёвки по концам дней и перевалы по маршруту из каталога Вестры (файл кэшируется, сервер обновляет его раз в неделю). `cli.py` — сборка в отдельном процессе. |
| `server/app.py` | HTTP-сервер на стандартной библиотеке: вход по имени, проекты в SQLite, очередь сборки, выдача файлов, прокси тайлов с кэшем. |
| `web/` | Интерфейс без сборки: `app.js`, `style.css`, Leaflet и шрифты лежат рядом. |
| `fonts/` | DejaVu Sans — шрифты самих карт. |

Проект похода — один JSON: `meta` (название, район, дата первого дня, коэффициент), `pieces` (куски нитки: день, роль), `points` (точки с типами; `grade` — категория, `key` — определяющее препятствие, `label_pos` — сторона подписи), `bands` (участки с особым характером пути: тип + начало и конец на нитке), `custom_types`, `sheets` (какие листы собирать, `logo` — логотип клуба в углу, `adjust` — ручные рамки листов: центр и масштаб). Готовые файлы лежат в `data/renders/<id>` и удаляются через `NITKA_KEEP_DAYS` (7); проект остаётся.

## Запуск

```
python3 -m server.app        # NITKA_PORT=8080 NITKA_HOST=127.0.0.1 NITKA_DATA=./data
python3 -m engine.cli build project.json out_dir [--only overview,day-5]
```

Нужны Python 3.11+, Pillow, numpy, reportlab. `NITKA_CODE=слово` включает общее кодовое слово клуба при входе.

## Развёртывание

Сервису нужен один процесс и папка для данных; наружу его удобно выставлять через любой обратный прокси с HTTPS.

```
python3 -m venv .venv && .venv/bin/pip install pillow numpy reportlab
NITKA_DATA=/var/lib/nitka NITKA_PORT=8080 .venv/bin/python -m server.app
```

- `deploy/nitka.service` — пример юнита systemd (отдельный пользователь, запись только в папку данных).
- `deploy/deploy.sh <ssh-хост>` — выкладка: ждёт, пока закончатся идущие сборки, копирует код в `/opt/nitka` и перезапускает сервис.
- Прокси должен передавать заголовок `X-Forwarded-Proto: https` — тогда кука входа ставится с флагом `Secure`.

## Откуда данные

- Топоосновы — тайлы [nakarte.me](https://nakarte.me) (Генштаб) и [OpenTopoMap](https://opentopomap.org); высоты — тайлы Terrarium (AWS Open Data). Тайлы кэшируются на диске.
- Каталог перевалов — база «Вестры» в том виде, в каком её отдаёт nakarte.me.
- Шрифты карт — DejaVu Sans; интерфейса — Onest и Sofia Sans Condensed (OFL). Карта в редакторе — Leaflet.

## Лицензия

Код — MIT (см. `LICENSE`). У сторонних частей свои лицензии: Leaflet — BSD-2-Clause; шрифты DejaVu — Bitstream Vera / public domain; Onest и Sofia Sans Condensed — SIL OFL. Логотип турклуба «Восхождение» в `web/img/` под лицензию не подпадает.
