# SecureVault 开发文档

> 面向维护者。本文只描述**代码里真实存在**的行为，所有函数签名均摘自源码。
>
> - 模块路径：`securevault`（`go.mod`：`go 1.24`）
> - 语言/约束：纯 Go，`CGO_ENABLED=0`，只依赖标准库 + `golang.org/x/sys/windows` + `modernc.org/sqlite`
> - 交付形态：单文件 `dist\SecureVault.exe`，无外部依赖，不写注册表
> - 接口契约的历史版本见 `internal/CONTRACTS.md`（部分签名在实现中已演进，以本文件与源码为准）

---

## 目录

1. [项目定位](#1-项目定位)
2. [总体架构](#2-总体架构)
3. [目录结构](#3-目录结构)
4. [各包职责与关键 API](#4-各包职责与关键-api)
5. [关键流程时序](#5-关键流程时序)
6. [关键实现细节](#6-关键实现细节)
7. [数据文件格式](#7-数据文件格式)
8. [构建与发布](#8-构建与发布)
9. [测试策略与怎么跑](#9-测试策略与怎么跑)
10. [扩展指南](#10-扩展指南)
11. [已知限制与技术债](#11-已知限制与技术债)

---

## 1. 项目定位

SecureVault 是一个 Windows 桌面端的「U 盘/移动硬盘监控 + 增量自动备份」工具，需求要点（见仓库根目录 `需求.md`）：

- 三种模式：**监控 / 复制 / 关闭**，支持 `HH:MM - HH:MM → 模式` 的定时切换（含跨午夜、按星期）。
- 打开程序**不显示窗口**，只驻留托盘；首次使用先设密码（≥8 位）→ 恢复码**只显示一次**。
- 关闭窗口＝收到托盘 + **立即上锁（界面）**；默认设置下后台监控/复制继续运行，彻底退出必须走托盘菜单并再次输密码。
- 复制、扫描、错误处理**全程静默**（不弹窗、不调用 cmd/powershell/robocopy）。
- **所有数据只写在 exe 同级目录**，SQLite 强加密存储。
- 复制用**增量**语义；排除规则覆盖十余种设备属性 + 文件/文件夹名通配符 + 扩展名白名单。
- 屏幕 DPI 自适应。

工程上的取舍（重要）：

- 需求原文希望 UI 用 `lxn/walk`，但构建机**无 C 编译器、无外网**（walk 必须 cgo + 联网），因此 UI 改为**自研纯 Go 原生 Win32 封装**（`internal/ui` + `internal/ui/win`），代码不 import walk。
- 因此 `go test -race` 不可用（`-race` 强制要求 cgo），并发安全靠设计（互斥锁/单连接/串行化）与并发用例覆盖。

---

## 2. 总体架构

```
                        ┌──────────────────────────────┐
                        │      cmd/securevault         │  参数解析 / 进程入口
                        │      main.go                 │  -silent -debug -review -help
                        └──────────────┬───────────────┘
                                       │ app.Options{ExePath,Silent,Debug}
                                       ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                            internal/app                               │
   │  单实例 · 托盘驻留 · 上锁/解锁 · 主面板(7 标签页) · 后台任务回投 UI 线程 │
   │  app.go auth.go ui.go page_*.go uithread.go review.go helpers.go util.go│
   └───┬───────────┬─────────────┬──────────────┬───────────────┬──────────┘
       │           │             │              │               │
       ▼           ▼             ▼              ▼               ▼
 ┌──────────┐ ┌─────────┐ ┌───────────┐ ┌────────────┐ ┌──────────────┐
 │ engine   │ │keystore │ │  store    │ │  logger    │ │  autostart   │
 │ 业务编排  │ │密码/恢复码│ │加密 SQLite│ │按天纯文本   │ │启动文件夹 .lnk│
 └─┬──┬──┬──┘ └─────────┘ └───────────┘ └────────────┘ └──────────────┘
   │  │  │
   │  │  └──────────────► presence  设备探测 + WM_DEVICECHANGE 监听
   │  └─────────────────► copier    增量复制引擎（扫描/过滤/并发/空间检查）
   └────────────────────► store.FileMark / Sink 接口（读写增量索引与规则）

        ┌──────────────────────────────────────────────────────────┐
        │  internal/ui   控件/窗口/标签页/状态栏/托盘/对话框（自研） │
        │  internal/ui/win  Win32 API 直调层（user32/gdi32/comctl32│
        │                   shell32/kernel32 + LazyProc 封装）      │
        └──────────────────────────────────────────────────────────┘
                     ▲ 被 app 使用（引擎与界面互不依赖）
   ┌───────────────────────────────────────────────────────────────────────┐
   │ 基础层：internal/model（纯数据结构/枚举/格式化）                        │
   │         internal/paths（exe 同级数据目录）  internal/fileutil（长路径、 │
   │         原子写、文件名净化、通配符、磁盘空间）                          │
   └───────────────────────────────────────────────────────────────────────┘
```

依赖方向（严格单向，无环）：

| 包 | 依赖 |
|---|---|
| `cmd/securevault` | `internal/app` |
| `internal/app` | `engine`、`keystore`、`store`、`logger`、`autostart`、`presence`、`copier`、`ui`、`ui/win`、`model`、`paths`、`fileutil` |
| `internal/engine` | `copier`、`presence`、`store`（仅 `FileMark` 类型与 `Sink` 接口签名）、`model`、`fileutil` |
| `internal/copier` | `model`、`fileutil` |
| `internal/store` | `model`、`fileutil`、`modernc.org/sqlite` |
| `internal/keystore` | `paths`、`fileutil` |
| `internal/presence` | `model`、`golang.org/x/sys/windows`、`syscall` |
| `internal/logger` | `paths`、`fileutil` |
| `internal/ui` | `ui/win` |
| `internal/ui/win` | `golang.org/x/sys/windows`、`syscall` |
| `internal/model` | 无（避免包循环） |

关键设计点：

- **引擎不依赖界面**：`engine.Events` 用回调把日志/状态/记录推给 app，app 再用 `PostMessage` 回到 UI 线程。
- **copier 不认识 store**：通过 `copier.Index` 接口注入增量索引，由 app/engine 适配（`markIndex`、`storeIndex`）。
- **app 是唯一的状态持有者**：`App` 里的 `ks/st/eng/ui/tray/msgw` 只在 UI 线程读写；后台线程通过 `app.postUI` 回到主线程。

---

## 3. 目录结构

```
auto-cy-file-2\
├─ cmd\securevault\main.go          进程入口（参数解析）
├─ internal\
│  ├─ CONTRACTS.md                  并行开发时的接口契约（历史文档）
│  ├─ app\                          业务编排 + 全部界面
│  │  ├─ app.go                     启动流程、托盘、上锁/退出、消息循环
│  │  ├─ auth.go                    首次设置 / 解锁 / 恢复码 / 改密确认 / 退出确认
│  │  ├─ ui.go                      主面板（标签页容器、状态栏、布局分发）
│  │  ├─ page_status.go             状态页
│  │  ├─ page_records.go            监控记录页（查询/详情/CSV/清除）
│  │  ├─ page_exclude.go            排除名单页（设备规则 + 文件名单）
│  │  ├─ page_schedule.go           定时切换页
│  │  ├─ page_tools.go              工具页（工具 1 桌面整理 + 工具 2 别名）+ 日志页
│  │  ├─ page_settings.go           设置页（含修改密码/轮换恢复码）
│  │  ├─ uithread.go                后台→UI 线程投递、工具 1 的复制任务
│  │  ├─ review.go                  -review 界面评审模式（演示数据）
│  │  ├─ helpers.go                 L() 缩放、时间/路径/枚举小工具
│  │  └─ util.go                    单实例激活消息注册
│  ├─ engine\engine.go              模式判定/定时/排除/命名/复制编排/状态快照
│  ├─ copier\copier.go             复制引擎（worker/重试/空间检查/索引）
│  ├─ copier\scan.go               扫描与过滤（含 ScanOnly、ShouldCopyFile）
│  ├─ copier\attrs_windows.go      隐藏/系统属性判定（Windows）
│  ├─ presence\presence.go         盘符枚举与单盘探测
│  ├─ presence\watch.go            WM_DEVICECHANGE 监听（隐藏窗口 + 去重）
│  ├─ presence\parse.go            设备路径/总线类型解析（纯逻辑，可单测）
│  ├─ store\store.go               加密 SQLite（字段级 AES-256-GCM + 盲索引）
│  ├─ keystore\keystore.go         密码/恢复码/主密钥（PBKDF2 + AES-GCM 包裹）
│  ├─ logger\logger.go             按天纯文本日志 + 保留策略
│  ├─ autostart\autostart.go       纯 Go 写 .lnk（启动文件夹）
│  ├─ ui\                          自研控件层（form/control/dialogs/tray/canvas…）
│  ├─ ui\win\                      Win32 API 直调层（api/funcs/gdi/menu/msgwin…）
│  ├─ paths\paths.go               数据目录（exe 同级）
│  ├─ fileutil\fileutil.go         长路径、原子写、净化、通配符、磁盘空间
│  └─ model\                       共享结构与枚举（model.go / format.go）
├─ scripts\                        开发/验证脚本（不参与交付）
│  ├─ dev.ps1                      离线构建环境
│  ├─ smoke\main.go                端到端自检
│  ├─ shot\main.go                 截图
│  ├─ winlist\main.go              窗口树检查
│  ├─ genicon\main.go              生成 assets\securevault.ico
│  └─ addmanifest\main.go          注入 PE 清单
├─ assets\securevault.ico          内嵌图标
├─ assets\securevault.manifest     comctl32 v6 + Per-Monitor V2 + longPathAware
└─ dist\SecureVault.exe            交付产物
```

---

## 4. 各包职责与关键 API

### 4.1 `internal/model` —— 纯数据

```go
type Mode int
const (ModeOff Mode = 0; ModeMonitor Mode = 1; ModeCopy Mode = 2)
func (m Mode) String() string   // "关闭" / "监控模式" / "复制模式"
func (m Mode) Key() string      // "off" / "monitor" / "copy"
func ParseMode(s string) Mode

type EventKind string  // EventArrival="arrival" / EventRemoval="removal"
type ActionKind string // ActionNone/ActionMonitor/ActionCopy/ActionExcluded/ActionError

type DeviceInfo struct { Letter, Name, BusType, Model, Vendor, VID, PID string
    USBSerial, DiskSerial, FS string; CapacityBytes, FreeBytes int64
    PhysicalDrive int; DeviceInstance, VolumeGUID, IsRemovable, DevicePath, SystemTime }
func (d DeviceInfo) DisplayName() string // 卷标 > 型号 > 盘符
func (d DeviceInfo) VIDPID() string      // "0951:1666"，缺一项返回空串

type Record struct { ID int64; Time string; Event EventKind; Action ActionKind
    Note string; Files, Bytes, Dest, Elapsed; DeviceInfo }
type Alias struct { ID int64; Match, Value, Alias, Remark string }
type ExcludeRule struct { ID int64; Type ExcludeRuleType; Value, Remark, CreatedAt string }
type NameList struct { ID int64; Kind, Value, Remark, CreatedAt string }
type FileFilter struct { ExcludeNames, ExcludeExts, IncludeExts []string
    UseInclude, SkipHidden, SkipZeroByte bool; MaxFileSizeMB int64 }
type CopyOptions struct { DestRoot, SubFolder string; Filter FileFilter
    Conflict ConflictPolicy; Incremental, VerifySize, PreserveTime bool; Workers, Retries int }
type CopyStats struct { Scanned, Copied, Skipped, Failed, Bytes, ElapsedMS int64 }
type ScheduleSlot struct { ID int64; Start, End string; Mode Mode; Days int; Enabled bool; Remark string }
type Settings struct { ManualMode Mode; AutoStart bool; LogRetention int
    DesktopSrc, DesktopDest, CopyDest string
    ScheduleEnable, RecordRemoval, MinimizeToTray bool }

func DefaultSettings() Settings      // 监控模式 / 自启=true / 日志14天 / 定时=true / 记录移除=true
func DefaultCopyOptions() CopyOptions// 覆盖 / 增量 / 校验大小 / 保留时间 / 4 worker / 2 次重试
func FormatCapacity(n int64) string
func ParseCapacity(expr string) (op string, bytes int64, err error)
func MatchCapacity(expr string, actual int64) bool
func RuleTypeLabel(t ExcludeRuleType) string
func AllRuleTypes() []ExcludeRuleType   // 界面上拉框的顺序来源
func DaysString(days int) string        // "每天" / "周一、周三" / "不生效"
var WeekLabels = [7]string{"一","二","三","四","五","六","日"}
```

### 4.2 `internal/paths` —— 「数据只在 exe 同级」

```go
const DirName = "SecureVaultData"
type Dirs struct { ExePath, ExeDir, Root, Logs, Temp string }
func New(exePath string) (*Dirs, error)      // 创建 Root/logs/tmp 并写 .writetest 探测可写性
func (d *Dirs) KeyStoreFile() string         // <Root>\keystore.json
func (d *Dirs) DBFile() string               // <Root>\vault.db
func (d *Dirs) ConfigFile() string           // <Root>\settings.json（当前实现未使用）
func (d *Dirs) LogFile(day string) string    // <Logs>\2006-01-02.txt
func (d *Dirs) BackupDir() string            // <Root>\backup（当前实现未使用）
func (d *Dirs) TempPath(prefix string) string
var ErrNotWritable error
```

### 4.3 `internal/keystore` —— 密码 / 恢复码 / 主密钥

```go
type State int; const (StateUninitialized State = iota; StateLocked; StateUnlocked)
type Status struct { State State; CreatedAt, ChangedAt string; FailCount int; LockedTill string }

var (ErrWrongPassword, ErrWrongRecovery, ErrLockedOut, ErrPasswordTooShort,
     ErrAlreadyInit, ErrNotInitialized, ErrLockedState, ErrCorruptKeyStore,
     ErrInvalidRecovery, ErrInvalidParameters error)

func New(dir *paths.Dirs) (*KeyStore, error)
func (k *KeyStore) Status() Status
func (k *KeyStore) Initialize(password string) (recoveryCode string, err error)
func (k *KeyStore) Unlock(password string) error
func (k *KeyStore) UnlockWithRecovery(code string) error
func (k *KeyStore) ChangePassword(oldPwd, newPwd string) error   // 会真正校验 oldPwd
func (k *KeyStore) ResetWithRecovery(code, newPassword string) (newCode string, err error)
func (k *KeyStore) RotateRecovery() (string, error)
func (k *KeyStore) DEK() ([]byte, error)   // 32 字节；未解锁报错
func (k *KeyStore) Lock()
```

实现要点：PBKDF2-HMAC-SHA256（16 字节随机 salt、200000 次迭代、派生出 64 字节 = 32 字节 KEK + 32 字节 KMAC）；DEK 用 AES-256-GCM 包裹（12 字节随机 nonce，AAD 为 `SecureVault|keystore|v1|pwd` / `...|rec`）；校验值是 `HMAC-SHA256(KMAC, "<label>|verify")`，用 `crypto/subtle.ConstantTimeCompare` 比较；恢复码 `XXXX-XXXX-XXXX`（`crypto/rand` + 拒绝采样），比较时忽略 `-`/`_`/空白并统一大写；连续 5 次失败冷却 60 秒且**持久化**；`keystore.json` 用 `fileutil.WriteFileAtomic` 以 0600 写入。

**关于 `ChangePassword` 的旧密码校验（重要，且曾是安全缺陷）**：
早期实现是 `_ = oldPwd`——只要界面处于已解锁状态就能改密，设置页那一栏「当前密码」形同虚设。
现已补上真实校验：内部走 `deriveAndVerify()` 派生素材 + 常量时间 HMAC 比对，
失败返回 `ErrWrongPassword`；**故意不计入 unlock 的失败次数、不触发 60 秒冷却**
（否则用户在改密弹窗里连点几次错误旧密码就会把自己锁在门外）。
密码与恢复码的校验路径因此被合并成 `deriveAndVerify()` + `verifyUnlockedSecretLocked()` 两个内部函数，
`unlock()` 与 `ChangePassword()` 共用同一套密码学校验，避免两条路径再次分叉。

### 4.4 `internal/store` —— 加密 SQLite

```go
func Open(dbPath string, dek []byte) (*Store, error)  // dek 必须 32 字节
func (s *Store) Close() error                          // 幂等，关闭前 wal_checkpoint(TRUNCATE)

type RecordQuery struct { Keyword, Field, Event, Since string; Page, PageSize int }
func (s *Store) InsertRecord(r *model.Record) (int64, error)
func (s *Store) QueryRecords(q RecordQuery) (rows []model.Record, total int, err error)
func (s *Store) DeleteRecordsBefore(t string) (int64, error)  // t=="" 表示全部
func (s *Store) CountRecords() (int, error)

func (s *Store) ListExcludeRules() ([]model.ExcludeRule, error)
func (s *Store) AddExcludeRule(r *model.ExcludeRule) (int64, error)
func (s *Store) DeleteExcludeRule(id int64) error
func (s *Store) ClearExcludeRules() error

func (s *Store) ListLists() ([]model.NameList, error)
func (s *Store) AddList(kind, value, remark string) (int64, error)
func (s *Store) DeleteList(id int64) error
func (s *Store) ClearLists(kind string) error   // kind=="" 清空全部

func (s *Store) ListAliases() ([]model.Alias, error)
func (s *Store) UpsertAlias(a *model.Alias) (int64, error)
func (s *Store) DeleteAlias(id int64) error

func (s *Store) ListSchedule() ([]model.ScheduleSlot, error)
func (s *Store) AddSchedule(sl *model.ScheduleSlot) (int64, error)
func (s *Store) UpdateSchedule(sl *model.ScheduleSlot) error
func (s *Store) DeleteSchedule(id int64) error

func (s *Store) LoadSettings() (model.Settings, error)  // 无行/解密失败→DefaultSettings()
func (s *Store) SaveSettings(v model.Settings) error    // JSON 整体加密后 UPSERT

type FileMark struct { SourcePath string; Size, ModUnix int64; Crc32 uint32; DestPath, CopiedAt string }
func (s *Store) GetMark(sourcePath string) (FileMark, bool, error)
func (s *Store) PutMark(m FileMark) error
func (s *Store) DeleteMarksUnder(dir string) (int64, error)
func (s *Store) CountMarks() (int, error)

type Stats struct { Records, ExcludeRules, Aliases, Marks, CopySessions int; LastCopy string }
func (s *Store) Stats() (Stats, error)
```

实现要点见[第 6.7 节](#67-存储moderncorgsqlite--字段级-aes-256-gcm)与[第 7 节](#7-数据文件格式)。

### 4.5 `internal/logger`

```go
type Level int; const (LevelDebug Level = iota; LevelInfo; LevelWarn; LevelError)
type LogFile struct { Day, Path string; Size int64 }

func New(dir *paths.Dirs, retentionDays int) (*Logger, error) // 非法天数回退 14；New 时自动 Cleanup
func (l *Logger) Debug/Info/Warn/Error(format string, args ...any)
func (l *Logger) SetRetention(days int)     // 7/14/30，立即清理一次
func (l *Logger) Cleanup() (int, error)
func (l *Logger) ClearBefore(days int) (int, error)
func (l *Logger) ClearAll() (int, error)
func (l *Logger) Files() ([]LogFile, error) // 按日期倒序
func (l *Logger) Read(day string, tailLines int) (string, error)
func (l *Logger) TodayFile() string
func (l *Logger) Close() error
```

### 4.6 `internal/presence`

```go
type Device = model.DeviceInfo
const ProbeTimeout = 2 * time.Second

type DriveEvent struct { Letter string; Kind model.EventKind; Device model.DeviceInfo }

func Probes() ([]string, error)                          // 仅 DRIVE_REMOVABLE / DRIVE_FIXED
func Snapshot() (map[string]model.DeviceInfo, error)      // 单盘失败跳过，全失败才报错
func Probe(letter string) (model.DeviceInfo, error)
func Watch(hwnd uintptr, bufSize int, out chan<- DriveEvent) (stop func(), err error)

// 纯逻辑、可单测：
func DriveMaskToLetters(mask uint32) []string
func BusTypeString(busType uint32) string
func BusTypeFromInstanceID(instanceID string) string
func ParseUSBInstanceID(instanceID string) (vid, pid, serial string)
```

**探测到的设备属性**（`probeVolume` 的顺序）：盘符、是否可移动（`GetDriveTypeW`）、卷标 + 文件系统（`GetVolumeInformationW`）、容量/剩余（`GetDiskFreeSpaceExW`）、卷 GUID 路径（`GetVolumeNameForVolumeMountPointW`，失败时用 `GUID_DEVINTERFACE_VOLUME` 接口路径兜底）、物理磁盘号（`IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`）、设备号 + 分区号（`IOCTL_STORAGE_GET_DEVICE_NUMBER`）、总线类型 + 磁盘序列号 + 厂商 + 产品名（`IOCTL_STORAGE_QUERY_PROPERTY` + `STORAGE_DEVICE_DESCRIPTOR`）、设备实例 ID / VID / PID / USB 序列号 / 接口路径（SetupAPI `GUID_DEVINTERFACE_DISK` 枚举 + 沿设备树向上找 USB 节点）、采集时间。总线类型缺失时从实例 ID 前缀推断（USBSTOR/USB→USB，SCSI→SATA，NVME→NVMe）。

### 4.7 `internal/copier`

```go
type Mark struct { SourcePath string; Size, ModUnix int64; Crc32 uint32; DestPath, CopiedAt string }
type Index interface { GetMark(sourcePath string) (Mark, bool, error); PutMark(m Mark) error }
type Progress struct { Phase, Current string; Scanned, Copied, Skipped, Failed, Bytes, Total int64 }
type Result struct { Stats model.CopyStats; DestDir string; Errors []string; Canceled bool }
type Options struct { Copy model.CopyOptions; Index Index
    OnProgress func(Progress); OnLog func(level, msg string)
    ShouldStop func() bool; AferoFS any /*占位，勿用*/ }

func New(opts Options) *Engine
func (e *Engine) Copy(srcRoot, destRoot, subFolder string) (*Result, error)
func (e *Engine) CopyPaths(srcRoot string, items []string, destRoot, subFolder string) (*Result, error)
func ResolveDestDir(destRoot, subFolder string) string
func ShouldCopyFile(path string, filter model.FileFilter) bool
func ScanOnly(srcRoot string, filter model.FileFilter) (files int64, bytes int64, err error)
```

调优常量：`copyBufferSize=256KB`、`progressInterval=100ms`、`retryBaseDelay=200ms`、`maxErrors=200`、`spaceCheckEvery=200`、`defaultWorkers=4`、`maxWorkers=16`。

### 4.8 `internal/engine`

```go
type Sink interface {   // 由 *store.Store 满足
    InsertRecord(*model.Record) (int64, error)
    ListExcludeRules() ([]model.ExcludeRule, error)
    ListAliases() ([]model.Alias, error)
    ListLists() ([]model.NameList, error)
    ListSchedule() ([]model.ScheduleSlot, error)
    LoadSettings() (model.Settings, error); SaveSettings(model.Settings) error
    GetMark(sourcePath string) (store.FileMark, bool, error); PutMark(store.FileMark) error
}
type Events struct { OnLog func(level, msg string); OnRecord func(model.Record)
    OnStatus func(status string); OnBusy func(busy bool, text string) }

func New(sink Sink, events Events) (*Engine, error)
func (e *Engine) Reload() error
func (e *Engine) Start() error          // tickLoop(20s) + presence.Watch + 1.5s 后 initialScan
func (e *Engine) Stop()                 // 幂等
func (e *Engine) Recompute() model.Mode // 命中计划以计划为准，否则手动模式
func (e *Engine) HandleEvent(ev presence.DriveEvent)
func (e *Engine) FolderName(d model.DeviceInfo) string   // 别名 > 卷标 > 型号 > 盘符 + 净化
func (e *Engine) BuildFilter() model.FileFilter
func (e *Engine) Settings() model.Settings
func (e *Engine) SaveSettings(v model.Settings) error
func (e *Engine) SetManualMode(m model.Mode) error
func (e *Engine) EffectiveMode() model.Mode
func (e *Engine) ManualMode() model.Mode
func (e *Engine) State() Snapshot
func (e *Engine) ExcludeDevice(d model.DeviceInfo) error  // 一键加入排除名单
```

### 4.9 `internal/app`

```go
type Options struct { ExePath string; Silent, Debug bool }
func Run(opts Options) int
func EnableReviewMode()          // -review / SV_UI_REVIEW=1
func EnableAutoTest()            // -autotest

// 页面接口（每个标签页实现）
type page interface { build(parent *ui.Form); layout(r win.Rect); activated(); dispose() }
// 可选扩展接口
//   interface{ setVisible(bool) }        // 切页时由 mainUI 调用（显示当前页、隐藏其余页）
//   interface{ ownedControls() []ui.Control }
//   interface{ titleControl() ui.Control }
//   interface{ refresh() }              // 状态栏每秒刷新时联动
//
// 注意：layout(r) 的 r 一律是**设备像素**矩形（与控件坐标同一坐标系）。
// 历史坑：曾经把逻辑矩形传进来，而页面代码把它当设备像素用，
// 150% DPI 下算出的坐标会落到窗口外（表现为"控件凭空消失"）。

// 后台 → UI 线程
func (a *App) postUI(fn func())
```

**页面结构：扁平化（重要）**

主面板**不使用嵌套子窗口**，`pageBase.newContainer` 里的 `container` 就是主窗口本身，
所有页面控件都直接挂在主窗口上，切页靠 `pageBase.setVisible` 统一显隐。

原因（实测结论，勿改回嵌套容器）：把控件放进"子容器窗口"后，在标签页结构里它们不会出现在屏幕上；
而向导对话框（控件直接挂主窗口）一切正常。配套的机制：

- `Form.SetControlOwner(owner)` + `pageBase.TrackControl(c)`：控件在创建时**自动登记**到当前页面名下，
  不需要每个页面手写登记（漏登记会导致切页时旧页面的控件不隐藏，七个页面叠在一起）。
- `newContainer` 里 `SetControlOwner` 必须在创建**任何控件之前**调用，否则本页第一个控件（标题）
  会被登记到上一个页面名下（曾导致"日志页标题残留在设置页"）。
- `mainUI.layout` 通过 `layoutPageTitle` 接口统一排页面标题，再用 `p.layout(pageDev)` 排页面内容；
  **必须先 layout 再 `selectPage(0)`**，顺序反了会看到所有页面控件同时可见。

托盘菜单项 ID：`idTrayExit=1003`（右键菜单只保留"退出"一项）；自定义消息：`wmTrayCallback=WM_APP+1 / wmEngineEvent=WM_APP+2 / wmScheduleTick=WM_APP+3 / wmRunOnUI=WM_APP+10 / wmTick=WM_APP+11`；单实例互斥体名 `Local\SecureVault_SingleInstance_8F3A21`，激活消息 `SecureVault_Activate_8F3A21`。

### 4.10 `internal/ui` + `internal/ui/win`

- `ui` 层：`Form`（顶层/模态/子页面三种）、控件（`Label/TextEdit/PasswordEdit/TextArea/Button/CheckBox/RadioButton/ComboBox/ListView/TabControl/StatusBar/GroupBox/ProgressBar`）、`TrayIcon`、通用对话框（`Info/Warn/Error/Confirm/ConfirmYesNo/InputBox`）、自绘 `Canvas`。
- 尺寸 API（**这是全项目 DPI 约定的核心**）：

  ```go
  func S(v int) int32        // 逻辑 → 设备像素（96 DPI 设计值 × scale）
  func S32(v int32) int32
  func L(v int32) int32      // == S32，全局唯一的缩放点
  func R(l, t, r, b int32) win.Rect   // 纯打包，不缩放
  func ToLogical(r win.Rect) win.Rect
  func FromLogical(r win.Rect) win.Rect
  func InitDLL() error       // DPI 感知 + comctl32 + 字体（创建任何窗口前调用一次）
  func DPI() int32; func Scale() float64; func UIFont() win.Handle
  ```

- `win` 层：`Handle/Rect/Point` 等类型 + 全部 Win32 直调（`GetMessage/TranslateMessage/DispatchMessage/IsDialogMessage`、`CreateMutexNamed`、`NewMessageWindow`、`NewPopupMenu/TrackPopup`、`ShowFileDialog/PickFolder`、`ShellOpen`、`IconFromBytes`、GDI/自绘、`SessionInteractive` 等）。

### 4.11 `internal/autostart`

```go
const LinkName = "SecureVault.lnk"
func StartupDir() (string, error)   // SHGetKnownFolderPath(FOLDERID_Startup)，失败回退 %APPDATA%\...\Startup
func LinkPath() (string, error)
func Enabled() bool
func Enable(exePath string) error   // 写 .lnk，参数 -silent，工作目录 = exe 目录
func Disable() error                // 不存在时返回 nil
func Set(on bool, exePath string) error
```

`.lnk` 是**纯 Go 手写的 Shell Link 二进制**（ShellLinkHeader + LinkTargetIDList + LinkInfo + StringData + TerminalBlock），不依赖 COM；先写 `.tmp` 再 `os.Rename` 原子替换。

---

## 5. 关键流程时序

### 5.1 启动 → 托盘 → 解锁 → 打开数据库 → 启动引擎 → 消息循环

1. `main()` 解析参数（`-silent` / `-debug` / `-review` / `-help`），用 `os.Executable()` 得到 `ExePath`，调用 `app.Run`；`Run` 先 `runtime.LockOSThread()` 锁定 GUI 线程。
2. `paths.New(exePath)`：推导 `SecureVaultData\{logs,tmp}` 并创建，写 `.writetest` 做真实可写性探测。失败 → 写 stderr（GUI 子系统下尽力而为）→ **退出码 3**（此时还没有 GUI，不能弹窗）。
3. `logger.New(dirs, 14)`：建日志目录，自动 `Cleanup()` 一次。
4. `ui.InitDLL()`：`SetProcessDpiAwarenessContext(Per-Monitor V2)`（失败回退 `SetProcessDPIAware`）→ `InitCommonControlsEx(ICC_LISTVIEW_CLASSES|ICC_BAR_CLASSES|ICC_TAB_CLASSES|ICC_STANDARD_CLASSES|ICC_WIN95_CLASSES)` → `GetDpiForSystem()` → 计算 scale 并重建 9pt 字体（Win11 优先 `Segoe UI Variable Text`，回退 `Segoe UI` → `Microsoft YaHei UI` → `宋体`）。
5. **单实例**：`win.CreateMutexNamed("Local\\SecureVault_SingleInstance_8F3A21")`；`AlreadyExists` → `BroadcastMessage(RegisterWindowMessage("SecureVault_Activate_8F3A21"))` 让已有实例弹面板 → 本进程 **exit 0**。
6. `keystore.New(dirs)`：`keystore.json` 不存在＝未初始化；解析/版本/算法不合法 → 弹错误框并 **退出码 4**（避免误覆盖）。
7. `initTray()`：`win.NewMessageWindow("SecureVaultMsgWnd", broadcast=true, a.onMessage)`（0 尺寸隐藏顶层窗口，能收广播）→ 从 `//go:embed all:assets` 读 `securevault.ico` 建图标（失败用系统默认图标）→ `ui.NewTrayIcon` + `Show("SecureVault")`。托盘标题永远只有 `SecureVault`。
8. 解锁入口（按顺序判断）：`-autotest` → 执行自动验收（跑完直接 `shutdown()` 并返回其退出码）；`-review` → 评审模式；**非交互会话**（窗口站不是 WinSta0 或没有输入桌面）→ 只驻留托盘（避免无人点击的模态窗口卡死流程）；`-silent` → 只驻留托盘；否则 `showUnlockFlow()`：
   - `StateUninitialized` → `runFirstRunWizard()`（设密码 → 恢复码窗口）；
   - `StateLocked` → `runUnlockDialog(false)`（可切「忘记密码 / 用恢复码重置」）；
   - 用户取消 → 继续驻留托盘，不进入主面板。
9. `afterUnlock()`：`startCore()`（= `ks.DEK()` → `store.Open(vault.db, dek)` → `LoadSettings()` → `syncAutoUnlock()` → `syncAutoStart()` → `engine.New` + `eng.Start()`）→ `showMainPanel()`。
   `startCore()` 是幂等的，也是"后台运行"的入口：启动时 `autoResumeBackground()` 会先用 DPAPI 保护的密钥副本自动解锁，再走同一条 `startCore()`，但**不显示面板**。
10. `eng.Start()`：起 20 秒的 `tickLoop`（重算定时模式）；`presence.Watch` 成功则起 `eventLoop`，失败则退化为 **5 秒轮询** `pollLoop`；最后延迟 1.5 秒做一次 `initialScan`，把"启动前就插着"的设备当作接入事件处理（增量索引会自然跳过没变的文件）。
11. `showMainPanel()`：`ui.NewForm("SecureVault", 1200, 800)`（按工作区自动缩小，最小 900×580；`SetQuitOnDestroy(false)`）+ `TabControl`（7 项，**只占标签条那一行**）+ 3 格 `StatusBar`；创建 7 个页面（**不开子窗口**，控件直接挂主窗口）；绑定 `Painted`（标签条下方分隔线）/`Resized`/`Minimized`/`Closing`/`KeyDown`；用**真实客户区尺寸**做首次布局，**布局完成后再 `selectPage(0)`**；`Center()` + `Show()`；`SetTimer(1s)` 定时刷新。
12. `messageLoop()`：在消息窗口上 `SetTimer(1s)` 心跳 → `GetMessage` 循环（`WM_QUIT` → 返回 0 退出）→ 若主面板存在则先 `IsDialogMessage`（Tab/方向键导航）→ `TranslateMessage` + `DispatchMessage`。
13. `shutdown()`（`sync.Once` 保证幂等）：`stopCore()`（停引擎、关数据库）→ 销毁面板 → `ks.Lock()` → 隐藏/销毁托盘与消息窗口 → 销毁图标 → 写"SecureVault 已退出"日志。

**启动行为（需求硬要求，勿改）**

| 场景 | 行为 |
|---|---|
| 从未设过密码 | 弹出「首次设置」窗口；关掉该窗口会问「尚未设置密码，确定退出吗？」，确认即退出 |
| 已设过密码 | **只驻留托盘、不显示任何窗口**；左键单击托盘图标才打开主面板 |
| 非交互会话 / `-silent` | 只驻留托盘 |
| 托盘右键 | **只有「退出」一项** |
| 未设密码时点「退出」 | **直接退出**，不要求输密码 |

主面板窗口带 `WS_EX_TOOLWINDOW`（不是 `WS_EX_APPWINDOW`），所以"单击托盘打开面板"不会多出一个任务栏/Alt+Tab 条目。

### 5.2 插拔事件 → 排除判定 → 复制 → 记录

1. 系统广播 `WM_DEVICECHANGE` 落到 `presence` 内部的隐藏顶层窗口；窗口过程只做**拆参数 + 入队**（`rawEvent` 通道，缓冲 32，满则丢最旧）。
2. `dispatch` goroutine：
   - `DBT_DEVICEARRIVAL(0x8000)` / `DBT_DEVICEREMOVECOMPLETE(0x8004)`；
   - `DEV_BROADCAST_VOLUME.dbcv_unitmask` → `DriveMaskToLetters` 得盘符；`DEV_BROADCAST_DEVICEINTERFACE.dbcc_name` → 打开接口路径取 (磁盘号, 分区号) → 反查盘符（最多 3 次、间隔 250ms，等挂载点就绪）；
   - **去重**：同一盘符同一类事件 1.5 秒内只处理一次（Windows 一次插入会同时发 DEVICEINTERFACE 与 VOLUME 两种广播，不去重会记两条）。
3. 接入：`Probe(letter)`（单盘总耗时 ≤ 2 秒；内部 SetupAPI 枚举整体限时 3 秒）最多重试 3 次、间隔 300ms（解决"刚插入卷还没挂载好"）。探测结果写入缓存（供移除事件回填静态信息）。
4. `DriveEvent{Letter, Kind, Device}` 投入 `engine` 的事件通道（缓冲 32，满则丢最旧，绝不阻塞消息循环）。
5. `engine.HandleEvent`：
   - 补齐 `Letter`/`SystemTime`；
   - **移除**：`RecordRemoval==true` 且当前模式不是「关闭」时写一条 `ActionNone / "设备移除"` 记录，然后返回；
   - **接入**：读当前 `effective` 模式 → 「关闭」直接返回（只写日志）→ `matchExclude` 命中任一条规则 → 写 `ActionExcluded` 记录（备注"命中排除规则：类型 = 值"）→ 「监控模式」写 `ActionMonitor` 记录 → 「复制模式」但 `CopyDest` 为空 → 写 `ActionError / "未设置复制目标目录"` → 否则 `go runCopy`。
6. `runCopy`：`copyMu` 串行化（同一时刻只跑一个复制任务）；`srcRoot = "X:\"`；`FolderName()` 决定子目录名；`copier.ResolveDestDir(destRoot, sub)` 得到最终目录；`buildFilter()` 把三张名单表转成 `model.FileFilter`。
7. `copier.Copy`：
   - 快速失败项：源目录不存在/不是目录、目标根目录建不出来、取不到剩余空间；
   - `scan`（单 goroutine DFS，收集阶段）：过滤 → 增量判定 → 冲突策略 → 得到 `candidate` 列表与 `pending` 字节数；
   - **复制前空间检查**：`pending > free` → 直接返回 error（一个文件都不复制）；
   - 建目标子目录 → `dispatch`（4 个 worker、共用一个任务 channel，每个 worker 复用 256KB 缓冲）；
   - 每个文件失败按 `Retries=2` 重试（间隔 200ms×n，重试前刷新源文件状态）；每 200 个文件复查一次剩余空间，不足则置 `spaceStop` **安全停止**；`ShouldStop()` 为 true 时停止派发新任务并把结果标记 `Canceled`；
   - 单个文件成功后 `PutMark` 写增量索引。
8. 回写记录：`ActionCopy`（有失败则 `ActionError`），带 `Dest / Files / Bytes / Elapsed`，失败明细最多 20 条写进日志。
9. 通知界面：`Events.OnStatus/OnRecord/OnBusy` → `win.PostMessage(msgw, wmEngineEvent)` → `onMessage` → `ui.refreshStatus()`（更新 3 格状态栏 + 当前页的 `refresh()`）。

---

## 6. 关键实现细节

### 6.1 为什么修改子控件必须带 `WS_VISIBLE`

`ui.createChild`（`internal/ui/control.go`）：

```go
func createChild(class, text string, style, exStyle uint32, parent win.Handle, id int32, r win.Rect, visible bool) win.Handle {
    style |= win.WS_CHILD
    if visible {
        style |= win.WS_VISIBLE     // ← 关键
    }
    ...
}
```

Win32 里子窗口的**可见性样式是创建时确定的**：创建时不带 `WS_VISIBLE`，即使父窗口随后 `ShowWindow`，该子控件也**不会**自动出现；后续只能靠 `ShowWindow(SW_SHOW)` 单独打开。

**同一个坑在窗口级别也踩过一次（第 3 轮排查的核心结论）**：`ui.NewPage` 曾经写成

```go
style := uint32(win.WS_CLIPCHILDREN | win.WS_CLIPSIBLINGS)  // ← 错！
p.create(style, exStyle, 100, 100, parent.hwnd)             // create 内部再 |= WS_CHILD
```

问题在于这些 `WS_*` 常量**共用位**：`WS_CHILD=0x40000000`、`WS_CLIPSIBLINGS=0x04000000`、
`WS_CLIPCHILDREN=0x02000000` 本身不冲突，但漏掉 `WS_VISIBLE` 就足以让整棵子树不显示。
结果是页面容器创建出来就是隐藏的，里面所有控件统统看不见 —— 这正是用户反馈的
"单击托盘图标进去之后什么都没有"的**直接原因**。修复：

```go
style := uint32(win.WS_CHILD | win.WS_VISIBLE | win.WS_CLIPCHILDREN | win.WS_CLIPSIBLINGS)
```

并在 `Form.create` 里加了一条兜底断言：样式中含 `WS_VISIBLE` 却 `IsWindowVisible()==false` 时补一次 `ShowWindow`。
设置 `SV_TRACE_FILE=<路径>` 环境变量可以把每条窗口消息写进文件，排查这类"存在但不显示"的问题非常有效。

**第二层原因（同样已修）**：系统 Tab 控件会把**整个矩形**画成灰底。它和页面容器只要重叠就必然互相遮挡 ——
它盖住页面则页面空白，页面盖住它则标签看不见。所以现在**标签控件只占标签条那一行**，
页面从它下方开始，两者零重叠；再加上页面控件全部扁平挂在主窗口上，才彻底稳定。

### 6.2 窗口类注册时 `LpfnWndProc` 必须已就绪

`registerFormClass()`：

```go
if wndProcCallback == 0 {
    wndProcCallback = win.NewCallback(wndProc)   // 必须先拿到 thunk 地址
}
wc := win.WndClassEx{ ..., LpfnWndProc: wndProcCallback, ... }
win.RegisterClassEx(&wc)
```

`WNDCLASSEX.lpfnWndProc` 是 NULL 时 `RegisterClassEx` **依然会成功**，但之后 `CreateWindowEx` 一给这个类创建窗口就会访问空指针崩溃（曾实测为 `0xC000041D`）。因此回调必须在注册前创建，并且 `Form.create()` 里也有一道同样的兜底。

### 6.3 主消息循环必须用 `GetMessage`

```go
// 必须用 GetMessage：只有它会在 WM_QUIT 时返回 0，
// PeekMessage 永远不会返回 WM_QUIT（曾经的实现因此无法退出）。
for {
    r := win.GetMessage(&msg, 0, 0, 0)
    if r == 0 { return }        // WM_QUIT
    if r < 0 { /* 记录并退出 */ }
    if a.ui != nil && a.ui.form != nil && win.IsDialogMessage(a.ui.form.Handle(), &msg) { continue }
    win.TranslateMessage(&msg); win.DispatchMessage(&msg)
}
```

`PeekMessage` 只是"看一眼有没有消息"，它不会因为 `WM_QUIT` 而返回 0，用它做循环条件会导致 `PostQuitMessage` 之后程序仍然死转。模态窗口的 `Form.RunModal()` 同样用 `GetMessage` 循环。

### 6.4 单实例：必须用 `LazyProc.Call` 返回的 err

```go
r1, _, e := procCreateMutex.Call(0, 0, uintptr(unsafe.Pointer(mustU16(name))))
...
AlreadyExists: errnoCode(e) == errorAlreadyExists,   // ERROR_ALREADY_EXISTS = 183
```

`LazyProc.Call` 的第三个返回值**就是本次调用结束那一刻的 `GetLastError`**。如果先 `Call` 再单独调 `GetLastError()`，中间可能已经被其它调用覆盖，会**永远拿不到** `ERROR_ALREADY_EXISTS`，单实例检测静默失效（曾出现过这个 bug）。

### 6.5 DPI：进程声明 + 唯一缩放点

- 进程级声明（两道保险）：
  1. 代码：`ui.InitDLL()` → `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)`（`^uintptr(3)`），失败回退 `SetProcessDPIAware()`；
  2. 清单：`assets\securevault.manifest` 里 `<dpiAwareness>PerMonitorV2,PerMonitor</dpiAwareness>` + `<dpiAware>true/pm</dpiAware>` + `<longPathAware>true</longPathAware>`。
- 缩放约定：
  - `ui.K(v int32) int32`（等价于旧的 `L/S32`）是**全局唯一的缩放点**，把 96 DPI 的设计值换算成当前 DPI 的设备像素；
  - `ui.R(l,t,r,b)` **只打包矩形、绝不缩放**，所以调用点必须写成 `ui.R(x, y, x+K(120), y+K(24))`，保证只缩放一次、不会重复相乘；
  - `ui.RL(l,t,r,b)` 会**对四个参数都做缩放**，适合整块矩形一次性给值的场景；
  - **页面 `layout(r win.Rect)` 收到的 `r` 是设备像素矩形**（与控件坐标同一坐标系），页面内写死的尺寸一律过 `K()`；
  - `ui.Stack` 的 `NewStack(x设备像素, y设备像素, widthDIP)` —— 前两个是设备像素、宽度是 DIP，因为它内部按 DIP 累加后统一过 `K()`；`ui.ToLogical(r).Width()` 可以把设备像素宽度换成 DIP。
  - **坐标系混用是本项目最容易犯的错**：曾经把逻辑矩形传给 `layout()`、又在页面里当设备像素用，150% DPI 下控件被算到窗口外（看起来像"控件凭空消失"）。改动布局代码时务必先问自己"这个数是 DIP 还是设备像素"。
- `WM_DPICHANGED` 到达时重新 `setDPI()` 并按系统建议矩形 `SetWindowPos`；`WM_GETMINMAXINFO` 用缩放后的最小尺寸限制窗口。
- 窗口尺寸 API 约定：`NewForm`/`NewModalForm` 的宽高参数是**客户区 DIP**（内部用 `AdjustWindowRectEx` + `SetWindowPos` 修正），这样"客户区刚好装下内容"是可预期的；`Form.ClientDIP()` 可回读为客户区 DIP。

### 6.6 comctl32 v6 清单与 `scripts/addmanifest`

**为什么必需**：不申请 comctl32 v6，进程会加载 5.x 公共控件，`ListView` 只支持 ANSI 消息，发送 `LVM_INSERTCOLUMNW` 之类的宽字符消息不被识别 → 列表列头/内容异常甚至界面卡死。清单同时声明 Per-Monitor V2 DPI、`asInvoker`（不弹 UAC）、`longPathAware`、`activeCodePage=UTF-8`。

**为什么需要单独一步**：本机没有 `rc.exe` / `windres`，Go 链接器也没有"把资源塞进 exe"的能力，所以用纯 Go 直接构造 PE 资源节：

`scripts/addmanifest` 的工作方式：

1. `debug/pe` 解析输入 exe，若已存在 `.rsrc` 段则直接复制输出（幂等）；
2. 在文件末尾按 `FileAlignment(0x200)` 对齐处，按 `SectionAlignment(0x1000)` 对齐虚拟地址，构造新的 `.rsrc` 内容：根目录(3 项) → Type 目录(`RT_MANIFEST=24`) → Name 目录(`LANG=1033`) → DataEntry(16 字节，前 4 字节是数据 RVA) → 清单字节；
3. 追加节表项（名字 `.rsrc`、VirtualSize/VirtualAddress/SizeOfRawData/PointerToRawData、特征 `0x40000040` = INITIALIZED_DATA|READ），`NumberOfSections + 1`；
4. 更新 `OptionalHeader.DataDirectory[RESOURCE] = {rsrcVA, total}` 与 `SizeOfImage`（取最大值），最后整体写盘。

### 6.7 存储：`modernc.org/sqlite` + 字段级 AES-256-GCM

- 纯 Go 驱动（`CGO_ENABLED=0` 可用），连接池固定 **1 个连接**（`SetMaxOpenConns(1)`），天然串行化写入。
- DSN 见 `dsnFor`，长路径是重点：

  ```go
  p = fileutil.LongPath(p)                       // \\?\D:\...（突破 MAX_PATH）
  return "file:" + url.PathEscape(filepath.ToSlash(p)) +
      "?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)" +
      "&_pragma=foreign_keys(1)&_pragma=synchronous(NORMAL)&_pragma=temp_store(MEMORY)"
  ```

  踩过的坑：SQLite 的 URI 解析只把 `/` 当分隔符，且路径以 `//` 开头会被当成 authority；实测 `file://?/...`、`file:\\?\...`、裸 `\\?\...` 都**打不开**长路径（报 `unable to open database file` / `out of memory`）。先规范化成 `\\?\` 长路径再整体 `url.PathEscape`（分隔符变 `%2F`）由 SQLite 自己解码，>260 字符、含中文与空格的路径都能正常建库并启用 WAL。
- 字段级加密：`seal()` 用固定 AAD `SecureVault|field|v1`，密文布局 `nonce(12) || ciphertext || tag(16)`；空串存 `NULL`；`open()` 对任何异常（长度不足、密钥不符、密文被改）都返回**空串**，绝不 panic。
- 盲索引：`file_marks.path_key` 存 `HMAC-SHA256(从 DEK 派生的 bIdx, "mark" || 0 || 归一化路径)`，可以按路径等值查询又不泄露明文；`GetMark` 命中后仍会核对解密结果，必要时退化为全表扫描。
- 关键词搜索：密文带随机 nonce，`LIKE` 无意义。`QueryRecords` 只用 SQL 做时间/事件硬过滤 + `LIMIT 20000`，然后**逐行解密在 Go 侧做大小写不敏感匹配**，再按页切分；因此 `total` 最多反映 20000 行候选内的匹配数。
- 所有写操作都包在事务里；`Close()` 前执行 `PRAGMA wal_checkpoint(TRUNCATE)`，尽量不留 `-wal`/`-shm`。

### 6.8 增量判定只用 `(Size, ModUnix)`

`copier.Mark.Crc32` **恒为 0**——引擎不做全文件哈希，原因：

- 保持"轻量"这一首要需求：做哈希意味着复制前要把源文件完整读一遍，等于把 IO 翻倍；U 盘上大文件时体验会明显变差；
- `(Size, ModUnix)` 两字段比较足够覆盖"新增/改动"的绝大多数场景；
- 代价：**"大小与修改时间都没变、只有内容变了"这种极端情况会被当作未变化跳过**；该字段预留给将来做内容校验。

判定顺序（很关键，否则 `ConflictRename` 会无限增生副本）：

1. 增量命中（`mark.Size == size && mark.ModUnix == modUnix`）→ 直接跳过，目标保持原样；
2. 增量未命中，但索引里上次写入的目标文件仍存在且在本次目标目录内 → **原地覆盖更新**那个文件；
3. 其它情况目标已存在 → 才按 `Conflict` 策略处理（默认 `overwrite`；另有 `rename` 生成 `name (1).ext`、`skip`）。
4. 另外：源文件在复制期间被改写（扫描时大小 ≠ 实际读取字节数）会被判为失败并进入重试。

### 6.9 全部静默

- **不弹窗**：`ui.Info/Warn/Error/Confirm` 只在**用户主动操作**的失败/确认场景使用；复制、扫描、错误处理路径里一次都不调用。
- **不调用外部程序**：全仓库不执行 `cmd` / `powershell` / `wmic` / `robocopy`，文件操作全走 `os` + `golang.org/x/sys/windows`。
- **没有控制台**：交付 exe 用 `-H=windowsgui` 构建；stderr 通过 `win.WriteStdErr` 尽力而为（有控制台时才看得到）。
- **日志失败静默**：`logger` 写失败只累加内部计数、关闭句柄等下次重开，绝不 panic、不阻塞。
- **复制引擎三级兜底**：worker 有 `recover`；用户回调（`OnLog/OnProgress/ShouldStop`）各自包 `recover`；单文件失败只进 `Result.Errors`（最多 200 条，超出只在末尾写一行"另有 N 条未显示"）。
- 目标目录空间不足时分两级：**复制前**直接失败（不复制任何文件）；**复制中**（每 200 文件）安全停止，已完成的文件保持有效。

### 6.10 其它值得一提的实现

- **设备广播去重**：1.5 秒窗口（`eventDedupWindow`），键为 `arrival|E:` / `removal|<接口路径>`。
- **移除事件不重新探测**：设备已经拔出，`Probe` 会失败且可能卡住，因此只用缓存里的静态信息回填。
- **`presence.Watch` 自建隐藏窗口**：message-only 窗口收不到设备广播，所以创建 0 尺寸 `WS_POPUP` 顶层窗口 + 自己的 `GetMessage` 循环（`runtime.LockOSThread`），`stop()` 通过 `WM_CLOSE` + 线程 `WM_QUIT` 双保险唤醒。
- **工具 1 复用同一套文件名单**：`runDesktopCopyJob` 调 `a.eng.BuildFilter()`，但**不套用设备排除规则**（那是按设备判定的）。
- **设置整体加密存库**：`settings_kv` 只存一行 `settings`，值是 JSON 加密后的 BLOB，避免"配置文件 + 数据库两份真相"。
- **UI 线程回投**：`app.postUI` 把闭包登记进 `map[uintptr]func()` 再 `PostMessage(msgw, wmRunOnUI, 指针)`；`runUIJob` 在 UI 线程取出执行（后台 goroutine 直接碰控件是未定义行为）。

---

## 7. 数据文件格式

### 7.1 `SecureVaultData\` 下每个文件的用途

| 路径 | 用途 | 备注 |
|---|---|---|
| `keystore.json` | 密码/恢复码的派生参数、被包裹的 DEK、校验值、失败计数与冷却时间 | 权限 0600，原子写；**不含任何明文密钥** |
| `vault.db` | 加密 SQLite：记录/规则/名单/别名/计划/设置/增量索引 | sqlite + WAL |
| `vault.db-wal` / `vault.db-shm` | SQLite 运行时产物 | `Close()` 时 `wal_checkpoint(TRUNCATE)` 回收 |
| `logs\YYYY-MM-DD.txt` | 按天纯文本日志，UTF-8 + CRLF | 保留 7/14/30 天 |
| `tmp\` | `fileutil.WriteFileAtomic` 的临时文件、启动时的 `.writetest` 可写性探测 | 可安全清理（程序未运行时） |
| `autotest.log` | **仅 `-autotest` 时生成**：自动验收的逐项 PASS/FAIL 与汇总 | 开发期产物，正常使用不会出现 |

### 7.2 `keystore.json` 字段（**只存派生值与密文**）

```json
{
  "version": 1,
  "createdAt": "2026-09-17T14:20:01+08:00",
  "changedAt": "2026-09-17T14:20:01+08:00",
  "kdf":     { "algo": "PBKDF2-HMAC-SHA256", "iter": 200000, "salt": "<16 bytes base64>" },
  "pwdWrap": { "wrapped": "<nonce||ciphertext||tag>", "verifier": "<HMAC-SHA256 32B>" },
  "recWrap": { "wrapped": "<nonce||ciphertext||tag>", "verifier": "<HMAC-SHA256 32B>" },
  "failCount": 0,
  "lockedAt":  "", "lockedTill": ""
}
```

| 字段 | 含义 | 是否敏感 |
|---|---|---|
| `version` | 格式版本，当前必须为 1 | 否 |
| `createdAt` / `changedAt` | 初始化时间 / 最近一次改密时间（RFC3339） | 否 |
| `kdf.algo/iter/salt` | KDF 算法名、迭代次数、随机 salt | 否（salt 本身不敏感） |
| `pwdWrap.wrapped` | 用密码派生的 KEK 以 AES-256-GCM 包裹的 DEK（`nonce(12)‖ct‖tag`） | 密文 |
| `pwdWrap.verifier` | `HMAC-SHA256(KMAC, "SecureVault\|keystore\|v1\|pwd\|verify")` | 不可逆 |
| `recWrap.*` | 同上，用恢复码派生的 KEK / 恢复码标签 | 密文 / 不可逆 |
| `failCount` | 连续失败次数（达到 5 触发冷却） | 否 |
| `lockedAt` / `lockedTill` | 冷却起点 / 截止时间 | 否 |

**绝不落盘**：主密码、恢复码明文、DEK 明文。密码与恢复码只在内存里派生（KEK+KMAC），派生结果使用后立即 `zero()` 擦除；`Lock()` 清空内存中的 DEK。

### 7.3 `vault.db` 表结构与加密列

| 表 | 列 | 加密（BLOB，AES-256-GCM） | 明文 |
|---|---|---|---|
| `records` | 27 列 | `note`、`dest`、`letter`、`name`、`bus_type`、`model`、`vendor`、`vid`、`pid`、`usb_serial`、`disk_serial`、`fs`、`device_instance`、`volume_guid`、`device_path` | `id`、`time`、`event`、`action`、`files`、`byte_count`、`elapsed`、`capacity_bytes`、`free_bytes`、`physical_drive`、`system_time`、`is_removable` |
| `exclude_rules` | 5 列 | `value`、`remark` | `id`、`rule_type`、`created_at` |
| `name_lists` | 5 列 | `value`、`remark` | `id`、`kind`、`created_at` |
| `aliases` | 6 列 | `match_field`、`value`、`alias`、`remark` | `id`、`created_at` |
| `schedule` | 7 列 | `remark` | `id`、`start_time`、`end_time`、`mode`、`days`、`enabled` |
| `settings_kv` | 3 列 | `setting_value`（整个 Settings 的 JSON） | `setting_key`、`updated_at` |
| `file_marks` | 7 列 | `source_path`、`dest_path` | `id`、`path_key`（HMAC 盲索引）、`size`、`mod_unix`、`crc`（恒 0）、`copied_at` |

索引：`idx_records_time`、`idx_records_event`、`idx_exclude_rules_type`、`idx_name_lists_kind`、`idx_aliases_match`、`idx_marks_path_key`（UNIQUE）、`idx_marks_source_path`。

**查询与导出**：

- `QueryRecords`：SQL 只做 `time >= ?` / `event = ?` 与 `ORDER BY time DESC, id DESC LIMIT 20000`，关键词匹配在解密后于 Go 侧完成（字段限定：`model` 同时匹配厂商、`serial` 同时匹配 USB 序列号与磁盘序列号…）。
- `DeleteRecordsBefore("")` = 清空全部；否则删除 `time < t`。
- 导出 CSV 在 `app/page_records.go`：分页（每页 500）读满最多 20000 条，写 **UTF-8 BOM**（`0xEF 0xBB 0xBF`）后交给 `encoding/csv`，共 25 列。
- 长路径写入都用 `fileutil.LongPath`（例如导出的 CSV 目标路径）。

---

## 8. 构建与发布

### 8.1 离线环境

`scripts\dev.ps1` 是一切的入口（点源加载设置当前会话环境变量）：

```powershell
. .\scripts\dev.ps1          # 设置 GOPATH/GOMODCACHE/GOCACHE/GOTMPDIR/GOFLAGS/GOPROXY=off/CGO_ENABLED=0
. .\scripts\dev.ps1 build    # 或： .\scripts\dev.ps1  直接构建
```

它做的事：

| 变量 | 值 | 作用 |
|---|---|---|
| `GOPATH` / `GOCACHE` / `GOTMPDIR` | 仓库内 `.gopath` / `.gocache` / `.gotmp` | 不污染系统目录 |
| `GOMODCACHE` | `D:\DeepSeek-Harness\project\auto-cy-file\.gomodcache` | **复用本机已有的只读模块缓存**（无外网） |
| `GOFLAGS` | `-mod=mod` | 允许按 go.mod 解析 |
| `GOPROXY` / `GOSUMDB` | `off` | 完全离线；`go.mod`/`go.sum` 已完整生成 |
| `CGO_ENABLED` | `0` | 纯 Go 构建（sqlite 用 modernc 纯 Go 实现） |

`Invoke-SVBuild` 默认执行：`go build -ldflags '-s -w' -trimpath -o dist\SecureVault.exe .\cmd\securevault`。

### 8.2 交付构建：为什么是两步

```powershell
# 第 1 步：编译（GUI 子系统、去符号、去路径信息）
go build -ldflags="-s -w -H=windowsgui" -trimpath -o dist\SecureVault.exe .\cmd\securevault

# 第 2 步：注入应用程序清单（comctl32 v6 + Per-Monitor V2 + longPathAware + asInvoker）
go run .\scripts\addmanifest dist\SecureVault.exe dist\SecureVault.exe assets\securevault.manifest
```

- **必须分两步**：Go 链接器无法嵌入 Win32 资源，本机也没有 `rc.exe`/`windres`，所以清单要靠 `scripts\addmanifest` 事后往 PE 里**追加 `.rsrc` 段**（原理见 6.6）。`addmanifest` 支持原地输入输出（先把整个 exe 读进内存再写回），也支持分离输出。
- **不注入清单的后果**：comctl32 退回 5.x，`ListView` 收不到宽字符消息，界面异常/卡死。
- 其它可选脚本：`go run .\scripts\genicon` 重新生成 `assets\securevault.ico`（9 种尺寸，纯 Go 画盾牌+锁，无外部工具）。

### 8.3 产物

| 项 | 值 |
|---|---|
| 位置 | `dist\SecureVault.exe` |
| 大小 | 约 **7.7 MB**（多次构建实测 7.74–7.76 MB 之间，随 Go 工具链与内嵌图标/资源略有浮动；注入清单前约少 10 KB） |
| 形态 | 单文件、`CGO_ENABLED=0`、无外部 DLL 依赖（系统 DLL 除外） |
| 运行期生成 | exe 同级 `SecureVaultData\`（首次运行自动创建） |

> 校验建议：`Get-Item dist\SecureVault.exe | Select-Object Length`；想确认清单已注入，可在二进制里搜 `Microsoft.Windows.Common-Controls` 或 `PerMonitorV2`，或检查是否存在 `.rsrc` 段。

---

## 9. 测试策略与怎么跑

### 9.1 单元测试分布

| 文件 | 顶层 Test 函数（实测） | 覆盖内容 |
|---|---|---|
| `internal/keystore/keystore_test.go` | 38 | 初始化→解锁→错密码→改密码→恢复码重置/轮换→冷却锁定；PBKDF2 权威向量回归；恢复码归一化（大小写/横线/空格） |
| `internal/copier/copier_test.go` | 23 | 增量（新增/改动/无变化）、同名策略、长路径（>260 字符）、中文/超长文件名、重试、空间不足（注入替身）、`CopyPaths`、`ResolveDestDir`、`ScanOnly`、并发 |
| `internal/copier/skiphidden_windows_test.go` | 1 | Windows 隐藏/系统属性（`SkipHidden`）真实文件系统行为 |
| `internal/presence/presence_test.go` | 21 | 纯逻辑：`ParseUSBInstanceID`、USBSTOR 序列号兜底、`BusTypeFromInstanceID`、`BusTypeString`、`DriveMaskToLetters`、`NormalizeLetter`、广播结构体布局、去重、移除走缓存、超时返回部分信息 |
| `internal/store/store_test.go` | 18 | 增删查改往返、关键词/字段/分页、设置读写、增量标记、`DeleteMarksUnder` 边界，以及**磁盘上搜不到明文序列号**的断言 |
| `internal/logger/logger_test.go` | 17 | 按天分文件、跨天切换、行格式、保留策略、`ClearBefore/ClearAll`、并发写、IO 失败不 panic |
| `internal/engine`、`internal/app` | — | 无独立 `_test.go`；由 `scripts/smoke`（46 项断言）与 `SecureVault.exe -autotest`（22 项断言）端到端覆盖 |

> 上表是**顶层 `func Test` 函数数量**（本机实测）。`PROGRESS.md` 第五节的汇总口径是按用例/子用例统计：`copier ok`、`keystore` 39 个、`logger` 16 个、`presence` 25 个、`store` 17 个——两者统计方式不同，不代表测试缺失。

跑法：

```powershell
. .\scripts\dev.ps1
go test ./internal/...            # 全部
go test ./internal/keystore -v    # 单包
go build ./...                    # 全量编译
gofmt -l .\cmd .\internal .\scripts   # 格式检查（无输出即通过）
go vet ./...                      # 静态检查
```

> `go test -race` **不可用**：`-race` 强制要求 cgo，与 `CGO_ENABLED=0` 冲突（见第 11 节 L2）。

### 9.2 端到端自检 `scripts/smoke`

```powershell
go run .\scripts\smoke
```

在系统临时目录里搭一个假环境，走**真实运行时路径**（不是 mock）：

1. 造一个假 exe → `paths.New` 验证"数据目录全部位于 exe 同级"；
2. 日志按天落盘；
3. keystore：初始状态 → 初始化拿恢复码 → 错密码被拒 → 正确密码解锁 → DEK 32 字节；
4. 打开库、写设置（复制模式 + 目标目录）；
5. 造一棵"模拟 U 盘"目录树（含中文名、超长名、空文件、多级子目录）；
6. `engine.New` + 手工投递 `DriveEvent`（USB 假设备）→ 等待真实复制 → 断言文件数/字节数/结构/中文名/长名/内容/空文件；
7. 监控记录写入与关键字段（卷标、VID:PID、序列号、处理结果）；
8. 增量：改一个文件 + 加一个新文件 → 只复制这两个；第三次什么都不改 → 文件数不再变化；
9. 文件过滤：加 `*.tmp` 与 `.mp4` 名单 → 二者都不落盘；
10. 设备排除：加 USB 序列号规则 → 再插 → 记录为 `ActionExcluded`；
11. 定时切换：加一条全天计划 → `Recompute()` 返回计划里的模式；
12. **加密落盘检查**：`st.Close()` 后遍历数据目录，断言搜不到 `SMOKE-SN-0001` / `SMOKE-DISK-0001` / `TEST-USBDISK` 明文；
13. 重新打开库（模拟"上锁后再解锁"）→ 设置与记录仍在；
14. 恢复码解锁 → `ResetWithRecovery`（故意用小写+去掉横线的码）→ 新密码可解锁。

全部通过时输出 `✅ 全部自检通过`，任意一项失败退出码为 1。

### 9.3 界面验证工具

| 工具 | 用法 | 作用 |
|---|---|---|
| `scripts/shot` | `go run .\scripts\shot out.png`（整屏）或 `out.png <窗口标题子串>`（指定窗口） | 截图为 PNG。优先 `PrintWindow(PW_RENDERFULLCONTENT)`，输出全白时回退窗口 DC + `BitBlt`，再不济直接 `BitBlt` 屏幕 |
| `scripts/winlist` | `go run .\scripts\winlist SecureVault` | 列出匹配进程的所有顶层窗口与子窗口（类名/标题/可见性/位置尺寸），排查"控件没显示/位置错了" |
| `scripts/lvprobe` | 目录存在但当前为空目录 | 预留的 ListView 探针（无 `main.go`，不参与构建） |

### 9.4 `-review` 界面评审模式

```powershell
dist\SecureVault.exe -review      # 或设置 SV_UI_REVIEW=1
```

由 `internal/app/review.go` 实现，**跳过整个密码流程**直接进主面板：

1. 用固定密码 `ReviewPassw0rd!` 初始化密钥库（已初始化则直接解锁）；
2. 打开数据库，若设置为空则把复制目标填成 `D:\USB备份`、手动模式设为复制模式；
3. 造演示数据（仅当记录表为空）：4 条监控记录（含 USB/NVMe、已复制/已排除/仅记录）、3 条排除规则（容量 `<=2GB`、卷标"工作盘"、总线 "NVMe"）、3 条文件名单（`*.tmp`、`node_modules`、`.log`）、2 条定时计划（夜间复制、上班时段监控）、1 条别名；
4. 打开主面板并把当前 DPI 打到 stdout；
5. **120 秒后自动退出**，避免残留进程。

> ⚠️ 评审模式会**真实写入** `SecureVaultData`（演示数据落在真实库里），请在开发机上使用，不要对着自己的正式数据跑。
> 另外它的日志打印/注释里写的是"15 秒"，实际是 120 秒。

**用途**：逐页检查排版、DPI（144 DPI 下 100%/125% 混合屏）、控件是否重叠；配合 `scripts/shot` 出图评审。

### 9.5 自动验收 `-autotest`

```powershell
dist\SecureVault.exe -autotest
type SecureVaultData\autotest.log     # 逐项 PASS/FAIL + 汇总（同时打印到 stdout）
```

由 `internal/app/autotest.go` 实现，在 exe 同级 `SecureVaultData\autotest.log` 记录结果，**34 项断言**覆盖真实代码路径：

1. `ks.Initialize()` 首次设置密码成功、恢复码格式为 `XXXX-XXXX-XXXX`；
2. 正确密码解锁、`DEK()` 返回 32 字节、错误密码被拒绝；
3. 直接调用 `a.afterUnlock()` 走**真实解锁流程**（打开库 / 启动引擎 / 创建主面板），断言 `!a.locked`、`a.st != nil`、`a.eng != nil`、主面板句柄非 0、7 个页面全部构建；
4. **逐页切换**：每一页都断言"当前页有可见控件"且"其他页合计可见 0 个"（防止扁平结构下页面控件互相残留）；
5. 写入设置（监控模式 + 复制目标）后 `simulateArrival()` 投递一条假设备事件，断言监控记录条数增加、生效模式为监控模式；
6. 界面/DPI 硬指标：窗口客户区尺寸合理、**所有控件都在内容区可视范围内**（`countControlsOutsidePage`）、**没有零尺寸的可见控件**（空标签允许 0 尺寸）、**非当前页控件全部隐藏**；
7. `a.lock()` 后断言数据库已关闭、引擎已停止、`DEK()` 报错（密钥已清空）；
8. 用恢复码 `ResetWithRecovery()` 重置密码，断言新恢复码与旧码不同、新密码可用；
9. 修改密码必须校验当前密码：错误旧密码被拒、正确旧密码生效、改密后旧密码失效新密码可用、改密不丢密钥。

**评审模式可逐页自动切换**（便于截屏验收每一页）：

```powershell
dist\SecureVault.exe -review -review-autoplay=7   # 每 7 秒换一页，循环
```

退出码：全部通过为 0，有失败为 1。**它会真实改写目标数据目录里的密钥库/数据库**，只能在开发或专用测试目录里跑。

---

## 10. 扩展指南

### 10.1 新增一种「设备排除规则」类型

以新增"厂商（Vendor）"规则为例：

1. **`internal/model/model.go`**
   - 加常量：`RuleVendor ExcludeRuleType = "vendor"`；
   - `RuleTypeLabel()` 加中文名分支（界面下拉显示的文字）；
   - `AllRuleTypes()` 里按想要的顺序插入（**下拉框的顺序就是这个切片的顺序**）。
2. **`internal/engine/engine.go` → `matchRule()`**：加一个 `case`，决定匹配语义。可用的现成手段：`eq(field)`（完全相等、忽略大小写）、`fileutil.Match(v, field)`（通配符）、`containsFold`（包含）、`model.MatchCapacity`（容量表达式）。
3. **如果匹配值需要特殊校验**（像容量那样）：在 `internal/model/format.go` 加解析/校验函数，并在 `internal/app/page_exclude.go → addRule()` 里加前置校验与提示文案。
4. **存储层不用改**：`exclude_rules.rule_type` 是明文 TEXT 列，`value`/`remark` 统一加密存储。
5. **界面层一般不用改**：下拉项来自 `AllRuleTypes()`；`RuleTypeLabel()` 决定表格第一列显示。
6. **测试**：`internal/engine` 目前没有单测文件，建议新建 `internal/engine/engine_test.go`（用假的 `Sink` 实现即可，`Sink` 是接口），或在 `scripts/smoke` 里加一段断言。

同类改动要注意：**已存在的规则不会被重新评估**，规则只在下一次插拔事件时生效；添加/删除规则后 `page_exclude` 会调用 `a.eng.Reload()` 立即重载。

### 10.2 新增一个标签页

1. **新建 `internal/app/page_xxx.go`**：

   ```go
   type xxxPage struct {
       pageBase                 // 提供 newContainer / host / setVisible / dispose
       app  *App
       form *ui.Form            // 挂载窗口（= pageBase.container，就是主窗口）
       // ... 自己的控件
   }

   func newXxxPage(a *App) *xxxPage { return &xxxPage{app: a} }

   func (p *xxxPage) build(parent *ui.Form) {
       p.newContainer(parent, "标题")
       p.form = p.host()          // 注意：不是 p.container 造一个新窗口，而是主窗口
       // 用 ui.NewLabel/NewButton/... 创建控件，挂到 p.form 上；
       // 控件会被自动登记到本页（Form.SetControlOwner + TrackControl），切页时自动显隐。
       // 坐标此时无所谓，layout 会重排。
   }
   func (p *xxxPage) layout(r win.Rect) { /* r 是**设备像素**；写死的尺寸一律过 ui.K() */ }
   func (p *xxxPage) activated() { /* 切到本页时刷新 */ }
   func (p *xxxPage) refresh()   { /* 可选：状态栏每秒刷新时联动 */ }
   ```

2. **`internal/app/ui.go`**：
   - `tabTitles` 里加标题（顺序就是标签顺序）；
   - `build()` 里的 `mk("标题", newXxxPage(a))` 列表加一行（**顺序必须与 `tabTitles` 一致**，`selectPage` 用下标对应）；
   - 文件末尾的 `var _ page = (*xxxPage)(nil)` 断言块补一行（保证接口实现完整）。
3. **页面显隐**：只要实现了 `setVisible(bool)`（`pageBase` 已提供），`mainUI.layout()` 会在首次布局后由 `selectPage` 统一显示/隐藏本页控件。
4. **需要代码跳转到该页**：`a.ui.selectPage(索引)`（0 基；现有顺序：0 状态、1 监控记录、2 排除名单、3 定时切换、4 工具、5 设置、6 日志）。
5. **注意事项**：
   - **不要在 `layout()` 里创建控件**：`layout` 会被反复调用（窗口尺寸变化、DPI 变化），
     在里面创建控件会不断堆积（曾经每次布局都多出一个卡片标题，把内容一路推到窗口外）。
     卡片请用 `NewCard`（仅 build 阶段）+ `LayoutCard`（layout 阶段）成对使用。
   - 控件必须带 `WS_VISIBLE`（`ui.createChild` 已处理，除非你刻意用 `createChildHidden`）;
   - 弹出右键菜单时 `TrackPopup` 需要**顶层窗口句柄**：用 `p.app.ownerWindow()`（见 `page_tools.go` 的别名右键菜单）。

### 10.3 新增一个设置项

1. `internal/model/model.go`：`Settings` 加字段，`DefaultSettings()` 给默认值。
2. `internal/app/page_settings.go`：`build()` 创建控件 → `activated()` 回填 → `save()` 读取并写入 `s`，必要时在保存后触发副作用（例如 `a.log.SetRetention`、`a.syncAutoStart`）。
3. `internal/engine/engine.go`：设置已经通过 `Settings()/SaveSettings()` 整体存取，一般不用改；若影响引擎行为，直接读 `e.settings.XXX`（读时持 `e.mu.RLock()`）。
4. **兼容性提醒**：设置是整体 JSON 存进 `settings_kv` 的，加字段不需要迁移；但**老数据反序列化后新字段是零值**（不是 `DefaultSettings()` 的值），所以要么在使用处判零值兜底，要么在 `LoadSettings` 之后做一次补齐。

### 10.4 给监控记录新增一个字段

需要同步改 5 处（漏一处就会出现"界面空白"或"读不出来"）：

1. `internal/model/model.go`：`DeviceInfo` 或 `Record` 加字段（`DeviceInfo` 是嵌入的，记录表会跟着带）。
2. `internal/store/store.go`：
   - `initSchema` 的 `CREATE TABLE records`（⚠️ 见第 11 节的**迁移缺口**：`CREATE TABLE IF NOT EXISTS` 不会给已存在的库加列）；
   - `recordColumns` 常量、`insertRecordSQL`、`InsertRecord`（加密列加进 `sealer` 列表与参数）、`scanRecord`（`Scan` 列表 + 解密）。
3. `internal/presence/presence.go`：如果是设备属性，在 `probeVolume`/`applyDiskEntry` 里采集（记得处理超时 `partialRecorder`）。
4. `internal/app/page_records.go`：`recordColumns`（列表表头）、`query()` 的行输出、`showDetail()`、`exportCSV()` 的表头与行。
5. `scripts/smoke/main.go`：加断言（尤其是"磁盘上搜不到明文"那条要用新字段的示例值）。

### 10.5 其它常见扩展点

| 想做的事 | 改哪里 |
|---|---|
| 新增文件过滤维度（如"跳过大于 N MB"） | `model.FileFilter` + `copier/scan.go → shouldCopyInfo()` + 名单类型（`model.NameList` 的 kind + `page_exclude.go` 下拉 + `engine.buildFilter()`） |
| 复制策略可配置（并发数/重试/同名策略） | `model.DefaultCopyOptions()` 已定义字段，目前未接入界面；在 `engine.runCopy()` 组装 `opts` 时按设置覆盖，并给设置页加控件 |
| 新的复制触发方式（如手动"立即复制某个盘"） | 复用 `copier.Engine`：`app` 层拿 `eng.BuildFilter()` + `storeIndex`，仿照 `uithread.go → runDesktopCopyJob` 起后台任务，回投 UI |
| 托盘菜单加一项 | `internal/app/app.go`：加 ID 常量、`showTrayMenu()` 里 `AddItem`、`switch` 里处理 |
| 新增自定义消息 | 沿用 `win.WM_APP + n` 的约定，别和 `wmTrayCallback(1)/wmEngineEvent(2)/wmScheduleTick(3)/wmRunOnUI(10)/wmTick(11)` 冲突 |

---

## 11. 已知限制与技术债

### 11.1 `PROGRESS.md` 第四节（原文照抄）

| # | 项 | 说明 |
|---|---|---|
| L1 | 无 C 编译器、无外网 | `lxn/walk` 必须 cgo + 联网才能编译，本机两者都不具备，故 UI 改为**自研纯 Go 原生 Win32 封装**（用户已确认方案 A）。因此代码不 import `github.com/lxn/walk`。 |
| L2 | `go test -race` 不可用 | Go 的 `-race` 强制要求 cgo，与 `CGO_ENABLED=0` 冲突。并发安全靠设计 + 并发用例覆盖。 |
| L3 | USB 真机分支未端到端验证 | 本机只有 NVMe 固定盘，没有可插拔 U 盘。VID/PID/USB 序列号解析、USBSTOR 兜底、设备父节点上溯等逻辑已用伪造设备实例 ID 与伪造系统广播做单测覆盖，但**建议你插一次真 U 盘复核**。 |
| L4 | 复制目标不能位于源盘内部 | 若把 U 盘内容复制回该 U 盘自身，增量索引会导致行为不符合预期；界面未拦截该配置。 |
| L5 | 别名可以重名 | 两个别名指向同一设备时，按数据库返回顺序取第一个。 |
| L6 | 截图工具对深层子控件无效 | `WM_PRINT`/`PrintWindow` 不会递归绘制子窗口的子窗口，因此 `scripts/shot` 抓主面板时只能看到标签条与背景；界面正确性改用 `scripts/winlist` 核对窗口树与几何尺寸，以及 `-autotest` 的控件几何断言。 |
| L7 | 监控记录搜索有 2 万行上限 | `QueryRecords` 先在 SQL 里 `LIMIT 20000` 再在 Go 侧解密过滤（密文无法用 SQL 模糊匹配），记录超过 2 万条时搜索与总数会被截断。日常量级（每次插拔一行）不会触及。 |
| L8 | 数据库无 schema 迁移机制 | 建表只用 `CREATE TABLE IF NOT EXISTS`（见 11.2 的 D4）。 |
| L9 | ~~定时计划无法单独停用~~ | **已修复**：定时切换页新增「启用/停用选中」按钮。 |
| ~~L10~~ | ~~「最小化时收进托盘」设置项无实际作用~~ | **已修复（第 7 轮）**：`ui.Form` 新增 `Minimized` 回调，最小化窗口时真的收进托盘并上锁（后台监控照常运行）。 |

第 7 轮（2026-09-18）新增的已知限制：

| # | 项 | 说明 |
|---|---|---|
| L11 | 后台自动运行依赖 DPAPI 保护的密钥副本 | 为了让开机自启后不需要输密码也能自动复制，`keystore.json` 里会保存一份由 Windows 用户凭据（DPAPI）保护的 DEK 副本。它只在本机本用户下可解，但"能以该用户身份运行程序的人"确实不需要密码就能拿到 DEK。不接受这个权衡时请在设置里关闭「关闭面板后继续在后台监控与复制」。 |
| L12 | 开发模式使用独立数据目录 | `-review` / `-autotest` 使用 `SecureVaultData-dev\`（`paths.DevDirName`），与正式数据 `SecureVaultData\` 完全隔离。 |

> 说明：`PROGRESS.md` 第三节目前记录 **19 项**已修复问题（1–10 见 `CHANGELOG.md` 的 Fixed 表，11–15 为 UI 层修复，16–19 为本文档 11.2 节核对后新修的问题，详见下方 11.2 的更新）。

### 11.2 阅读源码时另外确认到的技术债与文档不一致

> 以下每一条都是**对照源码逐行确认**的结果，不是推测；括号里给出位置，便于修复。

| # | 位置 | 现象 | 影响 |
|---|---|---|---|
| ~~D1~~ | `internal/app/auth.go` | ~~`e := f.UserData().(string)` 断言 nil 接口~~ | **已修复**：这是本文档核对时发现的最严重问题——首次设置密码点「确定」即 panic，恢复码窗口根本弹不出来。已删除该死代码。教训：`-autotest` 走的是 `ks.Initialize()` 直连路径，绕过了对话框，所以自动验收必须有"真的点一遍按钮"的用例或在 CI 里加 UI 自动化。 |
| ~~D2~~ | `internal/keystore/keystore.go:ChangePassword` | ~~`_ = oldPwd`，不校验旧密码~~ | **已修复**：改为真正校验（`deriveAndVerify` + 常量时间比对），失败返回 `ErrWrongPassword`，且不计入 60 秒冷却（避免改密时把自己锁住）。`-autotest` 新增 5 项断言守住：错误旧密码被拒 / 正确旧密码可改 / 改密后密钥未丢 / 旧密码失效 / 新密码可用。 |
| ~~D3~~ | `internal/app/page_settings.go` + `internal/model/model.go:MinimizeToTray` | ~~该选项只被保存/回填，没有任何地方读它~~ | **已修复（第 7 轮）**：`ui.Form.Minimized` → `mainUI.build` 里最小化即收进托盘 + 上锁；同时「关闭面板后继续在后台监控与复制」成为真正可配置项（`model.Settings.BackgroundMonitor`）。 |
| D4 | `internal/store/store.go:initSchema` | 只执行 `CREATE TABLE IF NOT EXISTS`，没有 schema 版本表/`ALTER TABLE` | 已存在的 `vault.db` **不会**获得新增列：按 10.4 扩展记录字段后，老库会插入失败/读取为空，需要手工迁移或删库重建 |
| D5 | `internal/store/store.go:QueryRecords` | 关键词搜索是"先 `LIMIT 20000` 再解密过滤" | 记录超过 2 万条时，搜索结果与 `total` 只反映最近 2 万条候选内的匹配；界面没有任何提示 |
| D6 | `internal/engine/engine.go:slotActive` | 重叠时间段取"列表里第一条命中"（`ListSchedule` 按 `start_time` 排序） | 计划重叠时哪条生效不直观；界面上没有冲突校验 |
| ~~D7~~ | `internal/app/page_schedule.go` | ~~「状态：已启用/已停用」列无法切换~~ | **已修复**：新增「启用/停用选中」按钮（只翻 `Enabled` 位、保留选中行便于连续操作），停用后 `Reload()` 立即生效 |
| ~~D8~~ | `internal/app/review.go` | ~~注释写"15 秒"，实际 120 秒~~ | **已修复**（改注释）；同时让评审模式在口令不匹配时自动重建数据目录，不再因旧数据而打不开面板 |
| D9 | `internal/paths/paths.go` | `ConfigFile()`（`settings.json`）、`BackupDir()`（`backup\`）没有调用方 | 死代码；设置实际存在 `vault.db` 的 `settings_kv` 表里 |
| D10 | `internal/app/auth.go` | `showRecoveryCode(code string, firstTime bool)` 的 `firstTime` 参数未被使用 | 无害的死参数 |
| D11 | `internal/copier/copier.go:Options.AferoFS` | 占位字段（注释已标"勿使用"） | 死字段 |
| ~~D12~~ | `scripts/lvprobe/` | ~~空目录~~ | **已清理** |
| ~~D13~~ | `internal/presence` 的导出名与 `CONTRACTS.md` | ~~契约写 `ParseUSBPath`，实现是 `ParseUSBInstanceID`~~ | **已修**：见本节末尾的命名修正说明，以代码为准 |
| D14 | `internal/app/app.go:onMessage` | `wmScheduleTick` 常量已定义但没有发送方（定时重算实际由 `engine.tickLoop` 的 20 秒 ticker 驱动） | 无害；理解时序时别看错地方 |

> **核对方式**：11.2 的每一条都是先读源码再确认的。其中 D1/D2 是**会导致崩溃与安全语义不符**的实质缺陷，已修复并用 `-autotest`（现 68 项）与 `keystore` 单测双重覆盖；D3/D7/D8/D12/D13 属可用性/整洁性问题，也已处理。D4–D6、D9–D11、D14 保留为已知技术债。
>
> **第 7 轮的教训**：上面这些"逐行读源码"核对 + `-autotest`（内部函数调用）**仍然漏掉了三个致命缺陷**
> （关闭面板会退出进程、托盘创建失败即退出、关掉面板后不再复制），因为它们的触发点在"用户真的去点、去关窗口"之后。
> 现在补上了 `scripts\e2e`（真实按钮点击/窗口消息的端到端验收）与"关面板后真的复制一次"的断言。

### 11.3 行为边界（设计如此，但值得知道）

### 11.4 界面层（第 8 轮重构后的结构）

| 关注点 | 实现 |
|---|---|
| 主题 | `internal/ui/canvas.go` 的 `Theme`（`ui.T` / `ui.ColorXxx()`）：白底 `#FFFFFF`、浅灰轨道 `#F3F3F3`、描边 `#E0E0E0`、文本 `#1F1F1F`、强调 `#0078D7` |
| 卡片 | `ui.NewGroupBox` 不再用系统 `BS_GROUPBOX`，而是 `STATIC` + 子类化自绘：白底圆角 + 细边框 + 加粗标题 |
| 标签条 | `ui.SegmentedControl`（`segmented.go`）：浅灰轨道 + 白色选中块 + 底部强调条；点击由控件自己命中判断（`clickAt`）后回调 `selectPage` |
| 滚动 | 每个页面实现 `minContentHeightDIP()`；`mainUI.layoutPages()` 按"虚拟页面高度 ± 滚动偏移"排版，`scrollBy()` 由滚轮/键盘/滚动条驱动，`applyScrollClip()` 用 `SetWindowRgn` 裁掉滚出可视区的控件 |
| 抗闪烁 | `Form` 的 `WM_PAINT` 先画到离屏位图再 `BitBlt`（`paintBuffer()`），`WM_ERASEBKGND` 直接返回；列表控件启用 `LVS_EX_DOUBLEBUFFER` |
| 防重入 | `App.activating` + `App.modalDepth` + 400ms 去重，保证"点托盘只出现一个窗口" |
| 自检钩子 | `ui.SetAutoAnswer(true)` 让确认框在自动验收里一律返回"是"；`clickSegment()` 直接给分段栏发左键消息 |

- **后台运行与界面解锁是两件事**：默认设置下 `lock()`（关闭面板 / 最小化 / Esc / 立即上锁）只销毁界面，
  引擎与数据库继续运行；开机自启（`-silent`）会用 DPAPI 保护的密钥副本自动恢复后台监测，
  不需要输密码。把设置里的「关闭面板后继续在后台监控与复制」关掉，才会退化成"上锁即停引擎、关库、清密钥"。
- **面板销毁不能结束进程**：主面板用 `ui.Form.SetQuitOnDestroy(false)`；否则 `DestroyWindow` 会 `PostQuitMessage`，
  一关窗口整个程序就退出了（第 7 轮修的就是这个）。
- **非交互会话降级**：窗口站不是 `WinSta0` 或没有输入桌面时（服务、计划任务、无桌面环境），程序只驻留托盘，不弹任何窗口，避免模态窗口永远无人点击而卡死流程。
- **工具 1 也吃文件名单**：`runDesktopCopyJob` 复用 `BuildFilter()`，桌面整理同样会跳过 `*.tmp`、白名单外的扩展名等。
- **`-silent` 之外没有别的后台模式**，也没有 Windows 服务/计划任务实现（需求明确要求"用启动文件夹快捷方式"）。
