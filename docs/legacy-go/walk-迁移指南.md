# 改用 lxn/walk 需要什么（离线环境专用说明）

> 结论先说：**当前这台机器上做不到**，缺两样东西 —— `lxn/walk` 的源码，以及一个 C 编译器。
> 本文给出补齐这两样的具体做法；补齐后我可以把 UI 层换成 walk。

---

## 1. 为什么 walk 必须 cgo（不是我的选择）

`github.com/lxn/walk` 是一个 **cgo 包**：

- 它直接通过 `import "C"` 调用 Win32 API，源码里带 `.c` / `.h` 文件（`walk.go` 等有 cgo 指令）；
- 它依赖 `github.com/lxn/win`，那也是 cgo 包；
- 因此构建它必须 `CGO_ENABLED=1` **且**本机有一个可用的 C 编译器（MSVC `cl.exe` 或 MinGW-w64 `gcc`）。

Go 工具链在 `CGO_ENABLED=0` 时会直接报错：

```
build constraints exclude all Go files in ...\lxn\walk
# 或者
cgo: C compiler "gcc" not found
```

所以「用 walk 但不需要 C 编译」在技术上不成立 —— 必须补 C 编译器。

### 本机实测（2026-09-17 复核）

| 检查 | 结果 |
|---|---|
| `curl https://proxy.golang.org/github.com/lxn/walk/@v/list` | `000`（TCP 超时） |
| `curl https://goproxy.cn/...` | `000` |
| `curl https://github.com` | `000` |
| 模块缓存里有无 walk | 无（`GOMODCACHE` 下没有 `lxn`） |
| `gcc` / `clang` / `cl.exe` / `x86_64-w64-mingw32-gcc` / `zig` | 全部未找到 |
| `go env CGO_ENABLED` | `0` |

---

## 2. 需要你做的两件事

### 第 1 件：把 walk 依赖下载成本机离线模块代理

在**有网**的机器上（或本机网络恢复后）执行：

```powershell
pwsh -File .\scripts\download-walk.ps1 -Out D:\walk-offline
```

脚本会把 `lxn/walk` 及其全部传递依赖抓下来，按 Go 模块代理的目录结构存成
`D:\walk-offline\cache\download\...`。之后把这个目录拷到本机（或在原机执行完把整个目录带过来），
我就能用：

```powershell
$env:GOPROXY = "file:///D:/walk-offline/cache/download"
```

离线拉取 walk。

### 第 2 件：装一个 C 编译器（二选一）

**方案 A：MinGW-w64（推荐，免安装、绿色）**

1. 打开 https://winlibs.com/
2. 下载 **UCRT runtime / x86_64 / MSVCRT 或 UCRT 皆可** 的
   `winlibs-x86_64-posix-seh-gcc-*-mingw-w64ucrt-*.7z`（约 60–120 MB）
3. 解压到例如 `D:\mingw64`
4. 把 `D:\mingw64\bin` 加进 `PATH`

```powershell
$env:Path = "D:\mingw64\bin;$env:Path"
gcc --version          # 能打印版本就成功了
go env -w CGO_ENABLED=1
```

**方案 B：MSVC（体积大但官方）**

装 Visual Studio Build Tools 的「使用 C++ 的桌面开发」工作负载（含 MSVC v143 + Windows SDK），
然后在 **x64 Native Tools Command Prompt** 里构建（它会自动设好 `cl.exe` 的环境）。

---

## 3. 补齐之后我会怎么改

现有代码已经**为这次替换做好了分层**，改动是可控的：

```
internal/app/page_*.go      ← 只负责"有哪些控件、什么事件"（保留，稍作调整）
internal/ui/               ← 自研控件层（会被替换/旁路掉）
internal/ui/win/           ← Win32 绑定（walk 自带 lxn/win，可保留用于托盘/设备等非 UI 部分）
internal/{copier,store,keystore,presence,engine,logger,...}  ← 完全不动
```

具体步骤：

1. `go.mod` 增加 `github.com/lxn/walk` 与 `github.com/lxn/win`，`go mod tidy`（走离线代理）；
2. 新增 `internal/ui2/`（walk 实现），把 `internal/app/page_*.go` 的控件构造改成 walk 的
   `walk.MainWindow` / `walk.TabWidget` / `walk.TableView` / `walk.LineEdit` / `walk.PushButton` …；
   `Declarative` 或手写 `Create()` 都可以，我用声明式（更短、更少排版事故）；
3. 托盘继续用 `walk.NewNotifyIcon()`（walk 自带，不需要我自研的 `Shell_NotifyIcon` 封装）；
4. **替换的最大收益**：walk 用**对话框单位（DLU）+ 声明式布局**，天然解决"文字挤在一起、
   按钮跑到窗口外"这一类问题 —— 那正是你这次遇到的核心痛点；
5. 保留 `-autotest` 与 `scripts\smoke`，改成对 walk 窗口做同样的断言（控件存在性 + 尺寸/包含关系）；
6. 全量回归：`gofmt` / `go vet` / `go test ./internal/...` / `smoke` / `-autotest` / 整屏截图给你验收。

**风险与代价（如实说明）**

- exe 体积会变大（walk 会带进更多代码），启动内存略增；
- `CGO_ENABLED=1` 之后不再是"纯 Go 单文件"——但仍是不依赖外部 DLL 的单文件 exe
  （MinGW 静态链接即可），只是构建需要 C 编译器；
- 高 DPI：walk 对 **Per-Monitor V2** 的支持取决于其清单与 `lxn/win` 的版本，可能需要回退到
  System DPI 感知（那样在多显示器不同缩放时不如现在精确）。这一点要等我拿到 walk 源码后实测确认。

---

## 4. 在补齐之前，我已经修好的东西

即使不换 walk，这一轮已经解决了你列出的可用性问题（都与 UI 库无关）：

| 你的反馈 | 处理 |
|---|---|
| 双击就弹窗口 | 改成：**只出托盘图标，不显示窗口**；只有「还没设过密码」时才弹设置密码窗口 |
| 未设密码时无法退出 | 未初始化状态下「退出」**直接退出**，不再要求输密码 |
| 单击托盘出现两个窗口 | 主面板去掉了 `WS_EX_APPWINDOW`、改加 `WS_EX_TOOLWINDOW`，不再额外占一个任务栏/Alt+Tab 条目 |
| 右键托盘菜单项太多 | 右键**只保留「退出」**一项；打开面板用左键单击 |
| 进去之后什么都没有 | 修掉**页面容器盖住标签控件**的 Z 序问题（每次切页把标签控件提到最前） |
| 文字遮挡、按钮错位、用不了 | 修掉**DPI 双重缩放**与**窗口尺寸≠客户区尺寸**两个根因：现在窗口按客户区尺寸创建、所有布局走 DIP + `ui.K()` 单点缩放、对话框用 `ui.Stack` 流式排版 |
| 截屏只截一部分 | `scripts\shot` 改成**默认整屏截图**（可加 `--only` 只截窗口） |

配套的自动断言也从 24 项增加到 **31 项**，新增了"控件必须在页面可视范围内""不允许零尺寸可见控件"
这两条 —— 以后这类排版事故会被自动挡住。

---

## 5. 如果你暂时不方便装编译器

可选折中：**用我现在的自研 Win32 层继续打磨外观**（不依赖任何外部库、保持 `CGO_ENABLED=0` 单文件）。
想走这条路就告诉我，我会按 Fluent 风格继续做：卡片式布局、系统强调色、更合理的字号层级、
列表控件视觉样式、深浅色主题跟随等。
