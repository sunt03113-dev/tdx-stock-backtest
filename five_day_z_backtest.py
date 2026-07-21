from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import os

import pandas as pd
import akshare as ak
from tqdm import tqdm


DATA_DIR = Path(r"D:\数据源\day_xlsx_完整字段")
OUTPUT_FILE = Path(__file__).with_name("B.xlsx")
LIMIT_START = pd.Timestamp("1996-12-16")


def board(code):
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "other"


def dynamic_limit_pct(trade_date, code):
    code = str(code).zfill(6)
    date = pd.Timestamp(trade_date)
    if date < LIMIT_START:
        return None
    if code.startswith(("43", "83", "87", "88", "92")):
        return Decimal("0.30")
    if code.startswith("688") and date >= pd.Timestamp("2019-07-22"):
        return Decimal("0.20")
    if code.startswith(("300", "301")) and date >= pd.Timestamp("2020-08-24"):
        return Decimal("0.20")
    return Decimal("0.10")


def limit_price(previous_close, limit_pct):
    return (Decimal(str(previous_close)) * (Decimal("1") + limit_pct)).quantize(
        Decimal("0.00"), rounding=ROUND_HALF_UP
    )


def is_10cm_strong_limit_up(today, previous_close, code):
    limit_pct = dynamic_limit_pct(today["日期"], code)
    if limit_pct != Decimal("0.10") or previous_close <= 0:
        return False
    upper = limit_price(previous_close, limit_pct)
    close = Decimal(str(today["收盘价"])).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)
    high = Decimal(str(today["最高价"])).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)
    return close == upper and high >= upper


def signed(value, suffix="%"):
    return f"{value:+.2f}{suffix}"


def range_amplitude(frame, previous_close):
    return (frame["最高价"].max() - frame["最低价"].min()) / previous_close * 100


def daily_amplitude(row, previous_close):
    return (row["最高价"] - row["最低价"]) / previous_close * 100


def screen_one(code, path):
    df = pd.read_excel(path, dtype={"股票代码": str})
    required = {"日期", "最高", "最低", "收盘", "成交额"}
    if not required.issubset(df.columns):
        return []
    df = df.rename(columns={"最高": "最高价", "最低": "最低价", "收盘": "收盘价", "成交额": "成交额(元)"})
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)
    if len(df) < 26:
        return []

    limits = [False] * len(df)
    for i in range(1, len(df)):
        limits[i] = is_10cm_strong_limit_up(df.iloc[i], df.iloc[i - 1]["收盘价"], code)

    rows = []
    for d1 in range(1, len(df) - 17):
        d2, d3, d4, d5 = d1 + 1, d1 + 2, d1 + 3, d1 + 4
        if limits[d1] or limits[d2] or not limits[d3] or limits[d4] or limits[d5]:
            continue
        highs = df.loc[d1:d5, "最高价"].to_list()
        lows = df.loc[d1:d5, "最低价"].to_list()
        if any(highs[i] < highs[i - 1] or lows[i] < lows[i - 1] for i in range(1, 5)):
            continue

        z = next((idx for idx in range(d5 + 1, d5 + 14) if limits[idx]), None)
        if z is None:
            continue
        if any(limits[idx] for idx in range(d5 + 1, z)):
            continue
        if z < 19:
            continue
        window = df.iloc[z - 19:z + 1]
        if df.iloc[z]["最高价"] < window["最高价"].max() or df.iloc[z]["成交额(元)"] < window["成交额(元)"].max():
            continue

        before_d1_close = df.iloc[d1 - 1]["收盘价"]
        rows.append({
            "股票代码": str(code).zfill(6),
            "后续目标涨停日期": df.iloc[z]["日期"].strftime("%Y-%m-%d"),
            "前三天区间振幅": signed(range_amplitude(df.iloc[d1:d3 + 1], before_d1_close)),
            "D3涨停当日振幅": signed(daily_amplitude(df.iloc[d3], df.iloc[d3 - 1]["收盘价"]), ""),
            "五日最后一日D5振幅": signed(daily_amplitude(df.iloc[d5], df.iloc[d5 - 1]["收盘价"]), ""),
            "五日整体区间涨幅": signed((df.iloc[d5]["收盘价"] - before_d1_close) / before_d1_close * 100),
            "五日整体区间振幅": signed(range_amplitude(df.iloc[d1:d5 + 1], before_d1_close)),
            "间隔交易日天数": z - d5,
            "Z日涨停振幅": signed(daily_amplitude(df.iloc[z], df.iloc[z - 1]["收盘价"]), ""),
            "间隔交易日区间涨幅": signed((df.iloc[z]["收盘价"] - df.iloc[d5]["收盘价"]) / df.iloc[d5]["收盘价"] * 100),
            "间隔交易日区间振幅": signed(range_amplitude(df.iloc[d5 + 1:z + 1], df.iloc[d5]["收盘价"])),
        })
    return rows


def screen_path(path):
    try:
        return screen_one(path.stem, path)
    except Exception as exc:
        return [("__error__", path.name, str(exc))]


def main():
    files = sorted(DATA_DIR.glob("*.xlsx"))
    if not files:
        raise FileNotFoundError(f"No cache files in {DATA_DIR}")
    results = []
    workers = min(16, os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for output in tqdm(executor.map(screen_path, files, chunksize=8), total=len(files), desc="筛选五日结构"):
            if output and isinstance(output[0], tuple):
                print(f"Skip {output[0][1]}: {output[0][2]}")
                continue
            results.extend(output)
    columns = [
        "股票代码", "后续目标涨停日期", "间隔交易日天数", "Z日涨停振幅", "股票名称",
        "间隔交易日区间涨幅", "间隔交易日区间振幅", "五日组合字段",
    ]
    result_df = pd.DataFrame(results)
    try:
        names = ak.stock_info_a_code_name().astype({"code": str})
        name_map = names.set_index("code")["name"].to_dict()
        result_df["股票名称"] = result_df["股票代码"].map(name_map).fillna("未找到标的")
    except Exception as exc:
        print(f"Stock-name lookup failed: {exc}")
        result_df["股票名称"] = "名称查询失败"
    result_df["五日组合字段"] = (
        result_df["五日整体区间涨幅"] + "*" + result_df["五日整体区间振幅"]
        + "+" + result_df["五日最后一日D5振幅"].str.lstrip("+")
    )
    result_df.reindex(columns=columns).to_excel(OUTPUT_FILE, index=False)
    print(f"{len(results)} rows written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
