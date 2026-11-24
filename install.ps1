# One-click installer for Antigravity Computer Use Skill + MCP Server
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillDst = Join-Path $env:USERPROFILE ".gemini\config\skills\computer-use"
$McpCfgPath = Join-Path $env:USERPROFILE ".gemini\config\mcp_config.json"

Write-Host "[1/3] Installing skill files to $SkillDst ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path (Join-Path $SkillDst "scripts") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $SkillDst "references") | Out-Null

Copy-Item -Force (Join-Path $RepoRoot "SKILL.md") (Join-Path $SkillDst "SKILL.md")
Copy-Item -Force (Join-Path $RepoRoot "references\commands.md") (Join-Path $SkillDst "references\commands.md")
Copy-Item -Force (Join-Path $RepoRoot "scripts\cu.py") (Join-Path $SkillDst "scripts\cu.py")
Copy-Item -Force (Join-Path $RepoRoot "scripts\mcp_server.py") (Join-Path $SkillDst "scripts\mcp_server.py")
Copy-Item -Force (Join-Path $RepoRoot "scripts\uia_scan.cs") (Join-Path $SkillDst "scripts\uia_scan.cs")

Write-Host "[2/3] Compiling native uia_scan.exe ..." -ForegroundColor Cyan
$fw = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319"
$csc = Join-Path $fw "csc.exe"
if (Test-Path $csc) {
    & $csc /nologo /optimize+ /target:exe `
        "/out:$(Join-Path $SkillDst 'scripts\uia_scan.exe')" `
        "/r:$fw\WPF\UIAutomationClient.dll" `
        "/r:$fw\WPF\UIAutomationTypes.dll" `
        "/r:$fw\WPF\WindowsBase.dll" `
        (Join-Path $SkillDst "scripts\uia_scan.cs")
} else {
    Write-Warning "csc.exe not found; uia_scan.exe will need to be compiled manually."
}

Write-Host "[3/3] Registering computer-use MCP server in $McpCfgPath ..." -ForegroundColor Cyan
$pythonExe = (Get-Command python -ErrorAction Stop).Source
$mcpScript = Join-Path $SkillDst "scripts\mcp_server.py"

if ((Test-Path $McpCfgPath) -and ((Get-Content $McpCfgPath -Raw).Trim().Length -gt 0)) {
    $cfg = Get-Content $McpCfgPath -Raw | ConvertFrom-Json
} else {
    $cfg = [PSCustomObject]@{ mcpServers = [PSCustomObject]@{} }
}
if (-not $cfg.mcpServers) {
    $cfg | Add-Member -MemberType NoteProperty -Name mcpServers -Value ([PSCustomObject]@{}) -Force
}

$serverEntry = [PSCustomObject]@{
    command = $pythonExe
    args    = @("-u", $mcpScript)
    env     = [PSCustomObject]@{ PYTHONUTF8 = "1" }
}
$cfg.mcpServers | Add-Member -MemberType NoteProperty -Name "computer-use" -Value $serverEntry -Force

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $McpCfgPath) | Out-Null
$cfg | ConvertTo-Json -Depth 10 | Set-Content -Path $McpCfgPath -Encoding UTF8

Write-Host "`nDone! Restart Antigravity or open a new chat to use all 15 cu_* tools." -ForegroundColor Green
