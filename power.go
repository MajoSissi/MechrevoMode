//go:build windows

package main

import (
	"fmt"
	"os/exec"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"unsafe"
)

// Windows 内置电源方案 GUID
const (
	planSaver    = "a1841308-3541-4fab-bc81-f71556f20b4a" // 节能
	planBalanced = "381b4222-f694-41f0-9685-ff5bb260df2e" // 平衡
	planHighPerf = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c" // 高性能
	planUltimate = "e9a42b02-d5df-448d-aa00-03f14749eb61" // 卓越性能（默认隐藏，需创建）
)

// powerSchemesKey 电源方案注册表根，用来判断某个方案是否真的存在
const powerSchemesKey = `SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes`

// powerPlan 一个电源方案
type powerPlan struct {
	GUID      string
	Name      string
	Installed bool
}

// builtinPlans 面板里固定展示的 4 个系统方案，顺序固定
var builtinPlans = []struct {
	guid string
	name string
}{
	{planSaver, "节能"},
	{planBalanced, "平衡"},
	{planHighPerf, "高性能"},
	{planUltimate, "卓越性能"},
}

func builtinName(guid string) string {
	for _, b := range builtinPlans {
		if strings.EqualFold(b.guid, guid) {
			return b.name
		}
	}
	return ""
}

// ---------------------------------------------------------------- 存在性 / 名称

// planInstalled 方案是否存在于本机
//
// 注意：PowerEnumerate(ACCESS_SCHEME) 在部分系统（含本机）只返回「当前活动方案」，
// 因此这里直接查注册表，既准确又不需要管理员权限。
func planInstalled(guid string) bool {
	if guid == "" {
		return true
	}
	return regKeyExists(hkeyLocalMachine, powerSchemesKey+`\`+guid)
}

// planRegistryName 读取方案的 FriendlyName，并把 "@dll,-id,Name" 形式化简
func planRegistryName(guid string) string {
	raw, ok := regGetStringRO(hkeyLocalMachine, powerSchemesKey+`\`+guid, "FriendlyName")
	if !ok || raw == "" {
		return ""
	}
	if strings.HasPrefix(raw, "@") {
		if i := strings.LastIndexByte(raw, ','); i >= 0 {
			if tail := strings.TrimSpace(raw[i+1:]); tail != "" {
				return tail
			}
		}
		return ""
	}
	return raw
}

// planDisplayName 给用户看的方案名
func planDisplayName(guid string) string {
	if guid == "" {
		return "未设置"
	}
	if n := builtinName(guid); n != "" {
		return n
	}
	if n := planRegistryName(guid); n != "" {
		return n
	}
	return "自定义方案"
}

// activePlan 返回当前活动的电源方案 GUID
func activePlan() string {
	var p *guid
	ret, _, _ := pPowerGetActiveScheme.Call(0, uintptr(unsafe.Pointer(&p)))
	if ret != 0 || p == nil {
		return ""
	}
	defer pLocalFree.Call(uintptr(unsafe.Pointer(p)))
	return guidString(p)
}

func guidString(g *guid) string {
	return fmt.Sprintf("%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x",
		g.Data1, g.Data2, g.Data3,
		g.Data4[0], g.Data4[1], g.Data4[2], g.Data4[3],
		g.Data4[4], g.Data4[5], g.Data4[6], g.Data4[7])
}

// ---------------------------------------------------------------- 下拉框选项

// planOptions 返回下拉框选项：4 个内置方案，固定顺序。
//
// 面板上一律只显示方案本名：部分方案（「卓越性能」）在本机默认不存在，
// 需要先复制才能用，但这一过程对用户完全透明，不做「未启用」之类的提示。
func planOptions() []powerPlan {
	out := make([]powerPlan, 0, len(builtinPlans))
	for _, b := range builtinPlans {
		out = append(out, powerPlan{GUID: b.guid, Name: b.name, Installed: true})
	}
	return out
}

// planLabel 生成下拉框显示文本
func planLabel(p powerPlan) string { return p.Name }

// ---------------------------------------------------------------- 模板方案别名表

// 有些内置方案（「卓越性能」）在本机只是「模板」，PowerSetActiveScheme 会返回
// ERROR_NOT_SUPPORTED，必须先 powercfg -duplicatescheme 复制一份再激活，而复制出来
// 的 GUID 每次都不一样。这里把「模板 GUID -> 实际 GUID」固定下来，既避免重复创建，
// 也让配置里始终保留标准 GUID（下拉框选中状态不会漂移）。
var (
	aliasMu   sync.Mutex
	planAlias = map[string]string{}
	aliasSave func(map[string]string)
)

// initPlanAlias 载入别名表；save 在别名变化时被回调（用于落盘）
func initPlanAlias(m map[string]string, save func(map[string]string)) {
	aliasMu.Lock()
	planAlias = make(map[string]string, len(m))
	for k, v := range m {
		planAlias[strings.ToLower(k)] = strings.ToLower(v)
	}
	aliasMu.Unlock()
	aliasSave = save
}

func aliasOf(guid string) string {
	aliasMu.Lock()
	defer aliasMu.Unlock()
	return planAlias[strings.ToLower(guid)]
}

func setAlias(tmpl, real string) {
	aliasMu.Lock()
	planAlias[strings.ToLower(tmpl)] = strings.ToLower(real)
	snapshot := make(map[string]string, len(planAlias))
	for k, v := range planAlias {
		snapshot[k] = v
	}
	aliasMu.Unlock()
	if aliasSave != nil {
		aliasSave(snapshot)
	}
}

func clearAlias(tmpl string) {
	aliasMu.Lock()
	_, had := planAlias[strings.ToLower(tmpl)]
	delete(planAlias, strings.ToLower(tmpl))
	snapshot := make(map[string]string, len(planAlias))
	for k, v := range planAlias {
		snapshot[k] = v
	}
	aliasMu.Unlock()
	if had && aliasSave != nil {
		aliasSave(snapshot)
	}
}

// resolvedPlan 返回配置里那个 GUID 在本机实际对应的方案 GUID
func resolvedPlan(guidStr string) string {
	if guidStr == "" {
		return ""
	}
	if a := aliasOf(guidStr); a != "" && planInstalled(a) {
		return a
	}
	return guidStr
}

// ---------------------------------------------------------------- 切换

var guidRe = regexp.MustCompile(`[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}`)

// applyPlan 切换到指定电源方案，返回实际生效的 GUID。
//
// 流程：别名 -> 直接激活 -> （仅内置方案）复制后激活。
// 「系统未启用」的方案在这里被静默补齐，调用方无需关心。
func applyPlan(guidStr string) (string, error) {
	if guidStr == "" {
		return "", nil // 不改变
	}

	// 1. 已有别名且仍然存在
	if a := aliasOf(guidStr); a != "" {
		if planInstalled(a) {
			if err := setActive(a); err == nil {
				return a, nil
			}
		}
		clearAlias(guidStr) // 别名失效（方案被删了），走下面的重建流程
	}

	// 2. 直接激活
	if err := setActive(guidStr); err == nil {
		return guidStr, nil
	}

	// 3. 只有内置方案才允许复制创建，避免把用户的任意 GUID 复制出新方案
	if builtinName(guidStr) == "" {
		return "", fmt.Errorf("无法激活电源方案 %s", guidStr)
	}
	fresh, err := duplicateScheme(guidStr)
	if err != nil {
		return "", err
	}
	if err := setActive(fresh); err != nil {
		return "", err
	}
	setAlias(guidStr, fresh)
	return fresh, nil
}

func setActive(guidStr string) error {
	g, ok := guidFromString(guidStr)
	if !ok {
		return fmt.Errorf("无效的电源方案 %q", guidStr)
	}
	ret, _, _ := pPowerSetActiveScheme.Call(0, uintptr(unsafe.Pointer(&g)))
	if ret != 0 {
		return fmt.Errorf("切换电源方案失败 (0x%X)", ret)
	}
	return nil
}

// duplicateScheme 用系统自带的 powercfg 复制方案，返回新方案的 GUID
func duplicateScheme(src string) (string, error) {
	before := map[string]bool{}
	for _, s := range regEnumSubKeys(hkeyLocalMachine, powerSchemesKey) {
		before[strings.ToLower(s)] = true
	}
	srcLower := strings.ToLower(src)

	cmd := exec.Command("powercfg.exe", "-duplicatescheme", src)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("创建电源方案失败: %v", err)
	}

	// powercfg 会把新方案的 GUID 打印出来
	for _, m := range guidRe.FindAllString(string(out), -1) {
		if g := strings.ToLower(m); g != srcLower && !before[g] {
			return g, nil
		}
	}
	// 解析不到就对比注册表新增项
	for _, s := range regEnumSubKeys(hkeyLocalMachine, powerSchemesKey) {
		if g := strings.ToLower(s); g != srcLower && !before[g] {
			return g, nil
		}
	}
	return "", fmt.Errorf("电源方案已创建但未能识别其 GUID")
}
