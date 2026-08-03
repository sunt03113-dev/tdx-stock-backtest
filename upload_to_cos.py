# -*- coding: utf-8 -*-
"""
Windows 端：将通达信 .day 日线数据 + stock_names.csv 上传到腾讯云 COS
用法: python upload_to_cos.py
前提: 已设置环境变量 COS_SECRET_ID 和 COS_SECRET_KEY（主账号）
"""
import os
import sys
import yaml
from pathlib import Path

try:
    from qcloud_cos import CosConfig, CosS3Client
except ImportError:
    print("请先安装 COS SDK: pip install cos-python-sdk-v5")
    sys.exit(1)

# ============================================================
# 配置
# ============================================================
BUCKET = 'tdx-vipdoc-kk-1408814916'
REGION = 'ap-guangzhou'

# 读取 config_windows.yaml 获取 TDX 路径
CONFIG_FILE = Path(__file__).parent / 'config_windows.yaml'
if CONFIG_FILE.exists():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    TDX_BASE = cfg.get('tdx_base', r'D:\05_software\02_programs\TDx')
    NAMES_FILE = cfg.get('stock_names_file', str(Path(__file__).parent / 'stock_names.csv'))
else:
    TDX_BASE = r'D:\05_software\02_programs\TDx'
    NAMES_FILE = str(Path(__file__).parent / 'stock_names.csv')

# COS 凭证（主账号，读写权限）
SECRET_ID = os.environ.get('COS_SECRET_ID', '')
SECRET_KEY = os.environ.get('COS_SECRET_KEY', '')

if not SECRET_ID or not SECRET_KEY:
    print("=" * 50)
    print("错误: 未检测到 COS 凭证环境变量")
    print("请先设置环境变量:")
    print("  set COS_SECRET_ID=你的主账号SecretId")
    print("  set COS_SECRET_KEY=你的主账号SecretKey")
    print("=" * 50)
    sys.exit(1)

config = CosConfig(Region=REGION, SecretId=SECRET_ID, SecretKey=SECRET_KEY)
client = CosS3Client(config)


def upload_day_files():
    """增量上传 .day 文件到 COS（跳过未变更文件）"""
    markets = {'sh': 'vipdoc/sh/lday', 'sz': 'vipdoc/sz/lday'}
    total_uploaded = 0
    total_skipped = 0

    for market, rel_path in markets.items():
        local_dir = os.path.join(TDX_BASE, rel_path)
        if not os.path.exists(local_dir):
            print(f"  [跳过] {market} 目录不存在: {local_dir}")
            continue

        day_files = [f for f in os.listdir(local_dir) if f.endswith('.day')]
        print(f"\n  {market} 市场: 共 {len(day_files)} 个 .day 文件")

        # 获取 COS 上已有文件列表（用于增量对比）
        cos_files = {}
        prefix = f'vipdoc/{market}/lday/'
        marker = ''
        while True:
            resp = client.list_objects(Bucket=BUCKET, Prefix=prefix, Marker=marker, MaxKeys=1000)
            for obj in resp.get('Contents', []):
                cos_files[obj['Key']] = int(obj.get('Size', 0))
            if resp.get('IsTruncated') == 'true':
                marker = resp.get('NextMarker', '')
            else:
                break

        for i, filename in enumerate(day_files):
            local_path = os.path.join(local_dir, filename)
            cos_key = f'vipdoc/{market}/lday/{filename}'
            local_size = os.path.getsize(local_path)

            # 增量判断：文件大小相同则跳过
            if cos_key in cos_files and cos_files[cos_key] == local_size:
                total_skipped += 1
                continue

            try:
                client.upload_file(Bucket=BUCKET, Key=cos_key, LocalFilePath=local_path)
                total_uploaded += 1
                if total_uploaded % 200 == 0:
                    print(f"    已上传 {total_uploaded} 个文件...")
            except Exception as e:
                print(f"    [错误] {filename}: {e}")

        print(f"  {market} 完成: 上传 {total_uploaded} 个, 跳过 {total_skipped} 个")

    return total_uploaded, total_skipped


def upload_stock_names():
    """上传 stock_names.csv"""
    if not os.path.exists(NAMES_FILE):
        print(f"\n  [警告] stock_names.csv 不存在: {NAMES_FILE}")
        print("  请先运行: python generate_stock_names.py")
        return False

    try:
        client.upload_file(Bucket=BUCKET, Key='stock_names.csv', LocalFilePath=NAMES_FILE)
        size_kb = os.path.getsize(NAMES_FILE) / 1024
        print(f"\n  stock_names.csv 上传完成 ({size_kb:.1f} KB)")
        return True
    except Exception as e:
        print(f"\n  [错误] stock_names.csv 上传失败: {e}")
        return False


def main():
    print("=" * 50)
    print("  TDX 日线数据上传到 COS")
    print(f"  存储桶: {BUCKET}")
    print(f"  区域:   {REGION}")
    print(f"  数据源: {TDX_BASE}")
    print("=" * 50)

    # 检查 TDX 目录
    if not os.path.exists(TDX_BASE):
        print(f"\n错误: 通达信目录不存在: {TDX_BASE}")
        print("请修改 config_windows.yaml 中的 tdx_base 路径")
        sys.exit(1)

    # 上传 .day 文件
    print("\n[1/2] 上传 .day 日线文件...")
    uploaded, skipped = upload_day_files()
    print(f"\n  总计: 新上传 {uploaded} 个, 跳过(未变更) {skipped} 个")

    # 上传 stock_names.csv
    print("\n[2/2] 上传 stock_names.csv...")
    upload_stock_names()

    print("\n" + "=" * 50)
    print("  上传完成!")
    print("  Mac 端运行 setup_boss.sh 即可拉取最新数据")
    print("=" * 50)


if __name__ == '__main__':
    main()
