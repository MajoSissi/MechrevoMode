//go:build windows

package main

import (
	"encoding/json"
	"strings"
	"testing"
)

// 用**真机抓到的** Fan/Status 报文验证 formatLimits，而不是自己编的输入。
// 报文来源：_sniff_full.txt（自定义模式）与 _limits_probe.py（各模式对比）

// 自定义模式 Profile1：注意 DynamicBoostSwitch=0 而 DynamicBoost=5 —— 开关没开，不能加
const rawCustom0 = `{"IsAC":true,"OperatingMode":"3","CustomProfileIndex":"0","ProfileName":"Mode4_Profile1",` +
	`"CPU_PL1":"75","CPU_PL2":"85","CPU_PL4":"85","CPU_AmdSPL":"38","CPU_AmdSPPT":"38","CPU_AmdFPPT":"45",` +
	`"CPU_AmdTccTarget":"85","CPU_TccOffsetSwitch":"1","TjMax":"95","CPU_TccOffset":"95","GPU_TargetTemperature":"87",` +
	`"GPU_ConfigurableTGPTarget":"50","GPU_ConfigurableTGPSwitch":"1","GPU_DynamicBoost":"5",` +
	`"GPU_DynamicBoostSwitch":"0","IsAMDPlatform":true,"IsNvGpu":true}`

// 系统自带狂暴模式（mode=2）：三档同为 210W；boost 开关未开
// CPU 侧：TccOffsetSwitch=0 且 AmdTccTarget=7（脏值）→ 温度墙应回退到 TjMax=95
const rawTurbo = `{"OperatingMode":"2","ProfileName":"Mode3_Profile1",` +
	`"CPU_PL1":"152","CPU_PL2":"152","CPU_PL4":"210","CPU_AmdSPL":"210","CPU_AmdSPPT":"210","CPU_AmdFPPT":"210",` +
	`"TjMax":"95","CPU_AmdTccTarget":"7","CPU_TccOffsetSwitch":"0","GPU_TargetTemperature":"87",` +
	`"GPU_ConfigurableTGPTarget":"150","GPU_DynamicBoost":"25","GPU_DynamicBoostSwitch":"0"}`

// 极端情况：开关**打开**但目标温度是脏值（7）—— 合理区间校验必须把它拦下来
const rawDirtyTemp = `{"OperatingMode":"2","CPU_AmdSPL":"80","CPU_AmdSPPT":"80","CPU_AmdFPPT":"95",` +
	`"TjMax":"95","CPU_AmdTccTarget":"7","CPU_TccOffsetSwitch":"1","GPU_TargetTemperature":"87",` +
	`"GPU_ConfigurableTGPTarget":"100","GPU_DynamicBoost":"0","GPU_DynamicBoostSwitch":"0"}`

// Dynamic Boost **已开启**的情况：应当把附加功耗算进总数
const rawBoostOn = `{"OperatingMode":"2","CPU_AmdSPL":"80","CPU_AmdSPPT":"80","CPU_AmdFPPT":"95","TjMax":"95",` +
	`"GPU_TargetTemperature":"87","GPU_ConfigurableTGPTarget":"100","GPU_DynamicBoost":"15",` +
	`"GPU_DynamicBoostSwitch":"1"}`

// 开关写成 JSON 布尔值：本机报文里 IsAC / OcSupport 就是这种写法，
// 厂商若把 DynamicBoostSwitch 也改过来，绝不能让整条报文解析失败
const rawBoostBool = `{"OperatingMode":"2","CPU_AmdSPL":"80","CPU_AmdSPPT":"80","CPU_AmdFPPT":"95","TjMax":"95",` +
	`"GPU_TargetTemperature":"87","GPU_ConfigurableTGPTarget":"100","GPU_DynamicBoost":"15",` +
	`"GPU_DynamicBoostSwitch":true}`

// 开关缺失：必须当成「关」，不能因为缺字段就凭空加上 boost
const rawBoostMissing = `{"OperatingMode":"2","CPU_AmdSPL":"80","CPU_AmdSPPT":"80","CPU_AmdFPPT":"95","TjMax":"95",` +
	`"GPU_TargetTemperature":"87","GPU_ConfigurableTGPTarget":"100","GPU_DynamicBoost":"15"}`

// 非 AMD 平台：Amd* 缺失，应回退到 PL1/PL2/PL4 三个档位
const rawIntel = `{"OperatingMode":"1","CPU_PL1":"45","CPU_PL2":"65","CPU_PL4":"80","TjMax":"100","CPU_TccOffsetSwitch":"0",` +
	`"GPU_TargetTemperature":"86","GPU_ConfigurableTGPTarget":"60","GPU_DynamicBoost":"0",` +
	`"GPU_DynamicBoostSwitch":"0"}`

// 只有温度、没有功耗字段：不得出现半截的功耗行
const rawTempOnly = `{"OperatingMode":"1","TjMax":"95","CPU_AmdTccTarget":"85","CPU_TccOffsetSwitch":"1","GPU_TargetTemperature":"87"}`

// 极限情况：什么都拿不到，两行都应为空
const rawEmpty = `{"OperatingMode":"1","ProfileName":"Mode1_Profile1"}`

func limitsOf(t *testing.T, raw string) Limits {
	t.Helper()
	var p fanStatusPayload
	if err := json.Unmarshal([]byte(raw), &p); err != nil {
		t.Fatalf("JSON 解析失败: %v", err)
	}
	return formatLimits(p)
}

func TestFormatLimits(t *testing.T) {
	cases := []struct {
		name string
		raw  string
		want []string // Rows() 的期望结果，顺序敏感
	}{
		{
			// 本机 IsAMDPlatform=true；boost 开关 = 0，所以是 50W 而不是 50+5W
			"自定义0", rawCustom0,
			[]string{"CPU 🌡85℃ ⚡38/38/45W", "GPU 🌡87℃ ⚡50W"},
		},
		{
			// 三档数值相同也照原样列出，不擅自合并（用户要的就是三个档位）
			"系统狂暴", rawTurbo,
			[]string{"CPU 🌡95℃ ⚡210/210/210W", "GPU 🌡87℃ ⚡150W"},
		},
		{
			// 开关开了但目标温度是脏值，必须被区间校验拦下
			"目标温度为脏值", rawDirtyTemp,
			[]string{"CPU 🌡95℃ ⚡80/80/95W", "GPU 🌡87℃ ⚡100W"},
		},
		{
			"DynamicBoost已开", rawBoostOn,
			[]string{"CPU 🌡95℃ ⚡80/80/95W", "GPU 🌡87℃ ⚡100+15W"},
		},
		{
			// 布尔写法必须和字符串写法得到同样的结果
			"开关为布尔值", rawBoostBool,
			[]string{"CPU 🌡95℃ ⚡80/80/95W", "GPU 🌡87℃ ⚡100+15W"},
		},
		{
			// 字段缺失不能当成「开」
			"开关字段缺失", rawBoostMissing,
			[]string{"CPU 🌡95℃ ⚡80/80/95W", "GPU 🌡87℃ ⚡100W"},
		},
		{
			"非AMD回退", rawIntel,
			[]string{"CPU 🌡100℃ ⚡45/65/80W", "GPU 🌡86℃ ⚡60W"},
		},
		{
			// 拿不到功耗就只剩温度，且**不能留下孤零零的 ⚡**
			"仅有温度", rawTempOnly,
			[]string{"CPU 🌡85℃", "GPU 🌡87℃"},
		},
		{"全空", rawEmpty, nil},
	}
	for _, c := range cases {
		got := limitsOf(t, c.raw).Rows()
		if len(got) != len(c.want) {
			t.Errorf("%s: 行数 %d 期望 %d\n  得到 %q\n  期望 %q",
				c.name, len(got), len(c.want), got, c.want)
			continue
		}
		for i := range got {
			if got[i] != c.want[i] {
				t.Errorf("%s: 第 %d 行得到 %q 期望 %q", c.name, i+1, got[i], c.want[i])
			}
		}
		t.Logf("%s OK  %q", c.name, got)
	}
}

func TestLimitsHas(t *testing.T) {
	if (Limits{}).Has() {
		t.Error("空 Limits 的 Has() 应为 false")
	}
	if !(Limits{GPU: "GPU 🌡87℃ ⚡50W"}).Has() {
		t.Error("只剩一行也应 Has()=true")
	}
}

// 单侧缺项时不能留下孤零零的图标（"CPU 🌡85℃ ⚡" 这种）。
// 注意每一项都**自带**图标，所以 joinFields 只负责用空格拼，不再插分隔符。
func TestJoinFields(t *testing.T) {
	cases := []struct {
		label  string
		fields []string
		want   string
	}{
		{"CPU", []string{"🌡85℃", "⚡38/38/45W"}, "CPU 🌡85℃ ⚡38/38/45W"},
		{"GPU", []string{"🌡87℃", "⚡50W"}, "GPU 🌡87℃ ⚡50W"},
		{"CPU", []string{"🌡85℃", ""}, "CPU 🌡85℃"},   // 没有功耗，不留尾随空格/图标
		{"GPU", []string{"", "⚡150W"}, "GPU ⚡150W"}, // 没有温度
		{"CPU", []string{"", ""}, ""},               // 整行空 → Rows 会跳过
		{"GPU", nil, ""},
	}
	for _, c := range cases {
		if got := joinFields(c.label, c.fields...); got != c.want {
			t.Errorf("joinFields(%q, %q) = %q, 期望 %q", c.label, c.fields, got, c.want)
		}
	}
}

// 提示里不该再出现 " - " 这种字段分隔符（档位占位符 "45/-/80W" 是另一回事）
func TestTipHasNoDashSeparator(t *testing.T) {
	for _, raw := range []string{rawCustom0, rawTurbo, rawIntel, rawBoostOn, rawTempOnly} {
		for _, row := range limitsOf(t, raw).Rows() {
			if strings.Contains(row, " - ") {
				t.Errorf("行里还有 \" - \" 分隔符：%q", row)
			}
			if strings.Contains(row, "°C") {
				t.Errorf("温度单位应该用 U+2103 的 ℃ 单字符，实际用到了 \"°C\"：%q", row)
			}
		}
	}
}

func TestJoinWatts(t *testing.T) {
	cases := []struct {
		a, b, c int
		want    string
	}{
		{0, 0, 0, ""},
		{38, 38, 45, "38/38/45W"},
		{210, 210, 210, "210/210/210W"},
		{45, 0, 80, "45/-/80W"}, // 缺档位要留占位符，不能把后面两个往前挪
		{-1, -1, -1, ""},
	}
	for _, cs := range cases {
		if got := joinWatts(cs.a, cs.b, cs.c); got != cs.want {
			t.Errorf("joinWatts(%d,%d,%d) = %q, 期望 %q", cs.a, cs.b, cs.c, got, cs.want)
		}
	}
}

// TestTooltipFitsSzTip 托盘提示的容器是 NOTIFYICONDATA.szTip[128]，超长会被静默截断，
// 所以限制信息放进提示之后必须确认拼起来仍然放得下
// （这也是不在提示里再拼模式名的原因）。
//
// emoji 让这条更要紧：🌡(U+1F321) 是**星平面**字符，在 UTF-16 里占 2 个码元，
// 比看上去贵一倍。
//
// 这里只盯真机报文：四份原始 Fan/Status 都放得下才算数。
// 「最宽可能值」那组在 telemetry_test.go 的 TestComposeTipWidestFitsSzTip。
func TestTooltipFitsSzTip(t *testing.T) {
	const maxContent = 128 - 1 // 留一个结尾 NUL
	for _, c := range []struct{ name, raw string }{
		{"自定义0", rawCustom0},
		{"系统狂暴", rawTurbo},
		{"非AMD", rawIntel},
		{"DynamicBoost已开", rawBoostOn},
	} {
		text := composeTip(limitsOf(t, c.raw).Rows())
		n := len(utf16Buf(text)) - 1
		if n > maxContent {
			t.Errorf("%s: 提示文本 %d 个码元，超出 szTip 容量 %d：%q", c.name, n, maxContent, text)
			continue
		}
		t.Logf("%s 提示 %d 码元 OK  %q", c.name, n, text)
	}
}

// switchOn 必须与 Dynamic Boost 的显示逻辑保持一致：
// 报文里只有 "0"/"1"，且字段缺失时不能当成「开」
func TestSwitchOn(t *testing.T) {
	for _, cs := range []struct {
		in   string
		want bool
	}{{"1", true}, {"0", false}, {"", false}, {"abc", false}, {"2", true}} {
		if got := switchOn(cs.in); got != cs.want {
			t.Errorf("switchOn(%q) = %v, 期望 %v", cs.in, got, cs.want)
		}
	}
}
