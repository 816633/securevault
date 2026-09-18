# 打包脚本：生成 dist\SecureVault\SecureVault.exe（正式交付）
#
# 用法：  .\build.ps1
#
# 需要本机有 Python 3.8+ 与 PyInstaller。
#
# 为什么只做文件夹版（不打单文件 exe）：本机 PyInstaller 3.6 + Python 3.8 打出来的
# 单文件 exe，Tcl 初始化会稳定失败（报 Can't find a usable init.tcl）。已经用
# `-diag` 逐层定位过：解压目录里 tcl/init.tcl 与 tcl/encoding 都在、内容也对，
# 但 Tcl_Init 仍然返回错误—— PyInstaller 3.6 的引导程序与 Tcl 8.6 在单文件模式下
# 不兼容。文件夹版把 Tcl/Tk 数据放在程序目录里，实测稳定、启动更快，也完全不往
# C 盘写临时文件；整个文件夹复制到任何 Windows 电脑上都能直接跑。

$ErrorActionPreference = "Stop"
# 让原生命令（PyInstaller）往 stderr 写警告时不至于中断整个脚本
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$root = $PSScriptRoot
Set-Location $root

# 找一个**装了 PyInstaller** 的 Python（PATH 上的 python 可能是 Anaconda 之类没装 PyInstaller 的）
function Test-HasPyInstaller($exe) {
    try {
        & $exe -c "import PyInstaller" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$python = ""
$candidates = @()
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd) { $candidates += $cmd.Source }
$candidates += @("C:\Program Files\python\python.exe", "python")
foreach ($candidate in $candidates) {
    if ($candidate -eq "python") { continue }
    if ((Test-Path $candidate) -and (Test-HasPyInstaller $candidate)) {
        $python = $candidate
        break
    }
}
if (-not $python) { throw "找不到带 PyInstaller 的 python.exe（试过：$($candidates -join ', ')）" }
Write-Host "使用 Python：$python"

$assets = (Resolve-Path .\assets).Path
$icon = (Resolve-Path .\assets\securevault.ico).Path

# PyInstaller 3.6 在 Python 3.8 下会因缓存目录已存在而报错，指到干净目录避免。
$env:PYINSTALLER_CONFIG_DIR = Join-Path $root ".build\pyi-config"
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR | Out-Null

$excludes = @(
    "--exclude-module", "bcrypt",
    "--exclude-module", "cffi",
    "--exclude-module", "cryptography",
    "--exclude-module", "lib2to3",
    "--exclude-module", "pydoc_data",
    "--exclude-module", "distutils",
    "--exclude-module", "setuptools",
    "--exclude-module", "pip",
    "--exclude-module", "test",
    "--exclude-module", "tkinter.test",
    "--exclude-module", "unittest.test",
    "--exclude-module", "PIL",
    "--exclude-module", "numpy",
    "--exclude-module", "pandas",
    "--exclude-module", "matplotlib"
)

Write-Host "== 1/3 打包程序（文件夹版）=="
$pyArgs = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
    "--name", "SecureVault",
    "--icon", $icon,
    "--add-data", "$assets;assets"
) + $excludes + @(
    "--distpath", ".build\dist-dir",
    "--workpath", ".build\work-onedir",
    "--specpath", ".build",
    "main.py"
)
Write-Host ("  " + $python + " " + ($pyArgs -join " "))
& $python @pyArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败（退出码 $LASTEXITCODE）" }

Write-Host "== 2/3 整理输出 =="
$target = Join-Path $root "dist\SecureVault"
if (Test-Path $target) { Remove-Item -LiteralPath $target -Recurse -Force }
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Path ".build\dist-dir\SecureVault\*" -Destination $target -Recurse -Force

Write-Host "== 3/3 附上说明 =="
$readme = @"
SecureVault v2.4 —— U 盘自动复制与监控工具

用法：把整个 SecureVault 文件夹复制到一个有写权限的位置，双击里面的
      SecureVault.exe 即可开始使用（首次启动会要求设置至少 8 位主密码，
      并显示一次恢复码，请务必保存）。

* SecureVault.exe        程序本体
* 其它 dll / tcl / tk 等   程序运行需要的库（不要删除、不要单独把 exe 拿走）

数据全部写在 SecureVaultData 子文件夹里，不会写到 C 盘其它位置。
关闭窗口 = 收进托盘并上锁；托盘图标右键「退出」才是彻底退出程序。
解锁/首次设置是老式小弹窗（带"显示密码"），解锁之前不会显示软件名；点标题条上的版本号可以打开项目主页
https://github.com/816633/securevault 。

详细说明见上级目录的 README.md 与 docs\使用手册.md。
"@
Set-Content -LiteralPath (Join-Path $target "请先读我.txt") -Value $readme -Encoding UTF8

Get-ChildItem .\dist | Select-Object Name, Length | Format-Table -AutoSize
Write-Host "打包完成。"
