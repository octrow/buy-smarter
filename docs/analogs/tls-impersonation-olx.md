# TLS-имперсонация для OLX.kz (repo-scout, 2026-10-13)

**Проблема.** `olx.kz` (и `/api/v1/offers/`, и главная) отдаёт `403` от CloudFront для httpx и curl с браузерным UA,
с домашнего KZ IP (Beeline). Headless Chromium получает `200`. Значит, WAF режет по TLS/HTTP2-отпечатку, а не по IP.

**Поиск:** `repo-scout --project buy-smarter --lang python`, 6 запросов + round two + Index: 430 найдено, 221 прошли
стоп-правила. Run: `~/.local/share/repo-scout/runs/20261013-120204-fast-lightweight-python-http-client-that.json`.

## Живая проверка (`GET /api/v1/offers/?query=крем&city_id=1`, тот же IP)

| Клиент | ★ / push | Результат | Время | Заметки |
|---|---|---|---|---|
| [lexiforest/curl_cffi](https://github.com/lexiforest/curl_cffi) | 6.7k / 2026-10-09 | **200** ×4 | 0.43–0.71 s | `AsyncSession(impersonate="chrome")`, requests-подобный API, proxy; wheel 12.9 MB (libcurl-impersonate) |
| [0x676e67/wreq-python](https://github.com/0x676e67/wreq-python) (бывш. rnet) | 1.5k / 2026-10-12 | **200** | 0.46 s | Rust; API менее похож на httpx (`Emulation.Chrome137`, `r.status`) |
| [deedy5/primp](https://github.com/deedy5/primp) | 612 / 2026-09-13 | **200** | 0.42 s | Rust; имена профилей сменились (`chrome_131` → ошибка, `chrome` ок) |
| [FlorianREGAZ/Python-Tls-Client](https://github.com/FlorianREGAZ/Python-Tls-Client) | 821 / 2026-10-10 | не проверялся | — | sync, Go shared lib |
| httpx / curl (сейчас) | — | 403 | — | — |
| Playwright Chromium (сейчас для Ozon) | — | 200 | ~18 s на запуск | тяжело |

## Вердикт

- `curl_cffi` → **Study**: транспорт OLX в `pylibs/market-sources` как optional extra (по образцу `browser`).
  Самый популярный и активный, async, прокси, ~0.5 s. Минус: тяжёлый wheel.
- `wreq-python`, `primp` → Inspect: тоже проходят и легче по размеру, но маленькое сообщество или API дальше от httpx.
- `Python-Tls-Client` → doubt.

Общие «обходчики» (CloakBrowser, botasaurus, CloudflareBypassForScraping) не нужны: решают JS-челленджи браузером,
а здесь хватает TLS-отпечатка.
