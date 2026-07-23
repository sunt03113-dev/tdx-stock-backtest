@echo off
chcp 65001 >nul
REM ============================================================
REM tdx-stock-backtest Windows 更新脚本
REM ============================================================

set SKILL_DIR=D:\tdx-stock-backtest

echo ========================================
echo   tdx-stock-backtest 一键更新
echo ========================================

if not exist "%SKILL_DIR%\.git" (
    echo.
    echo [首次] 正在克隆仓库...
    git clone https://gitee.com/fenghuanwubing/tdx-stock-backtest.git "%SKILL_DIR%"
    echo 仓库已克隆
) else (
    echo.
    echo 正在拉取最新代码...
    cd /d "%SKILL_DIR%"
    git pull origin master
    echo 代码已更新
)

echo.
echo 正在更新 Python 依赖...
pip install --upgrade pandas akshare openpyxl tqdm pyyaml

echo.
echo ========================================
echo   更新完成！
echo ========================================
echo.
echo   仓库位置: %SKILL_DIR%
echo   运行: cd %SKILL_DIR% ^&^& python StockBacktest_TdxAutoRun0630.py
echo ========================================
pause