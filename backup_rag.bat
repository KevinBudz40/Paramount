@echo off
:: RAG Backup — mirrors chroma_db and dashboard.db to OneDrive
:: Scheduled nightly at 2:00 AM via Windows Task Scheduler
:: Safe to run manually anytime: double-click or run from terminal

echo [%DATE% %TIME%] RAG backup starting...

robocopy "C:\GIT\chroma_db" "C:\Users\kevin\OneDrive\Backups\chroma_db" /MIR /Z /W:5 /R:3 /LOG+:"C:\GIT\backup_rag.log" /NP /TEE

robocopy "C:\GIT" "C:\Users\kevin\OneDrive\Backups\dashboard_db" dashboard.db /Z /W:5 /R:3 /LOG+:"C:\GIT\backup_rag.log" /NP /TEE

echo [%DATE% %TIME%] RAG backup complete.
