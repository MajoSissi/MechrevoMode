// click：开发期辅助工具。按「窗口类名 + 标题 + 控件 ID」找到子控件并点击/读状态，
// 用来在无法用鼠标操作的沙箱里验证按钮、复选框逻辑。
//
// 由于被操作的程序可能以管理员身份运行（高完整性级别），本工具自身也需要提权
// 才能读到真实状态；而提权后 stdout 会脱离调用方的管道，所以结果同时写进
// 同目录下的 _click.log，用 -r 参数读取。
//
// 用法：
//
//	click.exe <窗口类名|-> <窗口标题> <控件ID> [bm|post|state|check|uncheck]
//	  bm      用 SendMessage 发 BM_CLICK（默认，会阻塞到处理完）
//	  post    用 PostMessage 发 BM_CLICK（目标可能弹模态框时用这个）
//	  state   只打印状态
//	  check / uncheck  直接设置勾选状态
//
//	click.exe -msgbox <标题> yes|no
//	  找到该标题的消息框并按下「是」/「否」
//
//	click.exe -flag <exe路径|self> on|off
//	  给某个 exe 打上 / 清除 RUNASADMIN 兼容性标志
//
//	click.exe -r
//	  打印并清空 _click.log
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"unsafe"
)

var (
	user32           = syscall.NewLazyDLL("user32.dll")
	advapi32         = syscall.NewLazyDLL("advapi32.dll")
	pFind            = user32.NewProc("FindWindowW")
	pGetDlgItem      = user32.NewProc("GetDlgItem")
	pSendMessage     = user32.NewProc("SendMessageW")
	pPostMessage     = user32.NewProc("PostMessageW")
	pIsWindowEnabled = user32.NewProc("IsWindowEnabled")
	pGetWindowText   = user32.NewProc("GetWindowTextW")
	pGetClassNameW   = user32.NewProc("GetClassNameW")
	pEnumWindows     = user32.NewProc("EnumWindows")
	pIsWindowVisible = user32.NewProc("IsWindowVisible")

	pRegCreateKeyExW = advapi32.NewProc("RegCreateKeyExW")
	pRegSetValueExW  = advapi32.NewProc("RegSetValueExW")
	pRegDeleteValueW = advapi32.NewProc("RegDeleteValueW")
	pRegEnumValueW   = advapi32.NewProc("RegEnumValueW")
	pRegCloseKey     = advapi32.NewProc("RegCloseKey")
)

const (
	bmGetCheck = 0x00F0
	bmSetCheck = 0x00F1
	bmGetState = 0x00F2
	bmClick    = 0x00F5

	wmCommand = 0x0111
	wmClose   = 0x0010
	idYes     = 6
	idNo      = 7

	hkeyCurrentUser = 0x80000001
	keyAllAccess    = 0x000F003F
	regSZ           = 1
)

const layersKey = `Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers`

var logF *os.File

func say(format string, a ...interface{}) {
	s := fmt.Sprintf(format, a...)
	fmt.Println(s)
	if logF != nil {
		fmt.Fprintln(logF, s)
		logF.Sync()
	}
}

func openLog() {
	exe, err := os.Executable()
	if err != nil {
		return
	}
	p := filepath.Join(filepath.Dir(exe), "_click.log")
	logF, _ = os.OpenFile(p, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
}

func u(s string) *uint16 {
	p, err := syscall.UTF16PtrFromString(s)
	if err != nil {
		return nil
	}
	return p
}

func utf16b(s string) []uint16 {
	r, err := syscall.UTF16FromString(s)
	if err != nil {
		return []uint16{0}
	}
	return r
}

// resolveExe 解析 exe 路径。
//
// Git Bash 会对命令行里形如 C:\... 的参数做路径转换（把 \ 变 /，还会按 ; 拆开），
// 所以系统目录下的程序直接用裸名字给，由程序自己去拼 System32 的完整路径。
func resolveExe(p string) string {
	if p == "self" {
		exe, err := os.Executable()
		if err == nil {
			return exe
		}
		return p
	}
	if !strings.ContainsAny(p, `\/`) {
		root := os.Getenv("SystemRoot")
		if root == "" {
			root = `C:\Windows`
		}
		name := p
		if !strings.HasSuffix(strings.ToLower(name), ".exe") {
			name += ".exe"
		}
		return filepath.Join(root, "System32", name)
	}
	if abs, err := filepath.Abs(p); err == nil {
		return abs
	}
	return p
}

// purgeBad 清掉 Layers 里明显是被命令行转换弄坏的值（名字含 ';' 或不是合法绝对路径）
func purgeBad() {
	sub := utf16b(layersKey)
	var hKey uintptr
	ret, _, err := pRegCreateKeyExW.Call(hkeyCurrentUser,
		uintptr(unsafe.Pointer(&sub[0])), 0, 0, 0, keyAllAccess, 0,
		uintptr(unsafe.Pointer(&hKey)), 0)
	if ret != 0 {
		say("RegCreateKeyExW failed: %v", err)
		return
	}
	defer pRegCloseKey.Call(hKey)

	for i := 0; i < 256; i++ {
		name := make([]uint16, 1024)
		n := uint32(len(name))
		data := make([]uint16, 512)
		d := uint32(len(data) * 2)
		ret, _, _ := pRegEnumValueW.Call(hKey, uintptr(i),
			uintptr(unsafe.Pointer(&name[0])), uintptr(unsafe.Pointer(&n)), 0, 0,
			uintptr(unsafe.Pointer(&data[0])), uintptr(unsafe.Pointer(&d)))
		if ret != 0 {
			break
		}
		nm := syscall.UTF16ToString(name)
		if strings.Contains(nm, ";") || !filepath.IsAbs(nm) {
			pRegDeleteValueW.Call(hKey, uintptr(unsafe.Pointer(&name[0])))
			say("已清理无效项: %s", nm)
			i-- // 删掉后下标要回退
		}
	}
	say("清理完成")
}

// setCompatFlag 给指定 exe 打上 / 清除 RUNASADMIN 兼容性标志
func setCompatFlag(path string, on bool) {
	path = resolveExe(path)
	sub := utf16b(layersKey)
	var hKey uintptr
	ret, _, err := pRegCreateKeyExW.Call(hkeyCurrentUser,
		uintptr(unsafe.Pointer(&sub[0])), 0, 0, 0, keyAllAccess, 0,
		uintptr(unsafe.Pointer(&hKey)), 0)
	if ret != 0 {
		say("RegCreateKeyExW failed: %v", err)
		os.Exit(1)
	}
	defer pRegCloseKey.Call(hKey)

	name := utf16b(path)
	if !on {
		pRegDeleteValueW.Call(hKey, uintptr(unsafe.Pointer(&name[0])))
		say("已清除 RUNASADMIN: %s", path)
		return
	}
	val := utf16b("~ RUNASADMIN")
	ret, _, err = pRegSetValueExW.Call(hKey,
		uintptr(unsafe.Pointer(&name[0])), 0, regSZ,
		uintptr(unsafe.Pointer(&val[0])), uintptr(len(val)*2))
	if ret != 0 {
		say("RegSetValueExW failed: %v", err)
		os.Exit(1)
	}
	say("已设置 RUNASADMIN: %s", path)
}

func main() {
	openLog()
	if logF != nil {
		defer logF.Close()
	}

	if len(os.Args) < 2 {
		say("usage: click <class|-> <title> <id> [bm|post|state|check|uncheck]")
		say("       click -msgbox <title> yes|no")
		say("       click -flag <exePath|self> on|off")
		os.Exit(2)
	}

	switch os.Args[1] {
	case "-r":
		p := ""
		if exe, err := os.Executable(); err == nil {
			p = filepath.Join(filepath.Dir(exe), "_click.log")
		}
		b, err := os.ReadFile(p)
		if err != nil {
			fmt.Println("no log:", err)
			return
		}
		fmt.Print(string(b))
		_ = os.Remove(p)
		return

	case "-flag":
		if len(os.Args) < 4 {
			say("usage: click -flag <exePath|self|裸名> on|off")
			os.Exit(2)
		}
		setCompatFlag(os.Args[2], os.Args[3] == "on")
		return

	case "-purge":
		purgeBad()
		return

	case "-list":
		// 列出所有顶层窗口的「类名 | 标题」，用于确认实际的窗口标识
		var cb uintptr
		cb = syscall.NewCallback(func(hwnd, lparam uintptr) uintptr {
			cls := make([]uint16, 256)
			pGetClassNameW.Call(hwnd, uintptr(unsafe.Pointer(&cls[0])), 256)
			t := make([]uint16, 256)
			pGetWindowText.Call(hwnd, uintptr(unsafe.Pointer(&t[0])), 256)
			vis, _, _ := pIsWindowVisible.Call(hwnd)
			say("hwnd=%-10d vis=%v  %-28s | %s", hwnd, vis != 0,
				syscall.UTF16ToString(cls), syscall.UTF16ToString(t))
			return 1
		})
		pEnumWindows.Call(cb, 0)
		return

	case "-close":
		// 给顶层窗口发 WM_CLOSE，让目标程序自己走正常退出流程
		if len(os.Args) < 4 {
			say("usage: click -close <class|-> <title>")
			os.Exit(2)
		}
		class, title := os.Args[2], os.Args[3]
		if class == "-" {
			class = ""
		}
		var pc uintptr
		if class != "" {
			pc = uintptr(unsafe.Pointer(u(class)))
		}
		hwnd, _, _ := pFind.Call(pc, uintptr(unsafe.Pointer(u(title))))
		if hwnd == 0 {
			say("window not found: %s", title)
			os.Exit(1)
		}
		say("发送 WM_CLOSE -> hwnd=%d", hwnd)
		pPostMessage.Call(hwnd, wmClose, 0, 0)
		say("done")
		return

	case "-msgbox":
		if len(os.Args) < 3 {
			say("usage: click -msgbox <title> yes|no")
			os.Exit(2)
		}
		title := os.Args[2]
		want := "no"
		if len(os.Args) >= 4 {
			want = os.Args[3]
		}
		id := uintptr(idNo)
		if want == "yes" || want == "是" {
			id = idYes
		}
		hwnd, _, _ := pFind.Call(uintptr(unsafe.Pointer(u("#32770"))), uintptr(unsafe.Pointer(u(title))))
		if hwnd == 0 {
			say("messagebox not found: %s", title)
			os.Exit(1)
		}
		say("msgbox hwnd=%d -> 按下 %s", hwnd, want)
		pSendMessage.Call(hwnd, wmCommand, id, 0)
		say("done")
		return
	}

	if len(os.Args) < 4 {
		say("usage: click <class|-> <title> <id> [bm|post|state|check|uncheck]")
		os.Exit(2)
	}
	class, title, idStr := os.Args[1], os.Args[2], os.Args[3]
	mode := "bm"
	if len(os.Args) >= 5 {
		mode = os.Args[4]
	}
	if class == "-" {
		class = ""
	}
	id, err := strconv.Atoi(idStr)
	if err != nil {
		say("bad id: %s", idStr)
		os.Exit(2)
	}

	var pc uintptr
	if class != "" {
		pc = uintptr(unsafe.Pointer(u(class)))
	}
	hwnd, _, _ := pFind.Call(pc, uintptr(unsafe.Pointer(u(title))))
	if hwnd == 0 {
		say("window not found: %s", title)
		os.Exit(1)
	}
	ctl, _, _ := pGetDlgItem.Call(hwnd, uintptr(id))
	if ctl == 0 {
		say("control not found, id = %d", id)
		os.Exit(1)
	}

	txt := make([]uint16, 256)
	pGetWindowText.Call(ctl, uintptr(unsafe.Pointer(&txt[0])), 256)
	en, _, _ := pIsWindowEnabled.Call(ctl)
	st, _, _ := pSendMessage.Call(ctl, bmGetState, 0, 0)
	ck, _, _ := pSendMessage.Call(ctl, bmGetCheck, 0, 0)
	say("hwnd=%d ctl=%d text=%q enabled=%v check=%d state=0x%X",
		hwnd, ctl, syscall.UTF16ToString(txt), en != 0, ck, st)

	switch mode {
	case "state":
	case "check":
		pSendMessage.Call(ctl, bmSetCheck, 1, 0)
		say("-> checked")
	case "uncheck":
		pSendMessage.Call(ctl, bmSetCheck, 0, 0)
		say("-> unchecked")
	case "post":
		r, _, _ := pPostMessage.Call(ctl, bmClick, 0, 0)
		say("-> PostMessage(BM_CLICK) ret = %d", r)
	default:
		r, _, _ := pSendMessage.Call(ctl, bmClick, 0, 0)
		say("-> SendMessage(BM_CLICK) ret = %d", r)
	}
}
