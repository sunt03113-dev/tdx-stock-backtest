# -*- coding: utf-8 -*-
"""
规则A9 筛选 —— 直接读取通达信 .day 二进制文件
与0723A8筛选逻辑一致，输出字段差异：
  - D1-D3区间振幅（替代D1-D2）
  - 新增 D6~BaseDay-1 最大振幅日及日期
"""
import sys, time
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from StockBacktest_TdxAutoRun0630 import (
    is_strong_limit_up, TDX_BASE, SYSTEM_TYPE, StockNameTool
)

OUTPUT_DIR = SCRIPT_DIR / "results" / "A9"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "A9_backtest.xlsx"

DEFAULT_START = "1990-12-19"
DEFAULT_END = "2099-12-31"

DAY_DIRS = [
    str(TDX_BASE / "vipdoc" / "sh" / "lday"),
    str(TDX_BASE / "vipdoc" / "sz" / "lday")
]


def board(code):
    code = str(code).replace("sh", "").replace("sz", "").zfill(6)
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "other"


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


def range_amplitude(highs_slice, lows_slice, base_close):
    if base_close <= 0 or len(highs_slice) == 0:
        return 0.0
    return (max(highs_slice) - min(lows_slice)) / base_close * 100


def fmt_date(d_int):
    return f"{d_int // 10000:04d}-{d_int % 10000 // 100:02d}-{d_int % 100:02d}"


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
        opens[mask], highs[mask], lows[mask], closes[mask], amounts[mask], volumes[mask]
    )

    n = len(dates_raw)
    if n < 30:
        return []

    dates_pd = pd.to_datetime(dates_raw.astype(str), format='%Y%m%d')

    limits = np.zeros(n, dtype=bool)
    for i in range(1, n):
        limits[i] = is_strong_limit_up(
            float(closes[i]), float(highs[i]), float(closes[i - 1]),
            dates_pd[i], code
        )

    rows = []
    for d1 in range(1, n - 25):
        d2, d3, d4, d5, d6 = d1 + 1, d1 + 2, d1 + 3, d1 + 4, d1 + 5

        if limits[d1] or limits[d2] or limits[d3] or not limits[d4] or limits[d5]:
            continue
        h = highs[d1:d5 + 1]
        l = lows[d1:d5 + 1]
        if any(h[i] < h[i - 1] or l[i] < l[i - 1] for i in range(1, 5)):
            continue
        if limits[d6]:
            continue

        d5_high = highs[d5]
        d5_amt = amounts[d5]
        d5_close = closes[d5]
        d0_close = closes[d1 - 1]

        base_day = None
        for idx in range(d5 + 2, min(d5 + 12, n)):
            if idx < 20:
                continue
            if not limits[idx]:
                continue
            w_start = max(0, idx - 19)
            w_highs = highs[w_start:idx + 1]
            w_amts = amounts[w_start:idx + 1]
            if len(w_highs) < 20:
                continue
            if highs[idx] >= w_highs.max():
                continue
            if amounts[idx] >= w_amts.max():
                continue
            interval_ok = True
            for s in range(d6, idx):
                if highs[s] > d5_high or amounts[s] > d5_amt:
                    interval_ok = False
                    break
            if not interval_ok:
                break
            base_day = idx
            break

        if base_day is None:
            continue

        amp_d1 = daily_amplitude(highs[d1], lows[d1], closes[d1 - 1])
        amp_d2 = daily_amplitude(highs[d2], lows[d2], closes[d1])
        amp_d3 = daily_amplitude(highs[d3], lows[d3], closes[d2])
        amp_d4 = daily_amplitude(highs[d4], lows[d4], closes[d3])
        amp_d5 = daily_amplitude(highs[d5], lows[d5], closes[d4])

        amp_d1_d3 = range_amplitude(highs[d1:d4], lows[d1:d4], d0_close)
        amp_d1_d5 = range_amplitude(highs[d1:d6], lows[d1:d6], d0_close)

        interval_days = base_day - d6

        max_amp_val = -1.0
        for s in range(d6, base_day):
            a = daily_amplitude(highs[s], lows[s], closes[s - 1])
            if a > max_amp_val:
                max_amp_val = a
                # max_amp_date removed
        max_amp_str = f"{max_amp_val:.2f}"

        base_day_prev_close = closes[base_day - 1]
        interval_inc = (base_day_prev_close - d5_close) / d5_close * 100 if d5_close > 0 else 0.0
        interval_amp = range_amplitude(highs[d6:base_day], lows[d6:base_day], d5_close)
        amp_base = daily_amplitude(highs[base_day], lows[base_day], closes[base_day - 1])

        base_close = closes[base_day]
        t_fields = {}
        for t_off in range(8):
            t_idx = base_day + 1 + t_off
            if t_idx < n and base_close > 0:
                if t_off == 0:
                    low_pct = (lows[t_idx] - base_close) / base_close * 100
                    high_pct = (highs[t_idx] - base_close) / base_close * 100
                    _li = int(low_pct)
                    low_val = _li if low_pct <= 0 or low_pct == _li else _li + 1
                    t_fields["T+0(低/高)"] = f"{low_val:+d}%/{int(high_pct):+d}%"
                else:
                    high_pct = (highs[t_idx] - base_close) / base_close * 100
                    t_fields[f"T+{t_off}最高价"] = f"{int(high_pct):+d}%"
            else:
                t_fields["T+0(低/高)" if t_off == 0 else f"T+{t_off}最高价"] = "N/A"

        row = {
            "股票代码": str(code).zfill(6),
            "股票名称": "",
            "BaseDay日期": fmt_date(dates_raw[base_day]),
            "D1振幅": f"{amp_d1:.2f}%",
            "D2振幅": f"{amp_d2:.2f}%",
            "D3振幅": f"{amp_d3:.2f}%",
            "D4振幅": f"{amp_d4:.2f}%",
            "D5振幅": f"{amp_d5:.2f}%",
            "D1-D3区间振幅": f"{amp_d1_d3:.2f}%",
            "D1-D5区间总振幅": f"{amp_d1_d5:.2f}%",
            "D6至BaseDay前交易日数量": int(interval_days),
            "区间最大振幅": max_amp_str,
            "D6至BaseDay前区间涨幅": f"{interval_inc:+.2f}%",
            "D6至BaseDay前区间振幅": f"{interval_amp:.2f}%",
            "BaseDay当日振幅": f"{amp_base:.2f}",
        }
        row.update(t_fields)
        rows.append(row)

    return rows


def run_a9(start_date=DEFAULT_START, end_date=DEFAULT_END):
    start_int = int(start_date.replace("-", ""))
    end_int = int(end_date.replace("-", ""))
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"数据源: 直接读取 .day 二进制文件")

    print("\n===== 阶段1: 收集 .day 文件 =====")
    all_files = []
    for d in DAY_DIRS:
        p = Path(d)
        if p.exists():
            all_files.extend(sorted(p.glob("*.day")))
    main_files = [f for f in all_files if board(f.stem) == "main"]
    print(f"  总文件: {len(all_files)}, 主板10cm: {len(main_files)}")

    if not main_files:
        print("  [错误] 未找到 .day 文件")
        return

    print(f"\n===== 阶段2: 规则A9筛选 =====")
    t0 = time.time()
    results = []
    for f in tqdm(main_files, desc="  A9筛选"):
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
        "股票代码", "股票名称", "BaseDay日期",
        "D1振幅", "D2振幅", "D3振幅", "D4振幅", "D5振幅",
        "D1-D3区间振幅", "D1-D5区间总振幅",
        "D6至BaseDay前交易日数量", "区间最大振幅",
        "D6至BaseDay前区间涨幅", "D6至BaseDay前区间振幅",
        "BaseDay当日振幅",
        "T+0(低/高)",
        "T+1最高价", "T+2最高价", "T+3最高价",
        "T+4最高价", "T+5最高价", "T+6最高价", "T+7最高价",
    ]
    result_df = result_df.reindex(columns=columns)
    result_df.to_excel(OUTPUT_FILE, index=False)
    print(f"  完成: {len(results)} 条 -> {OUTPUT_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="规则A9 涨停形态筛选")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    args = parser.parse_args()
    run_a9(start_date=args.start, end_date=args.end)
