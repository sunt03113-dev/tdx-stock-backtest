# tdx-stock-backtest

A股涨停形态全量日线回测引擎。基于通达信日线数据，支持多规则时序筛选、动态涨跌停判定、Decimal 高精度涨停识别。

## 核心能力

- **全量回测**：每只个股自上市首日起全量日线，覆盖 1990-12-19 至今
- **动态涨跌停**：按交易日期 + 股票板块 + 标的类型自动匹配对应年份监管规则（5%/10%/20%/30%）
- **20cm 精准分界**：科创板 2019-07-22 起 ±20%，创业板 2020-08-24 起统一切换
- **Decimal 高精度**：涨停价计算使用 Python Decimal，四舍五入保留 2 位小数，消除浮点精度 bug
- **强封涨停定义**：最高价 ≥ 理论涨停价，收盘价 ≥ 理论涨停价

## 环境依赖

```bash
pip install pandas akshare openpyxl tqdm
```

## 数据源

默认读取通达信 `.day` 日线文件（Windows 路径 `D:\05_software\02_programs\TDx\vipdoc\...`）。
修改 `StockBacktest_TdxAutoRun0630.py` 中的 `TDX_BASE` 和 `DATA_CACHE` 变量即可适配不同环境。

## 文件结构

```
tdx-stock-backtest/
├── StockBacktest_TdxAutoRun0630.py   # 核心引擎：TDX转换 + 动态涨停判定 + 多规则调度
├── tdx-stock-backtest.md             # Claude Code Skill 定义文件
├── five_day_z_backtest.py            # 遗留规则：D3涨停变体
├── 0715A_backtest.py                 # 遗留规则：D4涨停 + Z日最强
├── 0716A2_backtest.py                # 当前规则：T4单涨停 + Tend非最强
├── 0716B2_backtest.py                # 当前规则：T3+T4双涨停 + Tend非最强
├── 0718A3_backtest.py                # 当前规则：T4单涨停 + T6天花板间隔压制
├── 0719B4_backtest.py                # 当前规则：T3+T4双涨停 + T6天花板 + Tend≤T6高
├── 0720A5_backtest.py                # 当前规则：T4单涨停 + T5天花板后置过滤
├── 0720B5_backtest.py                # 当前规则：T3+T4双涨停 + T5天花板 + Tend≤T4高
└── README.md
```

## 规则体系

所有规则共用 T1-T5 前结构：
- 选取连续 5 根交易日 K 线 T1~T5
- T2~T5 每日最高价 ≥ 前一日最高价，最低价 ≥ 前一日最低价
- T6 不触发涨停

### 0716A2：T4 单涨停 + Tend 非最强
- **前结构**：仅 T4 封 10cm 涨停
- **Tend**：T5+2 ~ T5+11，首个涨停且 **非** 20 日最高价和最大成交额
- **输出**：12 列（含 T1-T3 振幅、T4/T5 单日振幅等）

### 0716B2：T3+T4 双涨停 + Tend 非最强
- **前结构**：T3 和 T4 同时封 10cm 涨停
- **Tend**：T5+2 ~ T5+14，首个涨停且 **非** 20 日最高价和最大成交额
- **输出**：10 列（含 T1-T5 区间涨幅/振幅、T5 单日振幅等）

### 0718A3：T4 单涨停 + T6 天花板间隔压制
- **前结构**：仅 T4 涨停
- **间隔压制**：T7~Tend-1 每日最高价 ≤ T6 最高价，成交额 ≤ T6 成交额
- **Tend**：T5+2 ~ T5+11，首个涨停且非 20 日最强
- **输出**：12 列

### 0719B4：T3+T4 双涨停 + T6 天花板 + Tend ≤ T6 高
- **前结构**：T3+T4 双涨停
- **间隔压制**：T7~Tend-1 被 T6 天花板压制
- **Tend**：T5+2 ~ T5+14，涨停 + 非最强 + 最高价 ≤ T6 最高价
- **输出**：10 列

### 0720A5：T4 单涨停 + T5 天花板后置过滤
- **前结构**：仅 T4 涨停
- **Tend**：T5+2 ~ T5+11，首个涨停且非最强（无压制搜索）
- **后置过滤**：Tend 锁定后，验证 T6~Tend-1 ≤ T5 天花板
- **输出**：12 列

### 0720B5：T3+T4 双涨停 + T5 天花板 + Tend ≤ T4 高
- **前结构**：T3+T4 双涨停
- **Tend**：T5+2 ~ T5+14，涨停 + 非最强 + 最高价 ≤ T4 最高价
- **后置过滤**：Tend 锁定后，验证 T6~Tend-1 ≤ T5 天花板
- **输出**：10 列

## 使用方法

```bash
# 默认：使用现有缓存，全量历史
python 0720A5_backtest.py

# 先更新 TDX 数据再筛选
python 0720A5_backtest.py --update

# 指定日期范围
python 0720A5_backtest.py --start 2020-01-01 --end 2025-12-31
```

## 核心 API

```python
from StockBacktest_TdxAutoRun0630 import is_strong_limit_up

# 动态涨停判定（自动识别板块/日期/标的类型）
is_limit = is_strong_limit_up(
    today_close=10.0, today_high=10.0,
    y_close=9.09, trade_date="2024-01-15", code="000001"
)
```

## 数据规约

- 日期格式：YYYY-MM-DD
- 股价精度：Decimal 保留 2 位小数
- 全局起始日期：1990-12-19（覆盖老八股开市全量行情）
- 区间涨幅基准：区间前一日收盘价
- 区间振幅基准：区间前一日收盘价

## License

MIT
