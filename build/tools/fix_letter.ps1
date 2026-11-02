# fix_letter.ps1 — 把 SD 卡 FAT 分区的盘符装回去（需要管理员）
#
# 背景：flash_sd.ps1 那一步为了让 Windows 松开占用，用 mountvol /p 把 G: 的挂载点摘了。
#   后来裸写失败中止，盘符没自动恢复 —— 现象就是"资源管理器里看不到这张卡了"。
#   其实磁盘和分区都好好的（Get-Disk 里 Mass Storage Device / Online / Healthy），
#   只是缺一个盘符。这个脚本把 G: 装回去。

param(
    [int]$DiskNumber = 1,
    [int]$PartitionNumber = 1,
    [string]$Letter = 'G'
)

$log = "E:\pynq-magnetic-field-demo\tools\fix_letter.log"
function Say([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

Set-Content -Path $log -Value "=== fix_letter.ps1 ===" -Encoding UTF8

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Say "需要管理员权限"; Start-Sleep 10; exit 1 }

$p = Get-Partition -DiskNumber $DiskNumber -PartitionNumber $PartitionNumber -ErrorAction SilentlyContinue
if (-not $p) { Say ("找不到 磁盘{0} 分区{1}" -f $DiskNumber, $PartitionNumber); Start-Sleep 15; exit 1 }
Say ("目标：磁盘{0} 分区{1}  大小 {2:N2} GB  类型 {3}" -f $DiskNumber, $PartitionNumber, ($p.Size / 1GB), $p.Type)

Add-PartitionAccessPath -DiskNumber $DiskNumber -PartitionNumber $PartitionNumber -AccessPath ($Letter + ':') -ErrorAction SilentlyContinue
Say ("已尝试把 {0}: 分配给 磁盘{1}分区{2}" -f $Letter, $DiskNumber, $PartitionNumber)

Update-HostStorageCache
Start-Sleep -Seconds 4

$v = Get-Volume -DriveLetter $Letter -ErrorAction SilentlyContinue
if ($v) {
    Say ("成功：{0}:  卷标={1}  文件系统={2}  大小={3:N2} GB  状态={4}" -f $Letter, $v.FileSystemLabel, $v.FileSystem, ($v.Size / 1GB), $v.OperationalStatus)
    Say ("根目录: {0}:" -f $Letter)
} else {
    Say "还是没挂上 —— 那就打开 磁盘管理(diskmgmt.msc) 手动右键那个 0.13GB 的 FAT 分区 -> 更改驱动器号"
}
Start-Sleep -Seconds 15
