@echo off
chcp 936 >nul
title 006010 一键更新并推送
cd /d "%~dp0.."   rem 切到项目根

set "PY=%~dp0..\gui_venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [错误] 未找到 gui_venv, 请先在项目根目录双击"scripts\打开估值面板.bat"完成环境安装
  pause
  exit /b 1
)

echo [1/5] 抓取最新数据(净值+日线+干扰股)...
"%PY%" scripts\fetch_cloud.py
if errorlevel 1 goto :err

echo [2/5] 方案B 个股反推...
"%PY%" src\fund_holdings_infer.py
if errorlevel 1 goto :err

echo [3/5] 方案A v3 多模型估值...
"%PY%" src\fund_valuation_v2.py
if errorlevel 1 goto :err

echo [4/5] 生成手机页面...
"%PY%" scripts\gen_static.py
if errorlevel 1 goto :err

echo [5/5] 推送到 GitHub...
git add docs/ cache/result.json cache/infer.json
git diff --staged --quiet
if errorlevel 1 (
  git commit -m "update %date% %time%"
  git push
  if errorlevel 1 goto :err
  echo.
  echo 推送成功! 手机刷新页面即可看到最新完整估值。
) else (
  echo 数据无变化, 无需推送
)
echo.
echo 完成
pause
exit /b 0

:err
echo.
echo [失败] 请检查上方错误信息
pause
