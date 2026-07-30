param(
    [Parameter(Mandatory = $true)]
    [string]$AuthorizedKey
)

$ErrorActionPreference = "Stop"

$sshd = Get-WindowsCapability -Online | Where-Object Name -Like "OpenSSH.Server*"
if ($sshd.State -ne "Installed") {
    Add-WindowsCapability -Online -Name $sshd.Name | Out-Null
}

$authorizedKeysPath = "C:\ProgramData\ssh\administrators_authorized_keys"
New-Item -ItemType Directory -Path (Split-Path $authorizedKeysPath) -Force | Out-Null
Set-Content -Path $authorizedKeysPath -Value $AuthorizedKey -Encoding ascii
& icacls.exe $authorizedKeysPath /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F" | Out-Null

Set-Service sshd -StartupType Manual
Start-Service sshd
