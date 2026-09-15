# -*- coding: utf-8 -*-
"""
规则0827A 筛选 —— 10cm（沪市主板 600/601/603/605 + 深市主板 000/001/002/003）

时间线（D-0 为基准日 i，向前回看历史）：
  D-4 (i-4) : 非涨停；成交额非 20 日最大
  D-3 (i-3) : 非涨停；成交额非 20 日最大
  D-2 (i-2) : 涨停；成交额为 20 日最大；最高价为 20 日最高
  D-1 (i-1) : 非涨停；成交额为 20 日最大
  D-0 (i)   : 非涨停；成交额为 20 日最大；最高价为 20 日最高（基准日）

输出：
  1. D-0 日期
  2. D-2 单日振幅（数值不带 %，如 9.8）
  3. D-1 单日涨幅（数值不带 %）
  4. D-1 单日振幅（数值不带 %）
  5. D-0 单日涨幅（数值不带 %）
  6. D-0 单日振幅（数值不带 %）
  7. D-0/D-2 成交额百分比（数值带 %，如 240.14%）
  8. D-0/D-1 成交额百分比（数值带 %）
  9. T+0~T+7 价格走势（基准价 = D-0 收盘价）

注意：单日涨幅/振幅数值不带 %；成交额百分比数值带 %。

数据源：本地通达信 .day 二进制（D:\\05_software\\02_programs\\TDx\\vipdoc\\sh 与 sz）
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
    DEFAULT_START, DEFAULT_END,
)


def attach_stock_names(df):
    """直接通过 akshare 匹配股票名称（一次拉取全 A 股代码名称表）。失败则留空。"""
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


RULE_NAME = "0827A"
OUTPUT_DIR = Path(r"D:\09work\0827A_backtest")

# 日期阈值（整数 YYYYMMDD）
LIMIT_START = 19961216          # 1996-12-16 起 A 股涨跌停限制

OUTPUT_COLUMNS = [
    "股票代码", "股票名称", "D-0日期",
    # 单日振幅/涨幅（数值不带 %）
    "D-2振幅", "D-1涨幅", "D-1振幅", "D-0涨幅", "D-0振幅",
    # 成交额百分比（数值带 %）
    "D-0/D-2成交额百分比", "D-0/D-1成交额百分比",
    # ── D-0 之后的 T+0~T+7 价格走势（基准价 = D-0 收盘价）──
    "T+0(低/高)", "T+1最高价", "T+2最高价", "T+3最高价",
    "T+4最高价", "T+5最高价", "T+6最高价", "T+7最高价",
]

# generate_t_fields 直接以 D-0 为基准，产出键名与输出键一致，无需重命名


# ===================== .day 读取（保留整数分） =====================

_DT = np.dtype([
    ("d", "<i4"), ("o", "<i4"), ("h", "<i4"), ("l", "<i4"),
    ("c", "<i4"), ("amt", "<f4"), ("vol", "<i4"), ("r", "<i4"),
])


def read_day_raw(filepath):
    """读取 .day 二进制，返回整数分价格 + 浮点价格 + 成交额。"""
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


# ===================== 向量化涨停判定（整数分，与 Decimal 等价） =====================

def compute_limits(dates, close_c, high_c):
    """严格封死涨停（10cm 主板：收盘==涨停价 且 最高==涨停价）；权威实现见 backtest_common.compute_limit_flags。"""
    return compute_limit_flags(dates, close_c, high_c, None)


# ===================== 20 日滚动最大值 =====================

def rolling_max20(arr):
    """返回 max20[i] = max(arr[i-19:i+1])，i<19 为 NaN。"""
    n = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < 20:
        return out
    swv = sliding_window_view(arr, 20)  # shape (n-19, 20)
    out[19:] = swv.max(axis=1)
    return out


# ===================== 标的文件收集（10cm 过滤） =====================

def collect_stock_files():
    """收集 10cm 主板标的（沪市 600/601/603/605 + 深市 000/001/002/003）.day 文件。"""
    out = []
    for d in DAY_DIRS:
        p = Path(d)
        if not p.exists():
            continue
        for f in sorted(p.glob("*.day")):
            stem = f.stem.lower()
            if stem.startswith("sh"):
                code = stem[2:].zfill(6)
                if code.startswith(("600", "601", "603", "605")):  # 沪市主板 10cm
                    out.append((code, f))
            elif stem.startswith("sz"):
                code = stem[2:].zfill(6)
                if code.startswith(("000", "001", "002", "003")):  # 深市主板 10cm
                    out.append((code, f))
    return out


# ===================== 单股筛选 =====================

def screen_one(code, day_file, start_int, end_int):
    result = read_day_raw(day_file)
    if result is None:
        return []
    dates, opens, highs, lows, closes, amounts, close_c, high_c = result
    n = len(dates)
    if n < 24:  # 至少 20 日窗口 + 4 天回看（D-4）
        return []

    # 涨停标记（10cm 主板，统一 10% 涨跌幅）
    limits = compute_limits(dates, close_c, high_c)

    # 20 日最大成交额 / 最高价
    amt_max20 = rolling_max20(amounts)
    high_max20 = rolling_max20(highs)

    # is_max：当日值 == 其 20 日窗口最大值（含自身，故 == 即为最大，允许并列）
    with np.errstate(invalid="ignore"):
        is_amtmax = np.zeros(n, dtype=bool)
        is_highmax = np.zeros(n, dtype=bool)
        is_amtmax[19:] = amounts[19:] == amt_max20[19:]
        is_highmax[19:] = highs[19:] == high_max20[19:]

    not_limit = ~limits

    # —— 候选掩码（对齐 D-0=i）——
    # D-0(i): 非涨停 + 成交额 20 日最大 + 最高价 20 日最高
    d0_ok = not_limit & is_amtmax & is_highmax
    # D-1(i-1): 非涨停 + 成交额 20 日最大
    d1_ok = np.zeros(n, dtype=bool)
    d1_ok[1:] = not_limit[:-1] & is_amtmax[:-1]
    # D-2(i-2): 涨停 + 成交额 20 日最大 + 最高价 20 日最高
    d2_ok = np.zeros(n, dtype=bool)
    d2_ok[2:] = limits[:-2] & is_amtmax[:-2] & is_highmax[:-2]
    # D-3(i-3): 非涨停 + 成交额非 20 日最大
    d3_ok = np.zeros(n, dtype=bool)
    d3_ok[3:] = not_limit[:-3] & (~is_amtmax[:-3])
    # D-4(i-4): 非涨停 + 成交额非 20 日最大
    d4_ok = np.zeros(n, dtype=bool)
    d4_ok[4:] = not_limit[:-4] & (~is_amtmax[:-4])

    cand = d0_ok & d1_ok & d2_ok & d3_ok & d4_ok
    # 有效 i 范围：i-4>=19（D-4 的 20 日窗口起点），即 i>=23
    # 后续 T+0~T+7 不足时由 generate_t_fields 自动返回 N/A，不在此处截断
    cand[:23] = False

    # 日期范围过滤（按 D-0 日期）
    cand &= (dates >= start_int) & (dates <= end_int)

    idxs = np.nonzero(cand)[0]
    if idxs.size == 0:
        return []

    rows = []
    for i in idxs:
        i = int(i)

        # —— 输出指标 ——
        # 前收定义：D-k 的前一日收盘 = closes[i-k-1]
        #   D-2 前收 = D-3 收盘 = closes[i-3]
        #   D-1 前收 = D-2 收盘 = closes[i-2]
        #   D-0 前收 = D-1 收盘 = closes[i-1]
        d3_close = closes[i - 3]
        d2_close = closes[i - 2]
        d1_close = closes[i - 1]
        d0_close = closes[i]
        # 防御性除零
        if d3_close <= 0 or d2_close <= 0 or d1_close <= 0 or d0_close <= 0:
            continue

        # 单日涨幅 = (今收 - 前收) / 前收 * 100（输出时不带 %）
        # D-1 涨幅：今收 = D-1 收盘 = closes[i-1]，前收 = D-2 收盘 = closes[i-2]
        gain_d1 = (d1_close - d2_close) / d2_close * 100.0
        # D-0 涨幅：今收 = D-0 收盘 = closes[i]，前收 = D-1 收盘 = closes[i-1]
        gain_d0 = (d0_close - d1_close) / d1_close * 100.0

        # 单日振幅 = (高 - 低) / 前收 * 100（输出时不带 %）
        amp_d2 = (highs[i - 2] - lows[i - 2]) / d3_close * 100.0
        amp_d1 = (highs[i - 1] - lows[i - 1]) / d2_close * 100.0
        amp_d0 = (highs[i] - lows[i]) / d1_close * 100.0

        # D-0/D-2 成交额百分比 = D-0成交额 / D-2成交额 * 100
        amt_d2 = amounts[i - 2]
        if amt_d2 > 0:
            amt_pct_d0_d2 = amounts[i] / amt_d2 * 100.0
        else:
            amt_pct_d0_d2 = 0.0

        # D-0/D-1 成交额百分比 = D-0成交额 / D-1成交额 * 100
        amt_d1 = amounts[i - 1]
        if amt_d1 > 0:
            amt_pct_d0_d1 = amounts[i] / amt_d1 * 100.0
        else:
            amt_pct_d0_d1 = 0.0

        # ── D-0 之后的 T+0~T+7 价格走势（基准价 = D-0 收盘价）──
        # generate_t_fields 以 base_idx 为基准，产出 T+0(低/高) ~ T+7最高价，键名与输出键一致
        t_raw = generate_t_fields(
            opens, highs, lows, closes, limits,
            base_idx=i, n=n, t_count=8,
        )

        rows.append({
            "股票代码": code,
            "股票名称": "",
            "D-0日期": fmt_date(dates[i]),
            # 单日振幅/涨幅：数值不带 %（如 9.8）
            "D-2振幅": round(float(amp_d2), 2),
            "D-1涨幅": round(float(gain_d1), 2),
            "D-1振幅": round(float(amp_d1), 2),
            "D-0涨幅": round(float(gain_d0), 2),
            "D-0振幅": round(float(amp_d0), 2),
            # 成交额百分比：数值带 %（如 240.14%）
            "D-0/D-2成交额百分比": f"{round(float(amt_pct_d0_d2), 2)}%",
            "D-0/D-1成交额百分比": f"{round(float(amt_pct_d0_d1), 2)}%",
            "T+0(低/高)": t_raw.get("T+0(低/高)", "N/A"),
            "T+1最高价": t_raw.get("T+1最高价", "N/A"),
            "T+2最高价": t_raw.get("T+2最高价", "N/A"),
            "T+3最高价": t_raw.get("T+3最高价", "N/A"),
            "T+4最高价": t_raw.get("T+4最高价", "N/A"),
            "T+5最高价": t_raw.get("T+5最高价", "N/A"),
            "T+6最高价": t_raw.get("T+6最高价", "N/A"),
            "T+7最高价": t_raw.get("T+7最高价", "N/A"),
        })

    return rows


# ===================== 入口 =====================

def main():
    parser = argparse.ArgumentParser(description=f"规则{RULE_NAME} 涨停形态筛选（10cm）")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--limit", type=int, default=0,
                        help="仅处理前 N 只标的（0=全部），用于小样本验证")
    args = parser.parse_args()
    start_int = int(args.start.replace("-", ""))
    end_int = int(args.end.replace("-", ""))

    latest_date, file_count = check_data_freshness()
    print(f"规则: {RULE_NAME}")
    print(f"数据源最新日期: {latest_date}（共 {file_count} 个 .day 文件）")
    print(f"日期范围: {args.start} ~ {args.end}")
    print(f"适用标的: 10cm（沪市主板 600/601/603/605 + 深市主板 000/001/002/003）")
    print(f"输出目录: {OUTPUT_DIR}\n")

    print("===== 阶段1: 收集 10cm 标的 .day 文件 =====")
    stock_files = collect_stock_files()
    print(f"  10cm 标的: {len(stock_files)} 个")
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

    # 简要统计
    print(f"\n===== 统计 =====")
    print(f"  命中样本: {len(df)}")
    if len(df):
        print(f"  涉及个股: {df['股票代码'].nunique()}")
        print(f"  D-0 日期范围: {df['D-0日期'].min()} ~ {df['D-0日期'].max()}")
    return str(xlsx_path)


if __name__ == "__main__":
    main()
