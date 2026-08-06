# backup_chroma.ps1 — On-demand ChromaDB backup to OneDrive
# Usage: powershell -File C:\GIT\backup_chroma.ps1

$src  = "C:\GIT\chroma_db"
$dest = "C:\Users\kevin\OneDrive\Backups\chroma_db_backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"

Write-Host "Backing up $src -> $dest"
Copy-Item -Path $src -Destination $dest -Recurse

$size  = [math]::Round((Get-ChildItem $dest -Recurse | Measure-Object -Property Length -Sum).Sum / 1GB, 2)
$count = (Get-ChildItem $dest -Recurse -File).Count
Write-Host "Done. $size GB, $count files"
Write-Host $dest
