<#
.SYNOPSIS
  Step 2: pull the shim probe log, XNU console ring or RAM-rescue log from the
  end of userdata while the XZ1 is booted into TWRP (adb root).

.EXAMPLE
  .\tools\poplar-log-pull.ps1 shim logs\shim-1.txt
  .\tools\poplar-log-pull.ps1 xnu  logs\xnu-1.txt
  .\tools\poplar-log-pull.ps1 rescue logs\rescue-1.txt
#>
param(
    [ValidateSet('shim', 'xnu', 'rescue')][string]$What = 'shim',
    [string]$Out = ''
)
$ErrorActionPreference = 'Stop'

if (-not $Out) {
    $root = Split-Path -Parent $PSScriptRoot
    $Out = Join-Path $root "logs\$What.txt"
}

$dir = Split-Path -Parent $Out
if ($dir) { New-Item -ItemType Directory -Force $dir | Out-Null }

$uid = ((& adb shell id -u) -join '').Trim()
$su = if ($uid -eq '0') { '' } else { 'su -c ' }
$tmp = if (((& adb shell 'test -d /tmp && echo y') -join '').Trim() -eq 'y' -and $uid -eq '0') { '/tmp' } else { '/data/local/tmp' }

function RootSh([string]$cmd) {
    if ($su) { & adb shell "su -c '$cmd'" } else { & adb shell $cmd }
}

$dev = ((RootSh 'for d in /dev/block/bootdevice/by-name/userdata /dev/block/by-name/userdata; do [ -e $d ] && readlink -f $d && break; done') -join '').Trim()
if (-not $dev) { throw 'userdata block device not found (is the phone connected via adb with root/TWRP?)' }
$bytesStr = ((RootSh "blockdev --getsize64 $dev") -join '').Trim()
$bytes = [int64]$bytesStr
$blocks = [int64]($bytes / 4096)

switch ($What) {
    'shim'   { $skip = $blocks - 1025 - 64;      $count = 64 }
    'xnu'    { $skip = $blocks - 1025;           $count = 1025 }
    'rescue' { $skip = $blocks - 1025 - 64 - 64; $count = 64 }
}
$raw = "$Out.raw"
if (Test-Path $raw) { Remove-Item $raw }
RootSh "dd if=$dev of=$tmp/poplar_log.raw bs=4096 skip=$skip count=$count 2>/dev/null && chmod 644 $tmp/poplar_log.raw" | Out-Null
& adb pull "$tmp/poplar_log.raw" $raw | Out-Null
RootSh "rm -f $tmp/poplar_log.raw" | Out-Null

if ($What -eq 'shim') {
    [byte[]]$b = @()
    if (Test-Path $raw) {
        $b = [byte[]]([IO.File]::ReadAllBytes($raw) | Where-Object { $_ -ne 0 })
        Remove-Item $raw
    }
    if (-not $b -or $b.Length -eq 0) {
        Write-Host "UFS scratch empty; checking /sys/fs/pstore/console-ramoops-0 ..."
        $pstore = (RootSh 'cat /sys/fs/pstore/console-ramoops* 2>/dev/null') -join "`n"
        if ($pstore) {
            [IO.File]::WriteAllText($Out, $pstore)
            Get-Content $Out
            return
        }
        Write-Host "(No non-zero bytes found in UFS scratch or pstore)"
        return
    }
    [IO.File]::WriteAllBytes($Out, $b)
    Get-Content $Out
    Write-Host "`nSaved to $Out"
} elseif ($What -eq 'rescue') {
    & python -c "import struct,sys; r=open(sys.argv[1],'rb').read(); open(sys.argv[2],'wb').write(r[4096:4096+struct.unpack_from('<QI',r,8)[1]].replace(b'\0',b'') if r[:8]==b'SWEETRSC' else b'(no rescue)\n')" $raw $Out
    if (Test-Path $raw) { Remove-Item $raw }
    Get-Content $Out
} else {
    & python (Join-Path $PSScriptRoot 'sweet-log.py') $raw $Out
    if (Test-Path $raw) { Remove-Item $raw }
    if (Test-Path $Out) {
        Get-Content $Out -Tail 80
        Write-Host "`nSaved full XNU log to $Out"
    }
}
