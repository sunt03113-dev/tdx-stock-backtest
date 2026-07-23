#!/bin/bash
# ============================================================
# 从 COS 下载股票名称文件（放在 download.sh 之后执行）
# 用法: bash download_stock_names.sh
# ============================================================
set -e

NAMES_FILE="$HOME/tdx_cache/stock_names.csv"
COS_PATH="cos://tdx-vipdoc-kk-1408814916/stock_names.csv"

# 确保目录存在
mkdir -p "$(dirname "$NAMES_FILE")"

echo "正在从 COS 下载股票名称映射文件..."
echo "源: $COS_PATH"
echo "目标: $NAMES_FILE"

# 方式1: 如果你装了 coscli
if command -v coscli &> /dev/null; then
    coscli cp "$COS_PATH" "$NAMES_FILE"
    echo "✅ 下载完成（coscli）"

# 方式2: 用 Python cos-python-sdk-v5（需要先 pip install）
elif python3 -c "import qcloud_cos" 2>/dev/null; then
    python3 << PYEOF
import os
from qcloud_cos import CosConfig, CosS3Client

config = CosConfig(
    Region='ap-guangzhou',
    SecretId=os.environ.get('COS_SECRET_ID'),
    SecretKey=os.environ.get('COS_SECRET_KEY'),
)
client = CosS3Client(config)
client.download_file(
    Bucket='tdx-vipdoc-kk-1408814916',
    Key='stock_names.csv',
    DestFilePath='$NAMES_FILE'
)
print("✅ 下载完成（Python SDK）")
PYEOF

else
    echo "⚠️  未检测到 coscli 或 cos-python-sdk-v5，请手动下载："
    echo "   1. 登录腾讯云 COS 控制台"
    echo "   2. 进入 tdx-vipdoc-kk-1408814916 存储桶"
    echo "   3. 下载 stock_names.csv 到 $NAMES_FILE"
fi

# 验证
if [ -f "$NAMES_FILE" ]; then
    COUNT=$(wc -l < "$NAMES_FILE")
    SIZE=$(du -h "$NAMES_FILE" | cut -f1)
    echo "   文件信息: $SIZE, $COUNT 行"
else
    echo "⚠️  下载未成功，首次运行回测时会自动回退到 akshare 联网查询"
fi