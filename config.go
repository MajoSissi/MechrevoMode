//go:build windows

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"
	"unsafe"
)

// ModeItem 一个可切换的档位：既对应 GCU 的模式+档位，也承载托盘菜单与图标的展示设置。
type ModeItem struct {
	Key       string `json:"key"`        // 稳定标识，用于配置迁移
	Mode      int    `json:"mode"`       // GCU 模式：0 办公 / 1 均衡 / 2 狂暴 / 3 自定义
	Slot      int    `json:"slot"`       // ProfileIndex；-1 表示不带档位参数
	Name      string `json:"name"`       // 显示名称（可改）
	ShowTray  bool   `json:"show_tray"`  // 是否出现在托盘右键菜单
	IconColor uint32 `json:"icon_color"` // 托盘图标底色 0xRRGGBB
	IconGlyph string `json:"icon_glyph"` // 托盘图标上的单个字母/数字
	PowerPlan string `json:"power_plan"` // 电源计划 GUID；空 = 不改变
}

// Config 持久化设置，存放在 %APPDATA%\MechrevoMode\config.json
type Config struct {
	mu sync.Mutex `json:"-"`

	Version     int        `json:"version"`
	AutoStart   bool       `json:"auto_start"`   // 开机自启
	AutoElevate bool       `json:"auto_elevate"` // 以管理员身份运行（写当前用户兼容性标志）
	ClientIndex int        `json:"client_index"` // GCU MQTT 客户端序号，-1 = 自动探测
	Items       []ModeItem `json:"items"`        // 模式项，顺序即托盘菜单顺序
	CurKey      string     `json:"cur_key"`      // 当前选中的档位 Key

	// 启动命令：程序启动后延迟若干秒执行一次，用于 ryzenadj 之类的降压/调优工具
	RunEnabled bool   `json:"run_enabled"`   // 是否启用
	RunPath    string `json:"run_path"`      // 可执行文件完整路径
	RunArgs    string `json:"run_args"`      // 命令行参数
	RunDelay   int    `json:"run_delay_sec"` // 延迟秒数，0 = 立即
	RunElevate bool   `json:"run_elevate"`   // 以管理员身份运行（会弹 UAC）

	// PlanAlias 记录「内置模板方案 GUID -> 本机实际方案 GUID」。
	// 「卓越性能」之类的模板方案必须先 powercfg -duplicatescheme 复制一份才能激活，
	// 而复制出来的 GUID 每次都不同。用这张表把它固定下来，避免重复创建方案，
	// 也让 items 里始终保留标准 GUID，下拉框选中状态不会漂移。
	PlanAlias map[string]string `json:"plan_alias,omitempty"`
}

// 各档位的固定标识
const (
	keyOffice     = "office"
	keyBalance    = "balance"
	keyTurbo      = "turbo"
	keyCustomBase = "custom" // custom1 .. custom5
)

// legacyKeys 旧版本用过的 key。v3 有 turbo_game / turbo_silent 两个狂暴档位，
// v4 合并成单个 turbo，迁移时把这两个都归一到 turbo。
var legacyKeys = map[string]string{
	"turbo_game":   keyTurbo,
	"turbo_silent": keyTurbo,
}

// customKeyOf 生成自定义档位的固定 key
func customKeyOf(i int) string {
	return keyCustomBase + strconv.Itoa(i+1)
}

func defaultItems() []ModeItem {
	items := []ModeItem{
		// 静音（办公模式）
		{Key: keyOffice, Mode: ModeOffice, Slot: -1, Name: "静音", ShowTray: true,
			IconColor: 0x27AE60, IconGlyph: "E", PowerPlan: planBalanced},
		// 均衡
		{Key: keyBalance, Mode: ModeBalance, Slot: -1, Name: "均衡", ShowTray: true,
			IconColor: 0x2F80ED, IconGlyph: "B", PowerPlan: planBalanced},
		// 狂暴（不再区分子档位，下发时不带 ProfileIndex）
		{Key: keyTurbo, Mode: ModeTurbo, Slot: -1, Name: "狂暴", ShowTray: true,
			IconColor: 0xE2445C, IconGlyph: "G", PowerPlan: planHighPerf},
	}
	// 自定义配色：前三个沿用静音/均衡/狂暴的色系，后两个保持紫色
	customColors := []uint32{0x27AE60, 0x2F80ED, 0xE2445C, 0x9B51E0, 0x9B51E0}
	for i := 0; i < customSlotCount; i++ {
		c := uint32(0x9B51E0)
		if i < len(customColors) {
			c = customColors[i]
		}
		items = append(items, ModeItem{
			Key:       customKeyOf(i),
			Mode:      ModeCustom,
			Slot:      i,
			Name:      "自定义 " + string(rune('1'+i)),
			ShowTray:  true,
			IconColor: c,
			IconGlyph: string(rune('1' + i)),
			PowerPlan: planBalanced,
		})
	}
	return items
}

// origLabel 该档位在官方控制台里的原始标识。
// 名称允许用户随便改，这一列用于始终能看清「这行到底对应哪个硬件档位」。
func (m ModeItem) origLabel() string {
	switch m.Mode {
	case ModeOffice:
		return "静音"
	case ModeBalance:
		return "均衡"
	case ModeTurbo:
		return "狂暴"
	case ModeCustom:
		return fmt.Sprintf("自定义 · 档位 %d", m.Slot+1)
	}
	return "未知"
}

func defaultConfig() *Config {
	return &Config{
		Version:     6,
		ClientIndex: -1,
		Items:       defaultItems(),
		PlanAlias:   map[string]string{},
	}
}

func configDir() string {
	base, err := os.UserConfigDir()
	if err != nil || base == "" {
		base = os.Getenv("APPDATA")
	}
	dir := filepath.Join(base, "MechrevoMode")
	_ = os.MkdirAll(dir, 0o755)
	return dir
}

func configPath() string { return filepath.Join(configDir(), "config.json") }

func loadConfig() *Config {
	cfg := defaultConfig()

	data, err := os.ReadFile(configPath())
	if err != nil {
		return cfg
	}

	// 先读原始字段，兼容旧版本（v1 只有 mode/profile，没有 items）
	var raw struct {
		Version     int               `json:"version"`
		AutoStart   bool              `json:"auto_start"`
		AutoElevate bool              `json:"auto_elevate"`
		ClientIndex int               `json:"client_index"`
		Items       []ModeItem        `json:"items"`
		CurKey      string            `json:"cur_key"`
		RunEnabled  bool              `json:"run_enabled"`
		RunPath     string            `json:"run_path"`
		RunArgs     string            `json:"run_args"`
		RunDelay    int               `json:"run_delay_sec"`
		RunElevate  bool              `json:"run_elevate"`
		PlanAlias   map[string]string `json:"plan_alias"`

		Mode    int `json:"mode"`    // v1
		Profile int `json:"profile"` // v1
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return defaultConfig()
	}

	cfg.Version = 6
	cfg.AutoStart = raw.AutoStart
	cfg.AutoElevate = raw.AutoElevate
	cfg.ClientIndex = raw.ClientIndex
	cfg.RunEnabled = raw.RunEnabled
	cfg.RunPath = raw.RunPath
	cfg.RunArgs = raw.RunArgs
	cfg.RunDelay = raw.RunDelay
	cfg.RunElevate = raw.RunElevate
	cfg.PlanAlias = map[string]string{}
	for k, v := range raw.PlanAlias {
		if k != "" && v != "" {
			cfg.PlanAlias[strings.ToLower(k)] = strings.ToLower(v)
		}
	}

	if len(raw.Items) == 0 {
		cfg.Items = defaultItems()
		cfg.CurKey = keyForModeSlot(raw.Mode, raw.Profile)
	} else {
		cfg.Items = repairItems(raw.Items)
		cfg.CurKey = raw.CurKey
	}

	if cfg.ClientIndex > 9 {
		cfg.ClientIndex = -1
	}
	if cfg.indexOf(cfg.CurKey) < 0 {
		cfg.CurKey = keyBalance
	}
	return cfg
}

// legacyDefaults 各档位在历史版本里用过的默认外观。
// 只有当配置里的值仍等于旧默认值时才会被升级——用户手动改过的一律保留。
var legacyDefaults = map[string]struct {
	names  []string
	glyphs []string
	colors []uint32
}{
	keyOffice:  {names: []string{"办公"}, glyphs: []string{"O"}, colors: []uint32{0x2F80ED}},
	keyBalance: {glyphs: []string{"B"}, colors: []uint32{0x27AE60}},
	keyTurbo: {
		names:  []string{"游戏狂暴", "高能狂暴", "静音狂暴"},
		glyphs: []string{"G", "S", "T"},
		colors: []uint32{0xE2445C, 0xF2994A},
	},
	// 自定义 1-3 早先默认都是紫色，现在改成与静音/均衡/狂暴同色系
	customKeyOf(0): {colors: []uint32{0x9B51E0}},
	customKeyOf(1): {colors: []uint32{0x9B51E0}},
	customKeyOf(2): {colors: []uint32{0x9B51E0}},
}

func containsStr(list []string, v string) bool {
	for _, s := range list {
		if s == v {
			return true
		}
	}
	return false
}

func containsColor(list []uint32, v uint32) bool {
	for _, c := range list {
		if c == v {
			return true
		}
	}
	return false
}

// repairItems 归一化配置：合并旧版的两个狂暴档位、升级旧默认外观、
// 补齐缺失项、修正越界值。顺序始终以默认顺序为准。
func repairItems(in []ModeItem) []ModeItem {
	def := defaultItems()
	byKey := make(map[string]ModeItem, len(in))
	for _, it := range in {
		k := it.Key
		if nk, ok := legacyKeys[k]; ok {
			k = nk // turbo_game / turbo_silent -> turbo
		}
		it.Key = k
		if _, dup := byKey[k]; dup {
			continue // 合并时以先出现的那个为准（turbo_game 排在 turbo_silent 前面）
		}
		byKey[k] = it
	}

	out := make([]ModeItem, 0, len(def))
	for _, d := range def {
		it, ok := byKey[d.Key]
		if !ok {
			out = append(out, d)
			continue
		}
		// mode/slot 属于硬件契约，不允许被配置改坏
		it.Key = d.Key
		it.Mode = d.Mode
		it.Slot = d.Slot

		if it.IconColor > 0xFFFFFF {
			it.IconColor = d.IconColor
		}
		if utf8.RuneCountInString(it.IconGlyph) != 1 {
			it.IconGlyph = d.IconGlyph
		}

		// 仍是旧默认外观的，升级成当前默认
		if old, has := legacyDefaults[d.Key]; has {
			if containsStr(old.names, it.Name) {
				it.Name = d.Name
			}
			if containsStr(old.glyphs, it.IconGlyph) {
				it.IconGlyph = d.IconGlyph
			}
			if containsColor(old.colors, it.IconColor) {
				it.IconColor = d.IconColor
			}
		}

		if it.Name == "" {
			it.Name = d.Name
		}
		// 电源计划不再有「不改变」选项，空值一律填成该档位的默认方案
		if it.PowerPlan == "" {
			it.PowerPlan = d.PowerPlan
		}
		out = append(out, it)
	}
	return out
}

// keyForModeSlot 把 GCU 回报的 mode+profile 映射到档位 key
func keyForModeSlot(mode, profile int) string {
	switch mode {
	case ModeOffice:
		return keyOffice
	case ModeBalance:
		return keyBalance
	case ModeTurbo:
		// 狂暴不再区分子档位，模式一致即视为同一个档位
		return keyTurbo
	case ModeCustom:
		if profile >= 0 && profile < customSlotCount {
			return customKeyOf(profile)
		}
		return customKeyOf(0)
	}
	return keyBalance
}

// ---------------------------------------------------------------- 查询

// indexOf 返回 key 对应的下标，找不到返回 -1
func (c *Config) indexOf(key string) int {
	for i := range c.Items {
		if c.Items[i].Key == key {
			return i
		}
	}
	return -1
}

// indexForModeSlot 根据 GCU 回报的 mode/profile 找到对应档位下标
func (c *Config) indexForModeSlot(mode, profile int) int {
	return c.indexOf(keyForModeSlot(mode, profile))
}

// trayItems 返回需要显示在托盘菜单里的档位下标
func (c *Config) trayItems() []int {
	out := make([]int, 0, len(c.Items))
	for i := range c.Items {
		if c.Items[i].ShowTray {
			out = append(out, i)
		}
	}
	return out
}

// ---------------------------------------------------------------- 保存

// save 原子写入配置文件（先写临时文件再改名，避免并发写到一半损坏）
func (c *Config) save() {
	c.mu.Lock()
	defer c.mu.Unlock()

	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return
	}
	p := configPath()
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return
	}
	if err := os.Rename(tmp, p); err != nil {
		_ = os.Remove(tmp)
	}
}

// ---------------------------------------------------------------- 开机自启

const (
	autostartKey     = `Software\Microsoft\Windows\CurrentVersion\Run`
	autostartValName = "MechrevoMode"

	keyAllAccess         = 0x000F003F
	regOptionNonVolatile = 0
)

func regOpenOrCreate(hive uintptr, sub string) (uintptr, error) {
	var hKey uintptr
	subP := utf16FromString(sub)
	ret, _, err := pRegCreateKeyExW.Call(
		hive,
		uintptr(unsafe.Pointer(subP)),
		0, 0,
		regOptionNonVolatile,
		keyAllAccess,
		0,
		uintptr(unsafe.Pointer(&hKey)),
		0,
	)
	if ret != 0 {
		return 0, err
	}
	return hKey, nil
}

func regSetString(hive uintptr, sub, name, value string) error {
	hKey, err := regOpenOrCreate(hive, sub)
	if err != nil {
		return err
	}
	defer pRegCloseKey.Call(hKey)

	v := utf16Buf(value)
	ret, _, err := pRegSetValueExW.Call(
		hKey,
		uintptr(unsafe.Pointer(utf16FromString(name))),
		0,
		regSZ,
		uintptr(unsafe.Pointer(&v[0])),
		uintptr(len(v)*2),
	)
	if ret != 0 {
		return err
	}
	return nil
}

func regDeleteValue(hive uintptr, sub, name string) {
	hKey, err := regOpenOrCreate(hive, sub)
	if err != nil {
		return
	}
	defer pRegCloseKey.Call(hKey)
	pRegDeleteValueW.Call(hKey, uintptr(unsafe.Pointer(utf16FromString(name))))
}

func regGetString(hive uintptr, sub, name string) (string, bool) {
	hKey, err := regOpenOrCreate(hive, sub)
	if err != nil {
		return "", false
	}
	defer pRegCloseKey.Call(hKey)

	buf := make([]uint16, 2048)
	size := uint32(len(buf) * 2)
	ret, _, _ := pRegQueryValueExW.Call(
		hKey,
		uintptr(unsafe.Pointer(utf16FromString(name))),
		0, 0,
		uintptr(unsafe.Pointer(&buf[0])),
		uintptr(unsafe.Pointer(&size)),
	)
	if ret != 0 {
		return "", false
	}
	return utf16ToGoStr(buf), true
}

func exePath() string {
	p, err := os.Executable()
	if err != nil {
		return ""
	}
	return p
}

func applyAutoStart(enabled bool) error {
	if !enabled {
		regDeleteValue(hkeyCurrentUser, autostartKey, autostartValName)
		return nil
	}
	return regSetString(hkeyCurrentUser, autostartKey, autostartValName, `"`+exePath()+`"`)
}

func isAutoStartEnabled() bool {
	v, ok := regGetString(hkeyCurrentUser, autostartKey, autostartValName)
	return ok && len(v) > 0
}

// ---------------------------------------------------------------- 只读注册表访问（HKLM 无需管理员）

func regOpenRO(hive uintptr, sub string) (uintptr, bool) {
	var hKey uintptr
	ret, _, _ := pRegOpenKeyExW.Call(
		hive,
		uintptr(unsafe.Pointer(utf16FromString(sub))),
		0, keyRead,
		uintptr(unsafe.Pointer(&hKey)),
	)
	if ret != 0 {
		return 0, false
	}
	return hKey, true
}

func regKeyExists(hive uintptr, sub string) bool {
	h, ok := regOpenRO(hive, sub)
	if !ok {
		return false
	}
	pRegCloseKey.Call(h)
	return true
}

func regGetStringRO(hive uintptr, sub, name string) (string, bool) {
	h, ok := regOpenRO(hive, sub)
	if !ok {
		return "", false
	}
	defer pRegCloseKey.Call(h)

	buf := make([]uint16, 1024)
	size := uint32(len(buf) * 2)
	ret, _, _ := pRegQueryValueExW.Call(
		h,
		uintptr(unsafe.Pointer(utf16FromString(name))),
		0, 0,
		uintptr(unsafe.Pointer(&buf[0])),
		uintptr(unsafe.Pointer(&size)),
	)
	if ret != 0 {
		return "", false
	}
	buf[len(buf)-1] = 0
	return utf16ToGoStr(buf), true
}

// regEnumStringValues 列出某个键下所有 REG_SZ 值（诊断用）
func regEnumStringValues(hive uintptr, sub string) map[string]string {
	out := map[string]string{}
	h, ok := regOpenRO(hive, sub)
	if !ok {
		return out
	}
	defer pRegCloseKey.Call(h)

	for i := 0; i < 256; i++ {
		name := make([]uint16, 512)
		nameLen := uint32(len(name))
		buf := make([]uint16, 1024)
		size := uint32(len(buf) * 2)
		ret, _, _ := pRegEnumValueW.Call(
			h,
			uintptr(i),
			uintptr(unsafe.Pointer(&name[0])),
			uintptr(unsafe.Pointer(&nameLen)),
			0, 0,
			uintptr(unsafe.Pointer(&buf[0])),
			uintptr(unsafe.Pointer(&size)),
		)
		if ret != 0 {
			break
		}
		out[utf16ToGoStr(name)] = utf16ToGoStr(buf)
	}
	return out
}

// regEnumSubKeys 列出子键名（最多 512 个）
func regEnumSubKeys(hive uintptr, sub string) []string {
	h, ok := regOpenRO(hive, sub)
	if !ok {
		return nil
	}
	defer pRegCloseKey.Call(h)

	var out []string
	for i := 0; i < 512; i++ {
		buf := make([]uint16, 256)
		size := uint32(len(buf))
		ret, _, _ := pRegEnumKeyExW.Call(
			h,
			uintptr(i),
			uintptr(unsafe.Pointer(&buf[0])),
			uintptr(unsafe.Pointer(&size)),
			0, 0, 0, 0,
		)
		if ret != 0 {
			break
		}
		out = append(out, utf16ToGoStr(buf))
	}
	return out
}
