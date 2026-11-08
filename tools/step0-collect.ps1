<#
.SYNOPSIS
  Step 0 of the port: back up the XZ1's irreplaceable partitions and collect the
  hardware description the later steps need. Read-only on the phone.

.DESCRIPTION
  -Mode fastboot   phone in fastboot (Vol-up + plug USB on Sony): saves
                   `fastboot getvar all` (bootloader version, unlock state,
                   slot info, max-download-size).
  -Mode adb        phone in TWRP (adb is root there) or rooted Android:
                   * dumps the per-device partitions (TA, persist, modemst1/2,
                     fsg, fsc, boot, ...) into backup\
                   * pulls the live FDT (/sys/firmware/fdt) into dtb\live.dtb
                   * saves cpuinfo, MIDRs, iomem, kernel cmdline, dmesg and the
                     partition table into dtb\
                   * runs tools\poplar-fdt-summary.py on the FDT

  Nothing is written to the phone except temporary files in /tmp (TWRP) or
  /data/local/tmp (Android), which are removed again.
  backup\ and dtb\ hold your IMEI, serials and calibration data: keep them
  private and out of git (.gitignore already excludes them).

.EXAMPLE
  .\tools\step0-collect.ps1 -Mode fastboot
  .\tools\step0-collect.ps1 -Mode adb
  .\tools\step0-collect.ps1 -Mode adb -All     # every partition except the big OS/user ones
#>
param(
    [ValidateSet('fastboot', 'adb')][string]$Mode = 'adb',
    [switch]$All
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backup = Join-Path $root 'backup'
$dtb = Join-Path $root 'dtb'
New-Item -ItemType Directory -Force $backup, $dtb | Out-Null

function Need($exe) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
        throw "$exe not found. Install Android platform-tools and add it to PATH."
    }
}

if ($Mode -eq 'fastboot') {
    Need fastboot
    $out = Join-Path $backup 'fastboot-getvar-all.txt'
    # fastboot prints getvar results on stderr
    & cmd /c "fastboot getvar all 2>&1" | Tee-Object -FilePath $out
    Write-Host "`nSaved $out"
    Write-Host 'Check: unlocked = yes, and note the bootloader version and max-download-size.'
    return
}

Need adb
$state = (& adb get-state 2>&1) -join ''
if ($state -notmatch 'device|recovery') { throw "No adb device (state: $state). Boot TWRP or Android with USB debugging." }

$uid = ((& adb shell id -u) -join '').Trim()
$su = if ($uid -eq '0') { '' } else { 'su -c ' }
$tmp = if (((& adb shell 'test -d /tmp && echo y') -join '').Trim() -eq 'y' -and $uid -eq '0') { '/tmp' } else { '/data/local/tmp' }
Write-Host "adb uid=$uid, using '$su' for root, temp dir $tmp"
if ($uid -ne '0' -and ((& adb shell 'su -c id -u' 2>&1) -join '').Trim() -ne '0') {
    throw 'Need root: boot TWRP (fastboot boot twrp.img) or use a rooted Android.'
}

function RootSh([string]$cmd) {
    if ($su) { & adb shell "su -c '$cmd'" } else { & adb shell $cmd }
}

function PullRoot([string]$src, [string]$dst) {
    $name = [IO.Path]::GetFileName($dst)
    RootSh "cat $src > $tmp/$name && chmod 644 $tmp/$name" | Out-Null
    & adb pull "$tmp/$name" $dst | Out-Null
    RootSh "rm -f $tmp/$name" | Out-Null
}

# ---- partition table
$byname = '/dev/block/bootdevice/by-name'
$parts = (RootSh "ls $byname") -split '\s+' | Where-Object { $_ }
RootSh "ls -l $byname" | Set-Content (Join-Path $dtb 'partitions.txt')
Write-Host "Partitions: $($parts -join ' ')"

# Per-device data first. TA holds Sony's DRM keys and device calibration.
$wanted = @('TA', 'persist', 'modemst1', 'modemst2', 'fsg', 'fsc', 'ssd', 'misc',
            'boot', 'boot_a', 'boot_b', 'dtbo', 'dtbo_a', 'dtbo_b', 'vbmeta', 'vbmeta_a', 'vbmeta_b',
            'Qnovo', 'diag', 'apps_log', 'LTALabel')
if ($All) {
    $skip = '^(userdata|system.*|vendor.*|oem.*|cache)$'
    $wanted = $parts | Where-Object { $_ -notmatch $skip }
}
foreach ($p in $wanted) {
    if ($parts -notcontains $p) { continue }
    $dst = Join-Path $backup "$p.img"
    if (Test-Path $dst) { Write-Host "  $p exists, skipped"; continue }
    Write-Host "  dumping $p"
    RootSh "dd if=$byname/$p of=$tmp/$p.img bs=1048576 2>/dev/null && chmod 644 $tmp/$p.img" | Out-Null
    & adb pull "$tmp/$p.img" $dst | Out-Null
    RootSh "rm -f $tmp/$p.img" | Out-Null
}
Get-ChildItem $backup -Filter *.img | Get-FileHash -Algorithm SHA256 |
    ForEach-Object { "$($_.Hash)  $([IO.Path]::GetFileName($_.Path))" } |
    Set-Content (Join-Path $backup 'SHA256SUMS.txt')

# ---- hardware description
PullRoot '/sys/firmware/fdt' (Join-Path $dtb 'live.dtb')
RootSh 'cat /proc/cpuinfo' | Set-Content (Join-Path $dtb 'cpuinfo.txt')
RootSh 'for c in /sys/devices/system/cpu/cpu[0-9]*; do echo "$c $(cat $c/regs/identification/midr_el1 2>/dev/null)"; done' |
    Set-Content (Join-Path $dtb 'midr.txt')
RootSh 'cat /proc/iomem' | Set-Content (Join-Path $dtb 'iomem.txt')
RootSh 'cat /proc/cmdline' | Set-Content (Join-Path $dtb 'cmdline.txt')
RootSh 'dmesg' | Set-Content (Join-Path $dtb 'dmesg.txt')
RootSh 'cat /proc/version; getprop ro.product.device; getprop ro.build.fingerprint; getprop ro.boot.bootloader' |
    Set-Content (Join-Path $dtb 'version.txt')

Write-Host "`nSaved partitions to $backup and hardware info to $dtb"
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    $bootImg = Get-ChildItem $backup -Filter 'boot*.img' | Select-Object -First 1
    if ($bootImg) {
        $stockDir = Join-Path $root 'stock'
        Write-Host "`nUnpacking $($bootImg.Name) into $stockDir ..."
        & python (Join-Path $PSScriptRoot 'unpack_bootv2.py') $bootImg.FullName $stockDir
    }
    & python (Join-Path $PSScriptRoot 'poplar-fdt-summary.py') (Join-Path $dtb 'live.dtb') |
        Tee-Object -FilePath (Join-Path $dtb 'summary.txt')
}
Write-Host "`nCopy backup\ somewhere off this PC as well. Then send dtb\summary.txt, dtb\cmdline.txt and fastboot-getvar-all.txt for the next step."
