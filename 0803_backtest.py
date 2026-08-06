# -*- coding: utf-8 -*-
"""
规则0803 筛选 —— 20cm 标的（科创板 688xxx、创业板 300xxx/301xxx）
时间线：D-5 → D-4 → D-3 → D-2 → D-1 → D0 → T+0 → ... → T+6

筛选条件：
  D-1：涨停；股价（20日）最高；成交额（20日）最大
  D0：不是涨停
  D-2/D-3/D-4/D-5：不是涨停；成交额不是 20 日窗口最大

输出指标：
  D-1振幅（单日，不带%）、D0涨幅（带+号，不带%）、D0振幅（单日，不带%）、
  D0阴/阳、D0/D-1成交额百分比（带%）、
  T+0(低/高)(阴/阳/板) ~ T+6最高价(仅板)（基准价=D0收盘价）
"""
import sys
import argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from backtest_common import (
    is_20cm, normalize_code, read_day_file, precompute_limits,
    fmt_date, daily_amplitude, fmt_no_sign, fmt_plus, candle_form,
    generate_t_fields, run_backtest, check_data_freshness,
    DEFAULT_START, DEFAULT_END,
)

RULE_NAME = "0803"
TARGET_TYPE = "20cm"

OUTPUT_COLUMNS = [
    "股票代码", "股票名称", "D-1日期",
    "D-1振幅", "D0涨幅", "D0振幅", "D0阴/阳", "D0/D-1成交额百分比",
    "T+0(低/高)",
    "T+1最高价", "T+2最高价", "T+3最高价",
    "T+4最高价", "T+5最高价", "T+6最高价",
]


def screen_one(code, day_file, start_int, end_int):
    result = read_day_file(day_file)
    if result is None:
        return []
    dates_raw, opens, highs, lows, closes, amounts, volumes = result

    mask = (dates_raw >= start_int) & (dates_raw <= end_int)
    if mask.sum() == 0:
        return []
    dates_raw = dates_raw[mask]
    opens, highs, lows, closes, amounts, volumes = (
        opens[mask], highs[mask], lows[mask], closes[mask],
        amounts[mask], volumes[mask]
    )

    n = len(dates_raw)
    if n < 30:
        return []

    limits = precompute_limits(dates_raw, opens, highs, closes, code)

    rows = []
    for i in range(4, n - 8):
        dm1 = i       # D-1
        dm2 = i - 1   # D-2
        dm3 = i - 2   # D-3
        dm4 = i - 3   # D-4
        dm5 = i - 4   # D-5
        d0 = i + 1    # D0

        # === D-1 条件：涨停 ===
        if not limits[dm1]:
            continue

        # === D-1 条件：股价为 20 日窗口最高 ===
        if dm1 < 19:
            continue
        w_start_dm1 = dm1 - 19
        w_highs_dm1 = highs[w_start_dm1:dm1 + 1]
        if highs[dm1] < w_highs_dm1.max():
            continue

        # === D-1 条件：成交额为 20 日窗口最大 ===
        w_amts_dm1 = amounts[w_start_dm1:dm1 + 1]
        if amounts[dm1] < w_amts_dm1.max():
            continue

        # === D0 条件：不是涨停 ===
        if limits[d0]:
            continue

        # === D-2 ~ D-5 条件：不是涨停；成交额不是 20 日窗口最大 ===
        skip = False
        for dm in [dm2, dm3, dm4, dm5]:
            if limits[dm]:
                skip = True
                break
            if dm >= 19:
                w_start = dm - 19
                w_amts = amounts[w_start:dm + 1]
                if amounts[dm] >= w_amts.max():
                    skip = True
                    break
        if skip:
            continue

        # === 计算输出指标 ===

        # D-1 振幅（单日，不带 %）
        amp_dm1 = daily_amplitude(highs[dm1], lows[dm1], closes[dm2])

        # D0 涨幅（带+号，不带 %）
        d0_gain = (closes[d0] - closes[dm1]) / closes[dm1] * 100 if closes[dm1] > 0 else 0.0

        # D0 振幅（单日，不带 %）
        amp_d0 = daily_amplitude(highs[d0], lows[d0], closes[dm1])

        # D0 阴/阳
        d0_form = candle_form(opens[d0], closes[d0], limits[d0])

        # D0/D-1 成交额百分比（带 %）
        amt_ratio = amounts[d0] / amounts[dm1] * 100 if amounts[dm1] > 0 else 0.0

        # T+0 ~ T+6（基准价 = D0 收盘价）
        t_fields = generate_t_fields(
            opens, highs, lows, closes, limits, d0, n, t_count=7
        )

        row = {
            "股票代码": code,
            "股票名称": "",
            "D-1日期": fmt_date(dates_raw[dm1]),
            "D-1振幅": f"{amp_dm1:.2f}",
            "D0涨幅": fmt_plus(f"{d0_gain:.2f}"),
            "D0振幅": f"{amp_d0:.2f}",
            "D0阴/阳": d0_form,
            "D0/D-1成交额百分比": f"{amt_ratio:.2f}%",
        }
        row.update(t_fields)
        rows.append(row)

    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"规则{RULE_NAME} 涨停形态筛选（20cm标的）")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    args = parser.parse_args()

    latest_date, file_count = check_data_freshness()
    print(f"数据源最新日期: {latest_date}（共 {file_count} 个文件）\n")

    run_backtest(
        rule_name=RULE_NAME,
        screen_func=screen_one,
        target_type=TARGET_TYPE,
        output_columns=OUTPUT_COLUMNS,
        start_date=args.start,
        end_date=args.end,
    )
