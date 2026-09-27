//go:build windows

package main

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// 真机抓到的 System/FanInfo 报文（GCU 固定每 2 秒推一条）。
// 注意四个字段全是**原生数字**，而 System/CpuInfo 里的 CpuTemperature 偏偏是字符串 "63"，
// 同一个后端两种写法 —— 这正是 gcuNum 必须两种都吃的原因。
const rawFanInfo = `{"CpuFanDuty":55,"GpuFanDuty":55,"CpuFanRpm":2990,"GpuFanRpm":2854}`

// ---------------------------------------------------------------- gcuNum

func TestGcuNumAcceptsStringAndNumber(t *testing.T) {
	// CpuInfo 那条报文里温度是字符串，FanInfo 这条里转速是数字，两条都必须能解析
	var s struct {
		A gcuNum `json:"a"`
		B gcuNum `json:"b"`
	}
	if err := json.Unmarshal([]byte(`{"a":"63","b":2990}`), &s); err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	if s.A != 63 || s.B != 2990 {
		t.Errorf("得到 a=%d b=%d，期望 63 / 2990", s.A, s.B)
	}
}

func TestGcuNumEdgeCases(t *testing.T) {
	for _, c := range []struct {
		in   string
		want gcuNum
	}{
		{`"61"`, 61},
		{`61`, 61},
		{`61.6`, 62}, // 四舍五入，不往下抹零
		{`0`, 0},
		{`null`, 0},
		{`""`, 0},
		{`"abc"`, 0}, // 脏值不能让整条报文解析失败
	} {
		var n gcuNum
		if err := json.Unmarshal([]byte(c.in), &n); err != nil {
			t.Errorf("解析 %s 报错: %v", c.in, err)
			continue
		}
		if n != c.want {
			t.Errorf("解析 %s 得到 %d，期望 %d", c.in, n, c.want)
		}
	}
}

// ---------------------------------------------------------------- System/FanInfo

func TestFanInfoParsesRealPayload(t *testing.T) {
	var g GCU
	g.handleFanInfo([]byte(rawFanInfo))
	if got := g.FanRPM(); got.CPU != 2990 || got.GPU != 2854 {
		t.Errorf("得到 %+v，期望 {CPU:2990 GPU:2854}", got)
	}
}

func TestFanInfoMissingFieldIsUnknown(t *testing.T) {
	// 报文里没有 CpuFanRpm 时必须是「拿不到」，而不是「风扇不转」
	var g GCU
	g.handleFanInfo([]byte(`{"CpuFanDuty":55,"GpuFanDuty":55,"GpuFanRpm":2854}`))
	got := g.FanRPM()
	if got.CPU != unknownValue {
		t.Errorf("缺字段时 CPU = %d，期望 %d", got.CPU, unknownValue)
	}
	if got.GPU != 2854 {
		t.Errorf("另一个字段不该被牵连，GPU = %d，期望 2854", got.GPU)
	}
}

func TestFanInfoNullFieldIsUnknown(t *testing.T) {
	var g GCU
	g.handleFanInfo([]byte(`{"CpuFanRpm":null,"GpuFanRpm":2854}`))
	if got := g.FanRPM(); got.CPU != unknownValue {
		t.Errorf("null 时 CPU = %d，期望 %d", got.CPU, unknownValue)
	}
}

func TestFanInfoZeroIsAValidReading(t *testing.T) {
	// 风扇停转（或刚上电还没转起来）时 0 是合法读数，不能当成缺数据显示 --
	// 否则用户会以为提示坏了
	var g GCU
	g.handleFanInfo([]byte(`{"CpuFanRpm":0,"GpuFanRpm":0}`))
	if got := g.FanRPM(); got.CPU != 0 || got.GPU != 0 {
		t.Errorf("得到 %+v，期望两项都是 0", got)
	}
}

func TestFanInfoRejectsAbsurdRpm(t *testing.T) {
	var g GCU
	g.handleFanInfo([]byte(`{"CpuFanRpm":99999,"GpuFanRpm":-3}`))
	got := g.FanRPM()
	if got.CPU != unknownValue || got.GPU != unknownValue {
		t.Errorf("离群值应判为拿不到，得到 %+v", got)
	}
}

func TestFanInfoBadJSONKeepsPrevious(t *testing.T) {
	// 半条报文不能把上一次的好读数冲掉
	var g GCU
	g.handleFanInfo([]byte(rawFanInfo))
	g.handleFanInfo([]byte(`{"CpuFanRpm":`))
	if got := g.FanRPM(); got.CPU != 2990 || got.GPU != 2854 {
		t.Errorf("坏报文污染了上一份读数：%+v", got)
	}
}

func TestFanInfoUnknownableIsTheInitialValue(t *testing.T) {
	// 新实例（还没连上）就该显示 --，不能是 0 —— 0 是「风扇不转」，语义完全不同
	g := NewGCU(-1, nil)
	if got := g.FanRPM(); got.CPU != unknownValue || got.GPU != unknownValue {
		t.Errorf("初始值 %+v，期望两项都是 %d", got, unknownValue)
	}
}

// ---------------------------------------------------------------- 排版

func TestFanRPMRowsFormat(t *testing.T) {
	// 格式固定为 "CPU 🌀2700RPM"：图标顶着数字，数字**不**补空格
	cases := []struct {
		fan     FanRPM
		wantCPU string
		wantGPU string
	}{
		{FanRPM{CPU: 2990, GPU: 2854}, "CPU 🌀2990RPM", "GPU 🌀2854RPM"},
		{FanRPM{CPU: 933, GPU: 5600}, "CPU 🌀933RPM", "GPU 🌀5600RPM"},
		{FanRPM{CPU: 0, GPU: 0}, "CPU 🌀0RPM", "GPU 🌀0RPM"}, // 停转照原样显示
	}
	for _, c := range cases {
		rows := c.fan.Rows()
		if len(rows) != 2 {
			t.Fatalf("必须正好两行，得到 %d 行", len(rows))
		}
		if rows[0] != c.wantCPU || rows[1] != c.wantGPU {
			t.Errorf("FanRPM%+v 得到 %q / %q，期望 %q / %q",
				c.fan, rows[0], rows[1], c.wantCPU, c.wantGPU)
		}
	}
}

// 转速行里不该再出现空格补位和 " - " 之类的旧样式，也不该出现 °C
func TestFanRPMRowsHaveNoLegacyDecoration(t *testing.T) {
	rows := FanRPM{CPU: 933, GPU: 2990}.Rows()
	for _, row := range rows {
		if strings.Contains(row, " RPM") {
			t.Errorf("RPM 前不该有空格：%q", row)
		}
		if strings.Contains(row, " - ") || strings.Contains(row, "--") {
			t.Errorf("不该再出现分隔符/补位符：%q", row)
		}
		// 数字必须紧贴图标，中间不补空格
		if strings.Contains(row, iconFan+" ") {
			t.Errorf("图标和数字之间不该有空格：%q", row)
		}
	}
}

func TestFanRPMRowsShowPlaceholderWhenUnknown(t *testing.T) {
	rows := unknownFanRPM().Rows()
	for i, row := range rows {
		if !strings.Contains(row, "--") {
			t.Errorf("第 %d 行 %q 应该带 -- 占位", i+1, row)
		}
	}
	if rows[0] != "CPU 🌀--RPM" {
		t.Errorf("CPU 行 = %q，期望 %q", rows[0], "CPU 🌀--RPM")
	}
	if rows[1] != "GPU 🌀--RPM" {
		t.Errorf("GPU 行 = %q，期望 %q", rows[1], "GPU 🌀--RPM")
	}
}

func TestComposeTipHasNoSeparator(t *testing.T) {
	text := composeTip([]string{"CPU 🌡85℃ ⚡38/38/45W", "GPU 🌡87℃ ⚡50W"},
		FanRPM{CPU: 2990, GPU: 2854})
	lines := strings.Split(text, "\n")
	if len(lines) != 4 {
		t.Fatalf("应该正好四行（2 限制 + 2 转速），得到 %d 行：%q", len(lines), text)
	}
	// 第 3 行必须直接是 CPU 转速，中间不插任何分隔线
	if lines[2] != "CPU 🌀2990RPM" {
		t.Errorf("第 3 行 = %q，期望 %q", lines[2], "CPU 🌀2990RPM")
	}
	if lines[3] != "GPU 🌀2854RPM" {
		t.Errorf("第 4 行 = %q，期望 %q", lines[3], "GPU 🌀2854RPM")
	}
	for _, l := range lines {
		if strings.Contains(l, " - ") {
			t.Errorf("不该出现 \" - \" 分隔符：%q", l)
		}
		if strings.HasPrefix(l, "---") || strings.HasPrefix(l, "___") {
			t.Errorf("不该出现整行分隔线：%q", l)
		}
	}
}

// 限制信息还没到、但转速到了 —— 真读数不该被藏起来，这时就只显示两行转速
func TestComposeTipKeepsFanWhenLimitsMissing(t *testing.T) {
	text := composeTip(nil, FanRPM{CPU: 3111, GPU: 2222})
	if text != "CPU 🌀3111RPM\nGPU 🌀2222RPM" {
		t.Errorf("得到 %q", text)
	}
}

// 转速拿不到时那两行**照样**留着（显示 --）：GCU 遥测是间歇性的，把空行藏起来
// 会让用户以为功能不存在
func TestComposeTipKeepsPlaceholderWhenFanUnknown(t *testing.T) {
	text := composeTip([]string{"A", "B"}, unknownFanRPM())
	want := "A\nB\nCPU 🌀--RPM\nGPU 🌀--RPM"
	if text != want {
		t.Errorf("得到 %q，期望 %q", text, want)
	}
}

// 单侧未知仍然要显示 —— 风扇是可以坏一个的，另一侧的真实转速照样值得看
func TestComposeTipKeepsHalfKnownFan(t *testing.T) {
	text := composeTip([]string{"A"}, FanRPM{CPU: unknownValue, GPU: 2854})
	if !strings.Contains(text, "GPU 🌀2854RPM") {
		t.Errorf("单侧有效却被整块丢掉了：%q", text)
	}
	if !strings.Contains(text, "CPU 🌀--RPM") {
		t.Errorf("未知的那一侧该显示 --：%q", text)
	}
}

// composeTip 不能改到调用方传进来的那个切片（limitRows 常常是 Limits.Rows() 的返回值，
// 被就地追加会把 Limits 内部状态写坏）
func TestComposeTipDoesNotMutateInput(t *testing.T) {
	rows := make([]string, 2, 8) // 故意给足容量，让 append 有机会就地写
	rows[0], rows[1] = "A", "B"
	_ = composeTip(rows, FanRPM{CPU: 1000, GPU: 1000})
	if len(rows) != 2 || rows[0] != "A" || rows[1] != "B" {
		t.Errorf("入参被改坏了：%+v", rows)
	}
}

func TestFanRPMIsUnknown(t *testing.T) {
	cases := []struct {
		fan  FanRPM
		want bool
	}{
		{unknownFanRPM(), true},
		{FanRPM{CPU: 2990, GPU: 2854}, false},
		{FanRPM{CPU: 0, GPU: 0}, false},               // 停转也是有效读数
		{FanRPM{CPU: unknownValue, GPU: 2854}, false}, // 单侧有效就算有数据
		{FanRPM{CPU: 2990, GPU: unknownValue}, false},
	}
	for _, c := range cases {
		if got := c.fan.IsUnknown(); got != c.want {
			t.Errorf("%+v.IsUnknown() = %v，期望 %v", c.fan, got, c.want)
		}
	}
}

func TestComposeTipWidestFitsSzTip(t *testing.T) {
	// 最宽的组合：三位温度 + 三位功耗、转速顶到上界 —— 仍然必须小于 szTip 的 128 个码元
	const maxContent = 128 - 1
	rows := []string{
		joinFields("CPU", iconTemp+"105"+unitTemp, iconWatt+"210/210/210W"),
		joinFields("GPU", iconTemp+"105"+unitTemp, iconWatt+"100+15W"),
	}
	text := composeTip(rows, FanRPM{CPU: maxSaneRpm, GPU: maxSaneRpm})
	n := len(utf16Buf(text)) - 1
	if n > maxContent {
		t.Errorf("最宽组合 %d 个码元，超出 %d：%q", n, maxContent, text)
	}
	t.Logf("最宽组合 %d/%d 码元  %q", n, maxContent, text)
}

// emoji 在 UTF-16 里的实际代价。🌡(U+1F321) 和 🌀(U+1F300) 是**星平面**字符，
// 显示上是「一个字」，塞进 szTip 却要吃 2 个码元 —— 按字数估容量会低估一半。
// 这条把代价钉死，免得以后有人换图标时踩坑。
func TestTipEmojiBudget(t *testing.T) {
	cases := []struct {
		s    string
		want int // UTF-16 码元数（不含结尾 NUL）
	}{
		{iconTemp, 2},
		{iconFan, 2},
		{iconWatt, 1}, // ⚡ 在基本平面，只占 1 个
		{unitTemp, 1}, // ℃ 是单码位字符，不是 "°"+"C"
		{"CPU " + iconFan + "2990RPM", 13},
	}
	for _, c := range cases {
		if got := len(utf16Buf(c.s)) - 1; got != c.want {
			t.Errorf("%q 占 %d 个 UTF-16 码元，期望 %d", c.s, got, c.want)
		}
	}
}

// ---------------------------------------------------------------- 遥测唤醒

// 唤醒节流是纯逻辑，必须单独钉死：maybeArmFan 判定通过后会真去启动控制台，
// 一旦条件写错，用户就会在开机后被反复弹窗。
func TestShouldArmFan(t *testing.T) {
	base := time.Date(2026, 9, 28, 0, 0, 0, 0, time.Local)
	cases := []struct {
		name   string
		now    time.Time
		fanAt  time.Time
		armAt  time.Time
		online bool
		want   bool
	}{
		{"没连上不动", base, base.Add(-10 * time.Minute), time.Time{}, false, false},
		{"fanAt 零值=还没开始计时", base, time.Time{}, time.Time{}, true, false},
		{"刚连上不触发", base, base, time.Time{}, true, false},
		{"静默 10 秒还不该动", base, base.Add(-10 * time.Second), time.Time{}, true, false},
		{"静默刚过宽限就触发", base, base.Add(-fanArmGrace - time.Second), time.Time{}, true, true},
		{"静默很久但刚试过→限流", base, base.Add(-10 * time.Minute),
			base.Add(-time.Minute), true, false},
		{"静默很久且超过重试间隔→再试", base, base.Add(-10 * time.Minute),
			base.Add(-fanArmRetry - time.Second), true, true},
	}
	for _, c := range cases {
		if got := shouldArmFan(c.now, c.fanAt, c.armAt, c.online); got != c.want {
			t.Errorf("%s: shouldArmFan = %v, 期望 %v", c.name, got, c.want)
		}
	}
}

// 收到转速必须把静默计时打上，并把限流时钟清掉 ——
// 否则「遥测断了一阵又回来」这类情况会被旧的 armAt 一直卡住。
func TestFanInfoMarksArmedAndClearsThrottle(t *testing.T) {
	g := NewGCU(3, nil)
	g.mu.Lock()
	g.online = true
	g.armAt = time.Now() // 假装刚弹过一次控制台
	g.mu.Unlock()

	g.handleFanInfo([]byte(rawFanInfo))

	g.mu.Lock()
	fanAt, armAt, fan := g.fanAt, g.armAt, g.fan
	g.mu.Unlock()

	if fanAt.IsZero() {
		t.Error("收到 FanInfo 后 fanAt 仍为零值，静默计时没打上")
	}
	if !armAt.IsZero() {
		t.Error("收到 FanInfo 后 armAt 没清空，下次静默会被限流卡住")
	}
	if fan.CPU != 2990 || fan.GPU != 2854 {
		t.Errorf("转速没入库: %+v", fan)
	}
}

// 控制中心的 UWP 包族名不能写死（升级就变），所以从包 ID 折出来。
// 这里的输入是真机上查到的包 ID。
func TestPFNFromPackageID(t *testing.T) {
	cases := []struct {
		id   string
		want string
	}{
		{"CCU.WinUI_5.56.60.34_x64__wrbgcf7aesyd8", "CCU.WinUI_wrbgcf7aesyd8"},
		{"Microsoft.GamingApp_2508.1001.27.0_neutral_split.language-zh-hans_8wekyb3d8bbwe",
			"Microsoft.GamingApp_8wekyb3d8bbwe"},
		{"没有下划线", ""},
		{"", ""},
		{"缺发布者__", ""},
		{"Name_1.0_x64__", ""}, // 发布者哈希为空 → 认不出来，宁可返回空
	}
	for _, c := range cases {
		if got := pfnFromPackageID(c.id); got != c.want {
			t.Errorf("pfnFromPackageID(%q) = %q, 期望 %q", c.id, got, c.want)
		}
	}
}
