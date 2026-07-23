#!/bin/bash
# ============================================================
# tdx-stock-backtest 一键更新脚本
# 老板只需双击运行或在终端执行：bash update_skill.sh
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
pip3 install --upgrade pandas akshare openpyxl tqdm pyyaml --break-system-packages 2>/dev/null || \
pip3 install pandas akshare openpyxl tqdm pyyaml --break-system-packages

echo "✅ 依赖已更新"

# 3. 检查配置文件
if [ ! -f "$SKILL_DIR/config.yaml" ]; then
    echo ""
    echo "⚠️  未找到 config.yaml，正在创建默认配置..."
    cat > "$SKILL_DIR/config.yaml" << 'EOF'
# tdx-stock-backtest 配置文件
# 请根据实际情况修改路径

tdx_base: ~/Documents/TDx
data_cache: ~/tdx_cache/day_xlsx
stock_names_file: ~/tdx_cache/stock_names.csv
EOF
    echo "✅ 已创建默认 config.yaml，请确认路径是否正确"
fi

echo ""
echo "========================================"
echo "  ✅ 更新完成！"
echo "========================================"
echo ""
echo "  仓库位置: $SKILL_DIR"
echo "  配置文件: $SKILL_DIR/config.yaml"
echo ""
echo "  运行回测: cd $SKILL_DIR && python StockBacktest_TdxAutoRun0630.py"
echo "========================================"