<#
.SYNOPSIS
  Enrolls this Windows PC for Linko onboarding. Run in an elevated PowerShell; safe to re-run.
.DESCRIPTION
  Installs Tailscale (joins with an auth key), OpenSSH Server (authorizes the management server key, SSH reachable only
  from the Tailscale range), and Syncthing (starts at logon). With -ManagerDeviceId/-FolderId/-WindowsFolder it also trusts
  the management Syncthing device and pre-creates the share, so no Syncthing GUI click is needed. With -ManagerUrl and
  -EnrollmentToken it then reports hostname, Tailscale IP and Syncthing device ID to the manager over the tailnet (one
  time); otherwise it prints them for `python -m ai_manager.provision add-user`. The owner UI's Add User screen embeds
  this very file in a one-line command; nothing else is downloaded. Only a single-use Tailscale auth key and a one-time
  enrollment token are passed in - never an API key. See docs/ONBOARDING.md.
.EXAMPLE
  .\Linko-Setup.ps1 -TailscaleAuthKey $env:TAILSCALE_AUTHKEY -ManagementPublicKey (Get-Content .\manager.pub -Raw)
#>
param(
    [string]$TailscaleAuthKey = $env:TAILSCALE_AUTHKEY,
    [string]$ManagementPublicKey = $env:LINKO_MANAGEMENT_PUBKEY,
    [string]$WindowsFolder,
    [string]$ManagerUrl,
    [string]$ManagerDeviceId,
    [string]$FolderId,
    [string]$EnrollmentToken
)
$ErrorActionPreference = 'Stop'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this script as Administrator.' }
if (-not $TailscaleAuthKey) { throw 'Pass -TailscaleAuthKey (or set TAILSCALE_AUTHKEY).' }
if (-not $ManagementPublicKey -or $ManagementPublicKey -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+) ') {
    throw 'Pass -ManagementPublicKey with the management server PUBLIC key (one line), or set LINKO_MANAGEMENT_PUBKEY.'
}
$ManagementPublicKey = $ManagementPublicKey.Trim()

function Install-IfMissing([string]$Command, [string]$WingetId) {
    if (Get-Command $Command -ErrorAction SilentlyContinue) { Write-Host "ok: $Command already installed"; return }
    winget install --id $WingetId --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget could not install $WingetId" }
}

# Tailscale
Install-IfMissing 'tailscale' 'Tailscale.Tailscale'
$tailscale = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $tailscale) { $tailscale = "$env:ProgramFiles\Tailscale\tailscale.exe" }
$tsIp = (& $tailscale ip -4 2>$null | Select-Object -First 1)
if (-not $tsIp) {
    & $tailscale up --auth-key $TailscaleAuthKey --unattended
    if ($LASTEXITCODE -ne 0) { throw 'tailscale up failed' }
    $tsIp = (& $tailscale ip -4 | Select-Object -First 1)
}

# OpenSSH Server
$capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' | Select-Object -First 1
if ($capability.State -ne 'Installed') { Add-WindowsCapability -Online -Name $capability.Name | Out-Null }
Set-Service -Name sshd -StartupType Automatic
if ((Get-Service sshd).Status -ne 'Running') { Start-Service sshd }

# Management key: administrators use the shared file; append only when absent.
$keyFile = "$env:ProgramData\ssh\administrators_authorized_keys"
if (-not (Test-Path $keyFile)) { New-Item -ItemType File -Path $keyFile | Out-Null }
if (-not (Get-Content $keyFile -ErrorAction SilentlyContinue | Where-Object { $_.Trim() -eq $ManagementPublicKey })) {
    Add-Content -Path $keyFile -Value $ManagementPublicKey
}
icacls $keyFile /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null

# SSH only from the Tailscale range; remove the broad default rule.
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if (-not (Get-NetFirewallRule -Name 'Linko-SSH-Tailscale' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'Linko-SSH-Tailscale' -DisplayName 'Linko SSH (Tailscale only)' -Direction Inbound -Protocol TCP `
        -LocalPort 22 -RemoteAddress '100.64.0.0/10' -Action Allow -Profile Any | Out-Null
}

# Syncthing, started at logon of the installing user.
Install-IfMissing 'syncthing' 'Syncthing.Syncthing'
$syncthing = (Get-Command syncthing -ErrorAction SilentlyContinue).Source
if (-not $syncthing) { throw 'syncthing not found on PATH after install; open a new shell and re-run.' }
if (-not (Get-ScheduledTask -TaskName 'Linko Syncthing' -ErrorAction SilentlyContinue)) {
    $action = New-ScheduledTaskAction -Execute $syncthing -Argument '--no-browser --no-console'
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName 'Linko Syncthing' -Action $action -Trigger $trigger -Settings $settings | Out-Null
}
if (-not (Get-Process syncthing -ErrorAction SilentlyContinue)) { Start-ScheduledTask -TaskName 'Linko Syncthing' }
$deviceId = $null
for ($i = 0; $i -lt 15 -and -not $deviceId; $i++) {
    $deviceId = (& $syncthing --device-id 2>$null | Select-Object -First 1)
    if (-not $deviceId) { Start-Sleep -Seconds 2 }
}

# Syncthing: trust the management device and pre-create the share through the local API (idempotent), so the share
# needs no click in the Syncthing GUI once the manager adds this PC.
if ($ManagerDeviceId -and $FolderId -and $WindowsFolder) {
    New-Item -ItemType Directory -Force -Path $WindowsFolder | Out-Null
    $configXml = Join-Path $env:LOCALAPPDATA 'Syncthing\config.xml'
    for ($i = 0; $i -lt 30 -and -not (Test-Path $configXml); $i++) { Start-Sleep -Seconds 2 }
    if (-not (Test-Path $configXml)) { throw 'Syncthing did not create its configuration; open Syncthing once and re-run.' }
    [xml]$syncthingConfig = Get-Content $configXml
    $api = "http://$($syncthingConfig.configuration.gui.address)"
    $headers = @{ 'X-API-Key' = $syncthingConfig.configuration.gui.apikey }
    for ($i = 0; $i -lt 30; $i++) {
        try { Invoke-RestMethod -Headers $headers -Uri "$api/rest/system/ping" | Out-Null; break } catch { Start-Sleep -Seconds 2 }
    }
    $known = Invoke-RestMethod -Headers $headers -Uri "$api/rest/config/devices"
    if (-not ($known | Where-Object { $_.deviceID -eq $ManagerDeviceId })) {
        $device = @{ deviceID = $ManagerDeviceId; name = 'linko-manager'; addresses = @('dynamic'); autoAcceptFolders = $true }
        Invoke-RestMethod -Method Put -Headers $headers -ContentType 'application/json' -Uri "$api/rest/config/devices/$ManagerDeviceId" `
            -Body ($device | ConvertTo-Json -Depth 5) | Out-Null
    }
    $shares = Invoke-RestMethod -Headers $headers -Uri "$api/rest/config/folders"
    if (-not ($shares | Where-Object { $_.id -eq $FolderId })) {
        $share = @{ id = $FolderId; label = $FolderId; path = $WindowsFolder; type = 'sendreceive'; fsWatcherEnabled = $true
                    devices = @(@{ deviceID = $ManagerDeviceId }) }
        Invoke-RestMethod -Method Put -Headers $headers -ContentType 'application/json' -Uri "$api/rest/config/folders/$FolderId" `
            -Body ($share | ConvertTo-Json -Depth 5) | Out-Null
    }
    Write-Host "ok: Syncthing trusts the manager and shares $WindowsFolder"
}

# Report to the manager over the tailnet: one POST, authenticated by the one-time token, accepted once.
$reported = $false
if ($ManagerUrl -and $EnrollmentToken) {
    $report = @{ token = $EnrollmentToken; hostname = ($env:COMPUTERNAME -replace '[^A-Za-z0-9-]', '-'); tailscale_ip = $tsIp; device_id = $deviceId } | ConvertTo-Json
    for ($i = 0; $i -lt 10 -and -not $reported; $i++) {
        try {
            Invoke-RestMethod -Method Post -Uri "$ManagerUrl/api/enroll/report" -ContentType 'application/json' -Body $report -TimeoutSec 20 | Out-Null
            $reported = $true
        } catch { Start-Sleep -Seconds 6 }
    }
}

Write-Host ''
if ($reported) {
    Write-Host 'Done. This PC reported in; continue in the Add User screen on the manager.'
} else {
    if ($ManagerUrl) { Write-Host 'Could not reach the manager over the tailnet (token used already, or expired?). Give these values to the owner:' }
    else { Write-Host 'Give these three values to the provisioning command:' }
}
Write-Host "  Hostname:            $env:COMPUTERNAME"
Write-Host "  Tailscale IP:        $tsIp"
Write-Host "  Syncthing device ID: $deviceId"
