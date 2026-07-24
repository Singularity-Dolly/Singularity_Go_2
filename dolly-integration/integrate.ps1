# RobotGatewayClient Integration Script
# Copies robot_gateway.py to Dolly project and applies all patches.
#
# Usage:
#   .\dolly-integration\integrate.ps1
#
# Prerequisites:
#   - Dolly project at D:\github projects\Dolly_adx26
#   - This script run from Singularity_Go_2 root
#
# Safety: Creates .bak backups before modifying any file.

$ErrorActionPreference = "Stop"
$DollyRoot = "D:\github projects\Dolly_adx26"
$DollyApp = "$DollyRoot\backend\app"
$DollyTests = "$DollyRoot\tests"
$PatchDir = "$PSScriptRoot"

Write-Host "=== RobotGatewayClient Integration ===" -ForegroundColor Cyan
Write-Host "Dolly root: $DollyRoot" -ForegroundColor Gray

# ------------------------------------------------------------------
# Step 1: Copy robot_gateway.py
# ------------------------------------------------------------------
Write-Host "`n[1/4] Copying robot_gateway.py..." -ForegroundColor Yellow
$source = "$PatchDir\robot_gateway.py"
$target = "$DollyApp\robot_gateway.py"

if (Test-Path $target) {
    Copy-Item $target "$target.bak" -Force
    Write-Host "  Backup: $target.bak" -ForegroundColor Gray
}
Copy-Item $source $target -Force
Write-Host "  OK: $target" -ForegroundColor Green

# ------------------------------------------------------------------
# Step 2: Patch contracts.py — add ROBOT_* event types
# ------------------------------------------------------------------
Write-Host "`n[2/4] Patching contracts.py..." -ForegroundColor Yellow
$contractsFile = "$DollyApp\contracts.py"
$contractsContent = Get-Content $contractsFile -Raw -Encoding UTF8

$oldBlock = @'
STYLE_SUGGESTION = "style.suggestion"
    COST_UPDATED = "cost.updated"
    ERROR = "error"
'@

$newBlock = @'
STYLE_SUGGESTION = "style.suggestion"
    COST_UPDATED = "cost.updated"
    ROBOT_STATE = "robot.state"
    ROBOT_HEALTH = "robot.health"
    ROBOT_EVENT = "robot.event"
    ROBOT_COMMAND_RESULT = "robot.command_result"
    ROBOT_FRAME = "robot.frame"
    ROBOT_CONNECTED = "robot.connected"
    ROBOT_DISCONNECTED = "robot.disconnected"
    ERROR = "error"
'@

if ($contractsContent -match 'ROBOT_STATE = "robot.state"') {
    Write-Host "  SKIP: ROBOT_* event types already exist" -ForegroundColor Gray
} else {
    Copy-Item $contractsFile "$contractsFile.bak" -Force
    $contractsContent = $contractsContent.Replace($oldBlock, $newBlock)
    Set-Content $contractsFile -Value $contractsContent -Encoding UTF8 -NoNewline
    Write-Host "  OK: Added ROBOT_* event types" -ForegroundColor Green
}

# ------------------------------------------------------------------
# Step 3: Patch config.py — add robot settings
# ------------------------------------------------------------------
Write-Host "`n[3/4] Patching config.py..." -ForegroundColor Yellow
$configFile = "$DollyApp\config.py"
$configContent = Get-Content $configFile -Raw -Encoding UTF8

if ($configContent -match 'ROBOT_GATEWAY_URL') {
    Write-Host "  SKIP: Robot config already exists" -ForegroundColor Gray
} else {
    $insertMarker = "kinocut_version: Literal"
    $robotConfig = @'

    # ---- Robot Gateway ----
    robot_gateway_url: str = Field(
        "http://192.168.123.15:8780",
        validation_alias="ROBOT_GATEWAY_URL",
    )
    robot_gateway_enabled: bool = Field(
        False,
        validation_alias="ROBOT_GATEWAY_ENABLED",
    )
    robot_auth_token: str = Field(
        "",
        validation_alias="ROBOT_AUTH_TOKEN",
    )
    robot_health_interval_s: float = Field(
        5.0, ge=1.0, le=60.0,
        validation_alias="ROBOT_HEALTH_INTERVAL_S",
    )
    robot_state_interval_s: float = Field(
        0.5, ge=0.1, le=10.0,
        validation_alias="ROBOT_STATE_INTERVAL_S",
    )

'@
    Copy-Item $configFile "$configFile.bak" -Force
    $configContent = $configContent.Replace($insertMarker, "$robotConfig    $insertMarker")
    Set-Content $configFile -Value $configContent -Encoding UTF8 -NoNewline
    Write-Host "  OK: Added robot config fields" -ForegroundColor Green
}

# ------------------------------------------------------------------
# Step 4: Patch runtime.py — integrate RobotGatewayClient
# ------------------------------------------------------------------
Write-Host "`n[4/4] Patching runtime.py..." -ForegroundColor Yellow
$runtimeFile = "$DollyApp\runtime.py"
$runtimeContent = Get-Content $runtimeFile -Raw -Encoding UTF8

$patched = $false

# 4a: Add import
if ($runtimeContent -match 'from \.robot_gateway import RobotGatewayClient') {
    Write-Host "  SKIP: Import already exists" -ForegroundColor Gray
} else {
    $importMarker = "from .style import StyleService"
    $importLine = "from .robot_gateway import RobotGatewayClient"
    $runtimeContent = $runtimeContent.Replace($importMarker, "$importMarker`n$importLine")
    $patched = $true
    Write-Host "  OK: Added import" -ForegroundColor Green
}

# 4b: Add instance in __init__
if ($runtimeContent -match 'self\.robot_gateway = RobotGatewayClient') {
    Write-Host "  SKIP: robot_gateway instance already exists" -ForegroundColor Gray
} else {
    $initMarker = "self.editor: EditorService | None = None"
    $robotInit = @'
        self.robot_gateway = RobotGatewayClient(
            base_url=settings.robot_gateway_url,
            bus=self.bus,
            state=self.state,
            auth_token=settings.robot_auth_token or None,
            health_interval_s=settings.robot_health_interval_s,
            state_interval_s=settings.robot_state_interval_s,
            enabled=settings.robot_gateway_enabled,
        )
'@
    $runtimeContent = $runtimeContent.Replace($initMarker, "$initMarker`n$robotInit")
    $patched = $true
    Write-Host "  OK: Added robot_gateway instance" -ForegroundColor Green
}

# 4c: Add start() in initialize()
if ($runtimeContent -match 'robot_gateway\.start\(\)') {
    Write-Host "  SKIP: start() already exists" -ForegroundColor Gray
} else {
    $startMarker = 'await self.state.update("engine", {"running": True, "mode": self.public_mode(), "reason": "runtime_started_readiness_pending"'
    $robotStart = @'
        if self.robot_gateway.enabled:
            await self.robot_gateway.start()
'@
    # Insert BEFORE the engine state update
    $runtimeContent = $runtimeContent.Replace($startMarker, "$robotStart`n        $startMarker")
    $patched = $true
    Write-Host "  OK: Added robot_gateway.start()" -ForegroundColor Green
}

# 4d: Add stop() in shutdown()
if ($runtimeContent -match 'robot_gateway\.stop\(\)') {
    Write-Host "  SKIP: stop() already exists" -ForegroundColor Gray
} else {
    $stopMarker = 'await self.voice.stop()'
    $robotStop = "        if self.robot_gateway.enabled:`n            await self.robot_gateway.stop()"
    $runtimeContent = $runtimeContent.Replace($stopMarker, "$stopMarker`n$robotStop")
    $patched = $true
    Write-Host "  OK: Added robot_gateway.stop()" -ForegroundColor Green
}

if ($patched) {
    Copy-Item $runtimeFile "$runtimeFile.bak" -Force
    Set-Content $runtimeFile -Value $runtimeContent -Encoding UTF8 -NoNewline
    Write-Host "  OK: runtime.py patched" -ForegroundColor Green
} else {
    Write-Host "  SKIP: No changes needed" -ForegroundColor Gray
}

# ------------------------------------------------------------------
# Step 5: Copy tests
# ------------------------------------------------------------------
Write-Host "`n[5/5] Copying test file..." -ForegroundColor Yellow
$testSource = "$PatchDir\test_robot_gateway.py"
$testTarget = "$DollyTests\test_robot_gateway.py"

if (Test-Path $testTarget) {
    Copy-Item $testTarget "$testTarget.bak" -Force
}
Copy-Item $testSource $testTarget -Force
Write-Host "  OK: $testTarget" -ForegroundColor Green

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
Write-Host "`n=== Integration Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Files modified:" -ForegroundColor White
Write-Host "  $DollyApp\robot_gateway.py       (NEW)"
Write-Host "  $DollyApp\contracts.py           (patched)"
Write-Host "  $DollyApp\config.py              (patched)"
Write-Host "  $DollyApp\runtime.py             (patched)"
Write-Host "  $DollyTests\test_robot_gateway.py (NEW)"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Add to .env: ROBOT_GATEWAY_ENABLED=true"
Write-Host "  2. Add to .env: ROBOT_GATEWAY_URL=http://<robot-ip>:8780"
Write-Host "  3. Run: python -m pytest tests/test_robot_gateway.py -v"
Write-Host "  4. Start Dolly: python -m uvicorn backend.app.main:app"
Write-Host ""
Write-Host "Backups saved as .bak files." -ForegroundColor Gray