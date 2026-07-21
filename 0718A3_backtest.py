# -*- coding: utf-8 -*-
"""
0718A3 筛选规则 —— tdx-stock-backtest skill 扩展
规则：
  T1-T5 连续5日，T2-T5 高低点非递降，仅 T4 10cm 涨停
  T6 不涨停
  间隔压制：T7~Tend-1 每日最高价 ≤ T6最高价，每日成交额 ≤ T6成交额
  Tend = T5+2~T5+11 首个涨停且最高价非20日最高且成交额非20日最大
用法:
  python 0718A3_backtest.py
  python 0718A3_backtest.py --update
  python 0718A3_backtest.py --start 2020-01-01 --end 2025-12-31
"""
import sys
sys.path.insert(0, r"C:\Users\21412\.claude\skills\tdx-stock-backtest")

from pathlib import Path
import argparse
import pandas as pd
import akshare as ak
from tqdm import tqdm
from StockBacktest_TdxAutoRun0630 import is_strong_limit_up

# ===================== 路径配置 =====================
DATA_DIR = Path(r"D:\数据源\day_xlsx_完整字段")
OUTPUT_DIR = Path(r"D:\筛选结果\0715")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "0718A3.xlsx"

DEFAULT_START = "1990-12-19"
DEFAULT_END = "2099-12-31"


def board(code):
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "other"


def is_10cm_limit_up(today_close, today_high, y_close, trade_date, code):
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
    for t1 in range(1, len(df) - 5):
        t2, t3, t4, t5, t6 = t1 + 1, t1 + 2, t1 + 3, t1 + 4, t1 + 5

        # 仅 T4 涨停，T1/T2/T3/T5 不涨停
        if limits[t1] or limits[t2] or limits[t3] or not limits[t4] or limits[t5]:
            continue
        # T6 不涨停
        if limits[t6]:
            continue

        # T2-T5 高低点非递降
        highs = df.loc[t1:t5, "最高价"].to_list()
        lows = df.loc[t1:t5, "最低价"].to_list()
        if any(highs[i] < highs[i - 1] or lows[i] < lows[i - 1] for i in range(1, 5)):
            continue

        t6_high = df.iloc[t6]["最高价"]
        t6_amt = df.iloc[t6]["成交额(元)"]

        # Tend: T5+2~T5+11，首个满足全部条件
        tend = None
        for idx in range(t5 + 2, min(t5 + 12, len(df))):
            if not limits[idx] or idx < 20:
                continue

            # 间隔压制检查 (T7 ~ Tend-1)
            suppress_fail = False
            for s in range(t5 + 2, idx):
                if df.iloc[s]["最高价"] > t6_high or df.iloc[s]["成交额(元)"] > t6_amt:
                    suppress_fail = True
                    break
            if suppress_fail:
                # 如果某天不满足压制，这个 idx 不能作为 Tend，但继续搜下一个
                continue

            # Tend 非最强：高 < 前20日最高，额 < 前20日最大
            window = df.iloc[idx - 20:idx]
            if (df.iloc[idx]["最高价"] < window["最高价"].max() and
                    df.iloc[idx]["成交额(元)"] < window["成交额(元)"].max()):
                tend = idx
                break

        if tend is None:
            continue

        # ========== 指标计算 ==========
        t0_close = df.iloc[t1 - 1]["收盘价"]
        t3_close = df.iloc[t3]["收盘价"]
        t4_close = df.iloc[t4]["收盘价"]
        t5_close = df.iloc[t5]["收盘价"]

        t1_t3_amp = range_amplitude(df.iloc[t1:t3 + 1], t0_close)
        t4_amp = daily_amplitude(df.iloc[t4], t3_close)
        t5_amp = daily_amplitude(df.iloc[t5], t4_close)
        t1_t5_inc = (t5_close - t0_close) / t0_close * 100
        t1_t5_amp = range_amplitude(df.iloc[t1:t5 + 1], t0_close)
        interval_inc = (df.iloc[tend - 1]["收盘价"] - t5_close) / t5_close * 100
        interval_amp = range_amplitude(df.iloc[t6:tend], t5_close)
        tend_amp = daily_amplitude(df.iloc[tend], df.iloc[tend - 1]["收盘价"])

        rows.append({
            "股票代码": str(code).zfill(6),
            "股票名称": "",
            "Tend日期": df.iloc[tend]["日期"].strftime("%Y-%m-%d"),
            "T1-T3区间振幅": f"{t1_t3_amp:+.2f}%",
            "T4单日振幅": f"{t4_amp:.2f}%",
            "T5单日振幅": f"{t5_amp:.2f}%",
            "T1-T5区间总涨幅": f"{t1_t5_inc:+.2f}%",
            "T1-T5区间总振幅": f"{t1_t5_amp:.2f}%",
            "T6至Tend-1交易日数量": tend - t6,
            "T6至Tend-1区间涨幅": f"{interval_inc:+.2f}%",
            "T6至Tend-1区间振幅": f"{interval_amp:.2f}%",
            "Tend当日振幅": f"{tend_amp:.2f}%",
        })

    return rows


def screen_path(args):
    path, start_dt, end_dt = args
    try:
        return screen_one(path.stem, path, start_dt, end_dt)
    except Exception as exc:
        return [("__error__", path.name, str(exc))]


def update_data(start_dt, end_dt):
    from StockBacktest_TdxAutoRun0630 import run_module1_tdx_convert
    print("===== 阶段0：TDX数据源增量更新 =====")
    run_module1_tdx_convert(start_dt, end_dt)
    print("  数据更新完成\n")


def run_0718a3(start_date=DEFAULT_START, end_date=DEFAULT_END, do_update=False):
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    print(f"日期范围: {start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')}")

    if do_update:
        update_data(start_dt, end_dt)
    else:
        print("(跳过数据更新，使用现有缓存)")

    print("\n===== 阶段1：0718A3规则筛选(T4涨停,间隔压制,Tend非最强) =====")
    files = sorted(DATA_DIR.glob("*.xlsx"))
    if not files:
        raise FileNotFoundError(f"未找到数据文件: {DATA_DIR}")
    main_files = [f for f in files if board(f.stem) == "main"]
    print(f"  数据文件: {len(files)} 个, 主板10cm: {len(main_files)} 个")

    results = []
    for f in tqdm(main_files, desc="  0718A3筛选"):
        output = screen_path((f, start_dt, end_dt))
        if output and isinstance(output[0], tuple):
            print(f"  Skip {output[0][1]}: {output[0][2]}")
            continue
        results.extend(output)

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
        "股票代码", "股票名称", "Tend日期",
        "T1-T3区间振幅", "T4单日振幅", "T5单日振幅",
        "T1-T5区间总涨幅", "T1-T5区间总振幅",
        "T6至Tend-1交易日数量",
        "T6至Tend-1区间涨幅", "T6至Tend-1区间振幅",
        "Tend当日振幅",
    ]
    result_df = result_df.reindex(columns=columns)
    result_df.to_excel(OUTPUT_FILE, index=False)
    print(f"  完成: {len(results)} 条 → {OUTPUT_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="0718A3 涨停筛选规则")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    args = parser.parse_args()
    run_0718a3(start_date=args.start, end_date=args.end, do_update=args.update)
