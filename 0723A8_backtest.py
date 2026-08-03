# -*- coding: utf-8 -*-
"""
0723A8 筛选规则 —— 直接读取通达信 .day 二进制文件（跳过 xlsx 转换，速度快 50 倍）
"""
import sys, struct, time
from pathlib import Path
from datetime import datetime
import argparse
import numpy as np
import pandas as pd
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from StockBacktest_TdxAutoRun0630 import (
    is_strong_limit_up, get_20day_window, is_window_max,
    TDX_BASE, DATA_CACHE, SYSTEM_TYPE, STOCK_NAMES_FILE, StockNameTool
)

# 输出路径（相对路径，跨平台兼容）
OUTPUT_DIR = SCRIPT_DIR / "results" / "0723A8"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "0723A8_backtest.xlsx"

DEFAULT_START = "1990-12-19"
DEFAULT_END = "2099-12-31"

# .day 文件目录
DAY_DIRS = [
    str(TDX_BASE / "vipdoc" / "sh" / "lday"),
    str(TDX_BASE / "vipdoc" / "sz" / "lday")
]


def board(code):
    code = str(code).replace("sh","").replace("sz","").zfill(6)
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "other"


def read_day_file(filepath):
    """快速读取通达信 .day 二进制文件，返回 numpy 数组"""
    raw = np.fromfile(filepath, dtype=np.uint8)
    if len(raw) == 0:
        return None
    n = len(raw) // 32
    raw = raw[:n * 32].reshape(n, 32)

    # 解析 32 字节记录: date(int32), open(int32), high(int32), low(int32), close(int32), amount(float32), volume(int32), reserved(int32)
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


def screen_one(code, day_file, start_int, end_int):
    """对单只股票进行 0723A8 规则筛选"""
    result = read_day_file(day_file)
    if result is None:
        return []
    dates_raw, opens, highs, lows, closes, amounts, volumes = result

    # 日期过滤 (YYYYMMDD -> int 比较)
    mask = (dates_raw >= start_int) & (dates_raw <= end_int)
    if mask.sum() == 0:
        return []

    dates_raw = dates_raw[mask]
    opens = opens[mask]
    highs = highs[mask]
    lows = lows[mask]
    closes = closes[mask]
    amounts = amounts[mask]
    volumes = volumes[mask]

    n = len(dates_raw)
    if n < 30:
        return []

    # 将日期转为 pandas datetime 用于 is_strong_limit_up
    dates_pd = pd.to_datetime(dates_raw.astype(str), format='%Y%m%d')

    # 预计算涨停标记
    limits = np.zeros(n, dtype=bool)
    for i in range(1, n):
        limits[i] = is_strong_limit_up(
            float(closes[i]), float(highs[i]), float(closes[i-1]),
            dates_pd[i], code
        )

    rows = []
    for d1 in range(1, n - 25):
        d2, d3, d4, d5, d6 = d1 + 1, d1 + 2, d1 + 3, d1 + 4, d1 + 5

        # 条件1: 仅 D4 涨停
        if limits[d1] or limits[d2] or limits[d3] or not limits[d4] or limits[d5]:
            continue

        # 条件1: D2-D5 高低点非递降
        h = highs[d1:d5+1]
        l = lows[d1:d5+1]
        if any(h[i] < h[i-1] or l[i] < l[i-1] for i in range(1, 5)):
            continue

        # 条件2: D6 不涨停
        if limits[d6]:
            continue

        d5_high = highs[d5]
        d5_amt = amounts[d5]
        d5_close = closes[d5]
        d0_close = closes[d1 - 1]

        # 条件3+4: 搜索 BaseDay
        base_day = None
        for idx in range(d5 + 2, min(d5 + 12, n)):
            if idx < 20:
                continue
            # 3.① BaseDay 涨停
            if not limits[idx]:
                continue
            # 3.② BaseDay 最高价非20日窗口最大
            w_start = max(0, idx - 19)
            w_highs = highs[w_start:idx+1]
            w_amts = amounts[w_start:idx+1]
            if len(w_highs) < 20:
                continue
            if highs[idx] >= w_highs.max():
                continue
            # 3.③ BaseDay 成交额非20日窗口最大
            if amounts[idx] >= w_amts.max():
                continue

            # 条件4: D6~BaseDay-1 压制
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

        # ========== 指标计算 ==========
        amp_d1 = daily_amplitude(highs[d1], lows[d1], closes[d1-1])
        amp_d2 = daily_amplitude(highs[d2], lows[d2], closes[d1])
        amp_d3 = daily_amplitude(highs[d3], lows[d3], closes[d2])
        amp_d4 = daily_amplitude(highs[d4], lows[d4], closes[d3])
        amp_d5 = daily_amplitude(highs[d5], lows[d5], closes[d4])

        amp_d1_d2 = range_amplitude(highs[d1:d3], lows[d1:d3], d0_close)
        amp_d1_d5 = range_amplitude(highs[d1:d6], lows[d1:d6], d0_close)

        interval_days = base_day - d6

        base_day_prev_close = closes[base_day - 1]
        interval_inc = (base_day_prev_close - d5_close) / d5_close * 100 if d5_close > 0 else 0.0

        interval_amp = range_amplitude(highs[d6:base_day], lows[d6:base_day], d5_close)

        amp_base = daily_amplitude(highs[base_day], lows[base_day], closes[base_day - 1])

        # 额外字段: T+0~T+7
        base_close = closes[base_day]
        t_fields = {}
        for t_off in range(8):
            t_idx = base_day + 1 + t_off
            if t_idx < n and base_close > 0:
                if t_off == 0:
                    low_pct = (lows[t_idx] - base_close) / base_close * 100
                    high_pct = (highs[t_idx] - base_close) / base_close * 100
                    t_fields["T+0(低/高)"] = f"{low_pct:+.2f}%/{high_pct:+.2f}%"
                else:
                    high_pct = (highs[t_idx] - base_close) / base_close * 100
                    t_fields[f"T+{t_off}最高价"] = f"{high_pct:+.2f}%"
            else:
                if t_off == 0:
                    t_fields["T+0(低/高)"] = "N/A"
                else:
                    t_fields[f"T+{t_off}最高价"] = "N/A"

        # 日期格式化
        bd_date = dates_raw[base_day]
        bd_str = f"{bd_date//10000:04d}-{bd_date%10000//100:02d}-{bd_date%100:02d}"

        row = {
            "股票代码": str(code).zfill(6),
            "股票名称": "",
            "BaseDay日期": bd_str,
            "D1振幅": f"{amp_d1:.2f}%",
            "D2振幅": f"{amp_d2:.2f}%",
            "D3振幅": f"{amp_d3:.2f}%",
            "D4振幅": f"{amp_d4:.2f}%",
            "D5振幅": f"{amp_d5:.2f}%",
            "D1-D2区间振幅": f"{amp_d1_d2:.2f}%",
            "D1-D5区间总振幅": f"{amp_d1_d5:.2f}%",
            "D6至BaseDay前交易日数量": int(interval_days),
            "D6至BaseDay前区间涨幅": f"{interval_inc:+.2f}%",
            "D6至BaseDay前区间振幅": f"{interval_amp:.2f}%",
            "BaseDay当日振幅": f"{amp_base:.2f}%",
        }
        row.update(t_fields)
        rows.append(row)

    return rows


def run_0723a8(start_date=DEFAULT_START, end_date=DEFAULT_END):
    start_int = int(start_date.replace("-", ""))
    end_int = int(end_date.replace("-", ""))
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"数据源: 直接读取 .day 二进制文件（跳过 xlsx 转换）")

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

    print(f"\n===== 阶段2: 0723A8规则筛选 =====")
    t0 = time.time()
    results = []
    for f in tqdm(main_files, desc="  0723A8筛选"):
        try:
            code = f.stem.replace("sh","").replace("sz","")
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

    # 股票名称匹配
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
        "D1-D2区间振幅", "D1-D5区间总振幅",
        "D6至BaseDay前交易日数量",
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
    parser = argparse.ArgumentParser(description="0723A8 涨停形态筛选")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    args = parser.parse_args()
    run_0723a8(start_date=args.start, end_date=args.end)
