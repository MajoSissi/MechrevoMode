//go:build windows

package main

import (
	"fmt"
	"strconv"
	"strings"
	"syscall"
	"unicode/utf8"
	"unsafe"
)

// ---------------------------------------------------------------- 逻辑尺寸（96 DPI 基准）

const (
	uiWidth      = 780
	uiRowsStartY = 86
	uiRowH       = 32

	uiColShowX  = 20
	uiColNameX  = 68
	uiColNameW  = 148
	uiColOrigX  = 224
	uiColOrigW  = 116
	uiColGlyphX = 348
	uiColGlyphW = 38
	uiColColorX = 394
	uiColColorW = 92
	uiColPlanX  = 494
	uiColPlanW  = 266

	uiRunH = 96 // 启动命令分组框高度
)

// uiLayout 各区块的纵向位置。create 与 createControls 共用，避免两处算得不一致。
type uiLayout struct {
	modeBoxH int
	runTop   int
	statusY  int
	btnY     int
	clientH  int
}

func layoutFor(n int) uiLayout {
	modeBoxH := 52 + n*uiRowH
	runTop := 40 + modeBoxH + 8
	statusY := runTop + uiRunH + 10
	btnY := statusY + 28
	return uiLayout{modeBoxH, runTop, statusY, btnY, btnY + 48}
}

// ---------------------------------------------------------------- 控件 ID

const (
	uidAutoStart   = 100
	uidAutoElevate = 101

	uidRunEnable  = 110
	uidRunPath    = 111
	uidRunBrowse  = 112
	uidRunArgs    = 113
	uidRunDelay   = 114
	uidRunElevate = 115
	uidRunNow     = 116

	uidSave    = 900
	uidQuit    = 901
	uidReset   = 902
	uidStatus  = 910
	uidRowBase = 200
)

const (
	riShow = iota
	riName
	riOrig
	riGlyph
	riColor
	riPlan
	riCount
)

const (
	cbResetContent = 0x014B
)

func rowFieldFromID(id int) (row, field int, ok bool) {
	if id < uidRowBase {
		return 0, 0, false
	}
	d := id - uidRowBase
	row, field = d/10, d%10
	if field >= riCount {
		return 0, 0, false
	}
	return row, field, true
}

// ---------------------------------------------------------------- UI

type UI struct {
	hwnd  uintptr
	font  uintptr
	dpi   int
	scale float64

	chkAutoStart   uintptr
	chkAutoElevate uintptr
	rows           [][riCount]uintptr
	lblStatus      uintptr
	btnSave        uintptr
	btnQuit        uintptr
	btnReset       uintptr

	// 启动命令区块
	chkRun     uintptr
	chkElevate uintptr
	edRunPath  uintptr
	btnBrowse  uintptr
	edRunArgs  uintptr
	edRunDelay uintptr
	btnRunNow  uintptr

	planOpts []powerPlan
	custCols [16]uint32
	loading  bool
}

func newUI() *UI {
	u := &UI{}
	for i := range u.custCols {
		u.custCols[i] = 0xFFFFFF
	}
	return u
}

func (u *UI) px(v int) int32 {
	return int32(float64(v)*u.scale + 0.5)
}

// ---------------------------------------------------------------- 创建

func (u *UI) create() bool {
	u.dpi = systemDPI()
	u.scale = float64(u.dpi) / 96.0
	if u.scale < 1 {
		u.scale = 1
	}
	u.font = makeFont(9, u.dpi)

	hInst := getModuleHandle()
	className := utf16FromString("MechrevoModeMainWnd")

	cursor, _, _ := pLoadCursorW.Call(0, 32512) // IDC_ARROW
	wc := wndClassEx{
		CbSize:        uint32(unsafe.Sizeof(wndClassEx{})),
		LpfnWndProc:   syscall.NewCallback(u.wndProc),
		HInstance:     hInst,
		HCursor:       cursor,
		HbrBackground: 16, // COLOR_BTNFACE + 1
		LpszClassName: className,
	}
	if r, _, err := pRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc))); r == 0 {
		app.logf("主界面 RegisterClassExW 失败: %v", err)
		return false
	}

	n := len(app.cfg.Items)
	clientH := layoutFor(n).clientH

	style := uintptr(wsOverlapped | wsCaption | wsSysMenu | wsMinBox | wsClipSibling)
	exStyle := uintptr(0)
	wr := rect{0, 0, u.px(uiWidth), u.px(clientH)}
	pAdjustWindowRectExW.Call(uintptr(unsafe.Pointer(&wr)), style, 0, exStyle)
	winW := int32(wr.Right - wr.Left)
	winH := int32(wr.Bottom - wr.Top)

	// 默认摆在桌面工作区正中（刨掉任务栏）。用户之后拖到哪儿就停在哪儿，
	// 显示/隐藏都走 ShowWindow，不会再被拉回来。
	winX, winY := centerInWorkArea(winW, winH)

	hwnd, _, err := pCreateWindowExW.Call(
		exStyle,
		uintptr(unsafe.Pointer(className)),
		uintptr(unsafe.Pointer(utf16FromString("机械革命模式设置"))),
		style,
		uintptr(int32ToUintptr(winX)), uintptr(int32ToUintptr(winY)),
		uintptr(int32ToUintptr(winW)), uintptr(int32ToUintptr(winH)),
		0, 0, hInst, 0,
	)
	if hwnd == 0 {
		app.logf("主界面 CreateWindowExW 失败: %v", err)
		return false
	}
	u.hwnd = hwnd
	u.createControls(n)
	u.loadFromConfig()
	return true
}

func (u *UI) mk(class, text string, style uintptr, x, y, w, h int, id int) uintptr {
	hwnd := createWindowEx(0, class, text, style,
		u.px(x), u.px(y), u.px(w), u.px(h), u.hwnd, id)
	if hwnd != 0 && u.font != 0 {
		sendMsg(hwnd, wmSetFont, u.font, 1)
	}
	return hwnd
}

func (u *UI) createControls(n int) {
	btnStyle := uintptr(wsChild | wsVisible | wsTabStop)
	L := layoutFor(n)

	u.chkAutoStart = u.mk("BUTTON", "开机自启动",
		btnStyle|bsAutoCheckBox, 24, 14, 130, 22, uidAutoStart)

	// 需要写 MSR 的工具（ryzenadj 之类）只有在管理员权限下才能工作。
	// 勾上之后整个程序都以管理员身份运行，比单独给子进程提权省事。
	u.chkAutoElevate = u.mk("BUTTON", "以管理员身份运行",
		btnStyle|bsAutoCheckBox, 170, 14, 160, 22, uidAutoElevate)

	// 分组框（模式表）
	u.mk("BUTTON", "模式 · 托盘菜单 · 图标 · 电源计划",
		wsChild|wsVisible|bsGroupBox, 12, 40, uiWidth-24, L.modeBoxH, 0)

	// 表头
	hdr := func(text string, x, w int) {
		u.mk("STATIC", text, wsChild|wsVisible|ssLeft, x, 64, w, 18, 0)
	}
	hdr("显示", uiColShowX-4, 40)
	hdr("模式名称", uiColNameX, 100)
	hdr("原始模式", uiColOrigX, 80)
	hdr("图标", uiColGlyphX, 40)
	hdr("底色", uiColColorX, 40)
	hdr("电源计划", uiColPlanX, 90)

	u.rows = make([][riCount]uintptr, n)
	for i := 0; i < n; i++ {
		y := uiRowsStartY + i*uiRowH
		u.rows[i][riShow] = u.mk("BUTTON", "",
			btnStyle|bsAutoCheckBox, uiColShowX, y+5, 22, 22, rowCtrlID(i, riShow))
		u.rows[i][riName] = u.mk("EDIT", "",
			wsChild|wsVisible|wsTabStop|wsBorder|esLeft|esAutoHScroll,
			uiColNameX, y+2, uiColNameW, 24, rowCtrlID(i, riName))
		// 原始模式：只读提示，名称被改乱后仍能看清这行对应哪个硬件档位
		u.rows[i][riOrig] = u.mk("STATIC", "",
			wsChild|wsVisible|ssLeft, uiColOrigX, y+5, uiColOrigW, 20, 0)
		u.rows[i][riGlyph] = u.mk("EDIT", "",
			wsChild|wsVisible|wsTabStop|wsBorder|esLeft,
			uiColGlyphX, y+2, uiColGlyphW, 24, rowCtrlID(i, riGlyph))
		sendMsg(u.rows[i][riGlyph], emSetLimitText, 2, 0)
		u.rows[i][riColor] = u.mk("BUTTON", "",
			btnStyle|bsOwnerDraw, uiColColorX, y+2, uiColColorW, 24, rowCtrlID(i, riColor))
		u.rows[i][riPlan] = u.mk("COMBOBOX", "",
			wsChild|wsVisible|wsTabStop|wsVScroll|cbsDropDownList,
			uiColPlanX, y+2, uiColPlanW, 200, rowCtrlID(i, riPlan))
	}

	u.createRunSection(L, btnStyle)

	u.lblStatus = u.mk("STATIC", "", wsChild|wsVisible|ssLeft,
		20, L.statusY, uiWidth-40, 20, uidStatus)

	u.btnQuit = u.mk("BUTTON", "退出程序", btnStyle|bsPushButton,
		uiWidth-140, L.btnY, 118, 30, uidQuit)
	u.btnSave = u.mk("BUTTON", "保存设置", btnStyle|bsDefPushButton,
		uiWidth-272, L.btnY, 122, 30, uidSave)
	u.btnReset = u.mk("BUTTON", "恢复默认外观", btnStyle|bsPushButton,
		20, L.btnY, 122, 30, uidReset)
}

// createRunSection 启动命令区块
func (u *UI) createRunSection(L uiLayout, btnStyle uintptr) {
	u.mk("BUTTON", "启动命令",
		wsChild|wsVisible|bsGroupBox, 12, L.runTop, uiWidth-24, uiRunH, 0)

	y1 := L.runTop + 20
	u.chkRun = u.mk("BUTTON", "程序启动后运行命令",
		btnStyle|bsAutoCheckBox, 24, y1, 156, 22, uidRunEnable)
	u.chkElevate = u.mk("BUTTON", "需要管理员权限",
		btnStyle|bsAutoCheckBox, 188, y1, 124, 22, uidRunElevate)
	u.mk("STATIC", "延迟", wsChild|wsVisible|ssLeft, 318, y1+3, 30, 18, 0)
	u.edRunDelay = u.mk("EDIT", "",
		wsChild|wsVisible|wsTabStop|wsBorder|esCenter|esNumber,
		352, y1, 46, 22, uidRunDelay)
	sendMsg(u.edRunDelay, emSetLimitText, 4, 0)
	u.mk("STATIC", "秒后执行", wsChild|wsVisible|ssLeft, 404, y1+3, 64, 18, 0)
	u.btnRunNow = u.mk("BUTTON", "立即运行", btnStyle|bsPushButton,
		622, y1, 120, 22, uidRunNow)

	y2 := L.runTop + 48
	u.mk("STATIC", "程序", wsChild|wsVisible|ssLeft, 24, y2+4, 36, 18, 0)
	u.edRunPath = u.mk("EDIT", "",
		wsChild|wsVisible|wsTabStop|wsBorder|esLeft|esAutoHScroll,
		64, y2, 580, 24, uidRunPath)
	u.btnBrowse = u.mk("BUTTON", "浏览…", btnStyle|bsPushButton,
		652, y2, 90, 24, uidRunBrowse)

	y3 := L.runTop + 76
	u.mk("STATIC", "参数", wsChild|wsVisible|ssLeft, 24, y3+4, 36, 18, 0)
	u.edRunArgs = u.mk("EDIT", "",
		wsChild|wsVisible|wsTabStop|wsBorder|esLeft|esAutoHScroll,
		64, y3, 678, 24, uidRunArgs)
}

// 注：「程序」「参数」「浏览」「延迟」全部保持可编辑，不跟着上面那个复选框
// 一起灰掉。想试命令随时点「立即运行」就行，先填好再决定要不要开机自动跑才是最
// 顺手的顺序；之前灰掉它们导致「浏览…」点了没反应，看着像坏了。

func rowCtrlID(row, field int) int { return uidRowBase + row*10 + field }

// ---------------------------------------------------------------- 数据绑定

func (u *UI) loadFromConfig() {
	u.loading = true
	defer func() { u.loading = false }()

	cfg := app.cfg
	checkDlgButton(u.chkAutoStart, cfg.AutoStart)
	checkDlgButton(u.chkAutoElevate, cfg.AutoElevate)
	checkDlgButton(u.chkRun, cfg.RunEnabled)
	checkDlgButton(u.chkElevate, cfg.RunElevate)
	setWindowText(u.edRunPath, cfg.RunPath)
	setWindowText(u.edRunArgs, cfg.RunArgs)
	setWindowText(u.edRunDelay, strconv.Itoa(cfg.RunDelay))

	u.buildPlanOptions()
	for i := range cfg.Items {
		if i >= len(u.rows) {
			break
		}
		it := &cfg.Items[i]
		checkDlgButton(u.rows[i][riShow], it.ShowTray)
		setWindowText(u.rows[i][riName], it.Name)
		setWindowText(u.rows[i][riOrig], it.origLabel())
		setWindowText(u.rows[i][riGlyph], it.IconGlyph)
		u.fillPlanCombo(i, it.PowerPlan)
	}
	u.updateStatus()
}

// buildPlanOptions 汇总所有行用到的电源方案。
// 所有行共用同一份选项，这样任何一行的选中下标都能安全地映射回 GUID。
func (u *UI) buildPlanOptions() {
	opts := planOptions()
	seen := make(map[string]bool, len(opts))
	for _, p := range opts {
		seen[strings.ToLower(p.GUID)] = true
	}
	// 历史版本可能把「卓越性能」复制出来的实例 GUID 写进了配置，
	// 这类方案也要出现在下拉框里，否则选中状态会静默掉回「不改变」
	for _, it := range app.cfg.Items {
		g := it.PowerPlan
		if g == "" || seen[strings.ToLower(g)] {
			continue
		}
		seen[strings.ToLower(g)] = true
		opts = append(opts, powerPlan{GUID: g, Name: planDisplayName(g), Installed: true})
	}
	u.planOpts = opts
}

// fillPlanCombo 重建某一行的电源计划下拉框并选中当前值
func (u *UI) fillPlanCombo(row int, cur string) {
	cb := u.rows[row][riPlan]
	sendMsg(cb, cbResetContent, 0, 0)
	sel := 0
	for j, p := range u.planOpts {
		txt := utf16FromString(planLabel(p))
		sendMsg(cb, cbAddString, 0, uintptr(unsafe.Pointer(txt)))
		if strings.EqualFold(p.GUID, cur) {
			sel = j
		}
	}
	sendMsg(cb, cbSetCurSel, uintptr(sel), 0)
}

// applyFromControls 从控件读回配置（内存）
func (u *UI) applyFromControls() {
	cfg := app.cfg
	cfg.AutoStart = isChecked(u.chkAutoStart)
	cfg.AutoElevate = isChecked(u.chkAutoElevate)

	cfg.RunEnabled = isChecked(u.chkRun)
	cfg.RunElevate = isChecked(u.chkElevate)
	cfg.RunPath = strings.TrimSpace(getWindowText(u.edRunPath, 1024))
	cfg.RunArgs = strings.TrimSpace(getWindowText(u.edRunArgs, 1024))
	if n, err := strconv.Atoi(strings.TrimSpace(getWindowText(u.edRunDelay, 16))); err == nil && n >= 0 && n <= 3600 {
		cfg.RunDelay = n
	}

	for i := range cfg.Items {
		if i >= len(u.rows) {
			break
		}
		it := &cfg.Items[i]
		it.ShowTray = isChecked(u.rows[i][riShow])
		if s := strings.TrimSpace(getWindowText(u.rows[i][riName], 128)); s != "" {
			it.Name = s
		}
		if g := firstRune(getWindowText(u.rows[i][riGlyph], 16)); g != "" {
			it.IconGlyph = g
		}
		if sel := int(sendMsg(u.rows[i][riPlan], cbGetCurSel, 0, 0)); sel >= 0 && sel < len(u.planOpts) {
			it.PowerPlan = u.planOpts[sel].GUID
		}
	}
}

func firstRune(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	r, _ := utf8.DecodeRuneInString(s)
	return string(r)
}

func (u *UI) updateStatus() {
	if u.lblStatus == 0 {
		return
	}
	mode, profile, online := app.gcu.Snapshot()
	cur := app.cfg.CurKey
	if idx := app.cfg.indexForModeSlot(mode, profile); idx >= 0 {
		cur = app.cfg.Items[idx].Key
	}

	name := "未知"
	orig := ""
	if idx := app.cfg.indexOf(cur); idx >= 0 {
		it := app.cfg.Items[idx]
		name = it.Name
		orig = it.origLabel()
	}

	conn := "GCU 已连接"
	if !online {
		conn = "GCU 未连接"
	}
	plan := "未设置"
	if idx := app.cfg.indexOf(cur); idx >= 0 {
		plan = planDisplayName(app.cfg.Items[idx].PowerPlan)
	}

	// 名称可改，所以顺带把原始档位标出来，避免改乱后认不出当前是哪个模式
	label := name
	if orig != "" && orig != name {
		label = fmt.Sprintf("%s（%s）", name, orig)
	}
	perm := ""
	if isElevated() {
		perm = " · 管理员"
	}
	setWindowText(u.lblStatus, fmt.Sprintf("当前：%s · %s · 电源计划：%s%s", label, conn, plan, perm))
}

// ---------------------------------------------------------------- 显示 / 隐藏

func (u *UI) show() {
	if u.hwnd == 0 {
		return
	}
	u.loadFromConfig()
	pShowWindowW.Call(u.hwnd, swShowNormal)
	pSetForegroundWindow.Call(u.hwnd)
}

func (u *UI) hide() {
	if u.hwnd == 0 {
		return
	}
	pShowWindowW.Call(u.hwnd, swHide)
}

func (u *UI) destroy() {
	if u.hwnd != 0 {
		pDestroyWindow.Call(u.hwnd)
		u.hwnd = 0
	}
}

// ---------------------------------------------------------------- 保存

func (u *UI) commit(verbose bool) {
	u.applyFromControls()
	cfg := app.cfg
	cfg.save()

	if err := applyAutoStart(cfg.AutoStart); err != nil {
		app.logf("同步开机自启失败: %v", err)
		if verbose {
			app.balloon("机械革命模式", "写入开机自启失败："+err.Error())
		}
	}
	app.onConfigChanged()
	if verbose {
		setWindowText(u.lblStatus, "已保存 ✓")
	}
}

// ---------------------------------------------------------------- 颜色选择

func (u *UI) pickColor(row int) {
	if row >= len(app.cfg.Items) {
		return
	}
	cur := app.cfg.Items[row].IconColor
	cc := chooseColor{
		StructSize: uint32(unsafe.Sizeof(chooseColor{})),
		HwndOwner:  u.hwnd,
		RgbResult:  colorref(cur),
		CustColors: uintptr(unsafe.Pointer(&u.custCols[0])),
		Flags:      ccFullOpen | ccRgbInit | ccAnyColor,
	}
	ret, _, _ := pChooseColorW.Call(uintptr(unsafe.Pointer(&cc)))
	if ret == 0 {
		return
	}
	app.cfg.Items[row].IconColor = rgbFromColorref(cc.RgbResult)
	app.cfg.save()
	pInvalidateRectW.Call(u.rows[row][riColor], 0, 1)
	app.onConfigChanged()
}

// ---------------------------------------------------------------- 窗口过程

func (u *UI) wndProc(hwnd, msg, wparam, lparam uintptr) uintptr {
	defer func() {
		if r := recover(); r != nil {
			app.logf("!! PANIC @ui.wndProc: %v", r)
		}
	}()

	switch msg {
	case wmCommand:
		if !u.loading {
			u.onCommand(loword(wparam), hiword(wparam))
		}
		return 0

	case wmDrawItem:
		u.onDrawItem(lparam)
		return 1

	case wmClose:
		// 关闭按钮只隐藏到托盘，不退出进程
		u.commit(false)
		u.hide()
		return 0

	case wmDestroy:
		u.hwnd = 0
		return 0
	}

	r, _, _ := pDefWindowProcW.Call(hwnd, msg, wparam, lparam)
	return r
}

func (u *UI) onCommand(id, code int) {
	cfg := app.cfg

	switch id {
	case uidAutoStart:
		cfg.AutoStart = isChecked(u.chkAutoStart)
		u.commit(false)
		return
	case uidAutoElevate:
		u.toggleAutoElevate(isChecked(u.chkAutoElevate))
		return
	case uidRunEnable:
		cfg.RunEnabled = isChecked(u.chkRun)
		u.commit(false)
		return
	case uidRunElevate:
		cfg.RunElevate = isChecked(u.chkElevate)
		u.commit(false)
		return
	case uidRunNow:
		u.runNow()
		return
	case uidRunBrowse:
		f := pickFile(u.hwnd, "选择要启动的程序",
			"可执行文件 (*.exe)\x00*.exe\x00所有文件 (*.*)\x00*.*\x00",
			getWindowText(u.edRunPath, 1024), 1024)
		if f != "" {
			setWindowText(u.edRunPath, f)
			cfg.RunPath = f
			u.commit(false)
		}
		return
	case uidSave:
		u.commit(true)
		return
	case uidQuit:
		app.quit()
		return
	case uidReset:
		u.resetVisuals()
		return
	}

	row, field, ok := rowFieldFromID(id)
	if !ok || row >= len(cfg.Items) {
		return
	}
	it := &cfg.Items[row]

	switch field {
	case riShow:
		it.ShowTray = isChecked(u.rows[row][riShow])
		cfg.save()
		app.onConfigChanged()

	case riName:
		if code == enChange {
			if s := strings.TrimSpace(getWindowText(u.rows[row][riName], 128)); s != "" {
				it.Name = s
				cfg.save()
				// 名称只影响提示与菜单，不必重画图标
				app.syncTray()
				u.updateStatus()
			}
		}

	case riGlyph:
		if code == enChange {
			if g := firstRune(getWindowText(u.rows[row][riGlyph], 16)); g != "" {
				it.IconGlyph = g
				cfg.save()
				app.onConfigChanged()
			}
		}

	case riColor:
		if code == bnClicked {
			u.pickColor(row)
		}

	case riPlan:
		if code == cbSelChange {
			if sel := int(sendMsg(u.rows[row][riPlan], cbGetCurSel, 0, 0)); sel >= 0 && sel < len(u.planOpts) {
				it.PowerPlan = u.planOpts[sel].GUID
				cfg.save()
				u.updateStatus()
			}
		}
	}
}

// resetVisuals 把名称、图标字符、图标底色恢复成默认值。
// 只重置「外观」这三项——是否显示在托盘、电源计划属于功能性选择，保留不动。
func (u *UI) resetVisuals() {
	byKey := make(map[string]ModeItem)
	for _, d := range defaultItems() {
		byKey[d.Key] = d
	}

	cfg := app.cfg
	u.loading = true
	for i := range cfg.Items {
		if i >= len(u.rows) {
			break
		}
		d, ok := byKey[cfg.Items[i].Key]
		if !ok {
			continue
		}
		cfg.Items[i].Name = d.Name
		cfg.Items[i].IconGlyph = d.IconGlyph
		cfg.Items[i].IconColor = d.IconColor
		setWindowText(u.rows[i][riName], d.Name)
		setWindowText(u.rows[i][riGlyph], d.IconGlyph)
		pInvalidateRectW.Call(u.rows[i][riColor], 0, 1)
	}
	u.loading = false

	cfg.save()
	app.onConfigChanged()
	setWindowText(u.lblStatus, "已恢复默认外观 ✓")
}

// ---------------------------------------------------------------- 以管理员身份运行

// toggleAutoElevate 切换「以管理员身份运行」。
//
// 写的是当前用户的兼容性标志（HKCU），因此这一步本身不需要管理员权限。
// 但已经跑起来的进程不可能凭空拿到管理员令牌，必须重新启动才真正生效。
func (u *UI) toggleAutoElevate(on bool) {
	cfg := app.cfg
	if err := setRunAsAdmin(on); err != nil {
		app.logf("写入「以管理员身份运行」失败: %v", err)
		checkDlgButton(u.chkAutoElevate, !on) // 回滚勾选状态
		setWindowText(u.lblStatus, "写入失败："+err.Error())
		return
	}
	cfg.AutoElevate = on
	cfg.save()
	app.logf("「以管理员身份运行」= %v (当前进程已提权=%v)", on, isElevated())

	if !on {
		setWindowText(u.lblStatus, "已取消，下次启动不再自动提权（当前进程权限不变）")
		return
	}
	if isElevated() {
		setWindowText(u.lblStatus, "已启用：本程序当前就以管理员身份运行 ✓")
		return
	}
	if messageBoxYesNo("机械革命模式",
		"已设置为「以管理员身份运行」。\n\n"+
			"需要重新启动程序才会生效，届时会弹出 UAC 授权窗口。\n\n"+
			"现在立即重启吗？\n\n"+
			"（选「否」也没问题，本次先不重启，下次启动自动生效）", u.hwnd) {
		u.restartElevated()
		return
	}
	setWindowText(u.lblStatus, "已设置，下次启动程序时自动以管理员身份运行")
}

// restartElevated 以管理员身份重新启动自己，然后退出当前进程。
//
// 只有确认新进程真的起来了，本进程才会退出——否则宁可留在原地，
// 也不能把用户晾在一个「旧进程没了、新进程也没来」的空白状态里。
func (u *UI) restartElevated() {
	app.logf("用户选择立即以管理员身份重启")

	// 界面开着的时候重启，顺带把界面也带回来，别让用户以为程序没了
	var extra []string
	if v, _, _ := pIsWindowVisible.Call(u.hwnd); v != 0 {
		extra = append(extra, "-show")
	}
	setWindowText(u.lblStatus, "正在以管理员身份重新启动…")

	// 关键顺序：先放掉单实例锁再拉起新进程。
	// 反过来做的话新进程会先于旧进程退出而抢不到锁，直接被判成「已经在运行」。
	releaseInstance()

	hProc, errCode := relaunchElevated(extra...)
	if hProc == 0 {
		// 新进程根本没创建出来（UAC 被拒 / ShellExecute 失败），把锁拿回来继续跑
		acquireInstance()
		switch errCode {
		case errCancelled:
			setWindowText(u.lblStatus, "已取消管理员授权，继续以普通权限运行")
			app.logf("提权重启被取消，继续以普通权限运行")
		default:
			setWindowText(u.lblStatus,
				fmt.Sprintf("提权重启失败（错误码 %d），继续以普通权限运行", errCode))
			app.logf("提权重启失败，错误码 %d", errCode)
		}
		return
	}

	// 句柄有效只能说明进程创建成功，还要确认它没在启动瞬间就退出。
	// 等 400ms：返回 WAIT_TIMEOUT 表示还活着。
	st, _, _ := pWaitForSingleObj.Call(hProc, 400)
	closeHandle(hProc)

	if st != waitTimeout {
		acquireInstance()
		setWindowText(u.lblStatus, "新进程启动后立即退出，已保留当前实例（详见日志）")
		app.logf("提权重启：新进程创建后立即退出，保留当前实例继续运行")
		return
	}

	app.logf("提权重启成功，本进程退出")
	app.quitting = true
	app.quit()
}

// runNow 立即执行一次启动命令（用于测试）。放到后台线程跑，不阻塞界面。
func (u *UI) runNow() {
	u.commit(false) // 先把界面上的改动落盘，保证跑的就是看到的内容

	if strings.TrimSpace(app.cfg.RunPath) == "" {
		setWindowText(u.lblStatus, "请先填写程序路径")
		return
	}
	if app.cfg.RunElevate && !isElevated() {
		setWindowText(u.lblStatus, "正在运行…（会弹出 UAC 授权窗口）")
	} else {
		setWindowText(u.lblStatus, "正在运行…")
	}

	go func() {
		ok, detail := app.runStartupCommand()
		app.pendMu.Lock()
		app.runMsg = detail
		app.pendMu.Unlock()
		if app.hwnd != 0 {
			v := uintptr(0)
			if ok {
				v = 1
			}
			pPostMessageW.Call(app.hwnd, wmRunDone, v, 0)
		}
	}()
}

func (u *UI) onDrawItem(lparam uintptr) {
	// WM_DRAWITEM 的 lparam 就是 DRAWITEMSTRUCT*，由系统在消息期间保证有效。
	// go vet 的 unsafeptr 检查对此类 Win32 回调指针会误报，这里是有意为之。
	dis := (*drawItemStruct)(unsafe.Pointer(lparam))
	row, field, ok := rowFieldFromID(int(dis.CtlID))
	if !ok || field != riColor || row >= len(app.cfg.Items) {
		return
	}
	rgb := app.cfg.Items[row].IconColor

	brush, _, _ := pCreateSolidBrush.Call(uintptr(colorref(rgb)))
	if brush != 0 {
		pFillRectW.Call(dis.HDC, uintptr(unsafe.Pointer(&dis.RcItem)), brush)
		pDeleteObject.Call(brush)
	}
	frame, _, _ := pGetStockObjectW.Call(grayBrush)
	pFrameRectW.Call(dis.HDC, uintptr(unsafe.Pointer(&dis.RcItem)), frame)

	// 背景深时用白字，浅时用黑字
	lum := 0.299*float64((rgb>>16)&0xFF) + 0.587*float64((rgb>>8)&0xFF) + 0.114*float64(rgb&0xFF)
	textColor := uintptr(0x000000)
	if lum < 140 {
		textColor = uintptr(0xFFFFFF)
	}

	old := uintptr(0)
	if u.font != 0 {
		old, _, _ = pSelectObject.Call(dis.HDC, u.font)
	}
	pSetBkMode.Call(dis.HDC, transparent)
	pSetTextColor.Call(dis.HDC, textColor)

	rc := dis.RcItem
	rc.Left += 2
	rc.Right -= 2
	txt := utf16FromString(fmt.Sprintf("#%06X", rgb))
	pDrawTextW.Call(dis.HDC,
		uintptr(unsafe.Pointer(txt)), ^uintptr(0),
		uintptr(unsafe.Pointer(&rc)),
		dtCenter|dtVCenter|dtSingleLine|dtNoPrefix)
	if old != 0 {
		pSelectObject.Call(dis.HDC, old)
	}
}

// ---------------------------------------------------------------- 电源计划名称（见 power.go）
