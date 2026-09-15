# -*- coding: utf-8 -*-
"""
规则0814 筛选 —— 全 A 股（沪深主板 10cm + 科创板/创业板 20cm）

时间线（D0 为基准起点 i）：
  D0   (i)   : 非涨停；当日成交额非 20 日最大
  D+1  (i+1) : 涨停；当日成交额为 20 日最大；当日股价(最高价)为 20 日最高
  D+2  (i+2) : 非涨停；当日成交额为 20 日最大
  间隔窗口 D+3 ~ T-1（长度 3~12 个交易日）：窗口内每一天成交额都不能是各自 20 日最大
               且窗口内无涨停
  基准日 T   : 窗口期结束后首个交易日（即自 D+3 起首个"成交额为 20 日最大"的交易日）
               满足：T 涨停 + T 成交额 20 日最大 + T 股价 20 日最高

输出：
  1. 基准日 T 日期
  2. D+1 日振幅
  3. D+2 日振幅
  4. 间隔区间(D+3 ~ T前一日)整体区间振幅
  5. 间隔区间整体区间涨幅
  6. 间隔区间内单日最大振幅
  7. 基准日 T 的单日振幅

实现要点：
  - 直接读取通达信 .day 二进制（sh/sz），按市场前缀过滤出 A 股股票（剔除指数/ETF/债券/B股）。
  - 涨停判定采用“整数分”向量化算法，与 StockBacktest_TdxAutoRun0630.is_strong_limit_up
    的 Decimal ROUND_HALF_UP 结果完全一致（价格以整数分存储，涨停价=round_half_up(y_cents*(1+pct))）。
  - 20 日最大成交额 / 最高价用 sliding_window_view 向量化预计算。
  - 强预过滤候选（D0/D+1/D+2 条件向量化掩码），再对每个候选做窗口扫描定位 T。
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

RULE_NAME = "0814"
OUTPUT_DIR = Path(r"D:\09work\0814_backtest")

# 日期阈值（整数 YYYYMMDD）
LIMIT_START = 19961216          # 1996-12-16 起 A 股涨跌停限制
STAR_20CM_DATE = 20190722       # 科创板 20cm 生效
# GEM_20CM_DATE = 20200824      # 创业板 20cm 生效（来自 backtest_common）

OUTPUT_COLUMNS = [
    "股票代码", "股票名称", "D0日期", "D+1日期", "D+2日期", "基准日T日期", "间隔天数",
    "D+1日振幅(%)", "D+2日振幅(%)", "间隔区间振幅(%)", "间隔区间涨幅(%)",
    "间隔区间单日最大振幅(%)", "基准日T单日振幅(%)",
    # ── 基准日T 之后的 T+n 基准价（基准价 = 基准日T 收盘价）──
    "T+1(低/高)", "T+2最高价", "T+3最高价", "T+4最高价",
    "T+5最高价", "T+6最高价", "T+7最高价",
]

# generate_t_fields 产出键 → 本规则输出键（基准日T 视为 T+0 基准，其后第1日为 T+1）
_T_RENAME = {"T+0(低/高)": "T+1(低/高)"}
for _k in range(1, 7):
    _T_RENAME[f"T+{_k}最高价"] = f"T+{_k+1}最高价"


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

def infer_board(code):
    c = str(code).zfill(6)
    if c.startswith("688"):
        return "star"
    if c.startswith(("300", "301")):
        return "chinext"
    if c.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "other"


def compute_limits(dates, close_c, high_c, code):
    """严格封死涨停（收盘==涨停价 且 最高==涨停价）；权威实现见 backtest_common.compute_limit_flags。"""
    return compute_limit_flags(dates, close_c, high_c, code)


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


# ===================== 标的文件收集（市场前缀过滤 A 股） =====================

def collect_stock_files():
    """收集 20cm 标的（科创板 688 + 创业板 300/301）.day 文件。"""
    out = []
    for d in DAY_DIRS:
        p = Path(d)
        if not p.exists():
            continue
        for f in sorted(p.glob("*.day")):
            stem = f.stem.lower()
            if stem.startswith("sh"):
                code = stem[2:].zfill(6)
                if code.startswith("688"):          # 科创板 20cm
                    out.append((code, f))
            elif stem.startswith("sz"):
                code = stem[2:].zfill(6)
                if code.startswith(("300", "301")):  # 创业板 20cm
                    out.append((code, f))
    return out


# ===================== 单股筛选 =====================

def screen_one(code, day_file, start_int, end_int):
    result = read_day_raw(day_file)
    if result is None:
        return []
    dates, opens, highs, lows, closes, amounts, close_c, high_c = result
    n = len(dates)
    if n < 35:
        return []

    board = infer_board(code)

    # 涨停标记
    limits = compute_limits(dates, close_c, high_c, code)

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

    # —— 候选掩码（对齐 D0=i）——
    cand = np.zeros(n, dtype=bool)
    # D0(i): 非涨停 & 成交额非 20 日最大
    d0_ok = not_limit & (~is_amtmax)
    # D+1(i+1): 涨停 & 成交额 20 日最大 & 最高价 20 日最高
    d1_ok = np.zeros(n, dtype=bool)
    d1_ok[: n - 1] = limits[1:] & is_amtmax[1:] & is_highmax[1:]
    # D+2(i+2): 非涨停 & 成交额 20 日最大
    d2_ok = np.zeros(n, dtype=bool)
    d2_ok[: n - 2] = not_limit[2:] & is_amtmax[2:]

    cand = d0_ok & d1_ok & d2_ok
    # 有效 i 范围：i>=19（D0 的 20 日窗口）；i+15<=n-1（T 最大为 i+15）
    cand[:19] = False
    if n - 15 > 0:
        cand[n - 15:] = False

    # 板块前置过滤（仅使用对应涨跌幅生效后的样本）
    if board == "chinext":
        chinext_mask = dates >= GEM_20CM_DATE
        cand &= chinext_mask
    elif board == "star":
        star_mask = dates >= STAR_20CM_DATE
        cand &= star_mask

    # 日期范围过滤（按 D0 日期）
    cand &= (dates >= start_int) & (dates <= end_int)

    idxs = np.nonzero(cand)[0]
    if idxs.size == 0:
        return []

    rows = []
    for i in idxs:
        i = int(i)
        # 窗口扫描：自 D+3(i+3) 起首个“成交额 20 日最大”日 → 即 T
        win = is_amtmax[i + 3 : i + 16]  # j = i+3 .. i+15（共 13 个）
        if not win.any():
            continue  # 窗口内无 20 日最大日 → 实际 T> i+15，间隔 >12，舍弃
        first_off = int(np.argmax(win))
        j = i + 3 + first_off  # T
        if j < i + 6:
            continue  # 间隔天数 < 3，舍弃
        # 间隔期 D+3 ~ T-1 内无涨停
        if limits[i + 3 : j].any():
            continue
        # T 条件：涨停 & 最高价 20 日最高（成交额 20 日最大已由扫描保证）
        if not (limits[j] and is_highmax[j]):
            continue

        # —— 输出指标 ——
        d0_close = closes[i]
        d1_close = closes[i + 1]
        d2_close = closes[i + 2]
        # 防御性除零
        if d0_close <= 0 or d1_close <= 0 or d2_close <= 0 or closes[j - 1] <= 0:
            continue

        amp_d1 = (highs[i + 1] - lows[i + 1]) / d0_close * 100.0
        amp_d2 = (highs[i + 2] - lows[i + 2]) / d1_close * 100.0

        # 间隔区间 D+3..T-1 = [i+3, j-1]
        rng_highs = highs[i + 3 : j]
        rng_lows = lows[i + 3 : j]
        rng_prev_closes = closes[i + 2 : j - 1]  # k 的前收 = closes[k-1]
        if rng_highs.size == 0 or rng_prev_closes.size == 0:
            continue
        range_amp = (rng_highs.max() - rng_lows.min()) / d2_close * 100.0
        range_gain = (closes[j - 1] - d2_close) / d2_close * 100.0
        single_amps = (rng_highs - rng_lows) / rng_prev_closes * 100.0
        max_single_amp = float(single_amps.max())

        amp_t = (highs[j] - lows[j]) / closes[j - 1] * 100.0
        gap_days = j - i - 3  # 间隔天数 L

        row = {
            "股票代码": code,
            "股票名称": "",
            "D0日期": fmt_date(dates[i]),
            "D+1日期": fmt_date(dates[i + 1]),
            "D+2日期": fmt_date(dates[i + 2]),
            "基准日T日期": fmt_date(dates[j]),
            "间隔天数": int(gap_days),
            "D+1日振幅(%)": round(float(amp_d1), 2),
            "D+2日振幅(%)": round(float(amp_d2), 2),
            "间隔区间振幅(%)": round(float(range_amp), 2),
            "间隔区间涨幅(%)": round(float(range_gain), 2),
            "间隔区间单日最大振幅(%)": round(max_single_amp, 2),
            "基准日T单日振幅(%)": round(float(amp_t), 2),
        }

        # ── 基准日T 之后的 T+1~T+7 基准价字段 ──
        # generate_t_fields 以 base_idx 为基准，产出 T+0(低/高) ~ T+6最高价
        # （其中 T+0 实指 base_idx+1，即本规则的 T+1）
        # 通过 _T_RENAME 映射为本规则输出键：T+0→T+1, T+1→T+2, ..., T+6→T+7
        # 注：后续交易日不足 7 天时，generate_t_fields 自动返回 "N/A"
        t_raw = generate_t_fields(
            opens, highs, lows, closes, limits,
            base_idx=j, n=n, t_count=7,
        )
        for src_key, dst_key in _T_RENAME.items():
            row[dst_key] = t_raw.get(src_key, "N/A")

        rows.append(row)

    return rows


# ===================== 入口 =====================

def main():
    parser = argparse.ArgumentParser(description=f"规则{RULE_NAME} 涨停形态筛选（全 A 股）")
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

    # 简要统计
    print(f"\n===== 统计 =====")
    print(f"  命中样本: {len(df)}")
    if len(df):
        print(f"  涉及个股: {df['股票代码'].nunique()}")
        print(f"  基准日T 日期范围: {df['基准日T日期'].min()} ~ {df['基准日T日期'].max()}")
        print(f"  间隔天数分布: {df['间隔天数'].min()}~{df['间隔天数'].max()} (中位数 {int(df['间隔天数'].median())})")
    return str(xlsx_path)


if __name__ == "__main__":
    main()
