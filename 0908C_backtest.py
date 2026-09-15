# -*- coding: utf-8 -*-
"""
规则0908C 筛选 —— 20cm（科创板 688 + 创业板 300/301）

时间线（D-0 为基准日 i，向前回看历史）：
  D-9 (i-9) : 非涨停；成交额非 20 日最大
  D-8 (i-8) : 非涨停；成交额非 20 日最大
  D-7 (i-7) : 非涨停；成交额为 20 日最大        注意：0908B 此处为 ~is_amtmax，条件互换
  D-6 (i-6) : 非涨停；成交额非 20 日最大        注意：0908B 此处为 is_amtmax，条件互换
  D-5 (i-5) : 非涨停；成交额为 20 日最大        注意：无股价最高条件（0908 有，0908B/0908C 无）
  D-4 (i-4) : 非涨停；成交额非 20 日最大
  D-3 (i-3) : 非涨停；成交额非 20 日最大
  D-2 (i-2) : 非涨停；成交额非 20 日最大
  D-1 (i-1) : 非涨停；成交额非 20 日最大
  D-0 (i)   : 涨停；成交额为 20 日最大；最高价为 20 日最高（基准日）

输出（20 列）：
  1. D-0 日期
  2. D-7 单日振幅（数值不带 %）
  3. D-5 单日振幅（数值不带 %）
  4. D-7~D-5 区间涨幅（数值带 %）
  5. D-7~D-5 区间振幅（数值带 %）
  6. D-4~D-1 区间涨幅（数值带 %）
  7. D-4~D-1 区间振幅（数值带 %）
  8. D-4~D-1 区间最大振幅（数值带 %）
  9. D-0 单日振幅（数值不带 %）
 10. D-0/D-5 成交额百分比（数值带 %）
 11-17. T+0~T+6 价格走势（基准价 = D-0 收盘价）

数据源：本地通达信 .day 二进制
"""
import sys
import time
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from backtest_common import (
    DAY_DIRS, fmt_date, output_excel, check_data_freshness,
    generate_t_fields,
    compute_limit_flags,
    DEFAULT_START, DEFAULT_END, GEM_20CM_DATE,
)


def attach_stock_names(df):
    try:
        import akshare as ak
        name_df = ak.stock_info_a_code_name()
        name_map = dict(zip(
            name_df["code"].astype(str).str.zfill(6),
            name_df["name"],
        ))
        df["股票名称"] = df["股票代码"].astype(str).str.zfill(6).map(name_map).fillna("未找到标的")
    except Exception as exc:
        print(f"  [名称匹配失败] {exc}")
        df["股票名称"] = ""
    return df


RULE_NAME = "0908C"
OUTPUT_DIR = Path(r"D:\09work\0908C_backtest")

LIMIT_START = 19961216
STAR_20CM_DATE = 20190722

OUTPUT_COLUMNS = [
    "股票代码", "股票名称", "D-0日期",
    # 单日振幅（数值不带 %）
    "D-7振幅", "D-5振幅",
    # D-7~D-5 区间（数值带 %）
    "D-7~D-5区间涨幅", "D-7~D-5区间振幅",
    # D-4~D-1 区间（数值带 %）
    "D-4~D-1区间涨幅", "D-4~D-1区间振幅", "D-4~D-1区间最大振幅",
    # 单日振幅（数值不带 %）
    "D-0振幅",
    # 成交额百分比（数值带 %）
    "D-0/D-5成交额百分比",
    # T+0~T+6
    "T+0(低/高)", "T+1最高价", "T+2最高价", "T+3最高价",
    "T+4最高价", "T+5最高价", "T+6最高价",
]


_DT = np.dtype([
    ("d", "<i4"), ("o", "<i4"), ("h", "<i4"), ("l", "<i4"),
    ("c", "<i4"), ("amt", "<f4"), ("vol", "<i4"), ("r", "<i4"),
])


def read_day_raw(filepath):
    raw = np.fromfile(filepath, dtype=np.uint8)
    if len(raw) == 0:
        return None
    n = len(raw) // 32
    if n == 0:
        return None
    recs = raw[: n * 32].view(_DT)
    dates = recs["d"].astype(np.int64)
    open_c = recs["o"].astype(np.int64)
    high_c = recs["h"].astype(np.int64)
    low_c = recs["l"].astype(np.int64)
    close_c = recs["c"].astype(np.int64)
    amounts = recs["amt"].astype(np.float64)
    opens = open_c / 100.0
    highs = high_c / 100.0
    lows = low_c / 100.0
    closes = close_c / 100.0
    return dates, opens, highs, lows, closes, amounts, close_c, high_c


def infer_board(code):
    c = str(code).zfill(6)
    if c.startswith("688"):
        return "star"
    if c.startswith(("300", "301")):
        return "chinext"
    return "other"


def compute_limits(dates, close_c, high_c, code):
    """严格封死涨停（收盘==涨停价 且 最高==涨停价）；权威实现见 backtest_common.compute_limit_flags。"""
    return compute_limit_flags(dates, close_c, high_c, code)


def rolling_max20(arr):
    n = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < 20:
        return out
    swv = sliding_window_view(arr, 20)
    out[19:] = swv.max(axis=1)
    return out


def collect_stock_files():
    out = []
    for d in DAY_DIRS:
        p = Path(d)
        if not p.exists():
            continue
        for f in sorted(p.glob("*.day")):
            stem = f.stem.lower()
            if stem.startswith("sh"):
                code = stem[2:].zfill(6)
                if code.startswith("688"):
                    out.append((code, f))
            elif stem.startswith("sz"):
                code = stem[2:].zfill(6)
                if code.startswith(("300", "301")):
                    out.append((code, f))
    return out


def screen_one(code, day_file, start_int, end_int):
    result = read_day_raw(day_file)
    if result is None:
        return []
    dates, opens, highs, lows, closes, amounts, close_c, high_c = result
    n = len(dates)
    if n < 29:  # 20 日窗口 + 9 天回看（D-9）
        return []

    board = infer_board(code)
    limits = compute_limits(dates, close_c, high_c, code)
    amt_max20 = rolling_max20(amounts)
    high_max20 = rolling_max20(highs)

    with np.errstate(invalid="ignore"):
        is_amtmax = np.zeros(n, dtype=bool)
        is_highmax = np.zeros(n, dtype=bool)
        is_amtmax[19:] = amounts[19:] == amt_max20[19:]
        is_highmax[19:] = highs[19:] == high_max20[19:]

    not_limit = ~limits

    # —— 候选掩码（对齐 D-0=i）——
    d0_ok  = limits & is_amtmax & is_highmax        # D-0: 涨停+最大+最高
    d1_ok  = np.zeros(n, dtype=bool); d1_ok[1:]  = not_limit[:-1] & (~is_amtmax[:-1])
    d2_ok  = np.zeros(n, dtype=bool); d2_ok[2:]  = not_limit[:-2] & (~is_amtmax[:-2])
    d3_ok  = np.zeros(n, dtype=bool); d3_ok[3:]  = not_limit[:-3] & (~is_amtmax[:-3])
    d4_ok  = np.zeros(n, dtype=bool); d4_ok[4:]  = not_limit[:-4] & (~is_amtmax[:-4])
    d5_ok  = np.zeros(n, dtype=bool); d5_ok[5:]  = not_limit[:-5] & is_amtmax[:-5]           # 无 is_highmax
    d6_ok  = np.zeros(n, dtype=bool); d6_ok[6:]  = not_limit[:-6] & (~is_amtmax[:-6])        # ~is_amtmax（0908B 互换）
    d7_ok  = np.zeros(n, dtype=bool); d7_ok[7:]  = not_limit[:-7] & is_amtmax[:-7]            # is_amtmax（0908B 互换）
    d8_ok  = np.zeros(n, dtype=bool); d8_ok[8:]  = not_limit[:-8] & (~is_amtmax[:-8])
    d9_ok  = np.zeros(n, dtype=bool); d9_ok[9:]  = not_limit[:-9] & (~is_amtmax[:-9])        # 新增

    cand = d0_ok & d1_ok & d2_ok & d3_ok & d4_ok & d5_ok & d6_ok & d7_ok & d8_ok & d9_ok
    # 有效 i：i-9>=19 → i>=28
    cand[:28] = False

    if board == "chinext":
        cand &= dates >= GEM_20CM_DATE
    elif board == "star":
        cand &= dates >= STAR_20CM_DATE
    cand &= (dates >= start_int) & (dates <= end_int)

    idxs = np.nonzero(cand)[0]
    if idxs.size == 0:
        return []

    rows = []
    for i in idxs:
        i = int(i)

        # —— 前收（区间基收）定义 ——
        #   D-7 振幅前收 = D-8 收 = closes[i-8]
        #   D-5 振幅前收 = D-6 收 = closes[i-6]
        #   D-7~D-5 区间基收 = D-8 收 = closes[i-8]；末收 = D-5 收 = closes[i-5]
        #   D-4~D-1 区间基收 = D-5 收 = closes[i-5]；末收 = D-1 收 = closes[i-1]
        #   D-0 振幅前收 = D-1 收 = closes[i-1]
        d8_close = closes[i - 8]   # D-7 振幅前收 / D-7~D-5 区间基收
        d6_close = closes[i - 6]   # D-5 振幅前收
        d5_close = closes[i - 5]   # D-7~D-5 区间末收 / D-4~D-1 区间基收 / D-0/D-5 成交额
        d1_close = closes[i - 1]   # D-0 振幅前收 / D-4~D-1 区间末收
        d0_close = closes[i]
        if d8_close <= 0 or d6_close <= 0 or d5_close <= 0 or d1_close <= 0 or d0_close <= 0:
            continue

        # 单日振幅（数值不带 %）
        amp_d7 = (highs[i - 7] - lows[i - 7]) / d8_close * 100.0
        amp_d5 = (highs[i - 5] - lows[i - 5]) / d6_close * 100.0
        amp_d0 = (highs[i] - lows[i]) / d1_close * 100.0

        # D-7~D-5 区间（三日：i-7, i-6, i-5；slice[i-7:i-4]）
        rng_highs_75 = highs[i - 7 : i - 4]
        rng_lows_75 = lows[i - 7 : i - 4]
        range_amp_75 = (rng_highs_75.max() - rng_lows_75.min()) / d8_close * 100.0
        range_gain_75 = (d5_close - d8_close) / d8_close * 100.0

        # D-4~D-1 区间（四日：i-4..i-1）
        rng_highs_41 = highs[i - 4 : i]
        rng_lows_41 = lows[i - 4 : i]
        range_amp_41 = (rng_highs_41.max() - rng_lows_41.min()) / d5_close * 100.0
        range_gain_41 = (d1_close - d5_close) / d5_close * 100.0
        # 区间最大振幅：逐日算振幅取 max
        amp_d4 = (highs[i - 4] - lows[i - 4]) / d5_close * 100.0
        amp_d3 = (highs[i - 3] - lows[i - 3]) / closes[i - 4] * 100.0
        amp_d2 = (highs[i - 2] - lows[i - 2]) / closes[i - 3] * 100.0
        amp_d1 = (highs[i - 1] - lows[i - 1]) / closes[i - 2] * 100.0
        range_max_amp_41 = max(amp_d4, amp_d3, amp_d2, amp_d1)

        # D-0/D-5 成交额百分比
        amt_d5 = amounts[i - 5]
        amt_pct = (amounts[i] / amt_d5 * 100.0) if amt_d5 > 0 else 0.0

        t_raw = generate_t_fields(
            opens, highs, lows, closes, limits,
            base_idx=i, n=n, t_count=7,
        )

        rows.append({
            "股票代码": code,
            "股票名称": "",
            "D-0日期": fmt_date(dates[i]),
            "D-7振幅": round(float(amp_d7), 2),
            "D-5振幅": round(float(amp_d5), 2),
            "D-7~D-5区间涨幅": f"{round(float(range_gain_75), 2)}%",
            "D-7~D-5区间振幅": f"{round(float(range_amp_75), 2)}%",
            "D-4~D-1区间涨幅": f"{round(float(range_gain_41), 2)}%",
            "D-4~D-1区间振幅": f"{round(float(range_amp_41), 2)}%",
            "D-4~D-1区间最大振幅": f"{round(float(range_max_amp_41), 2)}%",
            "D-0振幅": round(float(amp_d0), 2),
            "D-0/D-5成交额百分比": f"{round(float(amt_pct), 2)}%",
            "T+0(低/高)": t_raw.get("T+0(低/高)", "N/A"),
            "T+1最高价": t_raw.get("T+1最高价", "N/A"),
            "T+2最高价": t_raw.get("T+2最高价", "N/A"),
            "T+3最高价": t_raw.get("T+3最高价", "N/A"),
            "T+4最高价": t_raw.get("T+4最高价", "N/A"),
            "T+5最高价": t_raw.get("T+5最高价", "N/A"),
            "T+6最高价": t_raw.get("T+6最高价", "N/A"),
        })

    return rows


def main():
    parser = argparse.ArgumentParser(description=f"规则{RULE_NAME} 涨停形态筛选（20cm）")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    start_int = int(args.start.replace("-", ""))
    end_int = int(args.end.replace("-", ""))

    latest_date, file_count = check_data_freshness()
    print(f"规则: {RULE_NAME}")
    print(f"数据源最新日期: {latest_date}（共 {file_count} 个 .day 文件）")
    print(f"日期范围: {args.start} ~ {args.end}")
    print(f"适用标的: 20cm（科创板 688 + 创业板 300/301）")
    print(f"输出目录: {OUTPUT_DIR}\n")

    print("===== 阶段1: 收集 20cm 标的 .day 文件 =====")
    stock_files = collect_stock_files()
    print(f"  20cm 标的: {len(stock_files)} 个")
    if args.limit > 0:
        stock_files = stock_files[: args.limit]
        print(f"  [小样本模式] 仅处理前 {args.limit} 只")

    print(f"\n===== 阶段2: {RULE_NAME} 筛选 =====")
    t0 = time.time()
    results = []
    for k, (code, f) in enumerate(stock_files):
        try:
            results.extend(screen_one(code, f, start_int, end_int))
        except Exception as exc:
            print(f"  [错误] {f.name}: {exc}")
        if (k + 1) % 500 == 0:
            elapsed = time.time() - t0
            print(f"  进度 {k+1}/{len(stock_files)}  累计命中 {len(results)}  耗时 {elapsed:.1f}s")
    elapsed = time.time() - t0
    print(f"\n  筛选完成: {len(results)} 条, 耗时 {elapsed:.1f}s")

    print(f"\n===== 阶段3: 股票名称匹配与输出 =====")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not results:
        print("  未找到符合条件的结果")
        return None

    df = pd.DataFrame(results)
    attach_stock_names(df)

    xlsx_path = OUTPUT_DIR / f"{RULE_NAME}_backtest.xlsx"
    csv_path = OUTPUT_DIR / f"{RULE_NAME}_backtest.csv"
    output_excel(df, OUTPUT_COLUMNS, xlsx_path)
    df.reindex(columns=OUTPUT_COLUMNS).to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"  CSV: {csv_path}")

    print(f"\n===== 统计 =====")
    print(f"  命中样本: {len(df)}")
    if len(df):
        print(f"  涉及个股: {df['股票代码'].nunique()}")
        print(f"  D-0 日期范围: {df['D-0日期'].min()} ~ {df['D-0日期'].max()}")
    return str(xlsx_path)


if __name__ == "__main__":
    main()
