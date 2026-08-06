# -*- coding: utf-8 -*-
"""
规则0805 筛选 —— 20cm 标的（科创板 688xxx、创业板 300xxx/301xxx）
时间线：D-5 → D-4 → D-3 → D-2 → D-1(涨停基准日) → D-0(涨停次日) → T+0 → ... → T+6

与0803差异：
  - 样本日期改为 D-0 日期（0803为D-1日期）
  - 新增 D-0最高价/D-0最低价（以D-1收盘价为基准）
  - T+0 改为收盘价变化值(阴/阳/板)（0803为低/高）
  - T+1~6 改为收盘价变化值，仅标注板（0803为最高价）
  - T+n 正数带+号（0803为正数不带+号）
  - T+n 不带%（0803带%）
  - 新增样本前置过滤：剔除创业板300标的2020-08-24前10cm阶段数据
"""
import sys
import time
import math
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from StockBacktest_TdxAutoRun0630 import (
    is_strong_limit_up, TDX_BASE, SYSTEM_TYPE, StockNameTool
)

OUTPUT_DIR = SCRIPT_DIR / "results" / "0805"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "0805_backtest.xlsx"

DEFAULT_START = "1990-12-19"
DEFAULT_END = "2099-12-31"

# 创业板20cm生效日期
GEM_20CM_DATE = 20200824

DAY_DIRS = [
    str(TDX_BASE / "vipdoc" / "sh" / "lday"),
    str(TDX_BASE / "vipdoc" / "sz" / "lday")
]


def is_20cm(code):
    """判断是否为 20cm 标的（科创板 + 创业板）"""
    c = str(code).replace("sh", "").replace("sz", "").zfill(6)
    return c.startswith(("688", "300", "301"))


def read_day_file(filepath):
    raw = np.fromfile(filepath, dtype=np.uint8)
    if len(raw) == 0:
        return None
    n = len(raw) // 32
    raw = raw[:n * 32].reshape(n, 32)
    dates = raw[:, 0:4].copy().view(np.int32).flatten()
    opens = raw[:, 4:8].copy().view(np.int32).flatten() / 100.0
    highs = raw[:, 8:12].copy().view(np.int32).flatten() / 100.0
    lows = raw[:, 12:16].copy().view(np.int32).flatten() / 100.0
    closes = raw[:, 16:20].copy().view(np.int32).flatten() / 100.0
    amounts = raw[:, 20:24].copy().view(np.float32).flatten()
    volumes = raw[:, 24:28].copy().view(np.int32).flatten()
    return dates, opens, highs, lows, closes, amounts, volumes


def daily_amplitude(high, low, prev_close):
    if prev_close <= 0:
        return 0.0
    return (high - low) / prev_close * 100


def fmt_date(d_int):
    return f"{d_int // 10000:04d}-{d_int % 10000 // 100:02d}-{d_int % 100:02d}"


def fmt_t0(val_pct):
    """T+0 取整规则（均变大）：负数保留整数位，正数向上取整"""
    if val_pct <= 0:
        return int(val_pct)
    return math.ceil(val_pct)


def fmt_tn(val_pct):
    """T+1~6 取整规则（均变小）：向下取整"""
    return math.floor(val_pct)


def fmt_signed(val):
    """带符号格式化：正数带+，负数带-（支持数字和字符串）"""
    s = str(val)
    if not s.startswith("-"):
        return f"+{s}"
    return s


def candle_form(open_val, close_val, is_limit):
    """判断K线形态：板/阳/阴"""
    if is_limit:
        return "板"
    if close_val > open_val:
        return "阳"
    return "阴"


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

    dates_pd = pd.to_datetime(dates_raw.astype(str), format='%Y%m%d')

    # 预计算涨停标记
    limits = np.zeros(n, dtype=bool)
    for i in range(1, n):
        limits[i] = is_strong_limit_up(
            float(closes[i]), float(highs[i]), float(closes[i - 1]),
            dates_pd[i], code
        )

    # 判断是否为创业板300标的（需前置过滤）
    c = str(code).zfill(6)
    is_gem_300 = c.startswith("300")

    rows = []
    # D-1 在索引 i 处，需要 i-4 >= 1（D-5），且 i+8 < n（T+6）
    for i in range(4, n - 8):
        dm1 = i       # D-1
        dm2 = i - 1   # D-2
        dm3 = i - 2   # D-3
        dm4 = i - 3   # D-4
        dm5 = i - 4   # D-5
        d0 = i + 1    # D-0

        # === 样本前置过滤：创业板300标的剔除2020-08-24前数据 ===
        if is_gem_300 and dates_raw[dm1] < GEM_20CM_DATE:
            continue

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

        # === D-0 条件：不是涨停 ===
        if limits[d0]:
            continue

        # === D-2 条件：不是涨停；成交额不是 20 日窗口最大 ===
        if limits[dm2]:
            continue
        if dm2 >= 19:
            w_start_dm2 = dm2 - 19
            w_amts_dm2 = amounts[w_start_dm2:dm2 + 1]
            if amounts[dm2] >= w_amts_dm2.max():
                continue

        # === D-3 条件：不是涨停；成交额不是 20 日窗口最大 ===
        if limits[dm3]:
            continue
        if dm3 >= 19:
            w_start_dm3 = dm3 - 19
            w_amts_dm3 = amounts[w_start_dm3:dm3 + 1]
            if amounts[dm3] >= w_amts_dm3.max():
                continue

        # === D-4 条件：不是涨停；成交额不是 20 日窗口最大 ===
        if limits[dm4]:
            continue
        if dm4 >= 19:
            w_start_dm4 = dm4 - 19
            w_amts_dm4 = amounts[w_start_dm4:dm4 + 1]
            if amounts[dm4] >= w_amts_dm4.max():
                continue

        # === D-5 条件：不是涨停；成交额不是 20 日窗口最大 ===
        if limits[dm5]:
            continue
        if dm5 >= 19:
            w_start_dm5 = dm5 - 19
            w_amts_dm5 = amounts[w_start_dm5:dm5 + 1]
            if amounts[dm5] >= w_amts_dm5.max():
                continue

        # === 计算输出指标 ===

        # D-1 振幅（单日，不带 %）
        amp_dm1 = daily_amplitude(highs[dm1], lows[dm1], closes[dm2])

        # D-0 涨幅（带+号，不带 %）
        d0_gain = (closes[d0] - closes[dm1]) / closes[dm1] * 100 if closes[dm1] > 0 else 0.0

        # D-0 振幅（单日，不带 %）
        amp_d0 = daily_amplitude(highs[d0], lows[d0], closes[dm1])

        # D-0 K线属性
        d0_form = candle_form(opens[d0], closes[d0], limits[d0])

        # D-0 最高价/最低价（以D-1收盘价为基准，带+号，不带%）
        dm1_close = closes[dm1]
        d0_high_pct = (highs[d0] - dm1_close) / dm1_close * 100 if dm1_close > 0 else 0.0
        d0_low_pct = (lows[d0] - dm1_close) / dm1_close * 100 if dm1_close > 0 else 0.0

        # D-0/D-1 成交额百分比（带 %）
        amt_ratio = amounts[d0] / amounts[dm1] * 100 if amounts[dm1] > 0 else 0.0

        # T+0 ~ T+6 股价走势（基准价 = D-0收盘价，收盘价变化值）
        base_close = closes[d0]
        t_fields = {}
        for t_off in range(7):
            t_idx = d0 + 1 + t_off
            if t_idx < n and base_close > 0:
                # 收盘价变化值
                close_pct = (closes[t_idx] - base_close) / base_close * 100
                if t_off == 0:
                    # T+0: 变化值(阴/阳/板)，带+号
                    val = fmt_t0(close_pct)
                    form = candle_form(opens[t_idx], closes[t_idx], limits[t_idx])
                    t_fields["T+0"] = f"{fmt_signed(val)}({form})"
                else:
                    # T+1~6: 变化值，仅涨停标注(板)，带+号
                    val = fmt_tn(close_pct)
                    if limits[t_idx]:
                        t_fields[f"T+{t_off}"] = f"{fmt_signed(val)}(板)"
                    else:
                        t_fields[f"T+{t_off}"] = f"{fmt_signed(val)}"
            else:
                t_fields[f"T+{t_off}" if t_off > 0 else "T+0"] = "N/A"

        row = {
            "股票代码": str(code).zfill(6),
            "股票名称": "",
            "样本日期": fmt_date(dates_raw[d0]),
            "D-1振幅": f"{amp_dm1:.2f}",
            "D-0涨幅": fmt_signed(f"{d0_gain:.2f}"),
            "D-0振幅": f"{amp_d0:.2f}",
            "D-0 K线属性": d0_form,
            "D-0最高价": fmt_signed(f"{d0_high_pct:.2f}"),
            "D-0最低价": fmt_signed(f"{d0_low_pct:.2f}"),
            "D-0/D-1成交额百分比": f"{amt_ratio:.2f}%",
        }
        row.update(t_fields)
        rows.append(row)

    return rows


def run_0805(start_date=DEFAULT_START, end_date=DEFAULT_END):
    start_int = int(start_date.replace("-", ""))
    end_int = int(end_date.replace("-", ""))
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"数据源: 直接读取 .day 二进制文件")
    print(f"适用标的: 20cm（科创板 688xxx、创业板 300xxx/301xxx）")
    print(f"样本过滤: 创业板300标的剔除2020-08-24前10cm阶段数据")

    print("\n===== 阶段1: 收集 .day 文件 =====")
    all_files = []
    for d in DAY_DIRS:
        p = Path(d)
        if p.exists():
            all_files.extend(sorted(p.glob("*.day")))
    cm_files = [f for f in all_files if is_20cm(f.stem)]
    print(f"  总文件: {len(all_files)}, 20cm标的: {len(cm_files)}")

    if not cm_files:
        print("  [错误] 未找到 20cm .day 文件")
        return

    print(f"\n===== 阶段2: 规则0805筛选 =====")
    t0 = time.time()
    results = []
    for f in tqdm(cm_files, desc="  0805筛选"):
        try:
            code = f.stem.replace("sh", "").replace("sz", "")
            output = screen_one(code, f, start_int, end_int)
            results.extend(output)
        except Exception as exc:
            print(f"  [错误] {f.name}: {exc}")

    elapsed = time.time() - t0
    print(f"\n  筛选完成: {len(results)} 条, 耗时 {elapsed:.1f}s")

    print(f"\n===== 阶段3: 股票名称匹配与输出 =====")
    if not results:
        print("  未找到符合条件的结果")
        return

    result_df = pd.DataFrame(results)
    try:
        name_tool = StockNameTool()
        name_df = name_tool.load_cache()
        name_map = dict(zip(
            name_df["code"].astype(str).str.zfill(6),
            name_df["name"]
        ))
        result_df["股票名称"] = result_df["股票代码"].map(name_map).fillna("未找到标的")
    except Exception as exc:
        print(f"  名称匹配失败: {exc}")
        result_df["股票名称"] = "名称查询失败"

    columns = [
        "股票代码", "股票名称", "样本日期",
        "D-1振幅", "D-0涨幅", "D-0振幅", "D-0 K线属性",
        "D-0最高价", "D-0最低价", "D-0/D-1成交额百分比",
        "T+0", "T+1", "T+2", "T+3", "T+4", "T+5", "T+6",
    ]
    result_df = result_df.reindex(columns=columns)
    result_df.to_excel(OUTPUT_FILE, index=False)
    print(f"  完成: {len(results)} 条 -> {OUTPUT_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="规则0805 涨停形态筛选（20cm标的）")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    args = parser.parse_args()
    run_0805(start_date=args.start, end_date=args.end)
