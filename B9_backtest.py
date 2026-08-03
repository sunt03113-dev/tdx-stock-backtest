# -*- coding: utf-8 -*-
"""
规则B9 筛选 —— 直接读取通达信 .day 二进制文件
与A9差异：
  - D3和D4双涨停（A9仅D4单涨停）
  - BaseDay搜索范围 D5+2~D5+14（A9为D5+2~D5+11）
  - 新增条件④ BaseDay最高价≤D4最高价
  - 输出D1-D2区间振幅（A9为D1-D3）
  - 单日振幅不带%，区间涨幅/振幅带%
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

OUTPUT_DIR = SCRIPT_DIR / "results" / "B9"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "B9_backtest.xlsx"

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


def daily_amp(high, low, prev_close):
    if prev_close <= 0:
        return 0.0
    return (high - low) / prev_close * 100


def range_amp(highs_slice, lows_slice, base_close):
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
    for d1 in range(1, n - 28):
        d2, d3, d4, d5, d6 = d1 + 1, d1 + 2, d1 + 3, d1 + 4, d1 + 5

        # 条件1: D3和D4双涨停，D1/D2/D5不涨停
        if limits[d1] or limits[d2] or not limits[d3] or not limits[d4] or limits[d5]:
            continue

        # D2-D5 高低点非递降
        h = highs[d1:d5 + 1]
        l = lows[d1:d5 + 1]
        if any(h[i] < h[i - 1] or l[i] < l[i - 1] for i in range(1, 5)):
            continue

        # 条件2: D6不涨停
        if limits[d6]:
            continue

        d4_high = highs[d4]
        d5_high = highs[d5]
        d5_amt = amounts[d5]
        d5_close = closes[d5]
        d0_close = closes[d1 - 1]

        # 条件3+4: BaseDay搜索 D5+2~D5+14
        base_day = None
        for idx in range(d5 + 2, min(d5 + 15, n)):
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
            # 3.④ BaseDay最高价≤D4最高价
            if highs[idx] > d4_high:
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

        # 指标计算
        a1 = daily_amp(highs[d1], lows[d1], closes[d1 - 1])
        a2 = daily_amp(highs[d2], lows[d2], closes[d1])
        a3 = daily_amp(highs[d3], lows[d3], closes[d2])
        a4 = daily_amp(highs[d4], lows[d4], closes[d3])
        a5 = daily_amp(highs[d5], lows[d5], closes[d4])

        amp_d1_d2 = range_amp(highs[d1:d3], lows[d1:d3], d0_close)
        amp_d1_d5 = range_amp(highs[d1:d6], lows[d1:d6], d0_close)

        interval_days = base_day - d6

        max_a_val = -1.0
        for s in range(d6, base_day):
            a = daily_amp(highs[s], lows[s], closes[s - 1])
            if a > max_a_val:
                max_a_val = a
                # max_a_date removed
        max_a_str = f"{max_a_val:.2f}"

        bd_prev = closes[base_day - 1]
        inc = (bd_prev - d5_close) / d5_close * 100 if d5_close > 0 else 0.0
        iamp = range_amp(highs[d6:base_day], lows[d6:base_day], d5_close)
        abase = daily_amp(highs[base_day], lows[base_day], closes[base_day - 1])

        base_close = closes[base_day]
        t_fields = {}
        for t_off in range(8):
            t_idx = base_day + 1 + t_off
            if t_idx < n and base_close > 0:
                if t_off == 0:
                    lp = (lows[t_idx] - base_close) / base_close * 100
                    hp = (highs[t_idx] - base_close) / base_close * 100
                    _li = int(lp)
                    _lv = _li if lp <= 0 or lp == _li else _li + 1
                    t_fields["T+0(低/高)"] = f"{_lv:+d}%/{int(hp):+d}%"
                else:
                    hp = (highs[t_idx] - base_close) / base_close * 100
                    t_fields[f"T+{t_off}最高价"] = f"{int(hp):+d}%"
            else:
                t_fields["T+0(低/高)" if t_off == 0 else f"T+{t_off}最高价"] = "N/A"

        row = {
            "股票代码": str(code).zfill(6),
            "股票名称": "",
            "BaseDay日期": fmt_date(dates_raw[base_day]),
            "D1振幅": f"{a1:.2f}",
            "D2振幅": f"{a2:.2f}",
            "D3振幅": f"{a3:.2f}",
            "D4振幅": f"{a4:.2f}",
            "D5振幅": f"{a5:.2f}",
            "D1-D2区间振幅": f"{amp_d1_d2:.2f}%",
            "D1-D5区间总振幅": f"{amp_d1_d5:.2f}%",
            "D6至BaseDay前交易日数量": int(interval_days),
            "区间最大振幅": max_a_str,
            "D6至BaseDay前区间涨幅": f"{inc:+.2f}%",
            "D6至BaseDay前区间振幅": f"{iamp:.2f}%",
            "BaseDay当日振幅": f"{abase:.2f}",
        }
        row.update(t_fields)
        rows.append(row)

    return rows


def run_b9(start_date=DEFAULT_START, end_date=DEFAULT_END):
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

    print(f"\n===== 阶段2: 规则B9筛选 =====")
    t0 = time.time()
    results = []
    for f in tqdm(main_files, desc="  B9筛选"):
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
        "D1-D2区间振幅", "D1-D5区间总振幅",
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
    parser = argparse.ArgumentParser(description="规则B9 涨停形态筛选")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    args = parser.parse_args()
    run_b9(start_date=args.start, end_date=args.end)
