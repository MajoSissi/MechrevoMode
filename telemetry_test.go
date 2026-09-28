//go:build windows

package main

import (
	"encoding/json"
	"strings"
	"testing"
)

// ---------------------------------------------------------------- gcuNum

func TestGcuNumAcceptsStringAndNumber(t *testing.T) {
	// GCU 同一后端两种写法：System/CpuInfo 里温度是字符串 "63"，
	// 而别的报文里同样的量是原生数字。两条都必须能解析。
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

// ---------------------------------------------------------------- 排版

func TestComposeTipJoinsRowsInOrder(t *testing.T) {
	text := composeTip([]string{"CPU 🌡85℃ ⚡38/38/45W", "GPU 🌡87℃ ⚡50W"})
	want := "CPU 🌡85℃ ⚡38/38/45W\nGPU 🌡87℃ ⚡50W"
	if text != want {
		t.Errorf("得到 %q，期望 %q", text, want)
	}
	for _, l := range strings.Split(text, "\n") {
		if strings.Contains(l, " - ") {
			t.Errorf("不该出现 \" - \" 分隔符：%q", l)
		}
		if strings.HasPrefix(l, "---") || strings.HasPrefix(l, "___") {
			t.Errorf("不该出现整行分隔线：%q", l)
		}
	}
}

func TestComposeTipWidestFitsSzTip(t *testing.T) {
	// 最宽的组合：三位温度 + 三位功耗 —— 仍然必须小于 szTip 的 128 个码元
	const maxContent = 128 - 1
	rows := []string{
		joinFields("CPU", iconTemp+"105"+unitTemp, iconWatt+"210/210/210W"),
		joinFields("GPU", iconTemp+"105"+unitTemp, iconWatt+"100+15W"),
	}
	text := composeTip(rows)
	n := len(utf16Buf(text)) - 1
	if n > maxContent {
		t.Errorf("最宽组合 %d 个码元，超出 %d：%q", n, maxContent, text)
	}
	t.Logf("最宽组合 %d/%d 码元  %q", n, maxContent, text)
}

// emoji 在 UTF-16 里的实际代价。🌡(U+1F321) 是**星平面**字符，显示上是「一个字」，
// 塞进 szTip 却要吃 2 个码元 —— 按字数估容量会低估一半。这条把代价钉死，
// 免得以后有人换图标时踩坑。
func TestTipEmojiBudget(t *testing.T) {
	cases := []struct {
		s    string
		want int // UTF-16 码元数（不含结尾 NUL）
	}{
		{iconTemp, 2},
		{iconWatt, 1}, // ⚡ 在基本平面，只占 1 个
		{unitTemp, 1}, // ℃ 是单码位字符，不是 "°"+"C"
	}
	for _, c := range cases {
		if got := len(utf16Buf(c.s)) - 1; got != c.want {
			t.Errorf("%q 占 %d 个 UTF-16 码元，期望 %d", c.s, got, c.want)
		}
	}
}
