# MechrevoMode 开发说明

面向维护者。**用户向的用法看 [README.md](README.md)** —— 那里只讲怎么用，这里讲为什么这么做。

## 工作原理

GCU 后台一共**两个**组件，缺一不可：

| 组件 | 形态 | 作用 |
| --- | --- | --- |
| `GCUBridge` | Windows 服务 | 内嵌 MQTT broker，监听 `127.0.0.1:13688` |
| `GCUService.exe` | 普通进程 | 真正发布状态、执行命令的那个 |

本工具作为 MQTT 客户端接入（凭据是官方协议里固定的那套 `UWPClient_*`）：

```text
订阅  Fan/Status       拿温度墙、SPL/SPPT/FPPT、GPU 功耗
订阅  Tray/Status      拿当前模式与档位
发布  Fan/Control      OPERATING_OFFICE_MODE / _GAMING_ / _TURBO_ / _CUSTOM_
```

订阅面**刻意收得很窄**：不订 `System/*`。那三十来个主题（`System/Control`、
`System/BatteryInfo`、`Monitor/Status`…）里能推数据的只有几个信息类主题，托盘提示
用不上；`System/Control` 更是**命令主题**，只收命令、不回包，订了也一条都不来。
GCU 则**任何主题都没有**整机功耗字段。

GCU 的 JSON **写法不统一**：同一个量在一处是字符串 `"63"`、另一处是原生数字 `2990`，
两种都得能解析（见 `gcuNum`）。

两个细节值得一提：

- **档位落定期**。GCU 切换过程中会先回几条旧状态，照单全收的话档位会被冲回上一个值。
  所以下发后有一段落定期，期间只接受与目标 `mode+slot` 完全一致的回报。
- **后端自愈**。`GCUBridge` 开机时会自己异常终止（系统日志事件 7023），
  而服务上**没有**配置恢复动作 —— 于是端口无人监听、永远连不上，
  直到你手动打开一次官方控制台。本工具会检测并主动把后端拉起来
  （间隔 40 秒、留足冷启动宽限期，不会催得过急）。

## 启动命令的三个触发时机

「程序启动时跑一次」对休眠/睡眠之后的场景是不够的：机器睡一觉，固件或驱动会把降压、
风扇曲线拉回默认值，而托盘程序自己**从不睡** —— 它从头到尾没退出过，也就不会有第二次
「程序启动」来把命令重跑一遍。所以加了三个可任意组合的时机：

| 时机 | 靠什么感知 |
| --- | --- |
| 程序启动 | 启动流程里直接调一次 |
| 睡眠/休眠唤醒 | `WM_POWERBROADCAST`（`PBT_APMRESUME*`） |
| 会话解锁 | `WM_WTSSESSION_CHANGE`（`WTS_SESSION_UNLOCK`） |

三条要注意的：

- **`WM_POWERBROADCAST` 是广播的、会重复来**：一次合盖再打开通常先到 `RESUMEAUTOMATIC`，
  用户一动设备再补 `RESUMESUSPEND`，随后往往还跟着一次解锁。指望它们按固定顺序排队是
  不可能的，所以 `scheduleRun` 只按时间合并（5 秒内的重复诉求重排同一次等待），
  而不是去数条数、更不是每条都跑。
- **`WM_WTSSESSION_CHANGE` 必须先登记**：不像电源广播那样不请自来，
  `WTSRegisterSessionNotification` 不成功就一条都收不到（失败只记日志，不影响其它功能）。
- **唤醒后额外等 3 秒**：刚落地那几秒磁盘和网络还没回来，而第三方工具失败是静默的
  —— 本工具不捕获、也不解释它们的退出码，`runStartupCommand` 只能报告「有没有拉起来」。

触发时机的配置项 (`run_on_launch` / `run_on_resume` / `run_on_unlock`) 是从 v7 才有的，
老配置加载时由 `migrateRunTriggers` 归一回「程序启动时」，避免升级后命令静默失效。

## 为什么没有风扇转速

早先提示里有两行 `🌀2990RPM`，已整块移除。原因是那条数据只有 GCU 的
`System/FanInfo` 一个来源，而它要求 GCU 内部先进入一套「武装」状态才会推送：

- 「武装」状态活在 **`GCUService` 进程内存里**，每次开机重置；
  结束进程就被清掉（GCUBridge 约 45 秒后会自动拉回新实例，但新实例同样不推）。
- **开机那一次武装会失败且不重试** —— 这就是「每次开机都要手动开一次官方控制台」的根子。

试过的唤醒手段：

| 手段 | 结果 |
| --- | --- |
| 从 MQTT 侧发 `System/Control {"Action":"System_ON"}` | 试了 5 种变体（client 1/2/7、QoS 0/1、带 retain、订阅 `System/#` 或 `#`）**全都叫不醒** —— 官方控制台的命令不走这条 TCP 通道，而是经 GCUBridge 的本地 IPC 桥 |
| 启动官方控制台 UWP 界面 | **有效**，约 10 秒后遥测开始。但会把控制台窗口拉到前台，开机时打扰用户，而且实测 **3 分钟里弹了 3 次**（`handleFanInfo` 里「收到转速就解除限流」这条设计在会反复断的信号上等于把限流关掉了） |

结论：代价大于收益，整块去掉。现在提示只显示温度墙 / 功耗墙两行，走 `Fan/Status`，一直很稳。

> 如果哪天想把它加回来，`_research/` 里那次排查用的脚本都还在（`_arm_*.py`、
> `_cold_arm_test.py`、`_e2e_arm_validate.py`、`_fix_validate.py` 等），
> 它们记录的是当时真机上的实测结论。

## 程序图标

exe 的图标来自 `img/logo.ico`，做法是把图标编译成资源对象 `rsrc_windows_amd64.syso`
放仓库根目录 —— Go 链接器会**自动**把同目录下的 `.syso` 链进产物，不需要改任何代码。
这个 `.syso` 已入库，所以直接 `go build` 也有图标。

窗口图标不用另外设：`main.go` 的 `wndClassEx` 不填 `HIcon`，Windows 会回退到 exe 自身的
图标，所以文件图标 / 标题栏 / Alt-Tab / 任务栏一次全覆盖。

换了 `logo.ico` 之后跑一次 `img\make-icon.bat` 重新生成（需要
`go install github.com/akavel/rsrc@latest`）。它只认 `.ico` 里的 256/48/32/16 四个尺寸，
换图时别只留一个尺寸。

验证图标真的进去了（不是只有个空目录项）：

```bash
python _research/_verify_exe_icon.py build/MechrevoMode.exe img/logo.ico
python _research/_shell_icon_check.py build/MechrevoMode.exe   # 问 Shell 要图标
```

后者用的是 `ExtractIconExW` —— 和资源管理器取文件图标走的是同一个口子，
返回 1 才说明 Windows 真的认这张图。（解析 `.rsrc` 目录树时注意：`rsrc` 生成的
子目录偏移是**相对节起点**的，不是绝对 RVA，别按 RVA 去递归。）

## 构建脚本的两个坑

`build.bat` / `img\make-icon.bat` 是纯 ASCII 的，这两条都是踩过之后写下来的：

1. **脚本保持纯 ASCII**。cmd 在 UTF-8 代码页（65001）下解析含中文的 `.bat` 会拆错行，
   连后面的纯 ASCII 行一起毁掉（`set OUTEXE=` 都能被吃掉）；开头加 `chcp 65001` 也救不回来。
2. **调 `go` 一定要写 `call go`**。mise / asdf / scoop 之类的 shims 目录里放的是
   `go.cmd`，而 .bat 里不写 `call` 直接调用另一个 `.cmd`，控制权就交出去不回来了 ——
   后面的步骤**静默消失**，脚本还返回 0，看起来像编译成功了。

## 代码导读

| 文件 | 职责 |
| --- | --- |
| `main.go` | 窗口过程、托盘（图标 / 提示 / 菜单）、单实例互斥体、启动流程与自检分支 |
| `telemetry.go` | 悬浮提示的显示常量（🌡 ⚡ ℃）与提示文本拼装 |
| `gcu.go` | GCU MQTT 客户端：连接与序号探测、状态解析、命令下发、后端自愈 |
| `config.go` | 配置结构与迁移、数据目录解析、开机自启（注册表 / 计划任务） |
| `ui.go` | 设置界面全部控件的创建与事件处理 |
| `power.go` | 电源计划枚举与切换（含「卓越性能」的按需复制与 GUID 固定） |
| `elevate.go` | 「以管理员身份运行」的兼容性标志读写、提权重启 |
| `icon.go` | 托盘图标位图生成（圆角正方形 + 5×7 点阵字模 + 超采样抗锯齿） |
| `icon_dump.go` | 图标自检图 |
| `win32.go` | 全部 Win32 API 绑定 |
| `img/logo.ico` | 程序图标源文件；`img/make-icon.bat` 据此生成 `rsrc_windows_amd64.syso` |
| `rsrc_windows_amd64.syso` | 图标资源对象（已入库），链接器自动把它嵌进 exe |
| `build.bat` | 一键构建，产物落到 `build\`（该目录不入库） |

`_research/` 是开发期的探测脚本（Python + ctypes），用来验证界面交互、托盘行为、
MQTT 报文格式等。不参与构建，但排查问题时很好用。

## 验证过的东西

开发过程中用脚本实测过的行为（`_research/`），改代码时可直接复跑：

- 托盘提示随档位联动，且不含模式名
- 悬浮提示两行拼起来稳在 `szTip` 的 128 上限内，最宽可能值也留了余量
  （`TestTooltipFitsSzTip`、`TestComposeTipWidestFitsSzTip`）
- 提示里的图标在「提示字体（Microsoft YaHei UI）」下能画出真实字形，不是豆腐块；
  星平面 emoji 的 UTF-16 码元开销也一并钉住（`_emoji_render_check.py`、`TestTipEmojiBudget`）
- GCU 报文的两种数字写法（字符串 / 原生数字）与脏值处理（`telemetry_test.go`，用真机报文）
- 右键菜单零延迟弹出（实测 10~27 ms）
- 左键单击 / 双击立刻打开设置界面（5.5 ms）
- 开机自启的开关与保存真的会改写计划任务 / 注册表
- 数据目录不可写时正确回退 `%APPDATA%`，旧配置正确迁移
- 托盘图标在系统里实际渲染为 24×24（宽高差 0），并量化检查留白
- exe 里真的嵌着 `img/logo.ico`（`ExtractIconExW` 报 1 个图标，0 图标的是对照组）

### 一些量过的数

| 项 | 值 |
| --- | --- |
| `szTip` 容量 | 128 个 UTF-16 码元（星平面 emoji 各占 2 个） |
| `sizeof(TOOLINFO)` x64 | 72 |
| `sizeof(STARTUPINFOW)` | 104 |
| `sizeof(PROCESSENTRY32W)` | 568 |
| `sizeof(SERVICE_STATUS)` | 28 |
| 菜单弹出到可见 | 10~27 ms |
| 打开设置界面 | 5.5 ms |
