# backup_chroma.ps1 — On-demand ChromaDB backup to Z:\ChromaBackups
# Usage: powershell -File C:\GIT\backup_chroma.ps1
# [2026-08-07] Fixed dest: was OneDrive\Backups (wrong), must always be Z:\ChromaBackups

$src  = "C:\GIT\chroma_db"
$dest = "Z:\ChromaBackups\chroma_db_backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"

Write-Host "Backing up $src -> $dest"
Copy-Item -Path $src -Destination $dest -Recurse

$size  = [math]::Round((Get-ChildItem $dest -Recurse | Measure-Object -Property Length -Sum).Sum / 1GB, 2)
$count = (Get-ChildItem $dest -Recurse -File).Count
Write-Host "Done. $size GB, $count files"
Write-Host $dest
