//go:build windows

package main

import (
	"os"
	"path/filepath"
	"testing"
)

// dirWritable 是「数据目录能不能用」的唯一判据。它必须真去写一个文件 ——
// 只 MkdirAll 判断不出来：目录已存在但没有写权限时 MkdirAll 一样返回 nil，
// 于是「程序目录不可写」会退化成「配置每次重启都还原」，而且全程没有任何报错。
func TestDirWritable(t *testing.T) {
	if !dirWritable(filepath.Join(t.TempDir(), "data")) {
		t.Error("临时目录下新建 data 目录应当可写")
	}

	// 拿一个**文件**当父路径：MkdirAll 必然失败，必须判成不可写。
	// 漏掉这种情形的话，程序会把「数据目录」指向一个根本不是目录的路径。
	f := filepath.Join(t.TempDir(), "afile")
	if err := os.WriteFile(f, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if dirWritable(filepath.Join(f, "data")) {
		t.Error("父路径是普通文件时应判为不可写")
	}

	// 已存在且可写的目录也要判成可写（不能因为「已经存在」就返回 false）
	dir := t.TempDir()
	if !dirWritable(dir) {
		t.Error("已存在的可写目录应判为可写")
	}
	// 探针文件不能留在目录里
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Errorf("可写性探测留下了残留文件: %v", entries)
	}
}

// 默认名称带 emoji 之后，老配置里那三个纯文字名称必须能在加载时被升级。
// 漏掉的话用户会看到「静音/均衡/狂暴」原样不动，以为改版根本没生效。
// 反过来，用户自己改过的名称一个字都不能动。
func TestLegacyDefaultNamesUpgraded(t *testing.T) {
	out := repairItems([]ModeItem{
		{Key: keyOffice, Name: "静音"},
		{Key: keyBalance, Name: "均衡"},
		{Key: keyTurbo, Name: "狂暴"},
	})
	byKey := make(map[string]string, len(out))
	for _, it := range out {
		byKey[it.Key] = it.Name
	}
	for key, want := range map[string]string{
		keyOffice:  "🍃静音",
		keyBalance: "❄️均衡",
		keyTurbo:   "🎮狂暴",
	} {
		if got := byKey[key]; got != want {
			t.Errorf("%s 的旧默认名称没有被升级：得到 %q，期望 %q", key, got, want)
		}
	}

	// 用户改过的名称必须原样保留
	out = repairItems([]ModeItem{{Key: keyOffice, Name: "我的省电档"}})
	for _, it := range out {
		if it.Key == keyOffice && it.Name != "我的省电档" {
			t.Errorf("用户自定义的名称被覆盖了：%q", it.Name)
		}
	}
}

// 「原始模式」列显示的是硬件档位，不是默认名称。
// 两者都写「静音」的话，用户一旦改了名称，这一列就失去参照作用了。
func TestOrigLabel(t *testing.T) {
	cases := []struct {
		item ModeItem
		want string
	}{
		{ModeItem{Mode: ModeOffice}, "办公"},
		{ModeItem{Mode: ModeBalance}, "均衡"},
		{ModeItem{Mode: ModeTurbo}, "狂暴"},
		{ModeItem{Mode: ModeCustom, Slot: 2}, "自定义 · 档位 3"},
	}
	for _, c := range cases {
		if got := c.item.origLabel(); got != c.want {
			t.Errorf("mode=%d slot=%d：origLabel = %q，期望 %q",
				c.item.Mode, c.item.Slot, got, c.want)
		}
	}
	// 默认名称与原始标识必须是两个不同的说法，否则这一列白留
	for _, it := range defaultItems() {
		if it.Name == it.origLabel() {
			t.Errorf("%s：默认名称和原始模式都叫 %q，改完名字后这一列就没参照了",
				it.Key, it.Name)
		}
	}
}

// 启动命令的「触发时机」是后来加的字段，老配置里一个都没有。
// 照字面读出来的话三个都是 false，升级后原本会跑的命令突然不跑了 ——
// 而用户根本不知道多了这么一排开关，只会觉得「新版把我的降压搞坏了」。
func TestRunTriggerMigration(t *testing.T) {
	// v6 及更早：归一到「程序启动时」，也就是它们在旧版本里的唯一行为
	old := &Config{RunEnabled: true, RunPath: `C:\tools\ryzenadj.exe`}
	migrateRunTriggers(configVersion-1, old)
	if !old.RunOnLaunch {
		t.Error("旧配置没有被归一到「程序启动时」，升级后启动命令会静默失效")
	}
	if old.RunOnResume || old.RunOnUnlock {
		t.Error("迁移不该替用户打开新开关：只还原旧行为，不多给")
	}

	// v7 起以文件为准：用户可以主动把三个都关掉，不能被迁移逻辑再填回来
	cur := &Config{RunEnabled: true, RunOnResume: true}
	migrateRunTriggers(configVersion, cur)
	if cur.RunOnLaunch {
		t.Error("当前版本的配置被迁移逻辑改写了，用户取消勾选会被覆盖")
	}
	if !cur.RunOnResume {
		t.Error("当前版本配置应当以文件为准")
	}
}

// 迁移逻辑依赖 legacyConfigDir 指的确实是旧位置（%APPDATA%\MechrevoMode）。
// 它要是和新位置重合，migrateLegacyConfig 就会自己搬给自己。
func TestLegacyConfigDir(t *testing.T) {
	d := legacyConfigDir()
	if d == "" {
		t.Skip("本机没有 APPDATA，跳过")
	}
	if filepath.Base(d) != appDirName {
		t.Errorf("legacyConfigDir = %q，末级目录应为 %q", d, appDirName)
	}
	if d == dataDir() {
		t.Errorf("旧位置和新数据目录重合了（%q），迁移会搬到自己头上", d)
	}
	t.Logf("旧配置位置 = %s；当前数据目录 = %s", d, dataDir())
}
