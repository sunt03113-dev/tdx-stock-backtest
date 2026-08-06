---
name: tdx-stock-backtest
description: A股涨停形态回测引擎。直接读取通达信.day二进制文件，全量日线数据回测，动态涨跌停判定，支持多规则时序筛选，输出Excel。用于本地股票数据筛选、涨停形态回测、规则验证。
---
# tdx-stock-backtest

## 适用场景
- 用户需要运行 A 股涨停形态回测筛选
- 用户需要验证某只股票是否满足特定规则
- 用户需要整理筛选规则并输出 Excel 结果
- 用户提到 TDX、通达信、日线数据、涨停、回测、筛选规则
- 用户提供新规则定义，需要创建回测脚本

## 核心文件
- `StockBacktest_TdxAutoRun0630.py`: 核心引擎（TDX .day 解析 + 动态涨停判定 + 股票名称匹配）
- `backtest_common.py`: **公共工具模块**（所有规则共享底座）
  - .day 二进制读取（`read_day_file`）
  - 标的板块判定（`is_20cm` / `is_10cm` / `is_gem_300`）
  - 涨停标记预计算（`precompute_limits`）
  - 日期/振幅/涨幅格式化（`fmt_date` / `daily_amplitude` / `fmt_plus`）
  - T+n 取整与格式化（`fmt_t0` / `fmt_tn` / `fmt_no_sign`）
  - K线形态判定（`candle_form`）
  - T+0~T+6 输出字段生成（`generate_t_fields`）
  - 股票名称匹配（`match_stock_names`）
  - Excel 输出（`output_excel`）
  - 数据源新鲜度检查（`check_data_freshness`）
  - 通用回测执行框架（`run_backtest`）
- `rule_template.py`: **新规则开发模板**（复制后修改筛选条件即可）
- `0803_backtest.py` / `0805_backtest.py`: 20cm 规则脚本（基于 backtest_common 实现）
- `A9_backtest.py` / `B9_backtest.py` / `0723A8_backtest.py`: 10cm 规则脚本
- `config.yaml`: 配置文件（数据路径，每台机器独立生成）

## 运行命令
```bash
# 运行已有规则
cd ~/tdx-stock-backtest && python3 <规则名>_backtest.py

# 指定日期范围
python3 <规则名>_backtest.py --start 2020-01-01 --end 2025-12-31

# 结果输出到 ~/tdx-stock-backtest/results/<规则名>/
```

---

## 通用设计规范（所有规则必须遵守）

### 一、执行前确认
每次执行回测前，必须先向用户确认数据源是否为最新（调用 `check_data_freshness()` 检查 .day 文件最新日期）。

### 二、数据源
- 通达信 `.day` 二进制文件
- 目录：`~/Documents/TDx/vipdoc/sh/lday/` 和 `~/Documents/TDx/vipdoc/sz/lday/`
- 股票名称优先使用本地 `stock_names.csv`，未找到时回退 akshare 联网查询
- 回测起始基准：`1990-12-19`（覆盖老八股开市全量行情）

### 三、标的筛选
按规则指定的板块前缀筛选标的：
- 10cm 主板：600/601/603/605/000/001/002/003
- 20cm 科创板：688
- 20cm 创业板：300/301

### 四、样本前置过滤
- 创业板 300xxx 标的剔除 2020-08-24 前 10cm 阶段数据
- 科创板 688xxx 标的剔除 2019-07-22 前数据
- 仅使用对应涨跌幅生效后的样本

### 五、动态涨跌停判定
所有模块必须调用 `is_strong_limit_up()`，不可硬编码涨跌幅比例：
- 1996-12-16 前无涨跌停限制
- 主板普通股 10%
- 科创板 688xxx：2019-07-22 起 20%
- 创业板 300xxx/301xxx：2020-08-24 起 20%，此前 10%
- 北交所 30%
- ST/风险警示/退市标的 5%

强封涨停定义：
- 最高价 ≥ 理论涨停价（Decimal 高精度计算，四舍五入保留 2 位小数）
- 收盘价 ≥ 理论涨停价
- 盘中触及涨停但收盘低于涨停价不算涨停

### 六、T+n 基准价输出规则（全局通用）

**基准价定义：**
- 基准价 = 规则指定的基准日收盘价（如 D-0 收盘价、D-1 收盘价等）
- T+0 = 基准日的下一个交易日

**输出内容：**
- T+0：输出最低基准价和最高基准价（两个值用 `/` 分隔）
  - 最低基准价 = (T+0最低价 - 基准日收盘价) / 基准日收盘价 × 100
  - 最高基准价 = (T+0最高价 - 基准日收盘价) / 基准日收盘价 × 100
- T+1 ~ T+n（n>0）：仅输出最高基准价
  - 最高基准价 = (T+n最高价 - 基准日收盘价) / 基准日收盘价 × 100

**取整规则：**
- 规则①（T+0，均变大）：
  - 负数：直接保留整数位（-1.1 → -1）
  - 正数：向上取整（1.1 → 2）
- 规则②（T+1~7，均变小）：
  - 负数：向下取整，取不大于原数的最大整数（-1.1 → -2）
  - 正数：向下取整，取不大于原数的最大整数（1.1 → 1）

**格式规范：**
- 所有 T+n 均带 `%`
- 正数不带 `+` 号
- T+0 格式：`低%/高%(阴/阳/板)` — 附带完整K线形态
- T+1~6 格式：`高%(板)` 或 `高%` — 仅涨停时标注板，非涨停不标注形态
- 示例：
  - T+0：`0%/6%(阴)`，`-4%/1%(阳)`，`4%/20%(板)`
  - T+1~6：`22%(板)`，`4%`，`-5%`

**K线形态判定：**
- 板 = 当日涨停
- 阳 = 收盘 > 开盘（非涨停）
- 阴 = 收盘 ≤ 开盘（非涨停）

**代码实现：**
T+n 输出由 `backtest_common.generate_t_fields()` 统一生成，无需各规则手动拼接。

### 七、振幅与涨幅输出规则

**振幅：**
- 区间振幅（多日）：带上 `%`，如 3.45%
- 单个交易日振幅：不带 `%`，如 3.45

**涨幅：**
- 单日涨幅：带+号（正数+，负数-），不带 `%`，如 +2.50 或 -1.30

**以某日收盘价为基准的变化值：**
- 带正负号，不带 `%`，如 +3.50 或 -1.20

### 八、脚本结构规范（基于 backtest_common）

所有新规则脚本必须遵循以下结构：
```python
import sys
from pathlib import Path
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from backtest_common import (
    read_day_file, precompute_limits,         # 数据读取
    is_20cm, is_gem_300, normalize_code,      # 标的判定
    fmt_date, daily_amplitude,                # 格式化
    fmt_plus, fmt_no_sign, candle_form,       # 格式化
    generate_t_fields,                         # T+n 生成
    run_backtest, check_data_freshness,        # 执行框架
    DEFAULT_START, DEFAULT_END, GEM_20CM_DATE, # 常量
)

# 1. 规则配置
RULE_NAME = "<规则名>"
TARGET_TYPE = "20cm"  # 或 "10cm"
OUTPUT_COLUMNS = [...]  # 输出列顺序

# 2. 筛选函数（只需实现筛选条件和输出指标）
def screen_one(code, day_file, start_int, end_int):
    result = read_day_file(day_file)
    # ... 日期过滤 ...
    limits = precompute_limits(dates_raw, opens, highs, closes, code)
    # ... 样本前置过滤 ...
    # ... 筛选条件实现 ...
    # ... 输出指标计算 ...
    t_fields = generate_t_fields(opens, highs, lows, closes, limits, base_idx, n)
    row = {...}
    row.update(t_fields)
    return rows

# 3. 入口
if __name__ == "__main__":
    run_backtest(RULE_NAME, screen_one, TARGET_TYPE, OUTPUT_COLUMNS)
```

### 九、约束
- 不可硬编码涨跌幅比例，必须调用 `is_strong_limit_up()`（通过 `precompute_limits` 间接调用）
- 不可恢复代码前缀过滤（如 10cm=60/00，20cm=30/688）
- 创业板 20% 切换日期精确为 2020-08-24
- 科创板 20% 不早于 2019-07-22
- 永不删除或重写原始 TDX `.day` 源文件
- 每次执行前必须向用户确认数据源是否为最新
- 输出路径必须使用相对路径（SCRIPT_DIR / "results" / "<规则名>"）
- 所有规则脚本必须从 `backtest_common` 导入公共函数，不可重复定义

---

## backtest_common 公共 API 速查

| 函数 | 用途 | 示例 |
|------|------|------|
| `read_day_file(path)` | 读取 .day 二进制文件 | `dates, opens, highs, lows, closes, amounts, volumes = read_day_file(f)` |
| `collect_day_files(type)` | 收集指定板块 .day 文件 | `files = collect_day_files("20cm")` |
| `precompute_limits(...)` | 预计算涨停标记 | `limits = precompute_limits(dates, opens, highs, closes, code)` |
| `is_20cm(code)` / `is_10cm(code)` | 板块判定 | `is_20cm("688001")` → True |
| `is_gem_300(code)` | 创业板300判定 | `is_gem_300("300001")` → True |
| `fmt_date(d_int)` | 日期格式化 | `fmt_date(20240101)` → "2024-01-01" |
| `daily_amplitude(h, l, prev_c)` | 单日振幅 | `daily_amplitude(11, 9, 10)` → 20.0 |
| `fmt_t0(val)` / `fmt_tn(val)` | T+n 取整 | `fmt_t0(-1.1)` → -1, `fmt_tn(1.1)` → 1 |
| `fmt_plus(val)` / `fmt_no_sign(val)` | 正负号格式化 | `fmt_plus("2.5")` → "+2.5" |
| `candle_form(o, c, is_limit)` | K线形态 | `candle_form(10, 11, False)` → "阳" |
| `generate_t_fields(...)` | T+0~T+6 输出 | 返回 `{"T+0(低/高)": "-4%/1%(阳)", "T+1最高价": "2%", ...}` |
| `match_stock_names(df)` | 股票名称匹配 | `match_stock_names(result_df)` |
| `output_excel(df, cols, path)` | Excel 输出 | `output_excel(df, columns, output_file)` |
| `check_data_freshness()` | 数据源新鲜度 | `(latest_date, file_count) = check_data_freshness()` |
| `run_backtest(...)` | 通用执行框架 | `run_backtest("0803", screen_one, "20cm", columns)` |

---

## 已有规则

### A9 规则
- 适用标的：10cm 主板
- 前结构：D4 涨停，D1-D5 高低点递增
- BaseDay：D5+2~D5+11 范围内首个涨停日
- 输出：23 列（含 T+0~T+7）

### B9 规则
- 适用标的：10cm 主板
- 前结构：D3/D4 双涨停
- BaseDay：D5+2~D5+14 范围
- 输出：23 列

### 0723A8 规则
- 适用标的：10cm 主板
- 前结构：D4 涨停
- 输出：23 列

### 0803 规则
- 适用标的：20cm（688/300/301）
- 时间线：D-5 → D-4 → D-3 → D-2 → D-1 → D0 → T+0~T+6
- D-1：涨停 + 20日量价最高
- D0/D-2~D-5：非涨停 + 成交额非最大
- 基准价：D0 收盘价
- 输出：15 列
- 基于 backtest_common 实现

### 0805 规则
- 适用标的：20cm（688/300/301）
- 时间线：D-5 → D-4 → D-3 → D-2 → D-1 → D-0 → T+0~T+6
- 与0803筛选条件相同
- 样本前置过滤：创业板300剔除2020-08-24前数据
- 基准价：D-0 收盘价
- 新增字段：D-0最高价/D-0最低价（以D-1收盘价为基准）
- 输出：17 列
- 基于 backtest_common 实现

---

## 新规则开发指南

当用户提供新规则时，按以下步骤创建脚本：

1. **确认规则参数：**
   - 适用标的（10cm/20cm）
   - 时间线和各日条件
   - 基准日（T+n 的基准价是哪天的收盘价）
   - 输出字段和格式要求
   - T+n 输出范围（T+0~T+6 还是 T+0~T+7）

2. **创建脚本：**
   - 复制 `rule_template.py` 为 `<规则名>_backtest.py`
   - 修改 `RULE_NAME`、`TARGET_TYPE`、`OUTPUT_COLUMNS`
   - 在 `screen_one()` 中实现筛选条件
   - 在「输出指标计算」区域实现输出字段
   - T+n 输出直接调用 `generate_t_fields()`，无需手动拼接

3. **验证：**
   - 语法检查：`python3 -c "import <规则名>_backtest"`
   - 小范围回测：`python3 <规则名>_backtest.py --start 2024-01-01 --end 2024-03-31`
   - 格式验证：检查输出 Excel 的列名、T+n 格式、振幅/涨幅格式
   - 全量回测

4. **更新 Skill：**
   - 在「已有规则」章节添加新规则摘要
   - 推送到 Gitee
