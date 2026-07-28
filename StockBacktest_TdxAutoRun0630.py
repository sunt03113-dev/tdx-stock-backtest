# -*- coding: utf-8 -*-
"""
Skill名称：StockBacktest_TdxAutoRun
一体化三模块：通达信日线转换 + 时序回测筛选 + 股票名称匹配输出
内置规则：
1. 默认日期：start=1990-12-19，end=本地最新行情日
2. 必填四项校验：涨跌幅类型、时序规则、输出指标、输出路径+命名
3. 成功生成文件后播放系统提示音
4. 固定20日窗口、最高价判断股价峰值、修正后区间涨跌幅/振幅公式
5. 按交易日期、板块和标的类型动态判定涨跌停，使用 Decimal 高精度计算
"""
import os
import struct
import glob
import logging
import traceback
import warnings
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from tqdm import tqdm
import pandas as pd
import akshare as ak
import platform
import yaml

# ===================== 全局配置与日志初始化 =====================
warnings.filterwarnings("ignore")
LOG_FILE = "/tmp/stock_backtest_run.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("StockBacktest_TdxAutoRun")

# ──────────── 配置文件加载 ────────────
def _load_config():
    """加载 config.yaml，不存在则使用默认值"""
    config_path = Path(__file__).parent / "config.yaml"
    defaults = {"tdx_base": None, "data_cache": None, "stock_names_file": None}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_cfg = yaml.safe_load(f) or {}
            defaults.update(user_cfg)
            logger.info("已加载配置文件：%s", config_path)
        except Exception as e:
            logger.warning("配置文件读取失败，使用默认路径：%s", e)
    return defaults

CONFIG = _load_config()

# ──────────── 路径配置（配置文件 > 系统默认） ────────────
SYSTEM_TYPE = platform.system()

def _resolve_path(cfg_key, win_default, mac_default):
    """解析路径：优先 config.yaml，其次系统默认"""
    if CONFIG.get(cfg_key):
        return Path(CONFIG[cfg_key]).expanduser().resolve()
    if SYSTEM_TYPE == "Windows":
        return Path(win_default)
    return Path(mac_default)

TDX_BASE = _resolve_path(
    "tdx_base",
    win_default=r"D:\05_software\02_programs\TDx",
    mac_default=str(Path.home() / "TDx")
)
DATA_CACHE = _resolve_path(
    "data_cache",
    win_default=r"D:\数据源\day_xlsx_完整字段",
    mac_default=str(Path.home() / "stock_data" / "day_xlsx_完整字段")
)
STOCK_NAMES_FILE = None
if CONFIG.get("stock_names_file"):
    STOCK_NAMES_FILE = Path(CONFIG["stock_names_file"]).expanduser().resolve()

DAY_DIRS = [
    str(TDX_BASE / "vipdoc" / "sh" / "lday"),
    str(TDX_BASE / "vipdoc" / "sz" / "lday")
]
DATA_CACHE.mkdir(parents=True, exist_ok=True)

logger.info("TDX_BASE=%s  DATA_CACHE=%s  STOCK_NAMES_FILE=%s", TDX_BASE, DATA_CACHE, STOCK_NAMES_FILE)

DEFAULT_GLOBAL_START_DATE = "1990-12-19"
DEFAULT_GLOBAL_START_DT = pd.to_datetime(DEFAULT_GLOBAL_START_DATE)

# ===================== 全局固化通用函数（Skill底层固定） =====================
# 1. 20日窗口：当日+前19根K线
def get_20day_window(df, current_idx):
    start_idx = max(0, current_idx - 19)
    window = df.iloc[start_idx:current_idx + 1]
    return window, len(window) == 20

# 2. 判断当前数值是否为窗口最大值（股价最高用最高价列表）
def is_window_max(window_vals, curr_val):
    if len(window_vals) == 0:
        return False
    return curr_val >= max(window_vals)

# 3. 区间涨幅（修正版：以区间前一日的收盘价为基准）
def calc_range_increase(end_close, base_close):
    """
    区间涨幅 = (区间最后一天收盘价 - 区间前一日收盘价) / 区间前一日收盘价 × 100%
    base_close: 区间前一日的收盘价
    end_close: 区间最后一天的收盘价
    """
    if base_close == 0:
        return 0.0
    res = (end_close - base_close) / base_close * 100
    return round(res, 2)

# 4. 区间振幅（修正版：以区间前一日的收盘价为基准）
def calc_range_amplitude(high_list, low_list, base_close):
    """
    区间振幅 = (区间最高价 - 区间最低价) / 区间前一日收盘价 × 100%
    base_close: 区间前一日的收盘价
    high_list: 区间内每日最高价列表
    low_list: 区间内每日最低价列表
    """
    if base_close == 0:
        return 0.0
    h_max = max(high_list)
    l_min = min(low_list)
    res = (h_max - l_min) / base_close * 100
    return round(res, 2)


# 5. Dynamic limit-up rules and strong-seal limit-up detection
COL_DATE = "日期"
COL_CLOSE = "收盘价"
COL_HIGH = "最高价"
STAR_MARKET_20CM_START = pd.Timestamp("2019-07-22")
CHINEXT_20CM_START = pd.Timestamp("2020-08-24")
A_SHARE_PRICE_LIMIT_START = pd.Timestamp("1996-12-16")


def normalize_stock_code(code):
    return str(code).strip().lower().replace("sh", "").replace("sz", "").zfill(6)


def infer_stock_board(code):
    c = normalize_stock_code(code)
    if c.startswith("688"):
        return "star"
    if c.startswith(("300", "301")):
        return "chinext"
    if c.startswith(("43", "83", "87", "88", "92")):
        return "bse"
    if c.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "other"


def infer_target_type(stock_name=None, target_type=None):
    raw_type = str(target_type or "").strip().lower()
    raw_name = str(stock_name or "").upper()
    if raw_type in {"st", "*st", "risk_warning", "delisting"} or "ST" in raw_name:
        return "risk_warning"
    return "normal"


def get_dynamic_limit_pct(trade_date, code, stock_name=None, target_type=None):
    dt = pd.to_datetime(trade_date)
    if dt < A_SHARE_PRICE_LIMIT_START:
        return None
    target = infer_target_type(stock_name=stock_name, target_type=target_type)
    if target == "risk_warning":
        return Decimal("0.05")
    board = infer_stock_board(code)
    if board == "bse":
        return Decimal("0.30")
    if board == "star" and dt >= STAR_MARKET_20CM_START:
        return Decimal("0.20")
    if board == "chinext" and dt >= CHINEXT_20CM_START:
        return Decimal("0.20")
    return Decimal("0.10")


def calc_limit_price(y_close, limit_pct):
    if limit_pct is None or y_close <= 0:
        return None
    return (Decimal(str(y_close)) * (Decimal("1") + limit_pct)).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)


def is_strong_limit_up(today_close, today_high, y_close, trade_date, code, stock_name=None, target_type=None):
    if y_close <= 0 or today_close <= 0:
        return False
    limit_price = calc_limit_price(y_close, get_dynamic_limit_pct(trade_date, code, stock_name, target_type))
    if limit_price is None:
        return False
    close_dec = Decimal(str(today_close)).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)
    high_dec = Decimal(str(today_high)).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)
    return close_dec >= limit_price and high_dec >= limit_price


# 7. 播放完成提示音
def play_finish_beep():
    try:
        if SYSTEM_TYPE == "Windows":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif SYSTEM_TYPE == "Darwin":
            os.system("osascript -e 'beep'")
        else:
            os.system("beep")
    except Exception as e:
        logger.warning(f"提示音播放失败：{e}")

# 8. 股票名称缓存工具（模块3）
class StockNameTool:
    def __init__(self):
        self.cache_df = None
    def load_cache(self):
        if self.cache_df is None:
            # ── 优先使用本地股票名称文件（速度快、无需联网）──
            if STOCK_NAMES_FILE and STOCK_NAMES_FILE.exists():
                logger.info("从本地文件加载股票名称：%s", STOCK_NAMES_FILE)
                self.cache_df = pd.read_csv(STOCK_NAMES_FILE, dtype={"code": str})
                self.cache_df["code"] = self.cache_df["code"].str.zfill(6)
            else:
                # ── 回退：akshare 联网查询 ──
                logger.info("本地股票名称文件不存在，使用 akshare 联网查询")
                self.cache_df = ak.stock_info_a_code_name()
        return self.cache_df
    def single_code_to_name(self, code):
        try:
            c = str(code).strip().zfill(6)
            df = self.load_cache()
            match = df[df["code"] == c]["name"]
            if not match.empty:
                return match.values[0]
            return "未找到标的"
        except:
            return "名称查询失败"
    def batch_append_name(self, input_excel, out_excel):
        df = pd.read_excel(input_excel)
        code_col = df.iloc[:, 0]
        tool = StockNameTool()
        df["股票名称"] = code_col.apply(tool.single_code_to_name)
        Path(out_excel).parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(out_excel, index=False)
        return out_excel

# ===================== 模块1：通达信DAY转标准化Excel（增量更新） =====================
def run_module1_tdx_convert(start_dt, end_dt):
    start_dt = min(pd.to_datetime(start_dt), DEFAULT_GLOBAL_START_DT)
    logger.info("===== 阶段1：通达信日线增量转换开始 =====")
    day_files = []
    for d in DAY_DIRS:
        p = Path(d)
        if p.exists():
            day_files.extend(glob.glob(str(p / "*.day")))
    logger.info(f"共扫描到{len(day_files)}个.day文件")

    def parse_day(path):
        data_list = []
        try:
            with open(path, "rb") as f:
                buf = f.read()
            pos = 0
            while pos + 32 <= len(buf):
                date, o, h, l, c, amt, vol, _ = struct.unpack("<IIIII f I I", buf[pos:pos+32])
                pos += 32
                dt = pd.to_datetime(str(date), format="%Y%m%d")
                if not (start_dt <= dt <= end_dt):
                    continue
                data_list.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "open": o / 100,
                    "high": h / 100,
                    "low": l / 100,
                    "close": c / 100,
                    "vol": vol,
                    "amt": amt
                })
            return data_list
        except Exception as e:
            logger.error(f"解析{path}失败：{traceback.format_exc()}")
            return []

    for file in tqdm(sorted(day_files), desc="转换进度"):
        fn = os.path.splitext(os.path.basename(file))[0]
        if len(fn) < 8 or not (fn.startswith("sh") or fn.startswith("sz")):
            continue
        code = fn[2:8]
        save_path = DATA_CACHE / f"{code}.xlsx"
        # 增量判断：时间戳 + Excel起始日期完整性校验
        if save_path.exists():
            t_day = os.path.getmtime(file)
            t_xls = os.path.getmtime(save_path)
            if t_day <= t_xls:
                # 二次校验：确保 Excel 起始日期覆盖 .day 文件第一根K线
                try:
                    # 读取 .day 第一根K线日期
                    with open(file, "rb") as f_day:
                        first_date_raw = struct.unpack("<I", f_day.read(4))[0]
                    first_bar_dt = pd.to_datetime(str(first_date_raw), format="%Y%m%d")
                    # 读取 Excel 第一条数据的日期
                    xls_df = pd.read_excel(save_path, usecols=["日期"], nrows=1)
                    xls_first_dt = pd.to_datetime(xls_df["日期"].iloc[0])
                    # 如果 Excel 起始日期晚于 .day 第一日超过 5 天，强制重新生成
                    if (xls_first_dt - first_bar_dt).days > 5:
                        logger.info(f"{code}: Excel起始{xls_first_dt.date()}滞后源文件{first_bar_dt.date()}，强制重建")
                    else:
                        continue
                except Exception:
                    continue
        bars = parse_day(file)
        if not bars:
            continue
        df = pd.DataFrame(bars)
        df["股票代码"] = code
        # 计算振幅
        df["prev_close"] = df["close"].shift(1)
        df["振幅（通达信标准）"] = ((df["high"] - df["low"]) / df["prev_close"] * 100).round(2)
        df.loc[0, "振幅（通达信标准）"] = None
        df = df[["股票代码", "date", "open", "high", "low", "close", "amt", "vol", "振幅（通达信标准）"]]
        headers = ["股票代码", "日期", "开盘", "最高", "最低", "收盘", "成交额", "成交量", "振幅（通达信标准）"]
        df.columns = headers
        with pd.ExcelWriter(save_path, engine="openpyxl") as w:
            df.to_excel(w, sheet_name="日线数据", index=False)
            ws = w.sheets["日线数据"]
            for cell in ws["A"]:
                cell.number_format = "@"
    logger.info("===== 阶段1 日线转换完成 =====")
    return str(DATA_CACHE)

# ===================== 模块2：时序回测筛选计算 =====================
def run_module2_filter(data_dir, limit_type, s_date, e_date, rule_text, output_cols):
    logger.info("===== 阶段2：选股回测计算开始 =====")
    xls_files = [f for f in os.listdir(data_dir) if f.endswith(".xlsx")]
    result_store = []
    required_fields = {"股票代码", "日期", "开盘", "最高", "最低", "收盘", "成交额", "振幅（通达信标准）"}

    for file in tqdm(xls_files, desc="选股计算"):
        code = file.replace(".xlsx", "")
        fpath = Path(data_dir) / file
        try:
            df = pd.read_excel(fpath, dtype={"股票代码": str})
        except Exception as e:
            logger.warning(f"读取{file}失败：{e}")
            continue
        if not required_fields.issubset(set(df.columns)):
            continue
        df = df.rename(columns={"最高":"最高价","最低":"最低价","收盘":"收盘价","成交额":"成交额(元)","开盘":"开盘价"})
        df["日期"] = pd.to_datetime(df["日期"])
        df = df[(df["日期"] <= pd.to_datetime(e_date))].copy()
        df = df.sort_values("日期").reset_index(drop=True)
        if len(df) < 18:
            continue
        # 预计算涨停标记
        df["是否涨停"] = False
        for i in range(1, len(df)):
            y_c = df.iloc[i-1]["收盘价"]
            t_c = df.iloc[i]["收盘价"]
            t_h = df.iloc[i]["最高价"]
            df.loc[i, "是否涨停"] = is_strong_limit_up(t_c, t_h, y_c, df.iloc[i][COL_DATE], code)
        # 遍历所有D0候选
        for d0_idx in df.index:
            if d0_idx + 15 >= len(df):
                break
            if d0_idx < 19:
                continue
            # 时间轴索引
            d_m2 = d0_idx - 2
            d_m1 = d0_idx - 1
            d0 = d0_idx
            d1 = d0_idx + 1
            d2 = d0_idx + 2
            d14 = d0_idx + 14
            d15 = d0_idx + 15
            # 校验全部20日窗口完整
            w_m2, ok_m2 = get_20day_window(df, d_m2)
            w_m1, ok_m1 = get_20day_window(df, d_m1)
            w0, ok0 = get_20day_window(df, d0)
            w1, ok1 = get_20day_window(df, d1)
            w15, ok15 = get_20day_window(df, d15)
            if not all([ok_m2, ok_m1, ok0, ok1, ok15]):
                continue
            window_d2_14 = []
            flag_all_20ok = True
            for idx in range(d2, d15):
                w, ok = get_20day_window(df, idx)
                if not ok:
                    flag_all_20ok = False
                    break
                window_d2_14.append((idx, w))
            if not flag_all_20ok or len(window_d2_14) != 13:
                continue
            # 提取各窗口成交额、最高价
            amt_m2 = df.iloc[d_m2]["成交额(元)"]
            amt_m2_list = w_m2["成交额(元)"].tolist()
            amt_m1 = df.iloc[d_m1]["成交额(元)"]
            amt_m1_list = w_m1["成交额(元)"].tolist()
            amt0 = df.iloc[d0]["成交额(元)"]
            amt0_list = w0["成交额(元)"].tolist()
            high0 = df.iloc[d0]["最高价"]
            high0_list = w0["最高价"].tolist()
            amt1 = df.iloc[d1]["成交额(元)"]
            amt1_list = w1["成交额(元)"].tolist()
            amt15 = df.iloc[d15]["成交额(元)"]
            amt15_list = w15["成交额(元)"].tolist()
            high15 = df.iloc[d15]["最高价"]
            high15_list = w15["最高价"].tolist()
            # 规则校验（固定V15规则，用户仅传入规则文本用于记录，判断逻辑固化）
            cond1 = (not df.iloc[d_m2]["是否涨停"]) and (not is_window_max(amt_m2_list, amt_m2))
            cond2 = (not df.iloc[d_m1]["是否涨停"]) and (not is_window_max(amt_m1_list, amt_m1))
            cond3 = df.iloc[d0]["是否涨停"] and is_window_max(amt0_list, amt0) and is_window_max(high0_list, high0)
            cond4 = (not df.iloc[d1]["是否涨停"]) and is_window_max(amt1_list, amt1)
            cond6 = (not df.iloc[d15]["是否涨停"]) and is_window_max(amt15_list, amt15) and is_window_max(high15_list, high15)
            cond5_pass = True
            for idx, w in window_d2_14:
                if df.iloc[idx]["是否涨停"] or is_window_max(w["成交额(元)"].tolist(), df.iloc[idx]["成交额(元)"]):
                    cond5_pass = False
                    break
            if not all([cond1, cond2, cond3, cond4, cond5_pass, cond6]):
                continue

            # ========== 指标计算（使用修正后的区间涨幅/振幅公式） ==========
            d15_date = df.iloc[d15]["日期"].strftime("%Y-%m-%d")
            amp_d0 = round(df.iloc[d0]["振幅（通达信标准）"], 2)
            amp_d1 = round(df.iloc[d1]["振幅（通达信标准）"], 2)

            # D+2~D+14区间：基准价为D+1收盘价
            base_close_d1 = df.iloc[d1]["收盘价"]           # 区间前一日收盘价（D+1）
            end_close_d14 = df.iloc[d14]["收盘价"]          # 区间最后一天收盘价（D+14）
            range_inc = calc_range_increase(end_close_d14, base_close_d1)

            high_list_d214 = df.iloc[d2:d15]["最高价"].tolist()   # D+2 到 D+14 的最高价
            low_list_d214 = df.iloc[d2:d15]["最低价"].tolist()    # D+2 到 D+14 的最低价
            range_amp = calc_range_amplitude(high_list_d214, low_list_d214, base_close_d1)

            # D+15涨幅：以D+14收盘价为基准
            inc_d15 = calc_range_increase(df.iloc[d15]["收盘价"], df.iloc[d14]["收盘价"])
            amp_d15 = round(df.iloc[d15]["振幅（通达信标准）"], 2)

            row_data = {
                "股票代码": code,
                "D+15日期": d15_date,
                "D-0振幅": amp_d0,
                "D+1振幅": amp_d1,
                "D+2~+14区间涨幅(%)": range_inc,
                "D+2~+14区间振幅(%)": range_amp,
                "D+15涨幅(%)": inc_d15,
                "D+15振幅": amp_d15
            }
            result_store.append(row_data)

    # 输出临时筛选表
    temp_out = Path(DATA_CACHE).parent / "temp_filter_result.xlsx"
    if len(result_store) == 0:
        logger.info("未筛选到符合条件个股")
        return None
        
    res_df = pd.DataFrame(result_store)
    
    # 列名校验：只保留output_cols中实际存在的列，避免KeyError
    valid_cols = [col for col in output_cols if col in res_df.columns]
    missing_cols = set(output_cols) - set(valid_cols)
    if missing_cols:
        logger.warning(f"以下输出列不存在，已自动忽略：{missing_cols}")
    
    # 如果有效列为空，则使用所有列
    if not valid_cols:
        logger.warning("output_cols中的所有列均不存在，将输出所有可用列")
        valid_cols = res_df.columns.tolist()
    
    res_df = res_df[valid_cols]
    res_df.to_excel(temp_out, index=False)
    logger.info(f"阶段2筛选完成，共{len(result_store)}条结果，临时文件：{temp_out}")
    return str(temp_out)

# ===================== 模块3：股票名称匹配+按规则命名导出 =====================
def run_module3_name(input_temp, save_dir, file_rule):
    logger.info("===== 阶段3：股票名称匹配与文件归档 =====")
    if not input_temp or not Path(input_temp).exists():
        logger.error("无筛选结果，跳过命名模块")
        return None
    # 强制后缀.xlsx
    if not file_rule.endswith(".xlsx"):
        file_rule += ".xlsx"
    save_path = Path(save_dir) / file_rule
    Path(save_dir).mkdir(parents=True, exist_ok=True)
    name_tool = StockNameTool()
    final_file = name_tool.batch_append_name(input_temp, str(save_path))
    logger.info(f"最终文件生成完成：{final_file}")
    return final_file

# ===================== 入口参数校验 & 总调度器 =====================
def main_run(params: dict):
    """
    params字典入参：
    {
        "limit_type": str optional legacy field; dynamic rules are always used,
        "rule_type": str "v15"(默认) | "311b",
        "start_date": str 可选，默认1990-12-19,
        "end_date": str 可选，自动取本地最新,
        "time_rule": str 时序规则文本（v15专用）,
        "output_index": list 输出指标列表,
        "save_folder": str 输出存放路径,
        "file_name_rule": str 文件名模板,
        "base": str 可选 "T_0"|"T_2"（311b专用）
    }
    """
    rule_type = params.get("rule_type", "v15")
    if rule_type == "311b":
        return main_run_311b(params)
    if rule_type == "111a":
        return main_run_111a(params)
    if rule_type == "211b":
        return main_run_211b(params)
    if rule_type == "3z5d":
        return main_run_3z5d(params)
    # 默认：V15规则
    miss_list = []
    if "time_rule" not in params or str(params["time_rule"]).strip() == "":
        miss_list.append("2.完整时序筛选规则")
    if "output_index" not in params or len(params["output_index"]) == 0:
        miss_list.append("3.输出指标清单")
    if "save_folder" not in params or str(params["save_folder"]).strip() == "" or "file_name_rule" not in params:
        miss_list.append("4.输出配置：存放路径 + 文件命名规则")
    if len(miss_list) > 0:
        tip = "【参数缺失提醒】请补齐以下信息后重新提交：\n" + "\n".join(miss_list)
        print(tip)
        logger.warning(tip)
        return tip
    start_dt_str = params.get("start_date", DEFAULT_GLOBAL_START_DATE)
    end_dt_str = params.get("end_date", None)
    start_dt = pd.to_datetime(start_dt_str)
    all_xls = list(Path(DATA_CACHE).glob("*.xlsx"))
    max_local_date = pd.to_datetime("2026-06-26")
    if all_xls:
        sample_df = pd.read_excel(all_xls[-1])
        max_local_date = pd.to_datetime(sample_df["日期"]).max()
    if end_dt_str is None:
        end_dt = max_local_date
    else:
        end_dt = pd.to_datetime(end_dt_str)
    cache_dir = run_module1_tdx_convert(start_dt, end_dt)
    temp_filter = run_module2_filter(
        data_dir=cache_dir,
        limit_type=params.get("limit_type", "dynamic"),
        s_date=start_dt_str,
        e_date=end_dt.strftime("%Y-%m-%d"),
        rule_text=params["time_rule"],
        output_cols=params["output_index"]
    )
    if temp_filter is None:
        print("本次无符合条件选股结果，流程终止，不播放提示音")
        return "无筛选结果"
    final_path = run_module3_name(
        input_temp=temp_filter,
        save_dir=params["save_folder"],
        file_rule=params["file_name_rule"]
    )
    play_finish_beep()
    success_msg = "StockBacktest_TdxAutoRun 全流程执行完毕！最终文件：" + str(final_path)
    print(success_msg)
    logger.info(success_msg)
    return success_msg

# ===================== 模块2V：311B规则 T_0/T_1/T_2连续→N1→N2 =====================
def run_module2_filter_311b(data_dir, limit_type, s_date, e_date, output_cols, base="T_0"):
    """T_0/T_1/T_2三连板, N1∈[1,13], N2∈[5,10], 仅五日涨停, base='T_0'|'T_2'"""
    import numpy as np
    logger.info("===== 阶段2(311B)：选股计算开始 =====")
    xls_files = [f for f in os.listdir(data_dir) if f.endswith(".xlsx")]
    logger.info(f"311B目标板块{limit_type}共{len(xls_files)}只个股")
    result_store = []
    for file in tqdm(xls_files, desc="311B选股"):
        code = file.replace(".xlsx", "")
        fpath = Path(data_dir) / file
        try: df = pd.read_excel(fpath, dtype={"股票代码": str})
        except Exception as e: logger.warning(f"读取{file}失败：{e}"); continue
        if not {"股票代码","日期","开盘","最高","最低","收盘","成交额","振幅（通达信标准）"}.issubset(set(df.columns)):
            continue
        df = df.rename(columns={"最高":"最高价","最低":"最低价","收盘":"收盘价","成交额":"成交额(元)","开盘":"开盘价"})
        df["日期"] = pd.to_datetime(df["日期"])
        df = df[(df["日期"] <= pd.to_datetime(e_date))].copy()
        df = df.sort_values("日期").reset_index(drop=True)
        n = len(df)
        if n < 30: continue
        high_vals = df["最高价"].values
        low_vals = df["最低价"].values
        close_vals = df["收盘价"].values
        is_limit = np.zeros(n, dtype=bool)
        for i in range(1, n):
            is_limit[i] = is_strong_limit_up(df.iloc[i][COL_CLOSE], df.iloc[i][COL_HIGH], df.iloc[i-1][COL_CLOSE], df.iloc[i][COL_DATE], code)
        limit_cumsum = np.cumsum(is_limit)
        def _has_any(cs, a, b):
            if a > b: return False
            return cs[b] - (cs[a - 1] if a > 0 else 0) > 0
        limit_indices = np.where(is_limit)[0]
        for t0_idx in limit_indices:
            t1_idx = t0_idx + 1
            if t1_idx >= n or not is_limit[t1_idx]: continue
            t2_idx = t0_idx + 2
            if t2_idx >= n or not is_limit[t2_idx]: continue
            for N1 in range(1, 14):
                t3_idx = t2_idx + N1 + 1
                if t3_idx >= n: break
                if not is_limit[t3_idx]: continue
                if _has_any(limit_cumsum, t2_idx + 1, t3_idx - 1): continue
                for N2 in range(5, 11):
                    t4_idx = t3_idx + N2 + 1
                    if t4_idx >= n: break
                    if not is_limit[t4_idx]: continue
                    if _has_any(limit_cumsum, t3_idx + 1, t4_idx - 1): continue
                    t4_date = df.iloc[t4_idx]["日期"].strftime("%Y-%m-%d")
                    if base == "T_2":
                        pre_c = close_vals[t2_idx - 1]
                        range_inc = calc_range_increase(close_vals[t4_idx], pre_c)
                        range_amp = calc_range_amplitude(high_vals[t2_idx:t4_idx+1].tolist(), low_vals[t2_idx:t4_idx+1].tolist(), pre_c)
                        col_inc, col_amp = "T_2~T_4区间涨幅(%)", "T_2~T_4区间振幅(%)"
                    else:
                        pre_c = close_vals[t0_idx - 1] if t0_idx > 0 else close_vals[0]
                        range_inc = calc_range_increase(close_vals[t4_idx], pre_c)
                        range_amp = calc_range_amplitude(high_vals[t0_idx:t4_idx+1].tolist(), low_vals[t0_idx:t4_idx+1].tolist(), pre_c)
                        col_inc, col_amp = "T_0~T_4区间涨幅(%)", "T_0~T_4区间振幅(%)"
                    amp_t4 = round(df.iloc[t4_idx]["振幅（通达信标准）"], 2)
                    result_store.append({
                        "股票代码": code, "T_4日期": t4_date, "N1": N1, "N2": N2,
                        col_inc: range_inc, col_amp: range_amp,
                        "T_4振幅": amp_t4,
                    })
    temp_out = Path(DATA_CACHE).parent / "temp_filter_result_311b.xlsx"
    if len(result_store) == 0:
        logger.info("311B：未筛选到符合条件个股"); return None
    res_df = pd.DataFrame(result_store)
    missing = [c for c in output_cols if c not in res_df.columns]
    if missing: logger.error(f"311B输出列不存在：{missing}"); return None
    res_df = res_df[output_cols]
    res_df.to_excel(temp_out, index=False)
    logger.info(f"311B筛选完成，共{len(result_store)}条结果")
    return str(temp_out)

def main_run_311b(params: dict):
    miss_list = []
    if "output_index" not in params or len(params["output_index"]) == 0:
        miss_list.append("输出指标清单")
    if "save_folder" not in params or str(params["save_folder"]).strip() == "" or "file_name_rule" not in params:
        miss_list.append("输出配置")
    if miss_list: return "【参数缺失】" + ", ".join(miss_list)
    start_dt_str = params.get("start_date", DEFAULT_GLOBAL_START_DATE)
    end_dt_str = params.get("end_date", None)
    start_dt = pd.to_datetime(start_dt_str)
    all_xls = list(Path(DATA_CACHE).glob("*.xlsx"))
    max_local_date = pd.Timestamp.today()
    if all_xls:
        try:
            sample_df = pd.read_excel(all_xls[-1])
            if "日期" in sample_df.columns:
                max_local_date = pd.to_datetime(sample_df["日期"]).max()
        except Exception as e: logger.warning(f"读取日期失败：{e}")
    end_dt = max_local_date if end_dt_str is None else pd.to_datetime(end_dt_str)
    cache_dir = run_module1_tdx_convert(start_dt, end_dt)
    temp_filter = run_module2_filter_311b(cache_dir, params.get("limit_type", "dynamic"), start_dt_str, end_dt.strftime("%Y-%m-%d"), params["output_index"], params.get("base","T_0"))
    if temp_filter is None: print("311B：无符合条件选股结果"); return "无筛选结果"
    final_path = run_module3_name(temp_filter, params["save_folder"], params["file_name_rule"])
    try: Path(temp_filter).unlink(missing_ok=True)
    except: pass
    play_finish_beep()
    msg = "311B 全流程执行完毕！最终文件：" + str(final_path)
    print(msg); logger.info(msg); return msg

# ===================== 模块2U：111A规则 T_0(量价双max)→N1→N2 =====================
def run_module2_filter_111a(data_dir, limit_type, s_date, e_date, output_cols):
    import numpy as np
    logger.info("===== 阶段2(111A)：选股计算开始 =====")
    fl = [f for f in os.listdir(data_dir) if f.endswith(".xlsx")]
    logger.info(f"111A共{len(fl)}只个股")
    rs = []
    for file in tqdm(fl, desc="111A选股"):
        code = file.replace(".xlsx", "")
        try: df = pd.read_excel(Path(data_dir)/file, dtype={"股票代码": str})
        except: continue
        if not {"股票代码","日期","开盘","最高","最低","收盘","成交额","振幅（通达信标准）"}.issubset(set(df.columns)): continue
        df = df.rename(columns={"最高":"最高价","最低":"最低价","收盘":"收盘价","成交额":"成交额(元)","开盘":"开盘价"})
        df["日期"] = pd.to_datetime(df["日期"])
        df = df[(df["日期"] <= pd.to_datetime(e_date))].copy()
        df = df.sort_values("日期").reset_index(drop=True)
        n = len(df); rs_local = []
        if n < 22: continue
        av = df["成交额(元)"].values; hv = df["最高价"].values; lv = df["最低价"].values; cv = df["收盘价"].values
        il = np.zeros(n, bool)
        for i in range(1, n): il[i] = is_strong_limit_up(df.iloc[i][COL_CLOSE], df.iloc[i][COL_HIGH], df.iloc[i-1][COL_CLOSE], df.iloc[i][COL_DATE], code)
        lcs = np.cumsum(il)
        def ha(cs, a, b):
            if a > b: return False
            return cs[b] - (cs[a-1] if a > 0 else 0) > 0
        a20 = pd.Series(av).rolling(20, min_periods=20).max().values
        h20 = pd.Series(hv).rolling(20, min_periods=20).max().values
        for t0 in np.where(il)[0]:
            if t0 < 19 or il[t0-1]: continue
            if np.isnan(a20[t0]) or np.isnan(h20[t0]): continue
            if av[t0] < a20[t0] or hv[t0] < h20[t0]: continue
            for N1 in range(1, 5):
                t1 = t0 + N1 + 1
                if t1 >= n: break
                if not il[t1] or ha(lcs, t0+1, t1-1): continue
                for N2 in range(1, 5):
                    t2 = t1 + N2 + 1
                    if t2 >= n: break
                    if not il[t2] or ha(lcs, t1+1, t2-1): continue
                    pc = cv[t0-1]; t2c = cv[t2]
                    rs.append({"股票代码":code,"T_2日期":df.iloc[t2]["日期"].strftime("%Y-%m-%d"),"N1":N1,"N2":N2,
                        "T_0振幅":round(df.iloc[t0]["振幅（通达信标准）"],2),
                        "T_1振幅":round(df.iloc[t1]["振幅（通达信标准）"],2),
                        "T_2振幅":round(df.iloc[t2]["振幅（通达信标准）"],2),
                        "T_0~T_2区间涨幅(%)":calc_range_increase(t2c,pc),
                        "T_0~T_2区间振幅(%)":calc_range_amplitude(hv[t0:t2+1].tolist(),lv[t0:t2+1].tolist(),pc)})
    to = Path(DATA_CACHE).parent / "temp_filter_result_111a.xlsx"
    if not rs: logger.info("111A：无结果"); return None
    rd = pd.DataFrame(rs)
    mc = [c for c in output_cols if c not in rd.columns]
    if mc: logger.error(f"111A列不存在：{mc}"); return None
    rd = rd[output_cols]; rd.to_excel(to, index=False)
    logger.info(f"111A完成，共{len(rs)}条"); return str(to)

def main_run_111a(params: dict):
    ml = []
    if not params.get("output_index"): ml.append("输出指标")
    if not params.get("save_folder") or not params.get("file_name_rule"): ml.append("输出配置")
    if ml: return "【参数缺失】"+",".join(ml)
    sd = params.get("start_date", DEFAULT_GLOBAL_START_DATE); ed = params.get("end_date",None)
    sdt = pd.to_datetime(sd)
    ax = list(Path(DATA_CACHE).glob("*.xlsx"))
    mld = pd.Timestamp.today()
    if ax:
        try:
            sf = pd.read_excel(ax[-1])
            if "日期" in sf.columns: mld = pd.to_datetime(sf["日期"]).max()
        except: pass
    edt = mld if ed is None else pd.to_datetime(ed)
    cd = run_module1_tdx_convert(sdt, edt)
    tf = run_module2_filter_111a(cd, params.get("limit_type", "dynamic"), sd, edt.strftime("%Y-%m-%d"), params["output_index"])
    if tf is None: print("111A：无结果"); return "无筛选结果"
    fp = run_module3_name(tf, params["save_folder"], params["file_name_rule"])
    try: Path(tf).unlink(missing_ok=True)
    except: pass
    play_finish_beep()
    m = "111A完成：" + str(fp); print(m); logger.info(m); return m

# ===================== 模块2W：211B规则 T_0/T_1→N1→N2（N1/N2范围可配置） =====================
def run_module2_filter_211b(data_dir, limit_type, s_date, e_date, output_cols, gap01=0, n1_range=(4,12), n2_range=(6,13)):
    """T_0首板(前5日无涨停), T_0→T_1间隔gap01日, N1/N2范围可配置"""
    import numpy as np
    logger.info("===== 阶段2(211B)：选股计算开始 =====")
    xls_files = [f for f in os.listdir(data_dir) if f.endswith(".xlsx")]
    logger.info(f"211B目标板块{limit_type}共{len(xls_files)}只个股")
    result_store = []
    for file in tqdm(xls_files, desc="211B选股"):
        code = file.replace(".xlsx", "")
        fpath = Path(data_dir) / file
        try: df = pd.read_excel(fpath, dtype={"股票代码": str})
        except Exception as e: logger.warning(f"读取{file}失败：{e}"); continue
        if not {"股票代码","日期","开盘","最高","最低","收盘","成交额","振幅（通达信标准）"}.issubset(set(df.columns)):
            continue
        df = df.rename(columns={"最高":"最高价","最低":"最低价","收盘":"收盘价","成交额":"成交额(元)","开盘":"开盘价"})
        df["日期"] = pd.to_datetime(df["日期"])
        df = df[(df["日期"] <= pd.to_datetime(e_date))].copy()
        df = df.sort_values("日期").reset_index(drop=True)
        n = len(df)
        if n < 30: continue
        high_vals = df["最高价"].values; low_vals = df["最低价"].values; close_vals = df["收盘价"].values
        is_limit = np.zeros(n, dtype=bool)
        for i in range(1, n):
            is_limit[i] = is_strong_limit_up(df.iloc[i][COL_CLOSE], df.iloc[i][COL_HIGH], df.iloc[i-1][COL_CLOSE], df.iloc[i][COL_DATE], code)
        limit_cumsum = np.cumsum(is_limit)
        def _has_any(cs, a, b):
            if a > b: return False
            return cs[b] - (cs[a - 1] if a > 0 else 0) > 0
        limit_indices = np.where(is_limit)[0]
        for t0_idx in limit_indices:
            if t0_idx < 5: continue
            if _has_any(limit_cumsum, t0_idx - 5, t0_idx - 1): continue
            t1_idx = t0_idx + 1 + gap01
            if t1_idx >= n or not is_limit[t1_idx]: continue
            if gap01 > 0 and is_limit[t0_idx + 1]: continue
            for N1 in range(n1_range[0], n1_range[1] + 1):
                t2_idx = t1_idx + N1 + 1
                if t2_idx >= n: break
                if not is_limit[t2_idx]: continue
                if _has_any(limit_cumsum, t1_idx + 1, t2_idx - 1): continue
                for N2 in range(n2_range[0], n2_range[1] + 1):
                    t3_idx = t2_idx + N2 + 1
                    if t3_idx >= n: break
                    if not is_limit[t3_idx]: continue
                    if _has_any(limit_cumsum, t2_idx + 1, t3_idx - 1): continue
                    t3_date = df.iloc[t3_idx]["日期"].strftime("%Y-%m-%d")
                    pre_c = close_vals[t0_idx - 1]
                    t3_c = close_vals[t3_idx]
                    result_store.append({
                        "股票代码": code, "T_3日期": t3_date, "N1": N1, "N2": N2,
                        "T_0~T_3区间涨幅(%)": calc_range_increase(t3_c, pre_c),
                        "T_0~T_3区间振幅(%)": calc_range_amplitude(high_vals[t0_idx:t3_idx+1].tolist(), low_vals[t0_idx:t3_idx+1].tolist(), pre_c),
                        "T_3振幅": round(df.iloc[t3_idx]["振幅（通达信标准）"], 2),
                    })
    temp_out = Path(DATA_CACHE).parent / "temp_filter_result_211b.xlsx"
    if len(result_store) == 0:
        logger.info("211B：无结果"); return None
    res_df = pd.DataFrame(result_store)
    missing = [c for c in output_cols if c not in res_df.columns]
    if missing: logger.error(f"211B输出列不存在：{missing}"); return None
    res_df = res_df[output_cols]; res_df.to_excel(temp_out, index=False)
    logger.info(f"211B完成，共{len(result_store)}条")
    return str(temp_out)

def main_run_211b(params: dict):
    ml = []
    if not params.get("output_index"): ml.append("输出指标")
    if not params.get("save_folder") or not params.get("file_name_rule"): ml.append("输出配置")
    if ml: return "【参数缺失】"+",".join(ml)
    sd = params.get("start_date", DEFAULT_GLOBAL_START_DATE); ed = params.get("end_date",None)
    sdt = pd.to_datetime(sd)
    ax = list(Path(DATA_CACHE).glob("*.xlsx"))
    mld = pd.Timestamp.today()
    if ax:
        try:
            sf = pd.read_excel(ax[-1])
            if "日期" in sf.columns: mld = pd.to_datetime(sf["日期"]).max()
        except: pass
    edt = mld if ed is None else pd.to_datetime(ed)
    cd = run_module1_tdx_convert(sdt, edt)
    gap01 = params.get("gap01", 0)
    n1r = params.get("n1_range", (4,12))
    n2r = params.get("n2_range", (6,13))
    tf = run_module2_filter_211b(cd, params.get("limit_type", "dynamic"), sd, edt.strftime("%Y-%m-%d"), params["output_index"], gap01, n1r, n2r)
    if tf is None: print("211B：无结果"); return "无筛选结果"
    fp = run_module3_name(tf, params["save_folder"], params["file_name_rule"])
    try: Path(tf).unlink(missing_ok=True)
    except: pass
    play_finish_beep()
    m = "211B完成：" + str(fp); print(m); logger.info(m); return m

# ===== 模块3Z：三连板后5日整理 D-2/D-1/D-0涨停, D+1~D+5非涨停 =====
def run_module2_filter_3z5d(data_dir, limit_type, s_date, e_date, output_cols):
    import numpy as np
    logger.info("===== 阶段2(3Z5D)：选股计算开始 =====")
    fl = [f for f in os.listdir(data_dir) if f.endswith(".xlsx")]
    logger.info(f"3Z5D共{len(fl)}只个股")
    rs = []
    for file in tqdm(fl, desc="3Z5D选股"):
        code = file.replace(".xlsx", "")
        try: df = pd.read_excel(Path(data_dir)/file, dtype={"股票代码": str})
        except: continue
        if not {"股票代码","日期","开盘","最高","最低","收盘","成交额","振幅（通达信标准）"}.issubset(set(df.columns)): continue
        df = df.rename(columns={"最高":"最高价","最低":"最低价","收盘":"收盘价","成交额":"成交额(元)","开盘":"开盘价"})
        df["日期"] = pd.to_datetime(df["日期"])
        df = df[(df["日期"] <= pd.to_datetime(e_date))].copy()
        df = df.sort_values("日期").reset_index(drop=True)
        n = len(df)
        if n < 10: continue
        hv = df["最高价"].values; lv = df["最低价"].values; cv = df["收盘价"].values
        il = np.zeros(n, bool)
        for i in range(1, n): il[i] = is_strong_limit_up(df.iloc[i][COL_CLOSE], df.iloc[i][COL_HIGH], df.iloc[i-1][COL_CLOSE], df.iloc[i][COL_DATE], code)
        for d0 in range(2, n - 5):
            if not (il[d0] and il[d0-1] and il[d0-2]): continue
            d1,d2,d3,d4,d5 = d0+1,d0+2,d0+3,d0+4,d0+5
            if any(il[i] for i in [d1,d2,d3,d4,d5]): continue
            d0_date = df.iloc[d0]["日期"].strftime("%Y-%m-%d")
            pc_d1 = cv[d0]
            # D+1~D+4
            inc14 = calc_range_increase(cv[d4], pc_d1)
            amp14 = calc_range_amplitude(hv[d1:d4+1].tolist(), lv[d1:d4+1].tolist(), pc_d1)
            # D+1~D+5
            inc15 = calc_range_increase(cv[d5], pc_d1)
            amp15 = calc_range_amplitude(hv[d1:d5+1].tolist(), lv[d1:d5+1].tolist(), pc_d1)
            rs.append({
                "股票代码": code,
                "D-0日期": d0_date,
                "D-2振幅": round(df.iloc[d0-2]["振幅（通达信标准）"], 2),
                "D-1振幅": round(df.iloc[d0-1]["振幅（通达信标准）"], 2),
                "D-0振幅": round(df.iloc[d0]["振幅（通达信标准）"], 2),
                "D+1~D+4区间涨幅(%)": inc14,
                "D+1~D+4区间振幅(%)": amp14,
                "D+1~D+5区间涨幅(%)": inc15,
                "D+1~D+5区间振幅(%)": amp15,
            })
    to = Path(DATA_CACHE).parent / "temp_filter_result_3z5d.xlsx"
    if not rs: logger.info("3Z5D：无结果"); return None
    rd = pd.DataFrame(rs)
    mc = [c for c in output_cols if c not in rd.columns]
    if mc: logger.error(f"3Z5D列不存在：{mc}"); return None
    rd = rd[output_cols]; rd.to_excel(to, index=False)
    logger.info(f"3Z5D完成，共{len(rs)}条"); return str(to)

def main_run_3z5d(params: dict):
    ml = []
    if not params.get("output_index"): ml.append("输出指标")
    if not params.get("save_folder") or not params.get("file_name_rule"): ml.append("输出配置")
    if ml: return "【参数缺失】"+",".join(ml)
    sd = params.get("start_date", DEFAULT_GLOBAL_START_DATE); ed = params.get("end_date",None)
    sdt = pd.to_datetime(sd)
    ax = list(Path(DATA_CACHE).glob("*.xlsx"))
    mld = pd.Timestamp.today()
    if ax:
        try:
            sf = pd.read_excel(ax[-1])
            if "日期" in sf.columns: mld = pd.to_datetime(sf["日期"]).max()
        except: pass
    edt = mld if ed is None else pd.to_datetime(ed)
    cd = run_module1_tdx_convert(sdt, edt)
    tf = run_module2_filter_3z5d(cd, params.get("limit_type", "dynamic"), sd, edt.strftime("%Y-%m-%d"), params["output_index"])
    if tf is None: print("3Z5D：无结果"); return "无筛选结果"
    fp = run_module3_name(tf, params["save_folder"], params["file_name_rule"])
    try: Path(tf).unlink(missing_ok=True)
    except: pass
    play_finish_beep()
    m = "3Z5D完成：" + str(fp); print(m); logger.info(m); return m

# ===================== 调用示例（用户@Skill输入后组装为字典传入main_run） =====================
if __name__ == "__main__":
    # 用户输入示例
    test_params = {
        "limit_type": "dynamic",  # Legacy compatibility only; does not choose the price-limit rule.
        "start_date": DEFAULT_GLOBAL_START_DATE,
        "end_date": "2026-06-26",
        "time_rule": """
D-2：非涨停，20日成交额非窗口最大
D-1：非涨停，20日成交额非窗口最大
D0：涨停，20日成交额窗口最大，20日最高价窗口最大
D+1：非涨停，20日成交额窗口最大
D+2~D+14：全部非涨停，每日成交额非窗口最大
D+15：非涨停，20日成交额窗口最大，20日最高价窗口最大
        """,
        "output_index": ["股票代码","D+15日期","D-0振幅","D+1振幅","D+2~+14区间涨幅(%)","D+2~+14区间振幅(%)","D+15涨幅(%)","D+15振幅"],
        "save_folder": r"D:\筛选结果\回测输出",
        "file_name_rule": "回测_动态涨停_20260626_筛选结果"
    }
    res = main_run(test_params)
    print(res)
