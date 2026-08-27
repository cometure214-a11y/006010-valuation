@echo off
chcp 936 >nul
title 006010 盘中估值面板（手机端）
cd /d "%~dp0.."   rem 切到项目根

set "VENV=%~dp0..\gui_venv\Scripts\python.exe"

if exist "%VENV%" goto :ready
echo [首次运行] 请先双击"打开估值面板.bat"完成环境安装
pause
exit /b 1

:ready
if exist "%~dp0..\gui\fund_web.py" goto :launch
echo [错误] 未找到 gui\fund_web.py
pause
exit /b 1

:launch
echo ============================================
echo  006010 盘中估值 - 手机端面板
echo ============================================
echo  启动后手机与本机连同一WiFi, 用浏览器打开
echo  屏幕显示的 http://IP:8080 地址即可查看
echo  (或扫屏幕上的二维码)
echo.
echo  按 Ctrl+C 停止服务
echo.
"%VENV%" "%~dp0..\gui\fund_web.py" 8080
pause
