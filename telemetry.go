//go:build windows

package main

import (
	"strconv"
	"strings"
)

// ---------------------------------------------------------------- 托盘实时读数
//
// 悬浮提示里**只**显示两个风扇的转速，别的什么温度、功耗都不要。
//
// 这不是偷懒：GCU 的 MQTT 侧压根没有把整机功耗发出来（挖遍全部 42 个主题常量，
// 一个功耗字段都没有），芯片温度虽然在 System/CpuInfo、System/GpuInfo 里，但
// 用户明确表示不显示。所以这里只留风扇转速这一条链路，代码也就能短到一屏看得完。

const (
	// unknownValue 表示「这一刻没拿到读数」，显示成 --。
	unknownValue = -1

	// 转速的合理性上界。笔记本风扇实测范围 2800~5600，不可能碰到四位数上限；
	// 超过它的只可能是脏数据或者字段串位。
	maxSaneRpm = 9999
)

// 提示里每类读数前面挂的图标。集中定义，改样式时不用在两个文件之间来回找。
//
// 两个是**星平面**字符（UTF-16 里占两个码元，见 TestTipEmojiBudget），
// 所以往 szTip 里塞之前先量一下长度，别等 Shell 静默截断。
const (
	iconTemp = "🌡" // U+1F321 THERMOMETER
	iconWatt = "⚡" // U+26A1  HIGH VOLTAGE（基本平面，只占 1 个码元）
	iconFan  = "🌀" // U+1F300 CYCLONE

	// 温度单位用 U+2103 这一个码位，而不是 "°" + "C" 两个字符 ——
	// 后者在点阵/等宽字体里会被拆成两个不同宽度的字形，看着是歪的。
	unitTemp = "℃"
)

// FanRPM 一次采样到的两个风扇转速
type FanRPM struct {
	CPU int
	GPU int
}

// unknownFanRPM 表示「还没拿到」或「刚断线恢复」的转速：两项都是 unknownValue
func unknownFanRPM() FanRPM {
	return FanRPM{CPU: unknownValue, GPU: unknownValue}
}

// IsUnknown 两侧都没拿到读数。用来判断「这两行到底有没有必要显示」——
// 全是 -- 的两行只是在浪费提示窗口的空间。
//
// 单侧未知仍然算「有数据」：风扇是可以坏一个的，另一侧真实转速照样值得看。
func (f FanRPM) IsUnknown() bool {
	return f.CPU == unknownValue && f.GPU == unknownValue
}

// Rows 按显示顺序返回两行：
//
//	CPU 🌀2700RPM
//	GPU 🌀2598RPM
//
// 图标顶着数字，两行天然对齐，所以数字**不**补空格 —— 补了反而会在
// 「🌀2700RPM」和「🌀 933RPM」之间错位。
//
// 永远返回两行：拿不到就读作 --，这样排版是稳定的，用户也能一眼区分
// 「没数据显示」和「提示坏了」。
func (f FanRPM) Rows() []string {
	return []string{
		"CPU " + iconFan + rpmText(f.CPU) + "RPM",
		"GPU " + iconFan + rpmText(f.GPU) + "RPM",
	}
}

// rpmText 转速的文本形式；unknownValue 显示成 --。
func rpmText(v int) string {
	if v == unknownValue {
		return "--"
	}
	return strconv.Itoa(v)
}

// composeTip 把「限制两行」和「风扇两行」拼成最终的提示文本：
//
//	CPU 🌡85℃ ⚡38/38/45W
//	GPU 🌡87℃ ⚡50W
//	CPU 🌀2700RPM
//	GPU 🌀2598RPM
//
// 中间**不**加分隔线 —— 四行本来就是同一组读数，图标已经足够把每一项分开了。
// 单独拆成函数是为了能脱离 App 直接测（尺寸上限、占位符都在这里定死）。
//
// 转速拿不到时**照样**留两行 --：GCU 的遥测是间歇性的，把空行藏起来会让用户
// 以为这个功能不存在；留一行 -- 才看得出「在等数据」而不是「没做」。
// 「两边都没东西」的情况由 App.tooltip 先拦掉，不会走到这里。
func composeTip(limitRows []string, fan FanRPM) string {
	rows := make([]string, 0, len(limitRows)+2)
	rows = append(rows, limitRows...)
	rows = append(rows, fan.Rows()...)
	return strings.Join(rows, "\n")
}
