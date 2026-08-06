# -*- coding: utf-8 -*-
"""
规则模板 —— 新规则开发起点。

使用方法：
  1. 复制本文件为 <规则名>_backtest.py
  2. 修改 RULE_NAME、TARGET_TYPE、OUTPUT_COLUMNS
  3. 在 screen_one() 中实现筛选条件
  4. 在「输出指标计算」区域实现输出字段
  5. 运行: python3 <规则名>_backtest.py

所有基础设施（.day 读取、涨停判定、格式化、T+n 生成、名称匹配、Excel 输出）
均由 backtest_common.py 提供，无需重复编写。
"""
import sys
import argparse
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from backtest_common import (
    # ── 标的判定 ──
    is_20cm, is_10cm, is_gem_300, normalize_code,
    # ── 数据读取 ──
    read_day_file, collect_day_files, precompute_limits,
    # ── 格式化 ──
    fmt_date, daily_amplitude, fmt_t0, fmt_tn, fmt_no_sign, fmt_plus, candle_form,
    # ── T+n 生成 ──
    generate_t_fields,
    # ── 输出与执行 ──
    match_stock_names, output_excel, check_data_freshness, run_backtest,
    # ── 常量 ──
    DEFAULT_START, DEFAULT_END, GEM_20CM_DATE,
)

# ===================== 规则配置（新规则只需修改这里） =====================

RULE_NAME = "template"          # 规则名称
TARGET_TYPE = "20cm"            # "20cm" | "10cm" | "all"
T_COUNT = 7                     # T+n 数量（T+0 ~ T+(T_COUNT-1)）

OUTPUT_COLUMNS = [
    "股票代码", "股票名称", "样本日期",
    # ── 在此添加输出字段 ──
    "T+0(低/高)", "T+1最高价", "T+2最高价", "T+3最高价",
    "T+4最高价", "T+5最高价", "T+6最高价",
]

# ===================== 筛选条件实现 =====================

def screen_one(code, day_file, start_int, end_int):
    """
    单股筛选函数。

    参数:
      code      : 股票代码（6 位字符串）
      day_file  : .day 文件路径
      start_int : 起始日期整数（YYYYMMDD）
      end_int   : 结束日期整数（YYYYMMDD）

    返回: list[dict]，每个 dict 为一条筛选结果。
    """
    result = read_day_file(day_file)
    if result is None:
        return []
    dates_raw, opens, highs, lows, closes, amounts, volumes = result

    # 日期过滤
    mask = (dates_raw >= start_int) & (dates_raw <= end_int)
    if mask.sum() == 0:
        return []
    dates_raw = dates_raw[mask]
    opens, highs, lows, closes, amounts, volumes = (
        opens[mask], highs[mask], lows[mask], closes[mask],
        amounts[mask], volumes[mask]
    )

    n = len(dates_raw)
    if n < 30:
        return []

    # 预计算涨停标记
    limits = precompute_limits(dates_raw, opens, highs, closes, code)

    # ── 样本前置过滤（如需） ──
    # 创业板 300 标的剔除 2020-08-24 前数据
    if is_gem_300(code):
        pass  # 在循环内按日期判断

    rows = []
    # 遍历候选日，实现筛选条件
    for i in range(4, n - T_COUNT - 1):
        # === 示例：D-1 在 i，D0 在 i+1 ===
        dm1 = i
        d0 = i + 1

        # === 样本前置过滤 ===
        if is_gem_300(code) and dates_raw[dm1] < GEM_20CM_DATE:
            continue

        # === 筛选条件（在此实现） ===
        # 示例：D-1 涨停
        if not limits[dm1]:
            continue

        # 示例：D0 非涨停
        if limits[d0]:
            continue

        # ... 添加更多筛选条件 ...

        # === 输出指标计算 ===
        base_idx = d0  # 基准日索引（T+n 基准价 = closes[base_idx]）

        # T+0 ~ T+6
        t_fields = generate_t_fields(
            opens, highs, lows, closes, limits,
            base_idx, n, t_count=T_COUNT
        )

        row = {
            "股票代码": code,
            "股票名称": "",
            "样本日期": fmt_date(dates_raw[d0]),
            # ── 在此添加输出字段 ──
        }
        row.update(t_fields)
        rows.append(row)

    return rows


# ===================== 入口 =====================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"规则{RULE_NAME} 涨停形态筛选")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    args = parser.parse_args()

    # 执行前检查数据源新鲜度
    latest_date, file_count = check_data_freshness()
    print(f"数据源最新日期: {latest_date}（共 {file_count} 个文件）")
    print(f"请确认数据源是否为最新后继续...\n")

    run_backtest(
        rule_name=RULE_NAME,
        screen_func=screen_one,
        target_type=TARGET_TYPE,
        output_columns=OUTPUT_COLUMNS,
        start_date=args.start,
        end_date=args.end,
    )
