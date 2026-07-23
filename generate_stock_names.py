# -*- coding: utf-8 -*-
"""
Windows 端：生成 A 股股票名称映射文件（stock_names.csv）
运行一次后上传到 COS，Mac 端即可离线使用，无需 akshare 联网
"""
import akshare as ak
import pandas as pd
from pathlib import Path
import sys
import os

# 输出路径（默认放在当前脚本目录，也可通过参数指定）
OUTPUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
OUTPUT_FILE = OUTPUT_DIR / "stock_names.csv"

print("=" * 50)
print("正在通过 akshare 获取 A 股全量股票名称...")
print("这可能需要 30-60 秒，请耐心等待...")
print("=" * 50)

try:
    df = ak.stock_info_a_code_name()
    # 统一 code 列格式：6 位字符串
    df["code"] = df["code"].astype(str).str.zfill(6)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"\n✅ 成功生成：{OUTPUT_FILE}")
    print(f"   共 {len(df)} 条记录，文件大小 {os.path.getsize(OUTPUT_FILE) / 1024:.1f} KB")
except Exception as e:
    print(f"\n❌ 生成失败：{e}")
    sys.exit(1)

# ──── 提示下一步 ────
print(f"""
========== 下一步操作 ==========
1. 将 {OUTPUT_FILE} 上传到腾讯云 COS 存储桶
   （例如上传到 tdx-vipdoc-kk-1408814916 的根目录或 vipdoc/ 目录）

2. Mac 端下载时，在 download.sh 中添加一行：
   coscli cp cos://tdx-vipdoc-kk-1408814916/stock_names.csv ~/tdx_cache/stock_names.csv

3. 建议：每季度重新运行一次本脚本，更新新股名称
""")