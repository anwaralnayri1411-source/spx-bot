# حقول البيانات المتاحة من Alpaca وTastytrade (نتائج آلية)

- عقد الفحص: `SPY261026C00700000` (انتهاء 2026-10-26)، سعر SPY ≈ 700.00. أسماء حقول وأمثلة أرقام فقط.

## Alpaca (feed=indicative) — لقطة العقد: ok

### حقول اللقطة

| الحقل | مثال |
|---|---|
| `dailyBar.c` | 59.91 |
| `dailyBar.h` | 63.86 |
| `dailyBar.l` | 59.58 |
| `dailyBar.n` | 62 |
| `dailyBar.o` | 60.55 |
| `dailyBar.t` | 2026-10-23T04:00:00Z |
| `dailyBar.v` | 65 |
| `dailyBar.vw` | 61.6068 |
| `greeks.delta` | 0.9448 |
| `greeks.gamma` | 0.0028 |
| `greeks.rho` | 0.054 |
| `greeks.theta` | -0.8127 |
| `greeks.vega` | 0.0768 |
| `impliedVolatility` | 0.5796 |
| `latestQuote.ap` | 61.89 |
| `latestQuote.as` | 50 |
| `latestQuote.ax` | M |
| `latestQuote.bp` | 60.41 |
| `latestQuote.bs` | 10 |
| `latestQuote.bx` | X |
| `latestQuote.c` |   |
| `latestQuote.t` | 2026-10-23T20:14:58.890394616Z |
| `latestTrade.c` | g |
| `latestTrade.p` | 60.41 |
| `latestTrade.s` | 3 |
| `latestTrade.t` | 2026-10-23T20:03:03.965222879Z |
| `latestTrade.x` | C |
| `minuteBar.c` | 59.91 |
| `minuteBar.h` | 59.91 |
| `minuteBar.l` | 59.91 |
| `minuteBar.n` | 2 |
| `minuteBar.o` | 59.91 |
| `minuteBar.t` | 2026-10-23T20:03:00Z |
| `minuteBar.v` | 5 |
| `minuteBar.vw` | 59.91 |
| `prevDailyBar.c` | 63.33 |
| `prevDailyBar.h` | 68.32 |
| `prevDailyBar.l` | 62.43 |
| `prevDailyBar.n` | 46 |
| `prevDailyBar.o` | 66.12 |
| `prevDailyBar.t` | 2026-10-22T04:00:00Z |
| `prevDailyBar.v` | 46 |
| `prevDailyBar.vw` | 65.2361 |

- آخر صفقة: ok

### آخر صفقة

| الحقل | مثال |
|---|---|
| `trades.SPY261026C00700000.c` | g |
| `trades.SPY261026C00700000.p` | 60.41 |
| `trades.SPY261026C00700000.s` | 3 |
| `trades.SPY261026C00700000.t` | 2026-10-23T20:03:03.965222879Z |
| `trades.SPY261026C00700000.x` | C |

- سجل الصفقات: HTTP 400
- شموع يومية: ok

### شموع يومية

| الحقل | مثال |
|---|---|
| `bars.SPY261026C00700000` | list[1] |
| `next_page_token` | None |


- تغذية OPRA الكاملة: HTTP 403

## Tastytrade — market-data/by-type

- equity-option: ok

### حقول equity-option

| الحقل | مثال |
|---|---|
| `ask` | 61.71 |
| `ask-size` | 50.0 |
| `bid` | 59.24 |
| `bid-size` | 1.0 |
| `close` | 59.91 |
| `close-price-type` | Regular |
| `day-high-price` | 63.86 |
| `day-low-price` | 59.58 |
| `delta` | 0.999579169 |
| `dx-mark` | 61.102115565 |
| `gamma` | 0.000079107 |
| `halt-end-time` | -1 |
| `halt-start-time` | -1 |
| `instrument-type` | Equity Option |
| `is-trading-halted` | False |
| `last` | 59.91 |
| `last-mkt` | 59.91 |
| `mark` | 60.475 |
| `mid` | 60.475 |
| `open` | 60.55 |
| `open-interest` | 8 |
| `prev-close` | 63.33 |
| `prev-close-date` | 2026-10-22 |
| `prev-close-price-type` | Regular |
| `rho` | 0.054534708 |
| `summary-date` | 2026-10-23 |
| `symbol` | SPY   261026C00700000 |
| `theo-price` | 61.102115565 |
| `theta` | -0.005094748 |
| `tick-size` | 0.01 |
| `updated-at` | 2026-10-23T23:58:57.283Z |
| `vega` | 0.001017869 |
| `volatility` | 0.284981651 |
| `volume` | 65.0 |

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

(آخر تشغيل 2026-10-24)
