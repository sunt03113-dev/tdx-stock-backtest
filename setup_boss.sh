#!/bin/bash
# ============================================================
# tdx-stock-backtest 老板一键部署脚本
# 用法: bash setup_boss.sh
# 完成后即可运行 A9/B9/0803/0723A8 回测
# ============================================================
set -e

# ── 颜色 ──
G="\033[32m"; Y="\033[33m"; R="\033[31m"; B="\033[34m"; N="\033[0m"

echo -e "${B}========================================${N}"
echo -e "${B}  tdx-stock-backtest 一键部署${N}"
echo -e "${B}  A股涨停形态回测引擎${N}"
echo -e "${B}========================================${N}"
echo ""

SKILL_DIR="$HOME/tdx-stock-backtest"
TDX_DIR="$HOME/Documents/TDx"
CACHE_DIR="$HOME/tdx_cache"

# ============================================================
# 步骤 1: 检查 Python 3
# ============================================================
echo -e "${G}【1/7】检查 Python 环境...${N}"
if ! command -v python3 &> /dev/null; then
    echo -e "${R}  未找到 python3，请先安装：${N}"
    echo -e "  ${Y}brew install python@3.10${N}"
    exit 1
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "  Python $PY_VER ${G}✓${N}"
echo ""

# ============================================================
# 步骤 2: 克隆/更新代码
# ============================================================
echo -e "${G}【2/7】获取最新代码...${N}"
if [ -d "$SKILL_DIR/.git" ]; then
    cd "$SKILL_DIR"
    git pull origin master 2>/dev/null || true
    echo -e "  代码已更新 ${G}✓${N}"
else
    git clone https://gitee.com/fenghuanwubing/tdx-stock-backtest.git "$SKILL_DIR"
    echo -e "  代码已克隆 ${G}✓${N}"
fi
cd "$SKILL_DIR"
echo ""

# ============================================================
# 步骤 3: 安装依赖
# ============================================================
echo -e "${G}【3/7】安装 Python 依赖...${N}"
pip3 install --user pandas numpy akshare openpyxl tqdm pyyaml cos-python-sdk-v5 2>/dev/null || \
pip3 install --break-system-packages pandas numpy akshare openpyxl tqdm pyyaml cos-python-sdk-v5 2>/dev/null || \
pip3 install pandas numpy akshare openpyxl tqdm pyyaml cos-python-sdk-v5
echo -e "  依赖安装完成 ${G}✓${N}"
echo ""

# ============================================================
# 步骤 4: 配置 COS 凭证
# ============================================================
echo -e "${G}【4/7】配置腾讯云 COS 凭证...${N}"
COS_FILE="$HOME/.tdx_cos_env"

if [ -f "$COS_FILE" ]; then
    echo -e "  发现已保存的 COS 凭证 ${G}✓${N}"
    source "$COS_FILE"
else
    echo -e "  ${Y}请输入腾讯云 CAM 子账号信息（老板专用子账号）：${N}"
    echo ""
    read -p "  SecretId:  " COS_SECRET_ID
    read -s -p "  SecretKey: " COS_SECRET_KEY
    echo ""
    echo ""

    # 保存凭证（权限 600，仅本人可读）
    cat > "$COS_FILE" << EOF
export COS_SECRET_ID="$COS_SECRET_ID"
export COS_SECRET_KEY="$COS_SECRET_KEY"
EOF
    chmod 600 "$COS_FILE"
    echo -e "  凭证已保存到 ~/.tdx_cos_env ${G}✓${N}"
fi
export COS_SECRET_ID
export COS_SECRET_KEY
echo ""

# ============================================================
# 步骤 5: 从 COS 下载日线数据
# ============================================================
echo -e "${G}【5/7】从 COS 下载日线数据...${N}"

# 创建目录
mkdir -p "$TDX_DIR/vipdoc/sh/lday"
mkdir -p "$TDX_DIR/vipdoc/sz/lday"
mkdir -p "$CACHE_DIR"

SH_COUNT=$(ls "$TDX_DIR/vipdoc/sh/lday/"*.day 2>/dev/null | wc -l | tr -d ' ')
SZ_COUNT=$(ls "$TDX_DIR/vipdoc/sz/lday/"*.day 2>/dev/null | wc -l | tr -d ' ')

if [ "$SH_COUNT" -gt 100 ] && [ "$SZ_COUNT" -gt 100 ]; then
    echo -e "  已有沪市 ${SH_COUNT} 个 + 深市 ${SZ_COUNT} 个 .day 文件"
    read -p "  是否重新下载全部数据？(y/N): " RE_DOWNLOAD
    RE_DOWNLOAD=${RE_DOWNLOAD:-N}
else
    RE_DOWNLOAD="y"
fi

if [ "$RE_DOWNLOAD" = "y" ] || [ "$RE_DOWNLOAD" = "Y" ]; then
    echo -e "  正在从 COS 下载全部 .day 文件（首次约5-10分钟）..."
    python3 << 'PYEOF'
import os, sys
from qcloud_cos import CosConfig, CosS3Client

BUCKET = 'tdx-vipdoc-kk-1408814916'
REGION = 'ap-guangzhou'
SECRET_ID = os.environ.get('COS_SECRET_ID', '')
SECRET_KEY = os.environ.get('COS_SECRET_KEY', '')

if not SECRET_ID or not SECRET_KEY:
    print("  [错误] COS 凭证未设置，请重新运行脚本")
    sys.exit(1)

config = CosConfig(Region=REGION, SecretId=SECRET_ID, SecretKey=SECRET_KEY)
client = CosS3Client(config)

TDX_BASE = os.path.expanduser('~/Documents/TDx')

# 下载沪市 .day 文件
for market in ['sh', 'sz']:
    prefix = f'vipdoc/{market}/lday/'
    local_dir = os.path.join(TDX_BASE, 'vipdoc', market, 'lday')
    os.makedirs(local_dir, exist_ok=True)

    # 列出所有对象
    marker = ''
    total = 0
    while True:
        resp = client.list_objects(
            Bucket=BUCKET,
            Prefix=prefix,
            Marker=marker,
            MaxKeys=1000
        )
        contents = resp.get('Contents', [])
        for obj in contents:
            key = obj['Key']
            if not key.endswith('.day'):
                continue
            filename = os.path.basename(key)
            local_path = os.path.join(local_dir, filename)

            # 跳过已存在的文件（除非文件大小不同）
            if os.path.exists(local_path):
                remote_size = int(obj.get('Size', 0))
                local_size = os.path.getsize(local_path)
                if remote_size == local_size and remote_size > 0:
                    continue

            try:
                client.download_file(
                    Bucket=BUCKET,
                    Key=key,
                    DestFilePath=local_path
                )
                total += 1
                if total % 200 == 0:
                    print(f"  {market}: 已下载 {total} 个文件...")
            except Exception as e:
                print(f"  [跳过] {filename}: {e}")

        if resp.get('IsTruncated') == 'true':
            marker = resp.get('NextMarker', '')
        else:
            break

    print(f"  {market} 市场下载完成: {total} 个新文件")

# 下载 stock_names.csv
names_local = os.path.expanduser('~/tdx_cache/stock_names.csv')
try:
    client.download_file(
        Bucket=BUCKET,
        Key='stock_names.csv',
        DestFilePath=names_local
    )
    print(f"  stock_names.csv 下载完成")
except Exception as e:
    print(f"  [警告] stock_names.csv 下载失败: {e}")
    print(f"  首次运行时会自动回退到 akshare 联网查询")
PYEOF
    echo -e "  数据下载完成 ${G}✓${N}"
else
    # 仅下载 stock_names.csv
    python3 << 'PYEOF'
import os
from qcloud_cos import CosConfig, CosS3Client
BUCKET = 'tdx-vipdoc-kk-1408814916'
config = CosConfig(Region='ap-guangzhou',
    SecretId=os.environ['COS_SECRET_ID'],
    SecretKey=os.environ['COS_SECRET_KEY'])
client = CosS3Client(config)
names_local = os.path.expanduser('~/tdx_cache/stock_names.csv')
try:
    client.download_file(Bucket=BUCKET, Key='stock_names.csv',
        DestFilePath=names_local)
    print(f"  stock_names.csv 已更新")
except Exception as e:
    print(f"  [警告] stock_names.csv 下载失败: {e}")
PYEOF
fi
echo ""

# 统计文件数
SH_COUNT=$(ls "$TDX_DIR/vipdoc/sh/lday/"*.day 2>/dev/null | wc -l | tr -d ' ')
SZ_COUNT=$(ls "$TDX_DIR/vipdoc/sz/lday/"*.day 2>/dev/null | wc -l | tr -d ' ')
echo -e "  当前数据: 沪市 ${SH_COUNT} 个 + 深市 ${SZ_COUNT} 个 .day 文件"
echo ""

# ============================================================
# 步骤 6: 生成配置文件
# ============================================================
echo -e "${G}【6/7】生成配置文件...${N}"
cat > "$SKILL_DIR/config.yaml" << EOF
# tdx-stock-backtest 配置文件（自动生成）
tdx_base: ~/Documents/TDx
data_cache: $CACHE_DIR/day_xlsx
stock_names_file: $CACHE_DIR/stock_names.csv
EOF
echo -e "  config.yaml 已生成 ${G}✓${N}"
echo ""

# ============================================================
# 步骤 7: 安装 TRAE 全局 Skill
# ============================================================
echo -e "${G}【7/7】安装 TRAE Skill...${N}"
TRAE_SKILL_DIR="$HOME/.trae-cn/skills/tdx-stock-backtest"
mkdir -p "$TRAE_SKILL_DIR"

# 复制 skill 定义文件
if [ -f "$SKILL_DIR/tdx-stock-backtest.md" ]; then
    cp "$SKILL_DIR/tdx-stock-backtest.md" "$TRAE_SKILL_DIR/SKILL.md"
    echo -e "  Skill 已安装到 ~/.trae-cn/skills/ ${G}✓${N}"
    echo -e "  ${Y}请在 TRAE Work 中新建对话即可自动识别此 Skill${N}"
else
    echo -e "  ${Y}未找到 tdx-stock-backtest.md，跳过 Skill 安装${N}"
fi
echo ""

# ============================================================
# 完成
# ============================================================
echo -e "${B}========================================${N}"
echo -e "${G}  ✅ 部署完成！${N}"
echo -e "${B}========================================${N}"
echo ""
echo -e "  代码位置:  $SKILL_DIR"
echo -e "  数据位置:  $TDX_DIR/vipdoc/"
echo -e "  配置文件:  $SKILL_DIR/config.yaml"
echo -e "  数据文件:  沪市 ${SH_COUNT} + 深市 ${SZ_COUNT} 个"
echo ""
echo -e "${Y}  ── 日常使用 ──${N}"
echo ""
echo -e "  运行 A9 回测 (10cm 主板):"
echo -e "    cd ~/tdx-stock-backtest && python3 A9_backtest.py"
echo ""
echo -e "  运行 B9 回测 (10cm 主板):"
echo -e "    cd ~/tdx-stock-backtest && python3 B9_backtest.py"
echo ""
echo -e "  运行 0803 回测 (20cm 科创板/创业板):"
echo -e "    cd ~/tdx-stock-backtest && python3 0803_backtest.py"
echo ""
echo -e "  运行 0723A8 回测 (10cm 主板):"
echo -e "    cd ~/tdx-stock-backtest && python3 0723A8_backtest.py"
echo ""
echo -e "  结果输出到: ~/tdx-stock-backtest/results/<规则名>/"
echo ""
echo -e "  更新代码+数据:"
echo -e "    cd ~/tdx-stock-backtest && bash update_skill.sh"
echo ""
echo -e "  重新下载全部数据:"
echo -e "    cd ~/tdx-stock-backtest && bash setup_boss.sh"
echo ""
echo -e "${B}========================================${N}"
