//go:build windows

package main

import (
	"os"
	"path/filepath"
	"strings"
)

// 「以管理员身份运行」用的是 Windows 的兼容性标志，和资源管理器里
// 「属性 → 兼容性 → 以管理员身份运行此程序」写的是同一处：
//
//	HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers
//	  值名 = 可执行文件完整路径
//	  数据 = "~ RUNASADMIN"
//
// 选它的理由：
//   - 属于当前用户范围（HKCU），写入时不需要管理员权限，不会先有鸡还是先有蛋；
//   - 由系统在 CreateProcess 阶段完成提权，开机自启、双击、快捷方式全都生效，
//     程序自身不需要处理「先启动再重启」这套逻辑；
//   - 用户随时能在资源管理器里看到并撤掉，不搞暗箱。
//
// 副作用：UAC 开着时每次启动都会弹一次授权窗口（除非把 UAC 调到「从不通知」）。
const appCompatLayersKey = `Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers`

// runAsAdminToken 兼容性标志里的提权令牌
const runAsAdminToken = "RUNASADMIN"

// errCancelled 用户在 UAC 授权窗口里点了「否」（ERROR_CANCELLED）
const errCancelled = 1223

// elevatedArg 提权重启时追加给自己的标记参数。
// 带上它就说明「已经尝试过提权」，避免提权失败时无限重启。
const elevatedArg = "-elevated-relaunch"

// compatValueName 兼容性标志的值名就是可执行文件的完整路径
func compatValueName() string {
	p := exePath()
	if p == "" {
		return ""
	}
	if abs, err := filepath.Abs(p); err == nil {
		p = abs
	}
	return p
}

// compatFlags 读取当前 exe 已有的兼容性标志（形如 ["~", "RUNASADMIN"]）
func compatFlags() []string {
	name := compatValueName()
	if name == "" {
		return nil
	}
	v, ok := regGetStringRO(hkeyCurrentUser, appCompatLayersKey, name)
	if !ok {
		return nil
	}
	return strings.Fields(v)
}

// isRunAsAdminSet 当前 exe 是否已被标记为「以管理员身份运行」。
// 这里同时也是配置项的权威来源——配置和注册表不会各说各话。
func isRunAsAdminSet() bool {
	for _, f := range compatFlags() {
		if strings.EqualFold(f, runAsAdminToken) {
			return true
		}
	}
	return false
}

// setRunAsAdmin 写入或清除 RUNASADMIN 令牌。
// 只动这一个令牌，用户手工加过的其它兼容性标志（比如兼容模式）原样保留。
func setRunAsAdmin(on bool) error {
	name := compatValueName()
	if name == "" {
		return os.ErrInvalid
	}

	keep := make([]string, 0, 4)
	for _, f := range compatFlags() {
		// "~" 是标志串的固定前缀、RUNASADMIN 是本次要重写的那个，其余原样留下
		if f == "" || f == "~" || strings.EqualFold(f, runAsAdminToken) {
			continue
		}
		keep = append(keep, f)
	}
	if on {
		keep = append(keep, runAsAdminToken)
	}

	if len(keep) == 0 {
		// 没有任何标志了就把整条值删掉，别留一个空的 "~"
		regDeleteValue(hkeyCurrentUser, appCompatLayersKey, name)
		return nil
	}
	return regSetString(hkeyCurrentUser, appCompatLayersKey, name, "~ "+strings.Join(keep, " "))
}

// quoteArg 按 Windows 命令行惯例给参数加引号
func quoteArg(s string) string {
	if s == "" {
		return `""`
	}
	if strings.ContainsAny(s, " \t\"") {
		return `"` + strings.ReplaceAll(s, `"`, `\"`) + `"`
	}
	return s
}

// relaunchElevated 以管理员身份重新启动自己。
//
// 返回 (子进程句柄, 错误码)：句柄非 0 表示新进程**确实已经创建**；
// 句柄为 0 时错误码说明原因，errCancelled 表示用户在 UAC 里点了「否」。
func relaunchElevated(extra ...string) (uintptr, uintptr) {
	exe := exePath()
	if exe == "" {
		return 0, 1
	}

	args := make([]string, 0, len(os.Args)+len(extra)+1)
	for _, a := range os.Args[1:] {
		if strings.EqualFold(a, elevatedArg) {
			continue // 旧标记不往新进程传，否则会被当成「已尝试过」
		}
		if containsStr(extra, a) {
			continue // 调用方要额外加的（如 -show）已经在参数里了，别重复
		}
		args = append(args, quoteArg(a))
	}
	args = append(args, extra...)
	args = append(args, elevatedArg)

	app.logf("提权重启: %s %s", exe, strings.Join(args, " "))
	return shellExecFileAs(exe, strings.Join(args, " "), filepath.Dir(exe))
}
