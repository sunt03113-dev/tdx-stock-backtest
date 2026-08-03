# -*- coding: utf-8 -*-
"""
规则0803 筛选 —— 20cm 标的（科创板 688xxx、创业板 300xxx/301xxx）
时间线：D-4 → D-3 → D-2 → D-1 → D0 → T+0 → ... → T+6

筛选条件：
  D-1：涨停；股价（20日）最高；成交额（20日）最大
  D0：不是涨停
  D-2/D-3/D-4：不是涨停；成交额不是 20 日窗口最大

输出指标：
  D-1振幅（单日，不带%）、D0涨幅（带%）、D0振幅（单日，不带%）、
  D0/D-1成交额百分比（带%）、T+0(低/高) ~ T+6最高价（基准价=D0收盘价）
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

OUTPUT_DIR = SCRIPT_DIR / "results" / "0803"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "0803_backtest.xlsx"

DEFAULT_START = "1990-12-19"
DEFAULT_END = "2099-12-31"

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
    """单日振幅 = (最高 - 最低) / 前收"""
    if prev_close <= 0:
        return 0.0
    return (high - low) / prev_close * 100


def fmt_date(d_int):
    return f"{d_int // 10000:04d}-{d_int % 10000 // 100:02d}-{d_int % 100:02d}"


def fmt_t0(val_pct):
    """T+0 取整规则（均变大）：负数保留整数位，正数向上取整"""
    if val_pct <= 0:
        return int(val_pct)  # -1.1 → -1
    return math.ceil(val_pct)  # 1.1 → 2


def fmt_tn(val_pct):
    """T+1~7 取整规则（均变小）：向下取整"""
    return math.floor(val_pct)  # -1.1 → -2, 1.1 → 1


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

    rows = []
    # D-1 在索引 i 处，需要 i-3 >= 1（D-4 需要前一天收盘），且 i+8 < n（T+6）
    for i in range(3, n - 8):
        dm1 = i       # D-1
        dm2 = i - 1   # D-2
        dm3 = i - 2   # D-3
        dm4 = i - 3   # D-4
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

        # === 计算输出指标 ===

        # D-1 振幅（单日，不带 %）
        amp_dm1 = daily_amplitude(highs[dm1], lows[dm1], closes[dm2])

        # D0 涨幅（带 %）
        d0_gain = (closes[d0] - closes[dm1]) / closes[dm1] * 100 if closes[dm1] > 0 else 0.0

        # D0 振幅（单日，不带 %）
        amp_d0 = daily_amplitude(highs[d0], lows[d0], closes[dm1])

        # D0/D-1 成交额百分比（带 %）
        amt_ratio = amounts[d0] / amounts[dm1] * 100 if amounts[dm1] > 0 else 0.0

        # T+0 ~ T+6 基准价（基准日 = D0 收盘价）
        base_close = closes[d0]
        t_fields = {}
        for t_off in range(7):
            t_idx = d0 + 1 + t_off
            if t_idx < n and base_close > 0:
                if t_off == 0:
                    # T+0: 输出低/高，按规则①取整
                    low_pct = (lows[t_idx] - base_close) / base_close * 100
                    high_pct = (highs[t_idx] - base_close) / base_close * 100
                    low_val = fmt_t0(low_pct)
                    high_val = fmt_t0(high_pct)
                    t_fields["T+0(低/高)"] = f"{low_val:+d}%/{high_val:+d}%"
                else:
                    # T+1~6: 仅输出最高价，按规则②取整
                    high_pct = (highs[t_idx] - base_close) / base_close * 100
                    val = fmt_tn(high_pct)
                    t_fields[f"T+{t_off}最高价"] = f"{val:+d}%"
            else:
                if t_off == 0:
                    t_fields["T+0(低/高)"] = "N/A"
                else:
                    t_fields[f"T+{t_off}最高价"] = "N/A"

        row = {
            "股票代码": str(code).zfill(6),
            "股票名称": "",
            "D-1日期": fmt_date(dates_raw[dm1]),
            "D-1振幅": f"{amp_dm1:.2f}",
            "D0涨幅": f"{d0_gain:+.2f}%",
            "D0振幅": f"{amp_d0:.2f}",
            "D0/D-1成交额百分比": f"{amt_ratio:.2f}%",
        }
        row.update(t_fields)
        rows.append(row)

    return rows


def run_0803(start_date=DEFAULT_START, end_date=DEFAULT_END):
    start_int = int(start_date.replace("-", ""))
    end_int = int(end_date.replace("-", ""))
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"数据源: 直接读取 .day 二进制文件")
    print(f"适用标的: 20cm（科创板 688xxx、创业板 300xxx/301xxx）")

    print("\n===== 阶段1: 收集 .day 文件 =====")
    all_files = []
    for d in DAY_DIRS:
        p = Path(d)
        if p.exists():
            all_files.extend(sorted(p.glob("*.day")))
    # 筛选 20cm 标的
    cm_files = [f for f in all_files if is_20cm(f.stem)]
    print(f"  总文件: {len(all_files)}, 20cm标的: {len(cm_files)}")

    if not cm_files:
        print("  [错误] 未找到 20cm .day 文件")
        return

    print(f"\n===== 阶段2: 规则0803筛选 =====")
    t0 = time.time()
    results = []
    for f in tqdm(cm_files, desc="  0803筛选"):
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
        "股票代码", "股票名称", "D-1日期",
        "D-1振幅", "D0涨幅", "D0振幅", "D0/D-1成交额百分比",
        "T+0(低/高)",
        "T+1最高价", "T+2最高价", "T+3最高价",
        "T+4最高价", "T+5最高价", "T+6最高价",
    ]
    result_df = result_df.reindex(columns=columns)
    result_df.to_excel(OUTPUT_FILE, index=False)
    print(f"  完成: {len(results)} 条 -> {OUTPUT_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="规则0803 涨停形态筛选（20cm标的）")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    args = parser.parse_args()
    run_0803(start_date=args.start, end_date=args.end)
