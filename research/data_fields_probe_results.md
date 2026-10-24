# حقول البيانات المتاحة من Alpaca وTastytrade (نتائج آلية)

- عقد الفحص: `SPY261026C00760000` (انتهاء 2026-10-26)، سعر SPY ≈ 759.97. أسماء حقول وأمثلة أرقام فقط.

## Alpaca (feed=indicative) — لقطة العقد: ok

### حقول اللقطة

| الحقل | مثال |
|---|---|
| `dailyBar.c` | 3.34 |
| `dailyBar.h` | 5.56 |
| `dailyBar.l` | 2.99 |
| `dailyBar.n` | 9237 |
| `dailyBar.o` | 4 |
| `dailyBar.t` | 2026-10-23T04:00:00Z |
| `dailyBar.v` | 46089 |
| `dailyBar.vw` | 3.79667 |
| `greeks.delta` | 0.5126 |
| `greeks.gamma` | 0.0483 |
| `greeks.rho` | 0.0317 |
| `greeks.theta` | -0.5907 |
| `greeks.vega` | 0.2747 |
| `impliedVolatility` | 0.1199 |
| `latestQuote.ap` | 3.41 |
| `latestQuote.as` | 10 |
| `latestQuote.ax` | H |
| `latestQuote.bp` | 3.4 |
| `latestQuote.bs` | 143 |
| `latestQuote.bx` | M |
| `latestQuote.c` |   |
| `latestQuote.t` | 2026-10-23T20:14:59.714588793Z |
| `latestTrade.c` | I |
| `latestTrade.p` | 3.4 |
| `latestTrade.s` | 5 |
| `latestTrade.t` | 2026-10-23T20:14:58.057415486Z |
| `latestTrade.x` | E |
| `minuteBar.c` | 3.34 |
| `minuteBar.h` | 3.4 |
| `minuteBar.l` | 3.34 |
| `minuteBar.n` | 22 |
| `minuteBar.o` | 3.35 |
| `minuteBar.t` | 2026-10-23T20:14:00Z |
| `minuteBar.v` | 51 |
| `minuteBar.vw` | 3.35902 |
| `prevDailyBar.c` | 6.93 |
| `prevDailyBar.h` | 8.97 |
| `prevDailyBar.l` | 5.48 |
| `prevDailyBar.n` | 323 |
| `prevDailyBar.o` | 8.97 |
| `prevDailyBar.t` | 2026-10-22T04:00:00Z |
| `prevDailyBar.v` | 920 |
| `prevDailyBar.vw` | 6.23862 |

- آخر صفقة: ok

### آخر صفقة

| الحقل | مثال |
|---|---|
| `trades.SPY261026C00760000.c` | I |
| `trades.SPY261026C00760000.p` | 3.4 |
| `trades.SPY261026C00760000.s` | 5 |
| `trades.SPY261026C00760000.t` | 2026-10-23T20:14:58.057415486Z |
| `trades.SPY261026C00760000.x` | E |

- سجل الصفقات: HTTP 400 — unexpected query parameter(s): feed
- شموع يومية: ok

### شموع يومية

| الحقل | مثال |
|---|---|
| `bars.SPY261026C00760000` | list[1] |
| `next_page_token` | None |


- تغذية OPRA الكاملة: HTTP 403 — OPRA agreement is not signed

## Tastytrade — market-data/by-type

- equity-option: ok

### حقول equity-option

| الحقل | مثال |
|---|---|
| `ask` | 3.35 |
| `ask-size` | 10.0 |
| `bid` | 3.34 |
| `bid-size` | 29.0 |
| `close` | 3.34 |
| `close-price-type` | Regular |
| `day-high-price` | 5.56 |
| `day-low-price` | 2.99 |
| `delta` | 0.5586745 |
| `dx-mark` | 3.659987618 |
| `gamma` | 0.051097108 |
| `halt-end-time` | -1 |
| `halt-start-time` | -1 |
| `instrument-type` | Equity Option |
| `is-trading-halted` | False |
| `last` | 3.34 |
| `last-mkt` | 3.34 |
| `mark` | 3.345 |
| `mid` | 3.345 |
| `open` | 4.0 |
| `open-interest` | 276 |
| `prev-close` | 6.93 |
| `prev-close-date` | 2026-10-22 |
| `prev-close-price-type` | Regular |
| `rho` | 0.032856493 |
| `summary-date` | 2026-10-23 |
| `symbol` | SPY   261026C00760000 |
| `theo-price` | 3.659987618 |
| `theta` | -0.535272821 |
| `tick-size` | 0.01 |
| `updated-at` | 2026-10-23T23:58:57.260Z |
| `vega` | 0.26515998 |
| `volatility` | 0.114935235 |
| `volume` | 46089.0 |

- equity: ok

### حقول equity

| الحقل | مثال |
|---|---|
| `ask` | 761.13 |
| `ask-size` | 840.0 |
| `beta` | 1.009477732 |
| `bid` | 761.11 |
| `bid-size` | 120.0 |
| `close` | 759.97 |
| `close-price-type` | Final |
| `day-high-price` | 763.47 |
| `day-low-price` | 758.68 |
| `dividend-amount` | 1.888834 |
| `dividend-frequency` | 4.0 |
| `halt-end-time` | -1 |
| `halt-start-time` | -1 |
| `high-limit-price` | 835.78 |
| `instrument-type` | Equity |
| `is-trading-halted` | False |
| `last` | 759.97 |
| `last-mkt` | 759.97 |
| `low-limit-price` | 683.82 |
| `mark` | 761.11 |
| `mid` | 761.12 |
| `open` | 759.896 |
| `prev-close` | 763.96 |
| `prev-close-date` | 2026-10-22 |
| `prev-close-price-type` | Final |
| `summary-date` | 2026-10-23 |
| `symbol` | SPY |
| `updated-at` | 2026-10-24T00:00:01.058Z |
| `volume` | 33423223.845658 |
| `volume-ext` | 4232678.544603 |
| `year-high-price` | 781.62 |
| `year-low-price` | 629.28 |

## شريط الصفقات التاريخي (عقد SPY261023C00760000، 2026-10-23T14:30:00Z → 2026-10-23T15:30:00Z)

- الصفقات: ok
- عدد الصفقات في الساعة: 200، الحجم 832، أكبر صفقة 49، بورصات 17، شروط ['I', 'S', 'a', 'b', 'g']

### عيّنة أول سجل

| الحقل | مثال |
|---|---|
| `c` | I |
| `p` | 1.76 |
| `s` | 1 |
| `t` | 2026-10-23T14:30:00.022160901Z |
| `x` | U |

- العروض: HTTP 404 — Not Found
(آخر تشغيل 2026-10-24)
