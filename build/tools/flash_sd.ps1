# flash_sd.ps1 — 把 PYNQ-Z2 镜像写回 SD 卡（需要管理员，脚本自己会检查）
#
# 为什么有它：2026-09-26 板子完全不启动 —— 电源灯亮、别的灯不亮、串口一个字节都没有。
#   （Zynq 的 BootROM 读不到 BOOT.BIN 时就是这样完全静默，连一个字都不吐。）
#   供电 12V/3A、JP5=REG、JP4=SD 都确认过；SD 卡内容在 PC 上读得出来、
#   BOOT.BIN 头的 0xAA995566 + "XLNX" 也都在 —— 全排除完之后，剩下最可能就是
#   卡本身（或卡槽接触），所以重烧一张干净的卡，回到已知良好状态。
#   卡里 FAT 分区的东西已经全部备份在 E:\pynq-magnetic-field-demo\sd_backup\，不会丢。
#
# 用法（普通窗口跑就行，脚本会检查权限）：
#   powershell -NoProfile -File E:\pynq-magnetic-field-demo\tools\flash_sd.ps1
#
# 四道安全闸（任何一条不满足就立刻退出，绝不写入）：
#   1) 目标盘总线必须是 USB       —— 系统盘是 NVMe，物理上写不进去
#   2) 目标盘不能是系统盘/启动盘
#   3) 目标盘容量必须 >= 镜像大小
#   4) 镜像开头必须有 MBR 签名 55 AA
#
# 日志：E:\pynq-magnetic-field-demo\tools\flash_sd.log（跑的时候可以随时看进度）

param(
    [string]$Image = "E:\pynq-magnetic-field-demo\pynq_z2_v3.1.img",
    [int]$DiskNumber = 1,
    [int]$ChunkMB = 4
)

$log = "E:\pynq-magnetic-field-demo\tools\flash_sd.log"

function Say([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}
function Die([string]$m) {
    Say "!! $m"
    Say "=== 已中止：一个字节都没写 ==="
    Start-Sleep -Seconds 20
    exit 1
}

Set-Content -Path $log -Value "=== flash_sd.ps1 ===" -Encoding UTF8

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Die "没有管理员权限 —— 请用管理员身份运行" }
Say "管理员权限 OK"

# ---------- 闸 4：镜像本身 ----------
if (-not (Test-Path -LiteralPath $Image)) { Die "找不到镜像：$Image" }
$fi = Get-Item -LiteralPath $Image
if ($fi.Length -lt 1GB) { Die ("镜像太小（{0:N1} MB），不像整卡镜像" -f ($fi.Length / 1MB)) }
Say ("镜像: {0}" -f $Image)
Say ("大小: {0:N0} 字节 = {1:N2} GB" -f $fi.Length, ($fi.Length / 1GB))

$head = New-Object byte[] 512
$fsHead = [System.IO.File]::OpenRead($Image)
[void]$fsHead.Read($head, 0, 512)
$fsHead.Close()
if ($head[510] -ne 0x55 -or $head[511] -ne 0xAA) { Die "镜像开头没有 MBR 签名 55 AA，不确定是可引导整卡镜像" }
Say "镜像 MBR 签名 55 AA OK"

# ---------- 闸 1/2/3：目标盘 ----------
$d = Get-Disk -Number $DiskNumber -ErrorAction SilentlyContinue
if (-not $d) { Die "没有物理磁盘 $DiskNumber" }

Say ""
Say "================ 目标盘（看清楚再往下）================"
Say ("  磁盘号 : {0}" -f $d.Number)
Say ("  型号   : {0}" -f $d.FriendlyName)
Say ("  总线   : {0}" -f $d.BusType)
Say ("  容量   : {0:N2} GB" -f ($d.Size / 1GB))
Say ("  序列号 : {0}" -f $d.SerialNumber)
Say ("  系统盘 : {0}    启动盘 : {1}" -f $d.IsSystem, $d.IsBoot)
foreach ($p in @(Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue)) {
    $v = Get-Volume -Partition $p -ErrorAction SilentlyContinue
    Say ("  分区{0}: 盘符 {1}  卷标 {2}  文件系统 {3}  大小 {4:N2} GB" -f $p.PartitionNumber, $v.DriveLetter, $v.FileSystemLabel, $v.FileSystem, ($p.Size / 1GB))
}
Say "======================================================"

if ($d.BusType -ne 'USB')   { Die ("目标盘总线是 {0}，不是 USB —— 这多半不是 SD 卡，停" -f $d.BusType) }
if ($d.IsSystem)            { Die "目标盘是系统盘 —— 停" }
if ($d.IsBoot)              { Die "目标盘是启动盘 —— 停" }
if ($d.Size -lt $fi.Length) { Die ("目标盘装不下镜像（{0:N2} GB < {1:N2} GB）" -f ($d.Size / 1GB), ($fi.Length / 1GB)) }

Say "四道安全闸全过 → 10 秒后开始写入（想撤销就关窗口）"
Start-Sleep -Seconds 10

# ---------- 写入 ----------
# ★ 可移动盘不能用 Set-Disk 下线（Windows 直接回 "Removable media cannot be set to offline"），
#   所以改用「卸载卷」让 Windows 松开占用 —— 这正是 Rufus / dd 内部做的事。
Say "卸载目标盘的卷，让 Windows 松开占用…"
foreach ($p in @(Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue)) {
    $v = Get-Volume -Partition $p -ErrorAction SilentlyContinue
    if (-not $v -or -not $v.DriveLetter) { continue }
    $letter = "{0}:" -f $v.DriveLetter
    Say ("  卸载 {0}（{1}）…" -f $letter, $v.FileSystemLabel)
    try {
        $wv = Get-CimInstance -ClassName Win32_Volume -Filter ("DriveLetter='{0}'" -f $letter) -ErrorAction Stop
        if ($wv) {
            $r = Invoke-CimMethod -InputObject $wv -MethodName Dismount -Arguments @{ Force = $true; Permanent = $false } -ErrorAction Stop
            Say ("    WMI Dismount 返回码 {0}（0=成功）" -f $r.ReturnValue)
        }
    } catch { Say ("    WMI Dismount 不行：" + $_.Exception.Message) }
    if (Test-Path $letter) {                        # 还挂着就走 mountvol 兜底
        $out = & mountvol $letter /p 2>&1
        Say ("    mountvol /p 返回码 {0} {1}" -f $LASTEXITCODE, ($out -join " "))
    }
    if (Test-Path $letter) { Say "    ⚠ 卷还挂着 —— 把资源管理器里开着的这个盘关掉再跑" }
    else { Say "    已卸载" }
}

$src = $null
$dst = $null
try {
    $src = [System.IO.File]::OpenRead($Image)
    $dst = New-Object System.IO.FileStream -ArgumentList @(
        ("\\.\PhysicalDrive{0}" -f $DiskNumber),
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
} catch {
    if ($src) { $src.Close() }
    Die ("打开裸设备失败：" + $_.Exception.Message + "  —— 多半是卷还被占着，先把资源管理器里开着的盘关掉再跑")
}

$buf = New-Object byte[] ($ChunkMB * 1MB)
$total = $fi.Length
$done = 0
$nextReport = 256MB
$sw = [System.Diagnostics.Stopwatch]::StartNew()
Say ("开始写 {0:N2} GB …" -f ($total / 1GB))

$failed = $false
try {
    while ($true) {
        $n = $src.Read($buf, 0, $buf.Length)
        if ($n -le 0) { break }
        if (($n % 512) -ne 0) {                     # 裸设备只吃整扇区，尾巴补 0
            $pad = 512 - ($n % 512)
            for ($i = 0; $i -lt $pad; $i++) { $buf[$n + $i] = 0 }
            $n += $pad
        }
        $dst.Write($buf, 0, $n)
        $done += $n
        if ($done -ge $nextReport) {
            $spd = ($done / 1MB) / [math]::Max($sw.Elapsed.TotalSeconds, 0.001)
            $eta = ($total - $done) / 1MB / [math]::Max($spd, 0.001)
            Say ("  已写 {0:N2}/{1:N2} GB   {2:N1} MB/s   约剩 {3:N0} 秒" -f ($done / 1GB), ($total / 1GB), $spd, $eta)
            $nextReport += 256MB
        }
    }
    $dst.Flush($true)
} catch {
    $failed = $true
    Say ("写入过程中出错：" + $_.Exception.Message)
} finally {
    if ($dst) { $dst.Close() }
    if ($src) { $src.Close() }
}

if ($failed) { Die "写入没完成，卡现在状态未知 —— 重新跑一次本脚本" }

$spd = ($done / 1MB) / [math]::Max($sw.Elapsed.TotalSeconds, 0.001)
Say ("写完了：{0:N0} 字节，用时 {1:N0} 秒，平均 {2:N1} MB/s" -f $done, $sw.Elapsed.TotalSeconds, $spd)

# ---------- 回读校验：抽样比对（不能只写不验）----------
$CMP = 4096
$samples = 41
$imgBuf = New-Object byte[] $CMP
$devBuf = New-Object byte[] $CMP
$md5 = [System.Security.Cryptography.MD5]::Create()
$bad = 0
$rdi = $null
$rdd = $null
Say ("回读校验：在镜像上均匀抽 {0} 个点，和卡上同一位置比对…" -f $samples)
try {
    $rdd = New-Object System.IO.FileStream -ArgumentList @(
        ("\\.\PhysicalDrive{0}" -f $DiskNumber),
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::None
    )
    $rdi = [System.IO.File]::OpenRead($Image)
    for ($i = 0; $i -lt $samples; $i++) {
        $off = [int64](($total - $CMP) * $i / ($samples - 1))
        [void]$rdi.Seek($off, 'Begin')
        [void]$rdd.Seek($off, 'Begin')
        $ra = $rdi.Read($imgBuf, 0, $CMP)
        $rb = $rdd.Read($devBuf, 0, $CMP)
        if ($ra -ne $CMP -or $rb -ne $CMP) { $bad++; Say ("  偏移 {0:N0}: 读不满 {1} 字节" -f $off, $CMP); continue }
        $ha = [BitConverter]::ToString($md5.ComputeHash($imgBuf))
        $hb = [BitConverter]::ToString($md5.ComputeHash($devBuf))
        if ($ha -ne $hb) { $bad++; Say ("  偏移 {0:N0}: 不一致！" -f $off) }
    }
} catch {
    Say ("  回读出错：" + $_.Exception.Message)
    $bad = -1
} finally {
    if ($rdd) { $rdd.Close() }
    if ($rdi) { $rdi.Close() }
    $md5.Dispose()
}
if ($bad -eq 0)      { Say ("★ {0} 个抽样点全部一致 —— 卡写对了" -f $samples) }
elseif ($bad -gt 0)  { Die ("有 {0} 个抽样点不一致 —— 别插回板子，重烧一次" -f $bad) }
else                 { Say "!! 回读没做成，无法确认，建议重烧一次" }

# ---------- 让卡重新挂出来（不影响卡上内容）----------
Say "让卡重新挂出来…"
Update-HostStorageCache
Start-Sleep -Seconds 6
$rem = @(Get-Volume | Where-Object { $_.DriveType -eq 'Removable' })
if ($rem.Count -gt 0) {
    foreach ($v in $rem) {
        Say ("  可移动卷 {0}: 卷标 {1}  文件系统 {2}  大小 {3:N2} GB" -f $v.DriveLetter, $v.FileSystemLabel, $v.FileSystem, ($v.Size / 1GB))
    }
} else {
    Say "  没有自动挂出来 —— 把读卡器拔了重插一下就行（不影响卡上内容）"
}
Say "=== 烧录结束 ==="
Start-Sleep -Seconds 20
