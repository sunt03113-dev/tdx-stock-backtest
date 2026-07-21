---
name: tdx-stock-backtest
description: Use for TDX local daily-stock backtests with StockBacktest_TdxAutoRun0630.py: parse .day files, cache full A-share history from 1990-12-19, run timing-rule screens, and export Excel. Detect limit-up dynamically by trade date, board, and target type; count only strong close-sealed limit-ups.
---
# tdx-stock-backtest

## Main Files
- `StockBacktest_TdxAutoRun0630.py`: the executable backtest script (Module 1: TDX .day→Excel, Module 2: screening, Module 3: name matching).
- `five_day_z_backtest.py`: full-market five-day rising structure and subsequent Z-day limit-up screen (D3-limit-up variant).
- `0715A_backtest.py` (at `D:\筛选结果\0715\`): 0715A rule variant — D4-limit-up, 1-10 day Z-gap, integrated with Module1 auto-update.
- `tdx-stock-backtest.md`: this skill contract.

## Rule 0715A: Five-Day D4-Limit-Up + Z-Day Screen
Run `0715A_backtest.py`:
- `python 0715A_backtest.py` — use existing cache, full history
- `python 0715A_backtest.py --update` — refresh TDX data via Module1 first, then screen
- `python 0715A_backtest.py --start 2020-01-01 --end 2025-12-31` — date range filter

**Core rule:**
- D1-D5 consecutive: only D4 is a 10% strong close-sealed limit-up; D1/D2/D3/D5 are NOT.
- D2-D5 highs and lows are non-decreasing (Hi≥Hi-1 and Li≥Li-1 for i=2,3,4,5).
- Z = next limit-up after D5, gap = 1~10 trading days (z-d5-1, excluding D5 and Z).
- Z must be: 20-day highest price AND 20-day max turnover.
- Only main-board 10cm stocks (600/601/603/605/000/001/002/003).

**Output columns:** 股票代码, 股票名称, Z日涨停日期, 三日区间振幅+D4振幅+D5振幅, 5日区间涨幅*区间振幅, 间隔交易日天数, Z日涨停振幅, 间隔交易日区间涨幅, 间隔交易日区间振幅

## Legacy Five-Day Z Screen (D3 variant)
Run `five_day_z_backtest.py` for the D1-D5 structure: only D3 is a 10% strong close-sealed limit-up; D2-D5 highs and lows are non-decreasing; the next limit-up after D5 must occur in 1-13 trading days and be the 20-day high-price and turnover maximum.

## Backtest Start Baseline
- Global default start date is `1990-12-19` so old-share history is not truncated.
- Each stock starts from its first local TDX `.day` bar, treated as the stock listing first day.
- If a listing/first-bar date cannot be identified, fall back to `1990-12-19`.
- Module 1 always converts from the full-history baseline even when a later legacy `start_date` is supplied.

## Dynamic Limit Rules
Do not hard-code a fixed 10% or 20% limit-up rule. All modules must call `is_strong_limit_up()`.

- Match by trade date + stock board + target type.
- Before `1996-12-16`, A-share fixed daily price limits are not applied; limit-up flag is False.
- Main-board normal stocks use 10%.
- STAR Market `688xxx`: 20% from `2019-07-22`.
- ChiNext `300xxx/301xxx`: 10% before `2020-08-24`, 20% from `2020-08-24` for both existing and newly listed stocks.
- BSE-style codes use 30%.
- ST/risk-warning/delisting targets use 5%.

## Strong Limit-Up Definition
A valid limit-up is a strong close-sealed limit-up:

- 最高价 ≥ 理论涨停价（Decimal高精度计算，四舍五入保留2位小数）；
- 收盘价 ≥ 理论涨停价（Decimal高精度计算，四舍五入保留2位小数）；
- 盘中触及涨停但收盘低于涨停价不算涨停；
- 高精度计算消除浮点误差，≥ 判断避免数据精度造成的误判。

## Constraints
- Treat `limit_type` as optional backward compatibility only; it never decides the actual limit percentage.
- Do not restore code-prefix filtering such as 10cm = `60/00`, 20cm = `30/688`.
- Do not treat all ChiNext history as 20%; the exact split date is `2020-08-24`.
- Do not extrapolate STAR 20% rules before `2019-07-22`.
- Never delete or rewrite original TDX `.day` source files.
