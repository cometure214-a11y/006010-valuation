@echo off
chcp 936 >nul
title 006010 盘中估值面板
cd /d "%~dp0"

set "VENV=%~dp0gui_venv\Scripts\python.exe"

if exist "%VENV%" goto :ready

echo [首次运行] 正在创建 GUI 环境并安装依赖(约2-3分钟)...
where python >nul 2>nul
if errorlevel 1 goto :err_nopython
python -m venv "%~dp0gui_venv"
if errorlevel 1 goto :err_venv
"%VENV%" -m pip install -q --upgrade pip
if errorlevel 1 goto :err_pip
"%VENV%" -m pip install -q numpy scipy scikit-learn matplotlib
if errorlevel 1 goto :err_pip
echo [完成] 环境就绪
echo.

:ready
if exist "%~dp0fund_gui.py" goto :launch
echo [错误] 未找到 fund_gui.py, 请确认脚本与bat在同一目录
goto :end

:launch
echo [1/2] 校验脚本文件...  OK
echo [2/2] 启动估值面板...
echo   面板打开后, 点右上角"刷新"按钮可重跑模型并获取最新行情
echo.
"%VENV%" "%~dp0fund_gui.py"
echo.
echo 面板已关闭。
goto :end

:err_nopython
echo [错误] 未找到系统 Python, 请先安装 Python 3.10+ 并勾选 Add to PATH
goto :end

:err_venv
echo [错误] venv 创建失败
goto :end

:err_pip
echo [错误] 依赖安装失败, 请检查网络后重试
goto :end

:end
pause
