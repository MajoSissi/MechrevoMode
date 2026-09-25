//go:build windows

package main

import (
	"syscall"
	"unsafe"
)

// ---------------------------------------------------------------- DLL 句柄

var (
	user32   = syscall.NewLazyDLL("user32.dll")
	kernel32 = syscall.NewLazyDLL("kernel32.dll")
	shell32  = syscall.NewLazyDLL("shell32.dll")
	advapi32 = syscall.NewLazyDLL("advapi32.dll")
	gdi32    = syscall.NewLazyDLL("gdi32.dll")
	comdlg32 = syscall.NewLazyDLL("comdlg32.dll")
	powrprof = syscall.NewLazyDLL("powrprof.dll")
)

// ---------------------------------------------------------------- user32

var (
	pRegisterClassExW       = user32.NewProc("RegisterClassExW")
	pCreateWindowExW        = user32.NewProc("CreateWindowExW")
	pDefWindowProcW         = user32.NewProc("DefWindowProcW")
	pDestroyWindow          = user32.NewProc("DestroyWindow")
	pGetMessageW            = user32.NewProc("GetMessageW")
	pTranslateMessage       = user32.NewProc("TranslateMessage")
	pDispatchMessageW       = user32.NewProc("DispatchMessageW")
	pPostQuitMessage        = user32.NewProc("PostQuitMessage")
	pPostMessageW           = user32.NewProc("PostMessageW")
	pCreatePopupMenu        = user32.NewProc("CreatePopupMenu")
	pAppendMenuW            = user32.NewProc("AppendMenuW")
	pDestroyMenu            = user32.NewProc("DestroyMenu")
	pTrackPopupMenuEx       = user32.NewProc("TrackPopupMenuEx")
	pSetForegroundWindow    = user32.NewProc("SetForegroundWindow")
	pGetCursorPos           = user32.NewProc("GetCursorPos")
	pDestroyIcon            = user32.NewProc("DestroyIcon")
	pCreateIconFromResource = user32.NewProc("CreateIconFromResourceEx")
	pGetSystemMetrics       = user32.NewProc("GetSystemMetrics")
	pLoadCursorW            = user32.NewProc("LoadCursorW")
	pMessageBoxW            = user32.NewProc("MessageBoxW")
	pGetDesktopWindow       = user32.NewProc("GetDesktopWindow")
	pRegisterWindowMessageW = user32.NewProc("RegisterWindowMessageW")
	pGetWindowThreadProcId  = user32.NewProc("GetWindowThreadProcessId")
	pIsWindowVisible        = user32.NewProc("IsWindowVisible")
	pSetProcessDPIAware     = user32.NewProc("SetProcessDPIAware")
)

// ---------------------------------------------------------------- shell32 / kernel32 / advapi32

var (
	pShellNotifyIconW = shell32.NewProc("Shell_NotifyIconW")
	pShellExecuteW    = shell32.NewProc("ShellExecuteW")
	pShellExecuteExW  = shell32.NewProc("ShellExecuteExW")

	pGetModuleHandleW = kernel32.NewProc("GetModuleHandleW")
	pCreateMutexW     = kernel32.NewProc("CreateMutexW")
	pGetLastError     = kernel32.NewProc("GetLastError")
	pCloseHandle      = kernel32.NewProc("CloseHandle")
	pWaitForSingleObj = kernel32.NewProc("WaitForSingleObject")

	pRegCreateKeyExW  = advapi32.NewProc("RegCreateKeyExW")
	pRegOpenKeyExW    = advapi32.NewProc("RegOpenKeyExW")
	pRegEnumKeyExW    = advapi32.NewProc("RegEnumKeyExW")
	pRegSetValueExW   = advapi32.NewProc("RegSetValueExW")
	pRegDeleteValueW  = advapi32.NewProc("RegDeleteValueW")
	pRegQueryValueExW = advapi32.NewProc("RegQueryValueExW")
	pRegEnumValueW    = advapi32.NewProc("RegEnumValueW")
	pRegCloseKey      = advapi32.NewProc("RegCloseKey")

	pLocalFree = kernel32.NewProc("LocalFree")

	pGetCurrentProcess = kernel32.NewProc("GetCurrentProcess")

	pOpenProcessToken    = advapi32.NewProc("OpenProcessToken")
	pGetTokenInformation = advapi32.NewProc("GetTokenInformation")
)

// tokenQuery / tokenElevation 用于判断进程是否以管理员身份运行
const (
	tokenQuery     = 0x0008
	tokenElevation = 20
)

type tokenElevationInfo struct{ TokenIsElevated uint32 }

// isElevated 当前进程是否已提权（管理员）。
// 需要写 MSR 的工具（ryzenadj 一类）必须提权才能工作，用于提前给出提示。
func isElevated() bool {
	var hToken uintptr
	self, _, _ := pGetCurrentProcess.Call()
	if r, _, _ := pOpenProcessToken.Call(self,
		tokenQuery, uintptr(unsafe.Pointer(&hToken))); r == 0 {
		return false
	}
	defer closeHandle(hToken)

	var info tokenElevationInfo
	var size uint32
	r, _, _ := pGetTokenInformation.Call(
		hToken, tokenElevation,
		uintptr(unsafe.Pointer(&info)), unsafe.Sizeof(info),
		uintptr(unsafe.Pointer(&size)),
	)
	return r != 0 && info.TokenIsElevated != 0
}

// shellExecRunAs 用 "runas" 动作启动 cmd.exe 执行给定命令行（会弹 UAC）。
// 返回值为 ShellExecuteW 的 HINSTANCE，<=32 表示失败（1223 = 用户拒绝授权）。
func shellExecRunAs(cmdLine, workDir string) uintptr {
	ret, _, _ := pShellExecuteW.Call(
		0,
		uintptr(unsafe.Pointer(utf16FromString("runas"))),
		uintptr(unsafe.Pointer(utf16FromString("cmd.exe"))),
		uintptr(unsafe.Pointer(utf16FromString(cmdLine))),
		uintptr(unsafe.Pointer(utf16FromString(workDir))),
		uintptr(swHide),
	)
	return ret
}

// shellExecFileAs 用 "runas" 动作启动一个可执行文件本身（不经 cmd 包装）。
// 用于让本程序自己以管理员身份重新启动。
//
// 这里必须用 ShellExecuteExW 而不是 ShellExecuteW：
//   - ShellExecuteW 是异步的，调用方随后立刻退出会让 shell 把这次启动一起收走，
//     表现就是「旧进程关了、新进程没起来」。SEE_MASK_NOASYNC 强制同步完成。
//   - ShellExecuteW 只回一个 HINSTANCE，没法判断新进程到底有没有真的创建。
//     ShellExecuteExW 配 SEE_MASK_NOCLOSEPROCESS 会给出子进程句柄，可以据此确认。
func shellExecFileAs(exe, params, workDir string) (hProcess uintptr, errCode uintptr) {
	sei := shellExecuteInfo{
		CbSize:       uint32(unsafe.Sizeof(shellExecuteInfo{})),
		FMask:        seeMaskNoCloseProcess | seeMaskNoAsync,
		LpVerb:       utf16FromString("runas"),
		LpFile:       utf16FromString(exe),
		LpParameters: utf16FromString(params),
		LpDirectory:  utf16FromString(workDir),
		NShow:        swShowNormal,
	}
	r, _, err := pShellExecuteExW.Call(uintptr(unsafe.Pointer(&sei)))
	if r == 0 {
		if e, ok := err.(syscall.Errno); ok && e != 0 {
			return 0, uintptr(e)
		}
		return 0, 1 // 没有错误码时给个非 0 兜底，避免被当成成功
	}
	return sei.HProcess, 0
}

// ---------------------------------------------------------------- 常量

const (
	wmDestroy     = 0x0002
	wmClose       = 0x0010
	wmCommand     = 0x0111
	wmNull        = 0x0000
	wmRButtonUp   = 0x0205
	wmLButtonUp   = 0x0202
	wmLButtonDbl  = 0x0203
	wmContextMenu = 0x007B
	wmApp         = 0x8000
)

const (
	nimAdd    = 0x00000000
	nimModify = 0x00000001
	nimDelete = 0x00000002

	nifMessage = 0x00000001
	nifIcon    = 0x00000002
	nifTip     = 0x00000004
	nifInfo    = 0x00000010

	niifNone = 0x00000000
	niifInfo = 0x00000001
)

const (
	mfString    = 0x00000000
	mfChecked   = 0x00000008
	mfSeparator = 0x00000800
	mfPopup     = 0x00000010
	mfGrayed    = 0x00000001
	mfDisabled  = 0x00000002

	tpmLeftAlign   = 0x0000
	tpmRightButton = 0x0002
	tpmBottomAlign = 0x0020
	tpmReturnCmd   = 0x0100
	tpmNoNotify    = 0x0080
)

const (
	smCXSmIcon     = 49
	smCYSmIcon     = 50
	smCXScreen     = 0
	smCYScreen     = 1
	spiGetWorkArea = 0x0030
)

// ShellExecuteEx 的 fMask 标志
const (
	seeMaskNoCloseProcess = 0x00000040 // 返回子进程句柄
	seeMaskNoAsync        = 0x00000100 // 同步完成再返回，调用方随后可安全退出
)

// WaitForSingleObject 返回值
const (
	waitObject0 = 0x00000000
	waitTimeout = 0x00000102
)

const hkeyCurrentUser = 0x80000001
const hkeyLocalMachine = 0x80000002
const keyRead = 0x20019

const regSZ = 1

// ---------------------------------------------------------------- 结构体

type point struct {
	X, Y int32
}

type msgStruct struct {
	Hwnd    uintptr
	Message uint32
	WParam  uintptr
	LParam  uintptr
	Time    uint32
	Pt      point
	Private uint32
}

type wndClassEx struct {
	CbSize        uint32
	Style         uint32
	LpfnWndProc   uintptr
	CbClsExtra    int32
	CbWndExtra    int32
	HInstance     uintptr
	HIcon         uintptr
	HCursor       uintptr
	HbrBackground uintptr
	LpszMenuName  *uint16
	LpszClassName *uint16
	HIconSm       uintptr
}

type notifyIconData struct {
	CbSize          uint32
	pad0            uint32
	Hwnd            uintptr
	UID             uint32
	Flags           uint32
	CallbackMessage uint32
	pad1            uint32
	HIcon           uintptr
	Tip             [128]uint16
	State           uint32
	StateMask       uint32
	Info            [256]uint16
	TimeoutVersion  uint32
	InfoTitle       [64]uint16
	InfoFlags       uint32
	GuidItem        [16]byte
	BalloonIcon     uintptr
}

type bitmapInfoHeader struct {
	Size          uint32
	Width         int32
	Height        int32
	Planes        uint16
	BitCount      uint16
	Compression   uint32
	SizeImage     uint32
	XPelsPerMeter int32
	YPelsPerMeter int32
	ClrUsed       uint32
	ClrImportant  uint32
}

// ---------------------------------------------------------------- 辅助

func utf16FromString(s string) *uint16 {
	p, err := syscall.UTF16PtrFromString(s)
	if err != nil {
		return nil
	}
	return p
}

func getSystemMetrics(index int32) int32 {
	r, _, _ := pGetSystemMetrics.Call(uintptr(index))
	return int32(r)
}

func getModuleHandle() uintptr {
	h, _, _ := pGetModuleHandleW.Call(0)
	return h
}

func closeHandle(h uintptr) {
	if h != 0 {
		pCloseHandle.Call(h)
	}
}

func destroyIcon(h uintptr) {
	if h != 0 {
		pDestroyIcon.Call(h)
	}
}

func messageBox(title, text string) {
	r, _, _ := pGetDesktopWindow.Call()
	_ = r
	pMessageBoxW.Call(0, uintptr(unsafe.Pointer(utf16FromString(text))),
		uintptr(unsafe.Pointer(utf16FromString(title))), 0x00000040)
}

// centerInWorkArea 在桌面工作区（刨掉任务栏）里居中一个 winW×winH 的窗口
func centerInWorkArea(winW, winH int32) (int32, int32) {
	var wa rect
	ok, _, _ := pSystemParametersInfoW.Call(spiGetWorkArea, 0, uintptr(unsafe.Pointer(&wa)), 0)
	if ok == 0 || wa.Right <= wa.Left || wa.Bottom <= wa.Top {
		wa = rect{0, 0, getSystemMetrics(smCXScreen), getSystemMetrics(smCYScreen)}
	}
	x := wa.Left + (wa.Right-wa.Left-winW)/2
	y := wa.Top + (wa.Bottom-wa.Top-winH)/2
	if x < wa.Left {
		x = wa.Left
	}
	if y < wa.Top {
		y = wa.Top
	}
	return x, y
}

// messageBoxYesNo 弹出「是/否」对话框，返回用户是否点了「是」。
// 默认按钮放在「否」上：重启这种事不该被一个回车误触。
func messageBoxYesNo(title, text string, owner uintptr) bool {
	r, _, _ := pMessageBoxW.Call(owner,
		uintptr(unsafe.Pointer(utf16FromString(text))),
		uintptr(unsafe.Pointer(utf16FromString(title))),
		mbYesNo|mbIconInfo|mbDefButton2)
	return r == idYes
}

// ================================================================ 主界面：user32

var (
	pSendMessageW          = user32.NewProc("SendMessageW")
	pGetWindowTextW        = user32.NewProc("GetWindowTextW")
	pSetWindowTextW        = user32.NewProc("SetWindowTextW")
	pShowWindowW           = user32.NewProc("ShowWindow")
	pSetWindowPosW         = user32.NewProc("SetWindowPos")
	pGetClientRectW        = user32.NewProc("GetClientRect")
	pInvalidateRectW       = user32.NewProc("InvalidateRect")
	pUpdateWindowW         = user32.NewProc("UpdateWindow")
	pEnableWindowW         = user32.NewProc("EnableWindow")
	pSetFocusW             = user32.NewProc("SetFocus")
	pBeginPaintW           = user32.NewProc("BeginPaint")
	pEndPaintW             = user32.NewProc("EndPaint")
	pGetDC                 = user32.NewProc("GetDC")
	pReleaseDC             = user32.NewProc("ReleaseDC")
	pFillRectW             = user32.NewProc("FillRect")
	pIsDialogMessageW      = user32.NewProc("IsDialogMessageW")
	pAdjustWindowRectExW   = user32.NewProc("AdjustWindowRectEx")
	pGetForegroundWindowW  = user32.NewProc("GetForegroundWindow")
	pDrawTextW             = user32.NewProc("DrawTextW")
	pFrameRectW            = user32.NewProc("FrameRect")
	pSystemParametersInfoW = user32.NewProc("SystemParametersInfoW")
)

// ================================================================ 主界面：gdi32

var (
	pCreateSolidBrush = gdi32.NewProc("CreateSolidBrush")
	pDeleteObject     = gdi32.NewProc("DeleteObject")
	pCreateFontW      = gdi32.NewProc("CreateFontW")
	pSetBkMode        = gdi32.NewProc("SetBkMode")
	pSetTextColor     = gdi32.NewProc("SetTextColor")
	pSelectObject     = gdi32.NewProc("SelectObject")
	pGetDeviceCaps    = gdi32.NewProc("GetDeviceCaps")
	pGetStockObjectW  = gdi32.NewProc("GetStockObject")
)

// ================================================================ 主界面：comdlg32

var (
	pChooseColorW         = comdlg32.NewProc("ChooseColorW")
	pGetOpenFileNameW     = comdlg32.NewProc("GetOpenFileNameW")
	pCommDlgExtendedError = comdlg32.NewProc("CommDlgExtendedError")
)

// ================================================================ 电源计划：powrprof

var (
	pPowerEnumerate        = powrprof.NewProc("PowerEnumerate")
	pPowerReadFriendlyName = powrprof.NewProc("PowerReadFriendlyName")
	pPowerSetActiveScheme  = powrprof.NewProc("PowerSetActiveScheme")
	pPowerGetActiveScheme  = powrprof.NewProc("PowerGetActiveScheme")
)

// ================================================================ 主界面常量

const (
	wsOverlapped  = 0x00000000
	wsPopup       = 0x80000000
	wsChild       = 0x40000000
	wsVisible     = 0x10000000
	wsTabStop     = 0x00010000
	wsClipSibling = 0x04000000
	wsBorder      = 0x00800000
	wsDlgFrame    = 0x00400000

	wsCaption = 0x00C00000
	wsSysMenu = 0x00080000
	wsMinBox  = 0x00020000

	wsExDlgModalFrame = 0x00000001
	wsExControlParent = 0x00010000
	wsVScroll         = 0x00200000

	bsPushButton    = 0x00000000
	bsDefPushButton = 0x00000001
	bsAutoCheckBox  = 0x00000003
	bsOwnerDraw     = 0x0000000B
	bsGroupBox      = 0x00000007

	esLeft        = 0x0000
	esCenter      = 0x0001
	esAutoHScroll = 0x0080
	esNumber      = 0x2000

	cbsDropDownList = 0x0003

	ssLeft   = 0x00000000
	ssCenter = 0x00000001
	ssRight  = 0x00000002

	swHide       = 0
	swShowNormal = 1
	swShow       = 5
	swRestore    = 9

	swpNoSize     = 0x0001
	swpNoMove     = 0x0002
	swpNoZOrder   = 0x0004
	swpNoActivate = 0x0010

	cbAddString    = 0x0143
	cbSetCurSel    = 0x014E
	cbGetCurSel    = 0x0147
	emSetLimitText = 0x00C5
	bmGetCheck     = 0x00F0
	bmSetCheck     = 0x00F1

	bnClicked   = 0
	enChange    = 0x0300
	cbSelChange = 1

	wmCreate          = 0x0001
	wmSize            = 0x0005
	wmSetFocus        = 0x0007
	wmKillFocus       = 0x0008
	wmPaint           = 0x000F
	wmEraseBkgnd      = 0x0014
	wmDrawItem        = 0x002B
	wmSetFont         = 0x0030
	wmKeyDown         = 0x0100
	wmSysCommand      = 0x0112
	wmInitDialog      = 0x0110
	wmQueryEndSession = 0x0011
	wmEndSession      = 0x0016

	scMinimize = 0xF020
	scClose    = 0xF060

	transparent = 1

	ccFullOpen = 0x00000002
	ccRgbInit  = 0x00000001
	ccAnyColor = 0x00000100

	ofnNoChangeDir   = 0x00000008
	ofnPathMustExist = 0x00000800
	ofnFileMustExist = 0x00001000
	ofnExplorer      = 0x00080000

	logPixelsY = 90

	accessScheme = 16

	odsSelected = 0x0001

	dtLeft        = 0x00000000
	dtCenter      = 0x00000001
	dtRight       = 0x00000002
	dtVCenter     = 0x00000004
	dtSingleLine  = 0x00000020
	dtNoPrefix    = 0x00000800
	dtEndEllipsis = 0x00008000

	whiteBrush = 0
	grayBrush  = 2
	nullBrush  = 5

	mbYesNo      = 0x00000004
	mbIconInfo   = 0x00000040
	mbDefButton2 = 0x00000100
	idYes        = 6
)

// ================================================================ 主界面结构体

type rect struct {
	Left, Top, Right, Bottom int32
}

type drawItemStruct struct {
	CtlType    uint32
	CtlID      uint32
	ItemID     uint32
	ItemAction uint32
	ItemState  uint32
	pad0       uint32
	HwndItem   uintptr
	HDC        uintptr
	RcItem     rect
	ItemData   uintptr
}

type guid struct {
	Data1 uint32
	Data2 uint16
	Data3 uint16
	Data4 [8]byte
}

type chooseColor struct {
	StructSize uint32
	pad0       uint32
	HwndOwner  uintptr
	Instance   uintptr
	RgbResult  uint32
	pad1       uint32
	CustColors uintptr
	Flags      uint32
	pad2       uint32
	CustData   uintptr
	Hook       uintptr
	Template   *uint16
}

// openFileName 对应 Win32 的 OPENFILENAMEW。
//
// 字段顺序要严格对齐 C 定义，尤其是尾部的两个 dwReserved：漏掉第二个会让
// FlagsEx 落到 148 而不是 152，sizeof 也从 160 缩到 152，错位后 comdlg32
// 会直接拿 CDERR_STRUCTSIZE 把对话框拒掉。
// x64 下 sizeof(OPENFILENAMEW) = 160；但实际下发的 lStructSize 见 ofnSizes。
type openFileName struct {
	StructSize     uint32
	pad0           uint32
	HwndOwner      uintptr
	Instance       uintptr
	Filter         *uint16
	CustomFilter   *uint16
	MaxCustFilter  uint32
	FilterIndex    uint32
	File           *uint16
	MaxFile        uint32
	pad1           uint32
	FileTitle      *uint16
	MaxFileTitle   uint32
	pad2           uint32
	InitialDir     *uint16
	Title          *uint16
	Flags          uint32
	FileOffset     uint16
	FileExtension  uint16
	DefExt         *uint16 // 104
	CustData       uintptr
	Hook           uintptr
	TemplateName   *uint16
	Reserved       uintptr // pvReserved
	ReservedDword  uint32  // dwReserved
	ReservedDword2 uint32  // dwReserved2
	FlagsEx        uint32  // 152
}

// shellExecuteInfo 对应 Win32 的 SHELLEXECUTEINFOW。
// 字段顺序与 C 定义一致，x64 下的对齐由 Go 自动补齐（与 MSVC 的自然对齐相同）。
type shellExecuteInfo struct {
	CbSize       uint32
	FMask        uint32
	Hwnd         uintptr
	LpVerb       *uint16
	LpFile       *uint16
	LpParameters *uint16
	LpDirectory  *uint16
	NShow        int32
	pad0         uint32
	HInstApp     uintptr
	LpIDList     uintptr
	LpClass      *uint16
	HkeyClass    uintptr
	DwHotKey     uint32
	pad1         uint32
	HIconMonitor uintptr // union { hIcon; hMonitor }
	HProcess     uintptr
}

// CDERR_STRUCTSIZE：lStructSize 和 comdlg32 认得的 sizeof(OPENFILENAMEW) 对不上。
const cdErrStructSize = 1

// ofnSizes 是 GetOpenFileNameW 依次尝试的 lStructSize。
//
// 标准 x64 的 sizeof(OPENFILENAMEW) 是 160（含 Vista 才加的 FlagsEx），但有些系统
// 上的 comdlg32 是老版本，只认 152（止于 dwReserved2）甚至 136（止于 lpTemplateName）。
// 本机实测：传 160 会被立刻以 CDERR_STRUCTSIZE 拒掉、对话框根本不弹；传 152 一切正常
// （标题、文件名初值、过滤器都读得对）。
//
// 三个尺寸下所有字段的偏移完全一致，老版本只是看不到尾部的 FlagsEx（我们本来就填 0），
// 所以按「新→旧」退化使用是安全的。
var ofnSizes = []uint32{
	uint32(unsafe.Sizeof(openFileName{})), // 160，新系统
	152,                                   // 止于 dwReserved2
	136,                                   // 止于 lpTemplateName，最老的版本
}

// ofnGoodSize 记住本机 comdlg32 接受过的 lStructSize。
//
// 只在第一次打开对话框时要撞一次 CDERR_STRUCTSIZE；之后直接从这个尺寸开始试，
// 既省掉一次必然失败的调用，也不会每次点「浏览…」都在日志里留一行噪声。
var ofnGoodSize uint32

// ofnCandidates 返回本次要依次尝试的 lStructSize：优先用已验证可用的那个。
func ofnCandidates() []uint32 {
	out := make([]uint32, 0, len(ofnSizes))
	if ofnGoodSize != 0 {
		out = append(out, ofnGoodSize)
	}
	for _, s := range ofnSizes {
		if s != ofnGoodSize {
			out = append(out, s)
		}
	}
	return out
}

// pickFile 弹出「打开文件」对话框，返回所选路径；取消返回空串。
//
// filter 需要是 "标题\0模式\0标题\0模式\0" 形式（结尾的双 null 由 utf16Buf 补上）。
// initial 是预填的当前值（可为空）：预填之后再次点「浏览…」会直接停在上次那个
// 目录、并带出文件名，不用每次从头找。
func pickFile(owner uintptr, title, filter, initial string, bufLen int) string {
	if bufLen < 520 {
		bufLen = 520
	}
	filterBuf := utf16Buf(filter)
	titleBuf := utf16FromString(title)
	defExtBuf := utf16FromString("exe")

	sizes := ofnCandidates()
	for _, size := range sizes {
		// 每次重试都要一份干净的缓冲区：失败的调用可能已经写过它
		fileBuf := make([]uint16, bufLen)
		if initial != "" {
			init := utf16Buf(initial)
			if len(init) > bufLen-1 {
				init = init[:bufLen-1]
			}
			copy(fileBuf, init)
		}

		ofn := openFileName{
			StructSize:  size,
			HwndOwner:   owner,
			Filter:      &filterBuf[0],
			FilterIndex: 1,
			File:        &fileBuf[0],
			MaxFile:     uint32(bufLen),
			Title:       titleBuf,
			DefExt:      defExtBuf,
			Flags:       ofnExplorer | ofnFileMustExist | ofnPathMustExist | ofnNoChangeDir,
		}
		ret, _, _ := pGetOpenFileNameW.Call(uintptr(unsafe.Pointer(&ofn)))

		// CommDlgExtendedError 是判断「尺寸是否被接受」的可靠信号：
		// CDERR_STRUCTSIZE 说明这个尺寸被拒；其它值（含 0 = 用户取消）都说明
		// comdlg32 已经认下这个尺寸并把对话框开起来了。
		e, _, _ := pCommDlgExtendedError.Call()
		if e == cdErrStructSize {
			app.logf("comdlg32 拒绝 lStructSize=%d（CDERR_STRUCTSIZE），换更小的尺寸重试", size)
			continue
		}

		if ofnGoodSize != size {
			ofnGoodSize = size
			if size != ofnSizes[0] {
				app.logf("文件对话框 lStructSize 降级为 %d（本机 comdlg32 不接受 %d），后续沿用", size, ofnSizes[0])
			}
		}
		if ret == 0 {
			// 用户按了「取消」，或者别的非尺寸类错误
			if e != 0 {
				app.logf("选择程序对话框失败，CommDlgExtendedError=0x%04X lStructSize=%d", e, size)
			}
			return ""
		}
		return utf16ToGoStr(fileBuf)
	}

	app.logf("选择程序对话框：%v 个尺寸全被 comdlg32 拒绝，无法打开", len(sizes))
	return ""
}

// guidFromString 解析 "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
func guidFromString(s string) (guid, bool) {
	var g guid
	clean := make([]byte, 0, 32)
	for i := 0; i < len(s); i++ {
		if s[i] != '-' && s[i] != '{' && s[i] != '}' {
			clean = append(clean, s[i])
		}
	}
	if len(clean) != 32 {
		return g, false
	}
	hex := func(b []byte) (uint64, bool) {
		var v uint64
		for _, c := range b {
			var d uint64
			switch {
			case c >= '0' && c <= '9':
				d = uint64(c - '0')
			case c >= 'a' && c <= 'f':
				d = uint64(c-'a') + 10
			case c >= 'A' && c <= 'F':
				d = uint64(c-'A') + 10
			default:
				return 0, false
			}
			v = v<<4 | d
		}
		return v, true
	}
	a, ok1 := hex(clean[0:8])
	b, ok2 := hex(clean[8:12])
	c, ok3 := hex(clean[12:16])
	if !ok1 || !ok2 || !ok3 {
		return g, false
	}
	g.Data1 = uint32(a)
	g.Data2 = uint16(b)
	g.Data3 = uint16(c)
	for i := 0; i < 8; i++ {
		v, ok := hex(clean[16+i*2 : 18+i*2])
		if !ok {
			return g, false
		}
		g.Data4[i] = byte(v)
	}
	return g, true
}

// ================================================================ 控件辅助

func sendMsg(hwnd uintptr, msg uint32, wparam, lparam uintptr) uintptr {
	r, _, _ := pSendMessageW.Call(hwnd, uintptr(msg), wparam, lparam)
	return r
}

func setWindowText(hwnd uintptr, s string) {
	pSetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(utf16FromString(s))))
}

func getWindowText(hwnd uintptr, max int) string {
	if max <= 0 {
		max = 512
	}
	buf := make([]uint16, max)
	n, _, _ := pGetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), uintptr(max))
	if n == 0 {
		return ""
	}
	buf[max-1] = 0
	return utf16ToGoStr(buf)
}

func checkDlgButton(hwnd uintptr, on bool) {
	v := uintptr(0)
	if on {
		v = 1
	}
	sendMsg(hwnd, bmSetCheck, v, 0)
}

func isChecked(hwnd uintptr) bool {
	return sendMsg(hwnd, bmGetCheck, 0, 0) == 1
}

func createWindowEx(exStyle uintptr, class, text string, style uintptr,
	x, y, w, h int32, parent uintptr, id int) uintptr {
	hwnd, _, _ := pCreateWindowExW.Call(
		exStyle,
		uintptr(unsafe.Pointer(utf16FromString(class))),
		uintptr(unsafe.Pointer(utf16FromString(text))),
		style,
		uintptr(int32ToUintptr(x)), uintptr(int32ToUintptr(y)),
		uintptr(int32ToUintptr(w)), uintptr(int32ToUintptr(h)),
		parent,
		uintptr(id),
		getModuleHandle(), 0,
	)
	return hwnd
}

func int32ToUintptr(v int32) uintptr {
	if v < 0 {
		return uintptr(int64(v))
	}
	return uintptr(v)
}

func makeFont(pt int, dpi int) uintptr {
	if dpi <= 0 {
		dpi = 96
	}
	h := -int(float64(pt)*float64(dpi)/72.0 + 0.5)
	f, _, _ := pCreateFontW.Call(
		uintptr(int32ToUintptr(int32(h))), 0, 0, 0, 400, 0, 0, 0,
		134,        // DEFAULT_CHARSET，保证中文正常
		0, 0, 5, 0, // CLEARTYPE_QUALITY
		uintptr(unsafe.Pointer(utf16FromString("Microsoft YaHei UI"))),
	)
	if f == 0 {
		f, _, _ = pCreateFontW.Call(
			uintptr(int32ToUintptr(int32(h))), 0, 0, 0, 400, 0, 0, 0,
			134, 0, 0, 5, 0,
			uintptr(unsafe.Pointer(utf16FromString("Segoe UI"))),
		)
	}
	return f
}

func systemDPI() int {
	hdc, _, _ := pGetDC.Call(0)
	if hdc == 0 {
		return 96
	}
	v, _, _ := pGetDeviceCaps.Call(hdc, logPixelsY)
	pReleaseDC.Call(0, hdc)
	if v < 72 || v > 480 {
		return 96
	}
	return int(v)
}

// colorref 把 0xRRGGBB 转成 Windows 的 COLORREF(0x00BBGGRR)
func colorref(rgb uint32) uint32 {
	return ((rgb & 0x0000FF) << 16) | (rgb & 0x00FF00) | ((rgb >> 16) & 0x0000FF)
}

func rgbFromColorref(c uint32) uint32 {
	return ((c & 0x0000FF) << 16) | (c & 0x00FF00) | ((c >> 16) & 0x0000FF)
}

func loword(v uintptr) int { return int(uint16(v & 0xFFFF)) }
func hiword(v uintptr) int { return int(uint16((v >> 16) & 0xFFFF)) }
