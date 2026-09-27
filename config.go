//go:build windows

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"unicode/utf16"
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

// Config 持久化设置，存放在**程序同目录**的 data\config.json（见 dataDir）
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
		// 办公（GCU 模式 0）。名称带 emoji 是为了在托盘菜单/界面上一眼分辨档位。
		{Key: keyOffice, Mode: ModeOffice, Slot: -1, Name: "🍃静音", ShowTray: true,
			IconColor: 0x27AE60, IconGlyph: "E", PowerPlan: planBalanced},
		// 均衡
		{Key: keyBalance, Mode: ModeBalance, Slot: -1, Name: "❄️均衡", ShowTray: true,
			IconColor: 0x2F80ED, IconGlyph: "B", PowerPlan: planBalanced},
		// 狂暴（不再区分子档位，下发时不带 ProfileIndex）
		{Key: keyTurbo, Mode: ModeTurbo, Slot: -1, Name: "🎮狂暴", ShowTray: true,
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
//
// 注意这一列**不要**跟默认名称混为一谈：模式 0 的官方叫法是「办公」，
// 而它的默认显示名称是「🍃静音」。两者都写「静音」的话，用户改了名称之后
// 这一列就完全失去参照作用了。
func (m ModeItem) origLabel() string {
	switch m.Mode {
	case ModeOffice:
		return "办公"
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

const (
	dataSubdirName = "data"         // 程序同目录下的数据目录
	appDirName     = "MechrevoMode" // 回退目录名（%APPDATA%\MechrevoMode，也是旧版本的位置）
)

var (
	dataOnce sync.Once
	dataPath string
	dataWhy  string // 目录是怎么选出来的，启动日志里要报一条
)

// dataDir 数据目录：**程序所在目录下的 data\**（config.json、log.txt、
// 各种自检输出的落点都在这里），这样整个工具是绿色的，拷到哪配置跟到哪。
//
// 程序目录写不进去时（装在 Program Files、运行在只读介质上）退回
// %APPDATA%\MechrevoMode。宁可换地方存，也不能让「存不下来」变成「程序不能用」——
// 而且写不进去是静默的，不兜底的话用户只会看到设置每次重启都还原。
func dataDir() string {
	dataOnce.Do(resolveDataDir)
	return dataPath
}

// dataDirNote 返回目录的选择理由，供启动日志使用
func dataDirNote() string {
	dataOnce.Do(resolveDataDir)
	return dataWhy
}

func resolveDataDir() {
	if exe, err := os.Executable(); err == nil {
		if d := filepath.Join(filepath.Dir(exe), dataSubdirName); dirWritable(d) {
			dataPath, dataWhy = d, "程序同目录"
			return
		}
	}
	base, err := os.UserConfigDir()
	if err != nil || base == "" {
		base = os.Getenv("APPDATA")
	}
	if base == "" {
		base = "." // 连 APPDATA 都没有（极端情况），退回当前工作目录
	}
	dataPath = filepath.Join(base, appDirName)
	dataWhy = "程序目录不可写，回退到 APPDATA"
	_ = os.MkdirAll(dataPath, 0o755)
}

// dirWritable 真的写一个文件试探。只 MkdirAll 判断不出来：目录已存在但没有写权限时
// MkdirAll 同样返回 nil，于是「数据目录不可写」会退化成「配置静默存不下来」。
func dirWritable(dir string) bool {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return false
	}
	f, err := os.CreateTemp(dir, ".probe-*")
	if err != nil {
		return false
	}
	name := f.Name()
	_ = f.Close()
	_ = os.Remove(name)
	return true
}

// legacyConfigDir 旧版本的配置位置。只用于一次性迁移，别再往这里写东西。
func legacyConfigDir() string {
	base, err := os.UserConfigDir()
	if err != nil || base == "" {
		base = os.Getenv("APPDATA")
	}
	if base == "" {
		return ""
	}
	return filepath.Join(base, appDirName)
}

// migrateLegacyConfig 把旧位置 %APPDATA%\MechrevoMode\config.json 搬到新的数据目录。
//
// 不搬的话，升级后用户的模式名称 / 图标字母与底色 / 电源计划 / 开机自启设置会
// 全部回到默认值，而旧配置还好端端躺在 %APPDATA% 里 —— 这种「设置凭空丢了」
// 最难解释。搬完把旧文件改名留底：既保留现场，也免得哪天又拿它把新配置覆盖回去。
func migrateLegacyConfig() {
	dst := configPath()
	if _, err := os.Stat(dst); err == nil {
		return // 新位置已经有配置了，不动
	}
	old := legacyConfigDir()
	if old == "" || old == dataDir() {
		return
	}
	src := filepath.Join(old, "config.json")
	data, err := os.ReadFile(src)
	if err != nil {
		return // 没有旧配置（全新安装），正常路径
	}
	if err := os.WriteFile(dst, data, 0o644); err != nil {
		app.logf("旧配置迁移失败（%s -> %s）: %v", src, dst, err)
		return
	}
	_ = os.Rename(src, src+".migrated")
	app.logf("已迁移旧配置: %s -> %s（旧文件改名为 .migrated 留底）", src, dst)
}

func configPath() string { return filepath.Join(dataDir(), "config.json") }

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
//
// 默认名称改成带 emoji 之后，旧的纯文字名称必须补进 names 里，否则
// 老配置会一直显示「静音/均衡/狂暴」，用户会以为改版没生效。
var legacyDefaults = map[string]struct {
	names  []string
	glyphs []string
	colors []uint32
}{
	keyOffice:  {names: []string{"办公", "静音"}, glyphs: []string{"O"}, colors: []uint32{0x2F80ED}},
	keyBalance: {names: []string{"均衡"}, glyphs: []string{"B"}, colors: []uint32{0x27AE60}},
	keyTurbo: {
		names:  []string{"游戏狂暴", "高能狂暴", "静音狂暴", "狂暴"},
		glyphs: []string{"G", "S", "T"},
		colors: []uint32{0xE2445C, 0xF2994A},
	},
	// 自定义 1-3 早先默认都是紫色，现在改成与办公/均衡/狂暴同色系
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

	// 需要管理员权限时改用「登录时触发 + 最高权限」的计划任务。
	//
	// 原因是 Windows 的设计：登录时不会把 Run 项 / 启动文件夹里的项提权拉起，
	// 这类条目会被静默跳过（没有报错、没有事件、任务管理器里仍显示为「已启用」）。
	// 因此「登录后要跑一个需要管理员权限的常驻程序」只能用计划任务的
	// RunLevel=HighestAvailable 表达，这也是官方的做法。
	autostartTaskName  = "MechrevoMode"
	autostartTaskDelay = "0000:10" // 登录后延迟 10 秒，避开登录阶段的磁盘/服务高峰

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

// autostartMechanism 记录上一次 applyAutoStart 实际采用的机制。
//
// 需要提权时会改用计划任务，这时「任务管理器 → 启动」里看不到本程序，
// 用户容易以为没设上。所以在界面的保存反馈里把机制讲明。
var autostartMechanism string

const (
	autostartViaTask = "计划任务（免 UAC）"
	autostartViaRun  = "注册表启动项"
)

func applyAutoStart(enabled bool) error {
	if !enabled {
		regDeleteValue(hkeyCurrentUser, autostartKey, autostartValName)
		removeAutostartTask()
		autostartMechanism = ""
		return nil
	}

	exe := exePath()
	if exe == "" {
		return fmt.Errorf("拿不到自身程序路径")
	}

	if isRunAsAdminSet() {
		// Run 项在登录时无法提权，改用计划任务
		if err := createAutostartTask(exe); err != nil {
			// 退路：至少把 Run 项留着。登录时会弹一次 UAC，用户点「是」也能起来，
			// 总比什么都不做、用户以为自启开了却毫无反应要好。
			_ = regSetString(hkeyCurrentUser, autostartKey, autostartValName, `"`+exe+`"`)
			autostartMechanism = autostartViaRun
			return fmt.Errorf("建开机自启计划任务失败，已退回注册表 Run 项（登录时可能需手动放行 UAC）：%w", err)
		}
		// 计划任务已能覆盖，删掉 Run 项，否则登录时会启动两个实例
		regDeleteValue(hkeyCurrentUser, autostartKey, autostartValName)
		autostartMechanism = autostartViaTask
		return nil
	}

	// 不需要提权：用 Run 项，用户能在「任务管理器 → 启动」里看到并自行管理
	removeAutostartTask()
	if err := regSetString(hkeyCurrentUser, autostartKey, autostartValName, `"`+exe+`"`); err != nil {
		return err
	}
	autostartMechanism = autostartViaRun
	return nil
}

// isAutoStartEnabled 注册表 Run 项和计划任务任一存在，都算已启用
func isAutoStartEnabled() bool {
	if v, ok := regGetString(hkeyCurrentUser, autostartKey, autostartValName); ok && len(v) > 0 {
		return true
	}
	return autostartTaskExe() != ""
}

// ---------------------------------------------------------------- 开机自启：计划任务

var (
	autostartMu       sync.Mutex
	autostartTaskPath string // 已查询到的任务程序路径
	autostartQueried  bool   // 是否已经查过
)

// runSchtasks 调 schtasks.exe，带上 CREATE_NO_WINDOW 以免 GUI 程序突然闪一个黑框
func runSchtasks(args ...string) (string, error) {
	cmd := exec.Command("schtasks.exe", args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func queryAutostartTaskExe() string {
	out, err := runSchtasks("/Query", "/TN", autostartTaskName, "/XML")
	if err != nil {
		return ""
	}
	const open, close = "<Command>", "</Command>"
	i := strings.Index(out, open)
	if i < 0 {
		return ""
	}
	rest := out[i+len(open):]
	j := strings.Index(rest, close)
	if j < 0 {
		return ""
	}
	return strings.TrimSpace(rest[:j])
}

// autostartTaskExe 返回计划任务里登记的程序路径；任务不存在返回 ""。
// 结果按进程缓存 —— 每次启动都去 schtasks 问一遍没必要，多花一百毫秒。
func autostartTaskExe() string {
	autostartMu.Lock()
	defer autostartMu.Unlock()
	if !autostartQueried {
		autostartTaskPath = queryAutostartTaskExe()
		autostartQueried = true
	}
	return autostartTaskPath
}

func invalidateAutostartTask() {
	autostartMu.Lock()
	autostartQueried = false
	autostartTaskPath = ""
	autostartMu.Unlock()
}

func removeAutostartTask() {
	if autostartTaskExe() == "" {
		return // 本来就没有，不必去调 schtasks
	}
	_, _ = runSchtasks("/Delete", "/F", "/TN", autostartTaskName)
	invalidateAutostartTask()
}

// createAutostartTask 注册「登录时触发 + 最高权限」的计划任务。
//
// 已经注册且路径没变时直接返回：既省一次注册，也避免在任务正在运行时重写它。
func createAutostartTask(exe string) error {
	if cur := autostartTaskExe(); cur == exe {
		return nil
	}

	xml := autostartTaskXML(exe)
	tmp := filepath.Join(dataDir(), "autostart_task.xml")
	// schtasks 认 UTF-16LE + BOM 的 XML（这也是任务计划程序自己导出的格式）
	if err := os.WriteFile(tmp, utf16LEWithBOM(xml), 0o644); err != nil {
		return fmt.Errorf("写任务定义失败: %w", err)
	}
	defer os.Remove(tmp)

	// 用 /XML 而不是命令行拼 /TR：路径带空格时 /TR 的引号嵌套极易出错，
	// 而且 XML 里才能写「无执行时限」——默认 72 小时上限会把常驻托盘程序杀掉。
	if out, err := runSchtasks("/Create", "/F", "/TN", autostartTaskName, "/XML", tmp); err != nil {
		return fmt.Errorf("schtasks /Create 返回: %v (%s)", err, out)
	}

	invalidateAutostartTask()
	if got := autostartTaskExe(); got != exe {
		return fmt.Errorf("任务写入后回读路径不一致：期望 %q，实际 %q", exe, got)
	}
	return nil
}

func autostartTaskXML(exe string) string {
	user := os.Getenv("USERNAME")
	if d := os.Getenv("USERDOMAIN"); d != "" && user != "" {
		user = d + `\` + user
	}
	esc := func(s string) string {
		return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(s)
	}
	// 登录后延迟 + 失败重试，是为了兜住「登录瞬间磁盘/OneDrive 还没就绪」这类偶发情况
	delay := strings.TrimSpace(autostartTaskDelay)
	delaySec := 10
	if len(delay) == 7 { // "0000:10"
		if n, err := strconv.Atoi(delay[5:]); err == nil {
			delaySec = n
		}
	}

	return `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>机械革命模式：登录后自动启动托盘工具。因为需要管理员权限，所以用计划任务而不是注册表 Run 项（登录时不会提权拉起 Run 项）。</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>` + esc(user) + `</UserId>
      <Delay>PT` + strconv.Itoa(delaySec) + `S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>` + esc(user) + `</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>` + esc(exe) + `</Command>
      <WorkingDirectory>` + esc(filepath.Dir(exe)) + `</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
`
}

// utf16LEWithBOM schtasks 读 XML 时按声明走 UTF-16，这里给足 BOM。
func utf16LEWithBOM(s string) []byte {
	u := utf16.Encode([]rune(s))
	b := make([]byte, 0, len(u)*2+2)
	b = append(b, 0xFF, 0xFE)
	for _, v := range u {
		b = append(b, byte(v), byte(v>>8))
	}
	return b
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

// regEnumSubKeysRO 列出某个键下的所有子键名（只读）。
//
// 用途：动态找出官方控制中心的 UWP 包族名。厂商自带的 ControlCenterU.exe
// 把包名写死成了 ControlCenter3_h329z55cwnj8g，控制中心升级后那个启动器
// 就再也拉不起界面了（实测：跑它什么都不会发生）。这里改成从注册表查。
func regEnumSubKeysRO(hive uintptr, sub string) []string {
	h, ok := regOpenRO(hive, sub)
	if !ok {
		return nil
	}
	defer pRegCloseKey.Call(h)

	var out []string
	for i := 0; i < 4096; i++ {
		buf := make([]uint16, 512)
		n := uint32(len(buf))
		ret, _, _ := pRegEnumKeyExW.Call(
			h,
			uintptr(i),
			uintptr(unsafe.Pointer(&buf[0])),
			uintptr(unsafe.Pointer(&n)),
			0, 0, 0, 0,
		)
		if ret != 0 { // ERROR_NO_MORE_ITEMS = 259，以及其它错误都收工
			break
		}
		buf[len(buf)-1] = 0
		out = append(out, utf16ToGoStr(buf))
	}
	return out
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
