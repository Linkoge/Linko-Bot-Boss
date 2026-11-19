<#
.SYNOPSIS
  Enrolls this Windows PC for Linko onboarding. Run in an elevated PowerShell; safe to re-run.
.DESCRIPTION
  Needs Windows PowerShell 5.1 or newer (checked first). Installs Tailscale (joins with an auth key), OpenSSH Server (authorizes the management server key, SSH reachable only
  from the Tailscale range), and Syncthing (starts at logon). With -ManagerDeviceId/-FolderId/-WindowsFolder it also trusts
  the management Syncthing device and pre-creates the share, so no Syncthing GUI click is needed. With -ManagerUrl and
  -EnrollmentToken it then reports hostname, Tailscale IP and Syncthing device ID to the manager over the tailnet (one
  time); otherwise it prints them for `python -m ai_manager.provision add-user`. The owner UI's Add User screen embeds
  this very file in a one-line command; nothing else is downloaded. Only a single-use Tailscale auth key and a one-time
  enrollment token are passed in - never an API key. Packages come from winget when `winget` exists; otherwise from the
  official direct downloads (Tailscale MSI from pkgs.tailscale.com, Syncthing zip from its GitHub release) over TLS 1.2.
  See docs/ONBOARDING.md.
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

# Minimum: Windows PowerShell 5.1 (the version in Windows 10/Server 2016 and newer; Windows 7/8.1 need WMF 5.1).
$MinPowerShell = '5.1'
if ($PSVersionTable.PSVersion -lt [version]$MinPowerShell) {
    throw "This script needs Windows PowerShell $MinPowerShell or newer; this is $($PSVersionTable.PSVersion). Update Windows or install WMF 5.1, then run it again."
}
# Old PowerShell/.NET defaults to TLS 1.0, which the download hosts refuse.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this script as Administrator.' }
if (-not $TailscaleAuthKey) { throw 'Pass -TailscaleAuthKey (or set TAILSCALE_AUTHKEY).' }
if (-not $ManagementPublicKey -or $ManagementPublicKey -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+) ') {
    throw 'Pass -ManagementPublicKey with the management server PUBLIC key (one line), or set LINKO_MANAGEMENT_PUBKEY.'
}
$ManagementPublicKey = $ManagementPublicKey.Trim()

$TailscaleMsiHost = 'https://pkgs.tailscale.com/stable'
$SyncthingReleaseApi = 'https://api.github.com/repos/syncthing/syncthing/releases/latest'
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$pkgArch = if ($arch -eq 'ARM64') { 'arm64' } else { 'amd64' }

function Get-Download([string]$Url, [string]$OutFile) {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $OutFile
    if (-not (Test-Path $OutFile)) { throw "download failed: $Url" }
}

function Install-TailscaleDirect {
    $msi = Join-Path $env:TEMP "tailscale-setup-$pkgArch.msi"
    Get-Download "$TailscaleMsiHost/tailscale-setup-latest-$pkgArch.msi" $msi
    $p = Start-Process -FilePath msiexec.exe -ArgumentList @('/i', "`"$msi`"", '/qn', '/norestart') -Wait -PassThru
    Remove-Item $msi -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) { throw "Tailscale MSI install failed (msiexec exit code $($p.ExitCode))" }
}

function Install-SyncthingDirect {
    $release = Invoke-RestMethod -UseBasicParsing -Uri $SyncthingReleaseApi
    $asset = $release.assets | Where-Object { $_.name -like "syncthing-windows-$pkgArch-v*.zip" } | Select-Object -First 1
    if (-not $asset) { throw "no Syncthing windows-$pkgArch zip in the latest GitHub release" }
    $work = Join-Path $env:TEMP ('linko-syncthing-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        $zip = Join-Path $work 'syncthing.zip'
        Get-Download $asset.browser_download_url $zip
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($zip, (Join-Path $work 'x'))
        $exe = Get-ChildItem -Path (Join-Path $work 'x') -Recurse -Filter syncthing.exe | Select-Object -First 1
        if (-not $exe) { throw 'syncthing.exe not found in the downloaded zip' }
        $target = Join-Path $env:ProgramFiles 'Syncthing'
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Get-Process syncthing -ErrorAction SilentlyContinue | Stop-Process -Force
        Copy-Item -Path $exe.FullName -Destination (Join-Path $target 'syncthing.exe') -Force
    } finally {
        Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Install-IfMissing([string]$Command, [string]$WingetId, [string]$Fallback) {
    if (Get-Command $Command -ErrorAction SilentlyContinue) { Write-Host "ok: $Command already installed"; return }
    # winget is only used when it exists (it is absent on older Windows and LTSC); otherwise download directly.
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id $WingetId --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { return }
        Write-Host "winget could not install $WingetId (exit $LASTEXITCODE); trying the direct download"
    } else {
        Write-Host "winget not found; downloading $Command directly"
    }
    & $Fallback
}

# Tailscale
Install-IfMissing 'tailscale' 'Tailscale.Tailscale' 'Install-TailscaleDirect'
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
Install-IfMissing 'syncthing' 'Syncthing.Syncthing' 'Install-SyncthingDirect'
$syncthing = (Get-Command syncthing -ErrorAction SilentlyContinue).Source
if (-not $syncthing -and (Test-Path "$env:ProgramFiles\Syncthing\syncthing.exe")) { $syncthing = "$env:ProgramFiles\Syncthing\syncthing.exe" }
if (-not $syncthing) { throw 'syncthing not found after install; open a new shell and re-run.' }
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
