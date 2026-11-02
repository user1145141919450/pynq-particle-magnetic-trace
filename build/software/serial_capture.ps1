# serial_capture.ps1 — 抓 PYNQ-Z2 的串口启动日志（不用装 SSCOM）
#
# 为什么要它：2026-09-25/26 板子表现为"电源灯亮、其他灯不亮、PC 侧以太网链路能协商到
# 100Mbps、但 Linux 完全不应答（全段 ARP 零应答）"。这一组症状最像 **PS 根本没启动成功**
# （SD 卡没读到 / 启动模式跳线不对 / 内核挂了），而这些在网口上是看不出来的
# —— 只有串口控制台能一眼看到卡在哪一行。
#
# 用法（PC 上，Git Bash 或 PowerShell 都行）：
#   # 1) 先看有哪些串口
#   powershell -NoProfile -File E:/pynq-magnetic-field-demo/software/serial_capture.ps1
#   # 2) 插好板子上的 Micro-USB（丝印 UART 的那个口），再列一次，找到新出现的 COM 口
#   # 3) 开抓（先开抓、再给板子断电重上电，才能抓到完整的启动日志）
#   powershell -NoProfile -File E:/pynq-magnetic-field-demo/software/serial_capture.ps1 -Port COM13 -Seconds 40
#
# 参数：PYNQ-Z2 串口固定 115200 8N1（无流控）

param(
    [string]$Port = "",
    [int]$Seconds = 40
)

# ★ 本文件必须存成 **UTF-8 with BOM**：Windows PowerShell 5.1 读 .ps1 时，
#   没有 BOM 就按系统 ANSI(GBK) 解码，中文注释会把语法解析撑坏（报一堆
#   "MissingEndCurlyBrace"）。加 BOM 的方法见文件末尾注释。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $Port) {
    $ports = [System.IO.Ports.SerialPort]::GetPortNames()
    if ($ports.Count -eq 0) {
        Write-Output "当前没有任何串口 —— 板子的 Micro-USB(UART) 线还没插"
    } else {
        Write-Output ("当前可用串口: " + ($ports -join ", "))
        Write-Output "（板子枚举出来的通常是 CP210x -> COM 口；本板历史上是 COM13）"
    }
    exit 0
}

try {
    $sp = New-Object System.IO.Ports.SerialPort $Port, 115200, 'None', 8, 'One'
} catch {
    Write-Output ("打不开 $Port : " + $_.Exception.Message)
    exit 1
}

$sp.ReadTimeout = 500
# 注意：**故意不去动 DTR/RTS**（保持默认 false）。PYNQ-Z2 的串口只用 TX/RX/GND，
# 但有些板子把 DTR/RTS 接到复位电路上 —— 不碰它就不可能因为我们抓日志而把板子按住复位。

try {
    $sp.Open()
} catch {
    Write-Output ("打开 $Port 失败（是不是被 SSCOM/PuTTY 占着？）：" + $_.Exception.Message)
    exit 1
}

Write-Output "======================================================"
Write-Output "已打开 $Port @ 115200 8N1，开始抓 $Seconds 秒"
Write-Output "★ 现在给板子断电 -> 等 3 秒 -> 重新上电，才能抓到完整启动日志"
Write-Output "======================================================"

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$buf = ""
while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
    try {
        $chunk = $sp.ReadExisting()
        if ($chunk.Length -gt 0) { $buf += $chunk }
    } catch { }
    if ($buf.Length -gt 0) {
        Write-Output -NoNewline $buf
        $buf = ""
    }
    Start-Sleep -Milliseconds 80
}
if ($buf.Length -gt 0) { Write-Output -NoNewline $buf }
$sp.Close()

Write-Output ""
Write-Output "======================================================"
Write-Output "抓取结束（$Seconds 秒）"
Write-Output "判读："
Write-Output "  · 一个字都没有    -> PS 完全没跑（供电/SD/启动模式），或串口线/口不对"
Write-Output "  · 有 FSBL/u-boot  -> PS 起来了但后面卡住，看最后一行卡在哪"
Write-Output "  · 有 kernel 日志  -> 系统在启动，看它为什么没配网 / 有没有 sd 卡报错"
Write-Output "======================================================"
