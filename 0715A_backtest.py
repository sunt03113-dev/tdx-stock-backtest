# -*- coding: utf-8 -*-
"""
0715A 筛选规则 —— tdx-stock-backtest skill 扩展
用法:
  python 0715A_backtest.py                        # 默认：用现有缓存，全量历史
  python 0715A_backtest.py --update               # 先更新 TDX 数据再筛选
  python 0715A_backtest.py --start 2020-01-01     # 指定起始日期
  python 0715A_backtest.py --end 2025-12-31       # 指定截止日期
"""
import sys
sys.path.insert(0, r"C:\Users\21412\.claude\skills\tdx-stock-backtest")

from pathlib import Path
import argparse

import pandas as pd
import akshare as ak
from tqdm import tqdm

# Skill 主脚本的涨停判定函数
from StockBacktest_TdxAutoRun0630 import is_strong_limit_up

# ===================== 路径配置 =====================
DATA_DIR = Path(r"D:\数据源\day_xlsx_完整字段")
OUTPUT_DIR = Path(r"D:\筛选结果\0715")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "0715A.xlsx"

DEFAULT_START = "1990-12-19"
DEFAULT_END = "2099-12-31"

# ===================== 工具函数 =====================
def board(code):
    """仅保留主板 10cm 标的"""
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "other"


def is_10cm_limit_up(today_close, today_high, y_close, trade_date, code):
    """10cm 强封涨停判定"""
    if y_close <= 0:
        return False
    return is_strong_limit_up(today_close, today_high, y_close, trade_date, code)


def range_amplitude(frame, base_close):
    if base_close <= 0 or len(frame) == 0:
        return 0.0
    return (frame["最高价"].max() - frame["最低价"].min()) / base_close * 100


def daily_amplitude(row, prev_close):
    if prev_close <= 0:
        return 0.0
    return (row["最高价"] - row["最低价"]) / prev_close * 100


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

    if len(df) < 26:
        return []

    limits = [False] * len(df)
    for i in range(1, len(df)):
        limits[i] = is_10cm_limit_up(
            df.iloc[i]["收盘价"], df.iloc[i]["最高价"],
            df.iloc[i - 1]["收盘价"], df.iloc[i]["日期"], code
        )

    rows = []
    for d1 in range(1, len(df) - 17):
        d2, d3, d4, d5 = d1 + 1, d1 + 2, d1 + 3, d1 + 4

        # 仅 D4 涨停
        if limits[d1] or limits[d2] or limits[d3] or not limits[d4] or limits[d5]:
            continue

        # 高低点递升
        highs = df.loc[d1:d5, "最高价"].to_list()
        lows = df.loc[d1:d5, "最低价"].to_list()
        if any(highs[i] < highs[i - 1] or lows[i] < lows[i - 1] for i in range(1, 5)):
            continue

        # Z日：间隔 1~10 天（z ∈ [d5+2, d5+11]）
        z = next((idx for idx in range(d5 + 2, min(d5 + 12, len(df))) if limits[idx]), None)
        if z is None:
            continue
        if any(limits[idx] for idx in range(d5 + 1, z)):
            continue

        # Z日 = 20日最高价 + 最大成交额
        if z < 19:
            continue
        window = df.iloc[z - 19:z + 1]
        if (df.iloc[z]["最高价"] < window["最高价"].max() or
                df.iloc[z]["成交额(元)"] < window["成交额(元)"].max()):
            continue

        # 计算指标
        before_d1 = df.iloc[d1 - 1]["收盘价"]
        d1_d3_amp = range_amplitude(df.iloc[d1:d3 + 1], before_d1)
        d4_amp = daily_amplitude(df.iloc[d4], df.iloc[d3]["收盘价"])
        d5_amp = daily_amplitude(df.iloc[d5], df.iloc[d4]["收盘价"])
        five_day_inc = (df.iloc[d5]["收盘价"] - before_d1) / before_d1 * 100
        five_day_amp = range_amplitude(df.iloc[d1:d5 + 1], before_d1)
        z_amp = daily_amplitude(df.iloc[z], df.iloc[z - 1]["收盘价"])
        gap = z - d5 - 1
        interval_inc = (df.iloc[z - 1]["收盘价"] - df.iloc[d5]["收盘价"]) / df.iloc[d5]["收盘价"] * 100
        interval_amp = range_amplitude(df.iloc[d5 + 1:z], df.iloc[d5]["收盘价"])

        rows.append({
            "股票代码": str(code).zfill(6),
            "股票名称": "",
            "Z日涨停日期": df.iloc[z]["日期"].strftime("%Y-%m-%d"),
            "三日区间振幅+D4振幅+D5振幅": f"{d1_d3_amp:+.2f}%+{d4_amp:.2f}%+{d5_amp:.2f}%",
            "5日区间涨幅*区间振幅": f"{five_day_inc:+.2f}%*{five_day_amp:.2f}%",
            "间隔交易日天数": gap,
            "Z日涨停振幅": f"{z_amp:.2f}%",
            "间隔交易日区间涨幅": f"{interval_inc:+.2f}%",
            "间隔交易日区间振幅": f"{interval_amp:.2f}%",
        })
    return rows


def screen_path(args):
    path, start_dt, end_dt = args
    try:
        return screen_one(path.stem, path, start_dt, end_dt)
    except Exception as exc:
        return [("__error__", path.name, str(exc))]


# ===================== 数据更新 =====================
def update_data(start_dt, end_dt):
    """调用 skill 主脚本 Module1 增量更新 TDX → Excel"""
    from StockBacktest_TdxAutoRun0630 import run_module1_tdx_convert
    print("===== 阶段0：TDX数据源增量更新 =====")
    run_module1_tdx_convert(start_dt, end_dt)
    print("  数据更新完成\n")


# ===================== 主流程 =====================
def run_0715a(start_date=DEFAULT_START, end_date=DEFAULT_END, do_update=False):
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    print(f"日期范围: {start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')}")

    # 可选：数据更新
    if do_update:
        update_data(start_dt, end_dt)
    else:
        print("(跳过数据更新，使用现有缓存。如需更新请加 --update)")

    # 筛选
    print("\n===== 阶段1：0715A规则筛选 =====")
    files = sorted(DATA_DIR.glob("*.xlsx"))
    if not files:
        raise FileNotFoundError(f"未找到数据文件: {DATA_DIR}")
    main_files = [f for f in files if board(f.stem) == "main"]
    print(f"  数据文件: {len(files)} 个, 主板10cm: {len(main_files)} 个")

    results = []
    # 单进程模式：避免多进程 DLL/page-file 问题
    for f in tqdm(main_files, desc="  0715A筛选(D4涨停)"):
        output = screen_path((f, start_dt, end_dt))
        if output and isinstance(output[0], tuple):
            print(f"  Skip {output[0][1]}: {output[0][2]}")
            continue
        results.extend(output)

    # 输出
    print(f"\n===== 阶段2：输出 =====")
    if not results:
        print("  未找到符合条件的结果")
        return

    result_df = pd.DataFrame(results)
    try:
        names = ak.stock_info_a_code_name().astype({"code": str})
        name_map = names.set_index("code")["name"].to_dict()
        result_df["股票名称"] = result_df["股票代码"].map(name_map).fillna("未找到标的")
    except Exception as exc:
        print(f"  名称匹配失败: {exc}")
        result_df["股票名称"] = "名称查询失败"

    columns = [
        "股票代码", "股票名称", "Z日涨停日期",
        "三日区间振幅+D4振幅+D5振幅", "5日区间涨幅*区间振幅",
        "间隔交易日天数", "Z日涨停振幅",
        "间隔交易日区间涨幅", "间隔交易日区间振幅",
    ]
    result_df = result_df.reindex(columns=columns)
    result_df.to_excel(OUTPUT_FILE, index=False)
    print(f"  完成: {len(results)} 条 → {OUTPUT_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="0715A 涨停筛选规则")
    parser.add_argument("--update", action="store_true", help="先更新TDX数据源再筛选")
    parser.add_argument("--start", type=str, default=DEFAULT_START, help="筛选起始日期")
    parser.add_argument("--end", type=str, default=DEFAULT_END, help="筛选截止日期")
    args = parser.parse_args()
    run_0715a(start_date=args.start, end_date=args.end, do_update=args.update)
