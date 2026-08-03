#!/bin/bash
# ============================================================
# tdx-stock-backtest 一键更新脚本
# 老板只需在终端执行：bash update_skill.sh
# 功能：更新代码 + 依赖 + Skill + 数据
# ============================================================
set -e

SKILL_DIR="$HOME/tdx-stock-backtest"

echo "========================================"
echo "  tdx-stock-backtest 一键更新"
echo "========================================"

# 1. 检查仓库是否存在
if [ ! -d "$SKILL_DIR/.git" ]; then
    echo ""
    echo "❌ 未找到仓库，正在首次克隆..."
    git clone https://gitee.com/fenghuanwubing/tdx-stock-backtest.git "$SKILL_DIR"
    echo "✅ 仓库已克隆到 $SKILL_DIR"
else
    echo ""
    echo "📦 正在拉取最新代码..."
    cd "$SKILL_DIR"
    git pull origin master
    echo "✅ 代码已更新到最新版本"
fi

# 2. 安装/更新依赖
echo ""
echo "📦 正在更新 Python 依赖..."
pip3 install --upgrade pandas numpy akshare openpyxl tqdm pyyaml cos-python-sdk-v5 --break-system-packages 2>/dev/null || \
pip3 install pandas numpy akshare openpyxl tqdm pyyaml cos-python-sdk-v5 --break-system-packages 2>/dev/null || \
pip3 install pandas numpy akshare openpyxl tqdm pyyaml cos-python-sdk-v5
echo "✅ 依赖已更新"

# 3. 检查配置文件
if [ ! -f "$SKILL_DIR/config.yaml" ]; then
    echo ""
    echo "⚠️  未找到 config.yaml，正在创建默认配置..."
    cat > "$SKILL_DIR/config.yaml" << 'EOF'
# tdx-stock-backtest 配置文件（自动生成）
tdx_base: ~/Documents/TDx
data_cache: ~/tdx_cache/day_xlsx
stock_names_file: ~/tdx_cache/stock_names.csv
EOF
    echo "✅ 已创建默认 config.yaml"
fi

# 4. 安装/更新 TRAE Skill
echo ""
echo "🔧 正在安装 TRAE Skill..."
TRAE_SKILL_DIR="$HOME/.trae-cn/skills/tdx-stock-backtest"
mkdir -p "$TRAE_SKILL_DIR"
if [ -f "$SKILL_DIR/tdx-stock-backtest.md" ]; then
    cp "$SKILL_DIR/tdx-stock-backtest.md" "$TRAE_SKILL_DIR/SKILL.md"
    echo "✅ Skill 已安装到 ~/.trae-cn/skills/tdx-stock-backtest/"
    echo "   在 TRAE Work 中新建对话即可自动识别此 Skill"
else
    echo "⚠️  未找到 tdx-stock-backtest.md，跳过 Skill 安装"
fi

# 5. 同步最新数据（如果已配置 COS 凭证）
COS_FILE="$HOME/.tdx_cos_env"
if [ -f "$COS_FILE" ]; then
    echo ""
    echo "📡 正在同步最新数据..."
    source "$COS_FILE"
    cd "$SKILL_DIR"
    python3 << 'PYEOF'
import os, sys
from qcloud_cos import CosConfig, CosS3Client

BUCKET = 'tdx-vipdoc-kk-1408814916'
REGION = 'ap-guangzhou'
SECRET_ID = os.environ.get('COS_SECRET_ID', '')
SECRET_KEY = os.environ.get('COS_SECRET_KEY', '')

if not SECRET_ID or not SECRET_KEY:
    print("  [跳过] COS 凭证未设置")
    sys.exit(0)

config = CosConfig(Region=REGION, SecretId=SECRET_ID, SecretKey=SECRET_KEY)
client = CosS3Client(config)

TDX_BASE = os.path.expanduser('~/Documents/TDx')

# 增量下载 .day 文件
total = 0
for market in ['sh', 'sz']:
    prefix = f'vipdoc/{market}/lday/'
    local_dir = os.path.join(TDX_BASE, 'vipdoc', market, 'lday')
    os.makedirs(local_dir, exist_ok=True)

    marker = ''
    while True:
        resp = client.list_objects(Bucket=BUCKET, Prefix=prefix, Marker=marker, MaxKeys=1000)
        for obj in resp.get('Contents', []):
            key = obj['Key']
            if not key.endswith('.day'):
                continue
            filename = os.path.basename(key)
            local_path = os.path.join(local_dir, filename)

            if os.path.exists(local_path):
                remote_size = int(obj.get('Size', 0))
                local_size = os.path.getsize(local_path)
                if remote_size == local_size and remote_size > 0:
                    continue

            try:
                client.download_file(Bucket=BUCKET, Key=key, DestFilePath=local_path)
                total += 1
            except Exception as e:
                print(f"  [跳过] {filename}: {e}")

        if resp.get('IsTruncated') == 'true':
            marker = resp.get('NextMarker', '')
        else:
            break

print(f"  新下载 {total} 个文件")

# 更新 stock_names.csv
names_local = os.path.expanduser('~/tdx_cache/stock_names.csv')
try:
    client.download_file(Bucket=BUCKET, Key='stock_names.csv', DestFilePath=names_local)
    print("  stock_names.csv 已更新")
except Exception as e:
    print(f"  [警告] stock_names.csv 下载失败: {e}")
PYEOF
    echo "✅ 数据同步完成"
else
    echo ""
    echo "⚠️  未配置 COS 凭证，跳过数据同步"
    echo "   首次使用请运行: bash setup_boss.sh"
fi

echo ""
echo "========================================"
echo "  ✅ 更新完成！"
echo "========================================"
echo ""
echo "  仓库位置:  $SKILL_DIR"
echo "  配置文件:  $SKILL_DIR/config.yaml"
echo ""
echo "  ── 可用规则 ──"
echo ""
echo "  A9 规则 (10cm 主板):"
echo "    cd ~/tdx-stock-backtest && python3 A9_backtest.py"
echo ""
echo "  B9 规则 (10cm 主板):"
echo "    cd ~/tdx-stock-backtest && python3 B9_backtest.py"
echo ""
echo "  0803 规则 (20cm 科创板/创业板):"
echo "    cd ~/tdx-stock-backtest && python3 0803_backtest.py"
echo ""
echo "  0723A8 规则 (10cm 主板):"
echo "    cd ~/tdx-stock-backtest && python3 0723A8_backtest.py"
echo ""
echo "  结果输出到: ~/tdx-stock-backtest/results/<规则名>/"
echo "========================================"
