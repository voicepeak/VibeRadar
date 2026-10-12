#!/usr/bin/env pwsh
# 每日备份脚本（Windows 任务计划示例）：
#   1. 备份数据库与公开站点到 data/backups/<时间戳>/
#   2. 校验 manifest 哈希
#   3. 通过环境变量 VIBERADAR_BACKUP_REMOTE（如 rclone remote:path）复制异机副本
# 恢复演练：viberadar restore --backup-dir <备份目录> --staging <隔离目录>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Push-Location $root
try {
    & $python -m viberadar backup
    if ($LASTEXITCODE -ne 0) { throw "备份失败：退出码 $LASTEXITCODE" }

    if ($env:VIBERADAR_BACKUP_REMOTE) {
        $latest = Get-ChildItem (Join-Path $root "data\backups") -Directory |
            Sort-Object Name -Descending | Select-Object -First 1
        if ($latest) {
            Write-Host "复制异机副本：$($latest.FullName) → $env:VIBERADAR_BACKUP_REMOTE"
            & rclone copy $latest.FullName $env:VIBERADAR_BACKUP_REMOTE
            if ($LASTEXITCODE -ne 0) { throw "异机复制失败：退出码 $LASTEXITCODE" }
        }
    }
    Write-Host "备份完成。"
}
finally {
    Pop-Location
}
