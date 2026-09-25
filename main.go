//go:build windows

package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"
)

// ---------------------------------------------------------------- 常量

const (
	wmTray         = wmApp + 1
	wmStateChanged = wmApp + 2
	wmBalloon      = wmApp + 3
	wmProbe        = wmApp + 4 // 自检：wparam = 要切换的档位下标
	wmRunDone      = wmApp + 5 // 手动运行命令结束：wparam != 0 表示成功

	trayUID = 1

	// 托盘菜单项 ID：1000 + 档位下标
	idModeBase = 1000
	idModeMax  = idModeBase + 128
)

type App struct {
	hwnd      uintptr
	nid       notifyIconData
	cfg       *Config
	gcu       *GCU
	ui        *UI
	icon      uintptr
	iconKey   int
	trayAdded bool
	menuOpen  bool
	quitting  bool
	logFile   *os.File

	pendMu   sync.Mutex
	pendText string
	runMsg   string // 最近一次手动运行的结果，供 UI 线程显示
}

var app = &App{iconKey: -9999}

// taskbarCreatedMsg 是 Explorer 重启后广播的消息，用于把托盘图标重新挂回去。
var taskbarCreatedMsg uintptr

// logPath 记录当前日志文件位置，供上次退出状态自检使用
var logPath string

// ---------------------------------------------------------------- 日志

func (a *App) logf(format string, args ...interface{}) {
	if a.logFile == nil {
		return
	}
	line := time.Now().Format("2006-01-02 15:04:05") + " " + fmt.Sprintf(format, args...) + "\r\n"
	_, _ = a.logFile.WriteString(line)
}

// recoverLog 捕获 panic。GUI 子系统没有控制台，未捕获的 panic 会让进程静默消失。
func (a *App) recoverLog(where string) {
	if r := recover(); r != nil {
		a.logf("!! PANIC @%s: %v\r\n%s", where, r, debug.Stack())
	}
}

func openLog() *os.File {
	base, err := os.UserConfigDir()
	if err != nil || base == "" {
		base = os.Getenv("APPDATA")
	}
	dir := filepath.Join(base, "MechrevoMode")
	_ = os.MkdirAll(dir, 0o755)
	p := filepath.Join(dir, "log.txt")
	logPath = p
	if fi, err := os.Stat(p); err == nil && fi.Size() > 512*1024 {
		_ = os.Remove(p)
	}
	f, err := os.OpenFile(p, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil
	}
	return f
}

// lastLogLine 读取日志文件最后一行
func lastLogLine(p string) string {
	f, err := os.Open(p)
	if err != nil {
		return ""
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil || fi.Size() == 0 {
		return ""
	}
	n := int64(2048)
	if fi.Size() < n {
		n = fi.Size()
	}
	buf := make([]byte, n)
	if _, err := f.ReadAt(buf, fi.Size()-n); err != nil {
		return ""
	}
	s := strings.TrimRight(string(buf), "\r\n")
	if i := strings.LastIndexByte(s, '\n'); i >= 0 {
		s = s[i+1:]
	}
	return strings.TrimRight(s, "\r")
}

// looksLikeCloudSync 判断路径是否位于常见的云同步目录里。
//
// 只用来给出提示，不做任何拦截 —— 用户把程序放在哪儿是他的自由。
func looksLikeCloudSync(p string) bool {
	if p == "" {
		return false
	}
	low := strings.ToLower(p)
	for _, marker := range []string{`\onedrive`, `\dropbox`, `\googledrive`, `\坚果云`, `\nutstore`} {
		if strings.Contains(low, marker) {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------- 工具

func utf16Buf(s string) []uint16 {
	r := utf16.Encode([]rune(s))
	return append(r, 0)
}

func utf16ToGoStr(buf []uint16) string {
	n := 0
	for n < len(buf) && buf[n] != 0 {
		n++
	}
	return string(utf16.Decode(buf[:n]))
}

func hasArg(want string) bool {
	for _, a := range os.Args[1:] {
		if strings.EqualFold(a, want) {
			return true
		}
	}
	return false
}

// argValue 读取 `-name value` 或 `-name=value` 形式的参数
func argValue(name string) string {
	args := os.Args[1:]
	lower := strings.ToLower(name)
	for i, a := range args {
		la := strings.ToLower(a)
		if la == lower && i+1 < len(args) {
			return args[i+1]
		}
		if strings.HasPrefix(la, lower+"=") {
			return a[len(name)+1:]
		}
	}
	return ""
}

// dumpPlans 自检：把电源方案枚举结果与一次「切到当前方案」的往返结果写入文件
func dumpPlans() {
	f, err := os.Create(filepath.Join(configDir(), "plans.txt"))
	if err != nil {
		return
	}
	defer f.Close()

	cur := activePlan()
	fmt.Fprintf(f, "active=%s (%s)\n", cur, planDisplayName(cur))
	fmt.Fprintln(f, "---- options ----")
	for i, p := range planOptions() {
		fmt.Fprintf(f, "%2d  %-40s %-10s installed=%v\n", i, p.GUID, p.Name, p.Installed)
	}
}

// testPlans 自检：逐个尝试切换内置电源方案，最后还原原来的方案
func testPlans() {
	f, err := os.Create(filepath.Join(configDir(), "plans_test.txt"))
	if err != nil {
		return
	}
	defer f.Close()

	orig := activePlan()
	fmt.Fprintf(f, "orig=%s (%s)\n", orig, planDisplayName(orig))
	for _, b := range builtinPlans {
		real, err := applyPlan(b.guid)
		if err != nil {
			fmt.Fprintf(f, "%-6s FAILED: %v\n", b.name, err)
			continue
		}
		time.Sleep(300 * time.Millisecond)
		fmt.Fprintf(f, "%-6s OK real=%s activeNow=%s\n", b.name, real, activePlan())
	}
	if orig != "" {
		real, err := applyPlan(orig)
		fmt.Fprintf(f, "restore -> %s err=%v activeNow=%s\n", real, err, activePlan())
	}
}

// testElevate 自检：往兼容性标志里写入 / 读回 / 清除 RUNASADMIN。
// 结果写到 %APPDATA%\MechrevoMode\elevate_test.txt，便于和注册表编辑器对照。
func testElevate(on bool) {
	f, err := os.Create(filepath.Join(configDir(), "elevate_test.txt"))
	if err != nil {
		return
	}
	defer f.Close()

	fmt.Fprintf(f, "exe            = %s\n", exePath())
	fmt.Fprintf(f, "valueName      = %s\n", compatValueName())
	fmt.Fprintf(f, "processElevated= %v\n", isElevated())
	fmt.Fprintf(f, "before         = %q set=%v\n", compatFlags(), isRunAsAdminSet())

	if err := setRunAsAdmin(on); err != nil {
		fmt.Fprintf(f, "setRunAsAdmin(%v) ERROR: %v\n", on, err)
		return
	}
	fmt.Fprintf(f, "after          = %q set=%v\n", compatFlags(), isRunAsAdminSet())
	fmt.Fprintln(f, "---- Layers 键下的全部值 ----")
	for name, val := range regEnumStringValues(hkeyCurrentUser, appCompatLayersKey) {
		fmt.Fprintf(f, "  %s = %q\n", name, val)
	}
}

// dumpGCU 自检：把 GCU 后端的现状与结构体尺寸写成文件。
//
// 「连不上 GCU」可能卡在好几个环节（服务没起 / 发布者没起 / 提权不够），
// 与其在日志里猜，不如一次把关键事实都摊开。结构体尺寸也要打印 ——
// Win32 结构体字段错位是静默失效（不报错、只是永远查不到东西），
// 尺寸对不上基本就能一眼看出。
func dumpGCU() {
	f, err := os.Create(filepath.Join(configDir(), "gcu.txt"))
	if err != nil {
		return
	}
	defer f.Close()

	fmt.Fprintf(f, "提权                    = %v\n", isElevated())
	fmt.Fprintf(f, "%s 服务状态 = %d (1=Stopped 4=Running)\n",
		gcuBridgeService, queryServiceState(gcuBridgeService))
	fmt.Fprintf(f, "服务 ImagePath          = %s\n", serviceImagePath(gcuBridgeService))
	fmt.Fprintf(f, "%-23s = %v\n", gcuPublisherExe+" pid", processPIDs(gcuPublisherExe))
	fmt.Fprintf(f, "%-23s = %v\n", "GCUBridge.exe pid", processPIDs("GCUBridge.exe"))
	fmt.Fprintf(f, "%-23s = %v\n", "SystrayComponent.exe pid", processPIDs("SystrayComponent.exe"))

	fmt.Fprintln(f, "---- 发布者候选（按尝试顺序）----")
	for i, c := range publisherCandidates() {
		st := "不存在"
		if fi, err := os.Stat(c); err == nil && !fi.IsDir() {
			st = fmt.Sprintf("存在 %d 字节", fi.Size())
		}
		fmt.Fprintf(f, "%2d  %s  [%s]\n", i, c, st)
	}

	fmt.Fprintln(f, "---- Win32 结构体尺寸（错位会静默失效，必须对得上）----")
	fmt.Fprintf(f, "sizeof(STARTUPINFOW)    = %d  (期望 104)\n", unsafe.Sizeof(startupInfo{}))
	fmt.Fprintf(f, "sizeof(PROCESSENTRY32W) = %d  (期望 568)\n", unsafe.Sizeof(processEntry32W{}))
	fmt.Fprintf(f, "sizeof(SERVICE_STATUS)  = %d  (期望 28)\n", unsafe.Sizeof(serviceStatus{}))
}

// testGCULaunch 自检：验证「静默启动一个进程」这条兜底路径本身可用。
//
// 兜底路径（broker 在、但发布者不在时，由本程序自己拉起 GCUService）在正常环境下
// 几乎跑不到 —— 实测 GCUBridge 会监护并重生它自己的子进程 GCUService，
// 我们还没来得及出手它就自己回来了。可代码没被执行过就等于没验证过，
// 所以留一个入口，可以指定任意 exe 单独把这条路径跑一遍。
func testGCULaunch(target string) {
	f, err := os.Create(filepath.Join(configDir(), "gcu_launch.txt"))
	if err != nil {
		return
	}
	defer f.Close()

	// 把每一步都立刻落盘（os.File 的写是直接的系统调用，没有缓冲），
	// 并且整段套一个 recover：GUI 子系统没有 stderr，panic 跑出去只会
	// 变成「进程静默消失 + 退出码 2」，什么线索都不剩。
	defer func() {
		if r := recover(); r != nil {
			fmt.Fprintf(f, "!! PANIC: %v\n%s\n", r, debug.Stack())
			f.Sync()
		}
	}()

	if target == "" {
		fmt.Fprintln(f, "没有指定目标 exe，用法：-test-gcu-launch <exe>")
		return
	}
	fmt.Fprintf(f, "提权            = %v\n", isElevated())
	fmt.Fprintf(f, "目标            = %s\n", target)
	if st, err := os.Stat(target); err != nil {
		fmt.Fprintf(f, "目标不可用      = %v\n", err)
		return
	} else {
		fmt.Fprintf(f, "目标大小        = %d 字节\n", st.Size())
	}

	fmt.Fprintln(f, "-> 进入 launchHidden")
	pid, err := launchHidden(target)
	fmt.Fprintln(f, "-> launchHidden 已返回")
	fmt.Fprintf(f, "launchHidden    = pid=%d err=%v\n", pid, err)
	if err != nil || pid == 0 {
		return
	}
	time.Sleep(1500 * time.Millisecond)
	img := processImageName(pid)
	want := filepath.Base(target)
	fmt.Fprintf(f, "1.5 秒后映像名   = %q（期望 %q）一致=%v\n", img, want, strings.EqualFold(img, want))

	if err := terminateProcess(pid); err != nil {
		fmt.Fprintf(f, "收尾结束进程    = 失败 %v\n", err)
		return
	}
	time.Sleep(500 * time.Millisecond)
	fmt.Fprintf(f, "收尾后还在吗     = %v（期望 false）\n", processImageName(pid) != "")
	fmt.Fprintln(f, "=> 结论：三个方向都通过（能起、能按 pid 认出、能收掉）")
}

// ---------------------------------------------------------------- 状态与图标

// current 返回当前档位下标；online 为 false 时无意义
func (a *App) current() (int, bool) {
	mode, profile, online := a.gcu.Snapshot()
	if !online || mode < 0 {
		return -1, false
	}
	return a.cfg.indexForModeSlot(mode, profile), true
}

// tooltip 光标停在托盘图标上时显示的文字：只报当前模式名
func (a *App) tooltip() string {
	idx, online := a.current()
	if !online {
		return "未连接"
	}
	if idx < 0 {
		return "机械革命模式"
	}
	return a.cfg.Items[idx].Name
}

func fillUTF16(dst []uint16, s string) {
	for i := range dst {
		dst[i] = 0
	}
	if len(dst) == 0 {
		return
	}
	u := utf16Buf(s)
	if len(u) > len(dst) {
		u = u[:len(dst)]
		u[len(u)-1] = 0
	}
	copy(dst, u)
}

func setTip(nid *notifyIconData, s string) {
	fillUTF16(nid.Tip[:], s)
}

// balloon 弹一个气泡提示
func (a *App) balloon(title, text string) {
	if a.hwnd == 0 || !a.trayAdded {
		return
	}
	fillUTF16(a.nid.Info[:], text)
	fillUTF16(a.nid.InfoTitle[:], title)
	a.nid.InfoFlags = niifInfo
	a.nid.TimeoutVersion = 4000
	a.nid.Flags = nifMessage | nifIcon | nifTip | nifInfo
	pShellNotifyIconW.Call(nimModify, uintptr(unsafe.Pointer(&a.nid)))
	a.nid.Flags = nifMessage | nifIcon | nifTip
}

// queueBalloon 供后台 goroutine 安全地请求弹气泡
func (a *App) queueBalloon(title, text string) {
	a.pendMu.Lock()
	a.pendText = title + "\x00" + text
	a.pendMu.Unlock()
	if a.hwnd != 0 {
		pPostMessageW.Call(a.hwnd, wmBalloon, 0, 0)
	}
}

func (a *App) flushBalloon() {
	a.pendMu.Lock()
	t := a.pendText
	a.pendText = ""
	a.pendMu.Unlock()
	if t == "" {
		return
	}
	parts := strings.SplitN(t, "\x00", 2)
	if len(parts) == 2 {
		a.balloon(parts[0], parts[1])
	}
}

func smallIconSize() int {
	n := int(getSystemMetrics(smCXSmIcon))
	if n < 16 {
		n = 16
	}
	if n > 64 {
		n = 64
	}
	return n
}

// syncTray 按当前状态刷新托盘图标与提示（仅 UI 线程调用）
func (a *App) syncTray() {
	idx, online := a.current()

	rgb := uint32(colUnknown)
	glyph := rune('?')
	key := -1
	if online && idx >= 0 {
		it := a.cfg.Items[idx]
		rgb = it.IconColor
		if g := firstRune(it.IconGlyph); g != "" {
			glyph = []rune(g)[0]
		}
		key = idx
	}

	// 需要换图标时先造新的，但**不要**立刻销毁旧的：托盘此刻还指着旧句柄，
	// 先销毁会让托盘在一小段时间里读已释放的内存，偶尔画出乱七八糟的图案。
	// 正确顺序是「让托盘指向新图标（NIM_MODIFY）之后」再销毁旧的。
	var old uintptr
	if key != a.iconKey {
		if h := makeHIcon(smallIconSize(), rgb, glyph); h != 0 {
			old = a.icon
			a.icon = h
			a.iconKey = key
			name := "未连接"
			if key >= 0 {
				name = a.cfg.Items[key].Name
			}
			a.logf("图标 -> %s (#%06X %c)", name, rgb, glyph)
		}
	}

	if !a.trayAdded {
		destroyIcon(old) // 没在托盘上，没人引用，直接回收
		return
	}
	a.nid.HIcon = a.icon
	a.nid.Flags = nifMessage | nifIcon | nifTip
	setTip(&a.nid, a.tooltip())
	pShellNotifyIconW.Call(nimModify, uintptr(unsafe.Pointer(&a.nid)))
	destroyIcon(old)
}

func (a *App) addTrayIcon() {
	if a.trayAdded {
		return
	}
	a.nid = notifyIconData{}
	a.nid.CbSize = uint32(unsafe.Sizeof(a.nid))
	a.nid.Hwnd = a.hwnd
	a.nid.UID = trayUID
	a.nid.Flags = nifMessage | nifIcon | nifTip
	a.nid.CallbackMessage = wmTray

	if a.icon == 0 {
		a.icon = makeHIcon(smallIconSize(), colUnknown, '?')
	}
	a.iconKey = -9999
	a.nid.HIcon = a.icon
	setTip(&a.nid, a.tooltip())

	if r, _, err := pShellNotifyIconW.Call(nimAdd, uintptr(unsafe.Pointer(&a.nid))); r == 0 {
		a.logf("Shell_NotifyIcon(NIM_ADD) 失败: %v", err)
		return
	}
	a.trayAdded = true
}

func (a *App) removeTrayIcon() {
	if !a.trayAdded {
		return
	}
	pShellNotifyIconW.Call(nimDelete, uintptr(unsafe.Pointer(&a.nid)))
	a.trayAdded = false
	destroyIcon(a.icon)
	a.icon = 0
	a.iconKey = -9999
}

// persistRuntimeState 把「当前档位」「可用客户端序号」落盘（只允许在 UI 线程调用）
func (a *App) persistRuntimeState() {
	dirty := false
	// 落定期内回报还不可信，此时写 CurKey 会把「当前档位」记错
	if !a.gcu.Settling() {
		if idx, online := a.current(); online && idx >= 0 {
			if k := a.cfg.Items[idx].Key; k != a.cfg.CurKey {
				a.cfg.CurKey = k
				dirty = true
			}
		}
	}
	if d := a.gcu.TakeDiscoveredIndex(); d >= 0 && a.cfg.ClientIndex != d {
		a.cfg.ClientIndex = d
		dirty = true
	}
	if dirty {
		a.cfg.save()
	}
}

// onConfigChanged 配置改动后刷新托盘（仅 UI 线程调用）
func (a *App) onConfigChanged() {
	a.iconKey = -9999
	a.syncTray()
	if a.ui != nil {
		a.ui.updateStatus()
	}
}

// ---------------------------------------------------------------- 托盘菜单

func appendItem(hMenu uintptr, flags uintptr, id int, text string) {
	pAppendMenuW.Call(hMenu, flags, uintptr(id), uintptr(unsafe.Pointer(utf16FromString(text))))
}

// showTrayMenu 托盘右键菜单：只列模式切换项
func (a *App) showTrayMenu() {
	if a.menuOpen {
		return
	}
	a.menuOpen = true
	defer func() { a.menuOpen = false }()
	defer a.recoverLog("showTrayMenu")

	curIdx, online := a.current()
	a.logf("托盘菜单打开 (当前=%d online=%v)", curIdx, online)

	hMenu, _, _ := pCreatePopupMenu.Call()
	defer pDestroyMenu.Call(hMenu)

	tray := a.cfg.trayItems()
	if len(tray) == 0 {
		appendItem(hMenu, mfString|mfGrayed|mfDisabled, 0, "（没有勾选任何托盘模式）")
	} else {
		names := make([]string, 0, len(tray))
		for _, idx := range tray {
			flags := uintptr(mfString)
			if online && idx == curIdx {
				flags |= mfChecked
			}
			appendItem(hMenu, flags, idModeBase+idx, a.cfg.Items[idx].Name)
			names = append(names, a.cfg.Items[idx].Name)
		}
		a.logf("托盘菜单项=%v", names)
	}

	var pt point
	pGetCursorPos.Call(uintptr(unsafe.Pointer(&pt)))
	pSetForegroundWindow.Call(a.hwnd)
	ret, _, _ := pTrackPopupMenuEx.Call(
		hMenu,
		tpmReturnCmd|tpmRightButton|tpmLeftAlign|tpmBottomAlign|tpmNoNotify,
		uintptr(int64(pt.X)), uintptr(int64(pt.Y)),
		a.hwnd, 0,
	)
	pPostMessageW.Call(a.hwnd, wmNull, 0, 0)

	if ret >= idModeBase && ret < idModeMax {
		a.switchToItem(int(ret) - idModeBase)
	}
}

// switchToItem 切换到指定档位，并联动电源计划
func (a *App) switchToItem(idx int) {
	if idx < 0 || idx >= len(a.cfg.Items) {
		return
	}
	it := a.cfg.Items[idx]

	if err := a.gcu.SetMode(it.Mode, it.Slot); err != nil {
		a.logf("切换失败 %s: %v", it.Name, err)
		a.balloon("机械革命模式", "切换失败："+err.Error())
		a.gcu.Refresh()
		return
	}

	a.logf("切换 -> %s (mode=%d slot=%d, 电源计划=%q)", it.Name, it.Mode, it.Slot, it.PowerPlan)
	a.cfg.CurKey = it.Key
	a.cfg.save()

	// 立即本地反映，避免图标延迟
	a.gcu.ApplyLocal(it.Mode, it.Slot)
	a.onConfigChanged()

	go a.applyPowerPlan(it)
	time.AfterFunc(600*time.Millisecond, a.gcu.Refresh)
	time.AfterFunc(2300*time.Millisecond, a.gcu.Refresh)
}

// applyPowerPlan 切换电源计划（可能触发创建「卓越性能」，故放后台线程）
func (a *App) applyPowerPlan(it ModeItem) {
	defer a.recoverLog("applyPowerPlan")
	if it.PowerPlan == "" {
		return
	}
	real, err := applyPlan(it.PowerPlan)
	if err != nil {
		a.logf("电源计划切换失败: %v", err)
		a.queueBalloon("机械革命模式", "电源计划切换失败："+err.Error())
		return
	}
	if real == "" {
		return
	}
	// 模板方案（如「卓越性能」）若被复制成了新 GUID，别名表已持久化，
	// 配置里仍保留标准 GUID，下拉框选中状态不会漂移。
	a.logf("电源计划 -> %s (real=%s)", planDisplayName(real), real)
	if a.hwnd != 0 {
		pPostMessageW.Call(a.hwnd, wmStateChanged, 0, 0)
	}
}

// ---------------------------------------------------------------- 启动命令

// splitArgs 按 Windows 命令行惯例拆分参数，支持双引号包裹带空格的片段。
func splitArgs(s string) []string {
	var out []string
	var cur strings.Builder
	inQuote, has := false, false
	for _, r := range s {
		switch {
		case r == '"':
			inQuote = !inQuote
			has = true
		case (r == ' ' || r == '\t') && !inQuote:
			if has {
				out = append(out, cur.String())
				cur.Reset()
				has = false
			}
		default:
			cur.WriteRune(r)
			has = true
		}
	}
	if has {
		out = append(out, cur.String())
	}
	return out
}

// normalizePath 把路径里的正斜杠统一成反斜杠。
//
// 这一步是必需的：exec.Command 会用 exe 路径本身作为命令行第一个词，
// 而 cmd.exe 解析自身命令行时不认正斜杠——`C:/Windows/System32/cmd.exe`
// 里的 /Windows/System32/... 会被当成一串选项，直接报「命令语法不正确」。
// 用户从别处粘贴路径时很常见正斜杠，所以在这里统一兜住。
func normalizePath(p string) string {
	return filepath.FromSlash(strings.TrimSpace(p))
}

// execDirect 以当前权限把目标拉起来，不等它跑完、也不读它的输出。
func execDirect(path string, args []string) error {
	cmd := exec.Command(path, args...)
	cmd.Dir = filepath.Dir(path)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	return cmd.Start()
}

// execElevated 经 ShellExecute 的 "runas" 动作提权拉起目标。
//
// "runas" 拿不到子进程句柄，因此这里也只能「发起」而不能观察结果 —— 这正好符合
// 本功能的定位：只负责把命令拉起来，成没生效由用户自己去验证。
func execElevated(path string, args []string) error {
	inner := `"` + path + `"`
	if len(args) > 0 {
		inner += " " + strings.Join(args, " ")
	}
	if ret := shellExecRunAs("/c "+inner, filepath.Dir(path)); ret <= 32 {
		if ret == 1223 {
			return errors.New("已取消管理员授权")
		}
		return fmt.Errorf("提权启动失败（ShellExecute 返回 %d）", ret)
	}
	return nil
}

// runStartupCommand 把配置的命令拉起来，ok 只表示「有没有成功启动它」。
//
// 刻意**不**捕获也不打印它的输出：那是用户自己的命令，跑得对不对由用户自己验证，
// 本工具不该去解释某个第三方程序的退出码或输出。
// 「需要管理员权限」勾选后走 runas 路径。
func (a *App) runStartupCommand() (ok bool, detail string) {
	defer func() {
		if r := recover(); r != nil {
			a.logf("!! PANIC @runStartupCommand: %v", r)
			ok, detail = false, fmt.Sprint(r)
		}
	}()

	path := normalizePath(a.cfg.RunPath)
	if path == "" {
		return false, "未填写程序路径"
	}
	args := splitArgs(a.cfg.RunArgs)

	elevated := a.cfg.RunElevate && !isElevated()
	if elevated {
		a.logf("启动命令（提权）: %s %s", path, strings.Join(args, " "))
	} else {
		a.logf("启动命令: %s %s", path, strings.Join(args, " "))
	}

	var err error
	if elevated {
		err = execElevated(path, args)
	} else {
		err = execDirect(path, args)
	}
	if err != nil {
		msg := err.Error()
		if !isElevated() && !a.cfg.RunElevate {
			msg += "；若目标程序需要管理员权限，请勾选「以管理员身份运行」"
		}
		a.logf("启动命令未能启动: %s", msg)
		return false, msg
	}
	return true, "已启动"
}

// ---------------------------------------------------------------- 退出

func (a *App) quit() {
	a.logf("用户选择退出")
	a.quitting = true
	if a.ui != nil {
		a.ui.commit(false)
		a.ui.destroy()
	}
	a.removeTrayIcon()
	if a.hwnd != 0 {
		pDestroyWindow.Call(a.hwnd)
	}
}

// ---------------------------------------------------------------- 窗口

func (a *App) wndProc(hwnd, msg, wparam, lparam uintptr) uintptr {
	defer a.recoverLog("wndProc")

	if taskbarCreatedMsg != 0 && msg == taskbarCreatedMsg {
		a.logf("Explorer 重启，重新添加托盘图标")
		// Explorer 重启会把整个托盘（含我们的图标）清掉，必须重新注册一次。
		// addTrayIcon 里靠 trayAdded 做幂等，这里先把它复位，否则会被直接
		// return 掉，图标就再也回不来了。
		a.trayAdded = false
		a.addTrayIcon()
		a.syncTray()
		return 0
	}

	switch msg {
	case wmTray:
		switch lparam {
		case wmRButtonUp:
			a.showTrayMenu()
			return 0
		case wmLButtonUp, wmLButtonDbl:
			a.showSettings()
			return 0
		}
		return 0

	case wmStateChanged:
		a.syncTray()
		a.persistRuntimeState()
		if a.ui != nil {
			a.ui.updateStatus()
		}
		return 0

	case wmBalloon:
		a.flushBalloon()
		return 0

	case wmProbe:
		a.switchToItem(int(wparam))
		return 0

	case wmRunDone:
		if a.ui != nil {
			a.pendMu.Lock()
			msg := a.runMsg
			a.pendMu.Unlock()
			prefix := "运行失败："
			if wparam != 0 {
				prefix = "运行成功："
			}
			setWindowText(a.ui.lblStatus, prefix+msg)
		}
		return 0

	case wmQueryEndSession:
		// 关机 / 注销：标记为非异常退出，避免下次启动误报
		a.quitting = true
		return 1

	case wmEndSession:
		a.logf("系统关机/注销，准备退出")
		a.removeTrayIcon()
		pDestroyWindow.Call(a.hwnd)
		return 0

	case wmDestroy:
		a.removeTrayIcon()
		pPostQuitMessage.Call(0)
		return 0
	}

	r, _, _ := pDefWindowProcW.Call(hwnd, msg, wparam, lparam)
	return r
}

func (a *App) showSettings() {
	if a.ui == nil || a.ui.hwnd == 0 {
		return
	}
	a.ui.show()
}

func (a *App) createWindow() bool {
	hInst := getModuleHandle()
	className := utf16FromString("MechrevoModeTrayWnd")

	wc := wndClassEx{
		CbSize:        uint32(unsafe.Sizeof(wndClassEx{})),
		LpfnWndProc:   syscall.NewCallback(a.wndProc),
		HInstance:     hInst,
		LpszClassName: className,
	}
	if r, _, err := pRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc))); r == 0 {
		a.logf("RegisterClassExW 失败: %v", err)
		return false
	}

	hwnd, _, err := pCreateWindowExW.Call(
		0,
		uintptr(unsafe.Pointer(className)),
		uintptr(unsafe.Pointer(utf16FromString("MechrevoMode"))),
		0, 0, 0, 0, 0, 0, 0, hInst, 0,
	)
	if hwnd == 0 {
		a.logf("CreateWindowExW 失败: %v", err)
		return false
	}
	a.hwnd = hwnd
	return true
}

func (a *App) messageLoop() {
	var msg msgStruct
	for {
		r, _, _ := pGetMessageW.Call(uintptr(unsafe.Pointer(&msg)), 0, 0, 0)
		if int32(r) <= 0 {
			a.logf("GetMessageW 返回 %d (lastMsg=0x%04X)，退出消息循环", int32(r), msg.Message)
			return
		}
		// 让主界面的 Tab / 回车 / 方向键等按对话框语义工作
		if a.ui != nil && a.ui.hwnd != 0 {
			if v, _, _ := pIsDialogMessageW.Call(a.ui.hwnd, uintptr(unsafe.Pointer(&msg))); v != 0 {
				continue
			}
		}
		pTranslateMessage.Call(uintptr(unsafe.Pointer(&msg)))
		pDispatchMessageW.Call(uintptr(unsafe.Pointer(&msg)))
	}
}

// ---------------------------------------------------------------- 入口

const errAlreadyExists = syscall.Errno(183)

// appMutex 单实例互斥体句柄。提权重启时需要先放锁再拉起新进程，
// 否则新进程会先于旧进程退出而抢不到锁，被误判成「已经在运行」。
var appMutex uintptr

// acquireInstance 尝试取得单实例锁，已被占用返回 false
func acquireInstance() bool {
	for _, name := range []string{
		"Global\\MechrevoModeTray_SingleInstance",
		"MechrevoModeTray_SingleInstance",
	} {
		n := utf16Buf(name)
		h, _, err := pCreateMutexW.Call(0, 0, uintptr(unsafe.Pointer(&n[0])))
		if h == 0 {
			continue // Global\ 可能无权创建，退到会话内名字
		}
		if e, ok := err.(syscall.Errno); ok && e == errAlreadyExists {
			closeHandle(h)
			return false
		}
		appMutex = h
		return true
	}
	return true
}

func releaseInstance() {
	if appMutex != 0 {
		closeHandle(appMutex)
		appMutex = 0
	}
}

// ---------------------------------------------------------------- 入口

func main() {
	runtime.LockOSThread()

	app.logFile = openLog()
	defer func() {
		if app.logFile != nil {
			_ = app.logFile.Close()
		}
	}()
	defer app.recoverLog("main")

	// 先在写本次足迹之前取「上一次运行的最后一条日志」。
	// 顺序很关键：写完足迹再读，读到的就是本进程刚写的那行，
	// 于是每次启动都会误报「上次运行未正常结束」，也会让「提权重启:」的交接判断失效。
	prevLastLine := lastLogLine(logPath)

	// 每次启动都先留一条足迹：提权状态、参数、程序路径。
	//
	// 放在所有自检分支之前 —— 那些分支会提前 return，不留痕。
	// 这样「开机自启到底有没有被拉起来」「是以什么权限起来的」可以直接从日志判断，
	// 不必再靠猜（之前排查登录自启失败时就吃过没有这条记录的亏）。
	app.logf("启动 提权=%v 参数=%q 路径=%s", isElevated(), os.Args[1:], exePath())

	// 自检：导出电源计划枚举结果到 %APPDATA%\MechrevoMode\plans.txt
	if hasArg("-dump-plans") {
		dumpPlans()
		return
	}

	// 自检：逐个尝试切换内置电源方案，测试完自动还原
	if hasArg("-test-plans") {
		testPlans()
		return
	}

	// 自检：写入 / 清除「以管理员身份运行」的兼容性标志
	if hasArg("-test-elevate") {
		testElevate(true)
		return
	}
	if hasArg("-test-elevate-off") {
		testElevate(false)
		return
	}

	// 自检：把托盘图标渲染成放大对照图，便于确认形状
	if hasArg("-dump-icons") {
		dumpIcons()
		return
	}

	// 自检：输出 GCU 后端现状（只读，不改动任何东西）
	if hasArg("-dump-gcu") {
		dumpGCU()
		return
	}

	// 自检：验证「静默启动进程」这条兜底路径（会真的起一个进程，随后收掉）
	if hasArg("-test-gcu-launch") {
		testGCULaunch(argValue("-test-gcu-launch"))
		return
	}

	// 让 GetSystemMetrics(SM_CXSMICON) 返回按 DPI 缩放后的真实尺寸
	pSetProcessDPIAware.Call()

	firstRun := false
	if _, err := os.Stat(configPath()); err != nil {
		firstRun = true
	}

	app.cfg = loadConfig()

	// 「以管理员身份运行」的权威来源是注册表里的兼容性标志
	app.cfg.AutoElevate = isRunAsAdminSet()

	// 标志正常生效时进程在启动前就已经拿到管理员令牌，这里不会触发。
	// 只有标志没起作用（被别的进程拉起、或刚在界面上勾上还没来得及重启）时，
	// 才主动提权重启一次；失败不阻塞，照常以普通权限继续跑。
	// 放在单实例检查之前，避免新进程和老进程抢锁。
	if app.cfg.AutoElevate && !isElevated() && !hasArg(elevatedArg) {
		app.logf("已启用「以管理员身份运行」但当前未提权，尝试提权重启")
		hProc, errCode := relaunchElevated()
		switch {
		case hProc != 0:
			closeHandle(hProc)
			app.logf("已发起提权重启，本进程退出（日志见新进程）")
			return
		case errCode == errCancelled:
			app.logf("用户在 UAC 中取消，继续以普通权限运行")
		default:
			app.logf("提权重启失败（错误码 %d），继续以普通权限运行", errCode)
		}
	}

	if !acquireInstance() {
		messageBox("机械革命模式", "程序已经在运行了（右下角托盘图标）。")
		return
	}

	if p, _, _ := pRegisterWindowMessageW.Call(uintptr(unsafe.Pointer(utf16FromString("TaskbarCreated")))); p != 0 {
		taskbarCreatedMsg = p
	}

	// 上一次正常退出会留下「已退出」；提权重启时旧进程会写下「提权重启: ...」
	// 然后立刻退出，新进程起来读日志时多半还没等到那句「已退出」，
	// 这属于正常的交接，不能当成异常退出报警。
	// 用启动时先取好的 prevLastLine，避免读到本进程刚写的足迹行。
	if prevLastLine != "" &&
		!strings.Contains(prevLastLine, "已退出") &&
		!strings.Contains(prevLastLine, "提权重启:") {
		app.logf("!! 上次运行未正常结束，最后一条日志：%s", prevLastLine)
	}

	// 注册表是「开机自启」的权威来源
	app.cfg.AutoStart = isAutoStartEnabled()
	if err := applyAutoStart(app.cfg.AutoStart); err != nil {
		app.logf("同步开机自启失败: %v", err)
	}

	// 自身在云同步目录里时提醒一句。
	// 这类目录的文件可能被「按需下载 / 释放空间」解除本地化，只剩一个云端占位，
	// 登录时无论注册表还是计划任务都拉不起来（而且是静默失败，很难查）。
	if looksLikeCloudSync(exePath()) {
		app.logf("提示：程序位于云同步目录（%s）。这类文件可能被「释放空间」变成云端占位，"+
			"导致登录自启静默失败；挪到普通本地目录最稳妥。", exePath())
	}

	// 电源方案别名表：模板方案（「卓越性能」）复制出来的 GUID 需要固定下来
	initPlanAlias(app.cfg.PlanAlias, func(m map[string]string) {
		app.cfg.PlanAlias = m
		app.cfg.save()
	})

	app.gcu = NewGCU(app.cfg.ClientIndex, func(mode, profile int, online bool) {
		if app.hwnd != 0 {
			pPostMessageW.Call(app.hwnd, wmStateChanged, 0, 0)
		}
	})
	app.gcu.SetLogger(app.logf)
	// 「自动拉起 GCU 服务」开关由后台线程按需读取（配置可能随时被界面改掉）
	app.gcu.SetAutoBackendFn(app.cfg.AutoGCUEnabled)

	if !app.createWindow() {
		messageBox("机械革命模式", "创建窗口失败，详见日志。")
		return
	}

	app.ui = newUI()
	if !app.ui.create() {
		messageBox("机械革命模式", "创建设置界面失败，详见日志。")
		return
	}

	app.addTrayIcon()
	app.syncTray()
	app.gcu.Start()

	// 启动时不做任何模式下发：GCU 的 Fan/Status 是 retained 消息，
	// 连上就会把当前真实模式推过来，直接以控制台为准即可。
	time.AfterFunc(1200*time.Millisecond, app.gcu.Refresh)

	app.logf("启动完成 提权=%v 自启=%v(%s) 自动提权标记=%v GCU自愈=%v 托盘项=%v 图标尺寸=%d",
		isElevated(), app.cfg.AutoStart, autostartMechanism, app.cfg.AutoElevate,
		app.cfg.AutoGCU, app.cfg.trayItems(), smallIconSize())

	// 启动命令：延迟若干秒执行一次（放在 GCU 同步之后，避免被模式切换覆盖）
	if app.cfg.RunEnabled && strings.TrimSpace(app.cfg.RunPath) != "" {
		if !isElevated() {
			app.logf("提示：本进程当前不是管理员（提权=%v）。需要写 MSR 的工具如 ryzenadj 会失败，"+
				"请勾选「以管理员身份运行」，或给启动命令单独勾「需要管理员权限」",
				isElevated())
		}
		d := app.cfg.RunDelay
		if d < 0 {
			d = 0
		}
		a := app
		go func() {
			time.Sleep(time.Duration(d) * time.Second)
			if ok, detail := a.runStartupCommand(); !ok {
				a.queueBalloon("启动命令执行失败", detail)
			}
		}()
	}

	if firstRun || hasArg("-show") || hasArg("--show") {
		app.showSettings()
	}

	// 自检/调试：启动后单次切换到指定档位（key 见 config.json）
	if k := argValue("-switch"); k != "" {
		idx := app.cfg.indexOf(k)
		if idx < 0 {
			app.logf("[switch-test] 未找到档位 %q", k)
		} else {
			app.logf("[switch-test] 单次切换 -> %s", app.cfg.Items[idx].Name)
			go func() {
				time.Sleep(4 * time.Second)
				if app.hwnd != 0 {
					pPostMessageW.Call(app.hwnd, wmProbe, uintptr(idx), 0)
				}
			}()
		}
	}

	app.messageLoop()

	app.logf("消息循环结束")
	app.gcu.Stop()
	app.logf("已退出")
}
