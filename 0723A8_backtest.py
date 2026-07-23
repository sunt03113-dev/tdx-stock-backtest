# -*- coding: utf-8 -*-
"""
0723A8 筛选规则 —— tdx-stock-backtest skill 扩展
规则：
  D1-D5 连续5日，D2-D5 高低点非递降，仅 D4 10cm 涨停
  D6 不涨停
  BaseDay: D5+2~D5+11 首个涨停且非20日最高价、非20日最大成交额
  间隔压制: D6~BaseDay-1 每日最高价≤D5最高价，每日成交额≤D5成交额
  额外输出: 以BaseDay收盘价为基准，T+0~T+7 涨幅
用法:
  python 0723A8_backtest.py
  python 0723A8_backtest.py --update
  python 0723A8_backtest.py --start 2020-01-01 --end 2025-12-31
"""
import sys
from pathlib import Path
import argparse
import pandas as pd
from tqdm import tqdm

# ── 跨平台路径：从脚本所在目录加载主模块 ──
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from StockBacktest_TdxAutoRun0630 import (
    is_strong_limit_up, get_20day_window, is_window_max,
    TDX_BASE, DATA_CACHE, SYSTEM_TYPE, STOCK_NAMES_FILE, StockNameTool
)

# ===================== 输出路径 =====================
if SYSTEM_TYPE == "Windows":
    OUTPUT_DIR = Path(r"D:\筛选结果\0723A8")
else:
    OUTPUT_DIR = Path.home() / "tdx_cache" / "results" / "0723A8"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "0723A8_backtest.xlsx"

DEFAULT_START = "1990-12-19"
DEFAULT_END = "2099-12-31"


# ===================== 辅助函数 =====================
def board(code):
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "other"


def is_10cm_limit_up(today_close, today_high, y_close, trade_date, code):
    if y_close <= 0:
        return False
    return is_strong_limit_up(today_close, today_high, y_close, trade_date, code)


def daily_amplitude(high, low, prev_close):
    if prev_close <= 0:
        return 0.0
    return (high - low) / prev_close * 100


def range_amplitude(high_list, low_list, base_close):
    if base_close <= 0 or len(high_list) == 0:
        return 0.0
    return (max(high_list) - min(low_list)) / base_close * 100


# ===================== 单股筛选 =====================
def screen_one(code, path, start_dt, end_dt):
    df = pd.read_excel(path, dtype={"股票代码": str})
    required = {"日期", "最高", "最低", "收盘", "成交额"}
    if not required.issubset(df.columns):
        return []

    df = df.rename(columns={
        "最高": "最高价", "最低": "最低价",
        "收盘": "收盘价", "成交额": "成交额(元)"
    })
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)
    df = df[(df["日期"] >= start_dt) & (df["日期"] <= end_dt)].reset_index(drop=True)

    n = len(df)
    if n < 30:
        return []

    # 预计算涨停标记
    closes = df["收盘价"].values
    highs = df["最高价"].values
    lows = df["最低价"].values
    amts = df["成交额(元)"].values
    dates = df["日期"].values

    limits = [False] * n
    for i in range(1, n):
        limits[i] = is_10cm_limit_up(closes[i], highs[i], closes[i-1], dates[i], code)

    rows = []
    for d1 in range(1, n - 25):
        d2, d3, d4, d5, d6 = d1 + 1, d1 + 2, d1 + 3, d1 + 4, d1 + 5

        # 条件1: 仅 D4 涨停，D1/D2/D3/D5 不涨停
        if limits[d1] or limits[d2] or limits[d3] or not limits[d4] or limits[d5]:
            continue

        # 条件1: D2-D5 高低点非递降
        h = highs[d1:d5+1]  # D1~D5
        l = lows[d1:d5+1]
        ok = True
        for i in range(1, 5):
            if h[i] < h[i-1] or l[i] < l[i-1]:
                ok = False
                break
        if not ok:
            continue

        # 条件2: D6 不涨停
        if limits[d6]:
            continue

        d5_high = highs[d5]
        d5_amt = amts[d5]
        d5_close = closes[d5]
        d0_close = closes[d1 - 1]  # D1 前一日收盘

        # 条件3+4: 搜索 BaseDay
        base_day = None
        for idx in range(d5 + 2, min(d5 + 12, n)):
            if idx < 20:
                continue
            # 3.① BaseDay 涨停
            if not limits[idx]:
                continue
            # 3.② BaseDay 最高价非20日窗口最大
            w, wok = get_20day_window(df, idx)
            if not wok:
                continue
            if is_window_max(w["最高价"].tolist(), highs[idx]):
                continue
            # 3.③ BaseDay 成交额非20日窗口最大
            if is_window_max(w["成交额(元)"].tolist(), amts[idx]):
                continue

            # 条件4: D6~BaseDay-1 每日最高价≤D5最高价，成交额≤D5成交额
            interval_ok = True
            for s in range(d6, idx):
                if highs[s] > d5_high or amts[s] > d5_amt:
                    interval_ok = False
                    break
            if not interval_ok:
                break  # 后续候选的区间只会更大，必然也失败

            base_day = idx
            break

        if base_day is None:
            continue

        # ========== 指标计算 ==========
        # 4-8: D1~D5 每日振幅
        amp_d1 = daily_amplitude(highs[d1], lows[d1], closes[d1-1])
        amp_d2 = daily_amplitude(highs[d2], lows[d2], closes[d1])
        amp_d3 = daily_amplitude(highs[d3], lows[d3], closes[d2])
        amp_d4 = daily_amplitude(highs[d4], lows[d4], closes[d3])
        amp_d5 = daily_amplitude(highs[d5], lows[d5], closes[d4])

        # 9: D1-D2 区间振幅（基准=D0收盘价）
        amp_d1_d2 = range_amplitude(highs[d1:d3].tolist(), lows[d1:d3].tolist(), d0_close)

        # 10: D1-D5 区间总振幅（基准=D0收盘价）
        amp_d1_d5 = range_amplitude(highs[d1:d6].tolist(), lows[d1:d6].tolist(), d0_close)

        # 11: D6~BaseDay-1 交易日数量
        interval_days = base_day - d6

        # 12: D6~BaseDay-1 区间涨幅（基准=D5收盘价）
        base_day_prev_close = closes[base_day - 1]
        interval_inc = (base_day_prev_close - d5_close) / d5_close * 100 if d5_close > 0 else 0.0

        # 13: D6~BaseDay-1 区间振幅（基准=D5收盘价）
        interval_amp = range_amplitude(
            highs[d6:base_day].tolist(), lows[d6:base_day].tolist(), d5_close
        )

        # 14: BaseDay 当日振幅
        amp_base = daily_amplitude(highs[base_day], lows[base_day], closes[base_day - 1])

        # ========== 额外字段: 以BaseDay收盘价为基准 T+0~T+7 ==========
        base_close = closes[base_day]
        t_fields = {}

        for t_off in range(8):
            t_idx = base_day + 1 + t_off  # T+0=BaseDay+1, T+1=BaseDay+2, ...
            if t_idx < n and base_close > 0:
                if t_off == 0:
                    # T+0: 同时输出 (最低价-基准)/基准 和 (最高价-基准)/基准
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

        row = {
            "股票代码": str(code).zfill(6),
            "股票名称": "",
            "BaseDay日期": pd.Timestamp(dates[base_day]).strftime("%Y-%m-%d"),
            "D1振幅": f"{amp_d1:.2f}%",
            "D2振幅": f"{amp_d2:.2f}%",
            "D3振幅": f"{amp_d3:.2f}%",
            "D4振幅": f"{amp_d4:.2f}%",
            "D5振幅": f"{amp_d5:.2f}%",
            "D1-D2区间振幅": f"{amp_d1_d2:.2f}%",
            "D1-D5区间总振幅": f"{amp_d1_d5:.2f}%",
            "D6至BaseDay前交易日数量": interval_days,
            "D6至BaseDay前区间涨幅": f"{interval_inc:+.2f}%",
            "D6至BaseDay前区间振幅": f"{interval_amp:.2f}%",
            "BaseDay当日振幅": f"{amp_base:.2f}%",
        }
        row.update(t_fields)
        rows.append(row)

    return rows


def screen_path(args):
    path, start_dt, end_dt = args
    try:
        return screen_one(path.stem, path, start_dt, end_dt)
    except Exception as exc:
        print(f"  [错误] {path.name}: {exc}")
        return []


def update_data(start_dt, end_dt):
    from StockBacktest_TdxAutoRun0630 import run_module1_tdx_convert
    print("===== 阶段0: TDX数据源增量更新(.day→.xlsx) =====")
    run_module1_tdx_convert(start_dt, end_dt)
    print("  数据更新完成\n")


def run_0723a8(start_date=DEFAULT_START, end_date=DEFAULT_END, do_update=False):
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    print(f"日期范围: {start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')}")

    if do_update:
        update_data(start_dt, end_dt)
    else:
        print("(跳过数据更新，使用现有缓存)")

    print("\n===== 阶段1: 0723A8规则筛选 =====")
    print("  规则: D4涨停 + D6不涨停 + BaseDay非20日最强 + D5天花板间隔压制")
    files = sorted(DATA_CACHE.glob("*.xlsx"))
    if not files:
        print(f"  [错误] 未找到数据文件: {DATA_CACHE}")
        print("  请先运行: python 0723A8_backtest.py --update")
        return

    main_files = [f for f in files if board(f.stem) == "main"]
    print(f"  数据文件: {len(files)} 个, 主板10cm: {len(main_files)} 个")

    results = []
    for f in tqdm(main_files, desc="  0723A8筛选"):
        output = screen_path((f, start_dt, end_dt))
        results.extend(output)

    print(f"\n===== 阶段2: 股票名称匹配与输出 =====")
    print(f"  筛选结果: {len(results)} 条")

    if not results:
        print("  未找到符合条件的结果")
        return

    result_df = pd.DataFrame(results)

    # 股票名称匹配：优先本地CSV，回退akshare
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

    # 列顺序
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
    parser = argparse.ArgumentParser(description="0723A8 涨停形态筛选规则")
    parser.add_argument("--update", action="store_true", help="先更新TDX数据(.day→.xlsx)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    args = parser.parse_args()
    run_0723a8(start_date=args.start, end_date=args.end, do_update=args.update)
