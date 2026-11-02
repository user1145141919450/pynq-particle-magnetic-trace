# verify_sd.ps1 — 把 SD 卡上的文件和 sd_backup\ 逐个 md5 比对
#
# 用途有两个：
#   1) 烧录前/失败后：确认卡上内容没被动过（flash_sd.ps1 中止时说"一个字节都没写"，用它证实）
#   2) 烧录后：确认镜像真的写进去了（对得上才算成功，不能只看进度条）
#
# 用法（普通窗口，不需要管理员）：
#   powershell -NoProfile -File E:\pynq-magnetic-field-demo\tools\verify_sd.ps1
#   powershell -NoProfile -File E:\pynq-magnetic-field-demo\tools\verify_sd.ps1 -Card H:\

param(
    [string]$Card = 'G:\',
    [string]$Backup = 'E:\pynq-magnetic-field-demo\sd_backup'
)

$skip = @('System Volume Information', '$RECYCLE.BIN', 'FOUND.000')

function HashDir([string]$root) {
    $h = @{}
    Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length).TrimStart('\')
        if ($skip | Where-Object { $rel -like ($_ + '*') }) { return }
        try {
            $h[$rel] = (Get-FileHash -LiteralPath $_.FullName -Algorithm MD5 -ErrorAction Stop).Hash
        } catch {
            $h[$rel] = 'ERR:' + $_.Exception.Message
        }
    }
    return $h
}

Write-Output "卡  : $Card"
Write-Output "备份: $Backup"
if (-not (Test-Path -LiteralPath $Card))   { Write-Output "!! 卡的盘符不存在：$Card"; exit 1 }
if (-not (Test-Path -LiteralPath $Backup)) { Write-Output "!! 备份目录不存在：$Backup"; exit 1 }

$c = HashDir $Card
$b = HashDir $Backup

$onlyBackup = @($b.Keys | Where-Object { -not $c.ContainsKey($_) })
$onlyCard   = @($c.Keys | Where-Object { -not $b.ContainsKey($_) })
$diff       = @($b.Keys | Where-Object { $c.ContainsKey($_) -and $b[$_] -ne $c[$_] })

Write-Output ""
Write-Output ("卡上文件数   : {0}" -f $c.Count)
Write-Output ("备份文件数   : {0}" -f $b.Count)
Write-Output ""
if ($onlyBackup.Count) {
    Write-Output ("只在备份里有（卡上缺）: {0} 个" -f $onlyBackup.Count)
    $onlyBackup | ForEach-Object { Write-Output ("   - {0}" -f $_) }
}
if ($diff.Count) {
    Write-Output ("内容不一样: {0} 个" -f $diff.Count)
    $diff | ForEach-Object { Write-Output ("   ! {0}" -f $_) }
}
if ($onlyCard.Count) {
    Write-Output ("只在卡上有（备份里没有，正常，比如系统文件）: {0} 个" -f $onlyCard.Count)
    $onlyCard | Select-Object -First 20 | ForEach-Object { Write-Output ("   + {0}" -f $_) }
}
Write-Output ""
if ($onlyBackup.Count -eq 0 -and $diff.Count -eq 0) {
    Write-Output "★★ 完全一致：备份里的每个文件都在卡上，且内容逐个 md5 相同 ★★"
} else {
    Write-Output "!! 有不一致 —— 看上面的清单"
}
