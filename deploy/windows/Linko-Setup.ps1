<#
.SYNOPSIS
  Enrolls this Windows PC for Linko onboarding. Run in an elevated PowerShell; safe to re-run.
.DESCRIPTION
  Installs Tailscale (joins with an auth key), OpenSSH Server (authorizes the management server key, SSH reachable only
  from the Tailscale range), and Syncthing (starts at logon). Prints hostname, Tailscale IP and Syncthing device ID for
  `python -m ai_manager.provision add-user`. Nothing secret is embedded: pass the keys as parameters or environment
  variables (TAILSCALE_AUTHKEY, LINKO_MANAGEMENT_PUBKEY). See docs/ONBOARDING.md.
.EXAMPLE
  .\Linko-Setup.ps1 -TailscaleAuthKey $env:TAILSCALE_AUTHKEY -ManagementPublicKey (Get-Content .\manager.pub -Raw)
#>
param(
    [string]$TailscaleAuthKey = $env:TAILSCALE_AUTHKEY,
    [string]$ManagementPublicKey = $env:LINKO_MANAGEMENT_PUBKEY
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

Write-Host ''
Write-Host 'Give these three values to the provisioning command:'
Write-Host "  Hostname:            $env:COMPUTERNAME"
Write-Host "  Tailscale IP:        $tsIp"
Write-Host "  Syncthing device ID: $deviceId"
