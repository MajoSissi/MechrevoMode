//go:build windows

package main

import "testing"

// 启动命令区块每加一行，uiRunH 必须跟着长，否则最后一行会压到分组框边框上。
// 这个坑踩过：原先三行的下沿是 runTop+100 而分组框只有 96 高，「参数」输入框
// 骑在下边框上，看着像界面坏了。这里把两者钉在一起。
func TestRunSectionFitsGroupBox(t *testing.T) {
	L := layoutFor(len(defaultItems()))

	// createRunSection 的行布局：第一行顶端 runTop+22，行距 30，控件高 uiRunRow
	first := L.runTop + 22
	lastBottom := first + (uiRunRows-1)*30 + uiRunRow

	if boxBottom := L.runTop + uiRunH; lastBottom > boxBottom {
		t.Errorf("启动命令区块最后一行下沿 %d 超出分组框下沿 %d（uiRunH=%d 该加 %d 了）",
			lastBottom, boxBottom, uiRunH, lastBottom-boxBottom)
	}
	// 分组框下面还挂着状态栏和按钮，同样不能被追尾
	if lastBottom > L.statusY {
		t.Errorf("启动命令区块下沿 %d 压到了状态栏 %d", lastBottom, L.statusY)
	}

	// 区块内的每一行都得有地方站
	if uiRunRow > 30 {
		t.Errorf("uiRunRow=%d 超过行距 30，相邻两行会叠在一起", uiRunRow)
	}
}

// 「立即运行」和延迟输入框所在的那两条横排，右边不能越过分组框的可用右边界。
// 控件坐标散在 createRunSection 里是字面量，这里至少把右边界钉住。
func TestRunSectionStaysInsideColumn(t *testing.T) {
	const (
		runNowRight = uiInnerR // 「立即运行」贴右边界
		unlockRight = 430      // 「解锁屏幕后」的右边缘，createRunSection 里 326+104
	)
	if runNowRight > uiInnerR {
		t.Errorf("「立即运行」右边缘 %d 越过了可用右边界 %d", runNowRight, uiInnerR)
	}
	if unlockRight >= uiInnerR-120 {
		t.Errorf("触发时机一行右边缘 %d 会撞上「立即运行」按钮（左边缘 %d）",
			unlockRight, uiInnerR-120)
	}
}
