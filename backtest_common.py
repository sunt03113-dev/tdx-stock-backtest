# -*- coding: utf-8 -*-
"""
回测公共工具模块 —— 所有规则脚本的共享底座。

提供以下能力：
  1. .day 二进制文件读取（numpy 向量化解析）
  2. 标的板块判定（10cm / 20cm）
  3. 日期 / 振幅 / 涨幅格式化
  4. T+n 基准价取整与格式化（规则①②）
  5. K 线形态判定（板 / 阳 / 阴）
  6. 涨停标记预计算
  7. T+0~T+6 输出字段生成
  8. 股票名称匹配
  9. Excel 输出
 10. 数据源新鲜度检查

新规则脚本只需 import 本模块并实现筛选条件，无需重复编写基础设施。
"""
import math
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

from StockBacktest_TdxAutoRun0630 import (
    is_strong_limit_up, TDX_BASE, SYSTEM_TYPE, StockNameTool,
    DAY_DIRS, DEFAULT_GLOBAL_START_DATE,
)

logger = logging.getLogger("backtest_common")

# ===================== 常量 =====================

DEFAULT_START = DEFAULT_GLOBAL_START_DATE  # "1990-12-19"
DEFAULT_END = "2099-12-31"

# 创业板 20cm 生效日期（整数 YYYYMMDD）
GEM_20CM_DATE = 20200824
# 科创板 20cm 生效日期
STAR_20CM_DATE = 20190722

# ===================== 标的板块判定 =====================

def is_20cm(code):
    """判断是否为 20cm 标的（科创板 688 + 创业板 300/301）"""
    c = str(code).replace("sh", "").replace("sz", "").zfill(6)
    return c.startswith(("688", "300", "301"))


def is_10cm(code):
    """判断是否为 10cm 主板标的"""
    c = str(code).replace("sh", "").replace("sz", "").zfill(6)
    return c.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))


def is_gem_300(code):
    """判断是否为创业板 300 标的（需 2020-08-24 前置过滤）"""
    return str(code).replace("sh", "").replace("sz", "").zfill(6).startswith("300")


def normalize_code(code):
    """标准化股票代码为 6 位字符串"""
    return str(code).replace("sh", "").replace("sz", "").zfill(6)


# ===================== .day 文件读取 =====================

def read_day_file(filepath):
    """
    读取通达信 .day 二进制文件，返回 numpy 数组。

    每条记录 32 字节：日期(int32) 开(int32) 高(int32) 低(int32) 收(int32) 额(float32) 量(int32) 保留(int32)
    价格字段已除以 100 转为元。
    """
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


def collect_day_files(target_type):
    """
    收集指定板块类型的 .day 文件列表。

    target_type: "20cm" | "10cm" | "all"
    返回: list[Path]
    """
    all_files = []
    for d in DAY_DIRS:
        p = Path(d)
        if p.exists():
            all_files.extend(sorted(p.glob("*.day")))

    if target_type == "20cm":
        return [f for f in all_files if is_20cm(f.stem)]
    elif target_type == "10cm":
        return [f for f in all_files if is_10cm(f.stem)]
    else:
        return all_files


# ===================== 涨停标记预计算 =====================

def precompute_limits(dates_raw, opens, highs, closes, code):
    """
    预计算每个交易日的涨停标记。

    调用核心引擎 is_strong_limit_up()，动态适配涨跌幅规则。
    返回: np.ndarray[bool]，长度等于 K 线数量。
    """
    n = len(dates_raw)
    dates_pd = pd.to_datetime(dates_raw.astype(str), format='%Y%m%d')
    limits = np.zeros(n, dtype=bool)
    for i in range(1, n):
        limits[i] = is_strong_limit_up(
            float(closes[i]), float(highs[i]), float(closes[i - 1]),
            dates_pd[i], code
        )
    return limits


# ===================== 格式化函数 =====================

def fmt_date(d_int):
    """整数日期 → YYYY-MM-DD 字符串"""
    d = int(d_int)
    return f"{d // 10000:04d}-{d % 10000 // 100:02d}-{d % 100:02d}"


def daily_amplitude(high, low, prev_close):
    """
    单日振幅 = (最高 - 最低) / 前收 × 100
    返回 float，不带 % 号。
    """
    if prev_close <= 0:
        return 0.0
    return (high - low) / prev_close * 100


# ── T+n 取整规则 ──

def fmt_t0(val_pct):
    """
    T+0 取整规则（均变大）：
      负数：直接保留整数位（-1.1 → -1）
      正数：向上取整（1.1 → 2）
    """
    if val_pct <= 0:
        return int(val_pct)
    return math.ceil(val_pct)


def fmt_tn(val_pct):
    """
    T+1~7 取整规则（均变小）：
      负数：向下取整，取不大于原数的最大整数（-1.1 → -2）
      正数：向下取整，取不大于原数的最大整数（1.1 → 1）
    """
    return math.floor(val_pct)


# ── 正负号格式化 ──

def fmt_no_sign(val):
    """T+n 用：正数不带 + 号，负数带 - 号"""
    return str(val)


def fmt_plus(val):
    """涨幅用：正数带 + 号，负数带 - 号"""
    s = str(val)
    if not s.startswith("-"):
        return f"+{s}"
    return s


# ── K 线形态 ──

def candle_form(open_val, close_val, is_limit):
    """
    判断 K 线形态：板 / 阳 / 阴
      板 = 当日涨停
      阳 = 收盘 > 开盘（非涨停）
      阴 = 收盘 ≤ 开盘（非涨停）
    """
    if is_limit:
        return "板"
    if close_val > open_val:
        return "阳"
    return "阴"


# ===================== T+n 输出字段生成 =====================

def generate_t_fields(opens, highs, lows, closes, limits, base_idx, n, t_count=7):
    """
    生成 T+0 ~ T+(t_count-1) 输出字段。

    参数:
      opens/highs/lows/closes/limits : K 线数组
      base_idx : 基准日索引（T+n 基准价 = closes[base_idx]）
      n : K 线总数
      t_count : T+n 数量（默认 7，即 T+0~T+6）

    返回: dict[str, str]
      "T+0(低/高)": "低%/高%(阴/阳/板)"
      "T+{i}最高价": "高%(板)" 或 "高%"
    """
    base_close = closes[base_idx]
    t_fields = {}
    for t_off in range(t_count):
        t_idx = base_idx + 1 + t_off
        if t_idx < n and base_close > 0:
            if t_off == 0:
                # T+0: 最低/最高价变化值%，附带阴/阳/板
                low_pct = (lows[t_idx] - base_close) / base_close * 100
                high_pct = (highs[t_idx] - base_close) / base_close * 100
                low_val = fmt_t0(low_pct)
                high_val = fmt_t0(high_pct)
                form = candle_form(opens[t_idx], closes[t_idx], limits[t_idx])
                t_fields["T+0(低/高)"] = f"{fmt_no_sign(low_val)}%/{fmt_no_sign(high_val)}%({form})"
            else:
                # T+1~6: 仅最高价变化值%，涨停标注板
                high_pct = (highs[t_idx] - base_close) / base_close * 100
                val = fmt_tn(high_pct)
                if limits[t_idx]:
                    t_fields[f"T+{t_off}最高价"] = f"{fmt_no_sign(val)}%(板)"
                else:
                    t_fields[f"T+{t_off}最高价"] = f"{fmt_no_sign(val)}%"
        else:
            if t_off == 0:
                t_fields["T+0(低/高)"] = "N/A"
            else:
                t_fields[f"T+{t_off}最高价"] = "N/A"
    return t_fields


# ===================== 股票名称匹配 =====================

def match_stock_names(result_df):
    """
    为结果 DataFrame 匹配股票名称（优先本地 stock_names.csv，回退 akshare）。

    直接在 result_df 上修改 "股票名称" 列。
    """
    try:
        name_tool = StockNameTool()
        name_df = name_tool.load_cache()
        name_map = dict(zip(
            name_df["code"].astype(str).str.zfill(6),
            name_df["name"]
        ))
        result_df["股票名称"] = result_df["股票代码"].map(name_map).fillna("未找到标的")
    except Exception as exc:
        logger.warning(f"名称匹配失败: {exc}")
        result_df["股票名称"] = "名称查询失败"
    return result_df


# ===================== Excel 输出 =====================

def output_excel(result_df, columns, output_file):
    """
    按 columns 顺序输出 Excel，自动补充缺失列。

    result_df : pd.DataFrame
    columns : list[str] 期望的列顺序
    output_file : Path 或 str
    """
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # 补充缺失列
    for col in columns:
        if col not in result_df.columns:
            result_df[col] = "N/A"

    result_df = result_df.reindex(columns=columns)
    result_df.to_excel(output_file, index=False)
    print(f"  完成: {len(result_df)} 条 -> {output_file}")
    return str(output_file)


# ===================== 数据源新鲜度检查 =====================

def check_data_freshness():
    """
    检查 .day 文件最新日期，返回 (最新日期字符串, 文件数)。

    用于执行前向用户确认数据源是否为最新。
    """
    all_files = collect_day_files("all")
    if not all_files:
        return ("无数据", 0)

    latest_date = 0
    for f in all_files:
        try:
            result = read_day_file(f)
            if result is not None:
                dates = result[0]
                if len(dates) > 0:
                    latest_date = max(latest_date, int(dates[-1]))
        except Exception:
            continue

    if latest_date == 0:
        return ("无数据", len(all_files))

    latest_str = fmt_date(latest_date)
    return (latest_str, len(all_files))


# ===================== 通用回测执行框架 =====================

def run_backtest(rule_name, screen_func, target_type, output_columns,
                 start_date=None, end_date=None, output_dir=None):
    """
    通用回测执行入口。

    参数:
      rule_name      : 规则名称（如 "0803"），用于输出目录
      screen_func    : 筛选函数，签名 (code, day_file, start_int, end_int) -> list[dict]
      target_type    : "20cm" | "10cm" | "all"
      output_columns : list[str] 输出列顺序
      start_date     : 起始日期字符串，默认 "1990-12-19"
      end_date       : 结束日期字符串，默认 "2099-12-31"
      output_dir     : 输出目录，默认 SCRIPT_DIR/results/<rule_name>/
    """
    start_date = start_date or DEFAULT_START
    end_date = end_date or DEFAULT_END
    start_int = int(start_date.replace("-", ""))
    end_int = int(end_date.replace("-", ""))

    print(f"规则: {rule_name}")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"适用标的: {target_type}")
    print(f"数据源: 直接读取 .day 二进制文件")

    # 确定脚本所在目录
    import inspect
    script_dir = Path(inspect.getfile(screen_func)).parent.resolve()
    if output_dir is None:
        output_dir = script_dir / "results" / rule_name
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{rule_name}_backtest.xlsx"

    print(f"\n===== 阶段1: 收集 .day 文件 =====")
    day_files = collect_day_files(target_type)
    print(f"  {target_type} 标的: {len(day_files)} 个")

    if not day_files:
        print("  [错误] 未找到 .day 文件")
        return None

    print(f"\n===== 阶段2: {rule_name} 筛选 =====")
    import time
    t0 = time.time()
    results = []
    for f in tqdm(day_files, desc=f"  {rule_name}筛选"):
        try:
            code = normalize_code(f.stem)
            output = screen_func(code, f, start_int, end_int)
            results.extend(output)
        except Exception as exc:
            print(f"  [错误] {f.name}: {exc}")

    elapsed = time.time() - t0
    print(f"\n  筛选完成: {len(results)} 条, 耗时 {elapsed:.1f}s")

    print(f"\n===== 阶段3: 股票名称匹配与输出 =====")
    if not results:
        print("  未找到符合条件的结果")
        return None

    result_df = pd.DataFrame(results)
    match_stock_names(result_df)
    output_excel(result_df, output_columns, output_file)
    return str(output_file)
