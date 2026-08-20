@echo off
chcp 936 >nul
title 006010 云端手动刷新
cd /d "%~dp0"

echo 本脚本调用 GitHub Actions 在云端重新跑完整模型并部署。
echo 首次使用前需先设置仓库信息与 Token:
echo   1. 在 GitHub 生成 Personal Access Token (权限: repo, workflow)
echo   2. 编辑本文件, 把下面 REPO 和 TOKEN 两行的值改好
echo.
echo 也可直接手机浏览器打开:
echo   https://github.com/<你的用户名>/<仓库名>/actions
echo   选中左侧 workflow, 点右上角 Run workflow
echo.
pause
