Set shell = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""D:\Apache\scripts\sync-all-github-repos.ps1"" -ConfigPath ""D:\Apache\scripts\repo-sync-config.json"""
shell.Run cmd, 0, False
