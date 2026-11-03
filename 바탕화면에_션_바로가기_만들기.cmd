@echo off
chcp 65001 >nul
setlocal
set "SYEON_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $dir = $env:SYEON_DIR.TrimEnd('\'); $desktop = [Environment]::GetFolderPath('Desktop'); $lnkPath = Join-Path $desktop '션 데스크탑 펫.lnk'; $exePath = Join-Path $dir 'SyeonPet.exe'; if (-not (Test-Path $exePath)) { $exePath = Join-Path $dir 'pythonw.exe' }; $iconPath = Join-Path $dir 'assets\icon.ico'; $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($lnkPath); $s.TargetPath = $exePath; if ($exePath.EndsWith('pythonw.exe')) { $s.Arguments = '"' + (Join-Path $dir 'main.py') + '"' }; $s.WorkingDirectory = $dir; if (Test-Path $iconPath) { $s.IconLocation = $iconPath }; $s.Description = '션 데스크탑 펫 (Syeon Desktop Pet)'; $s.Save(); Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('바탕화면에 ''션 데스크탑 펫'' 바로가기를 만들었어요!', '션 바로가기 생성 완료', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)"
endlocal
