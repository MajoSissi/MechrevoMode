//go:build windows

package main

import (
	"math"
	"testing"
)

// 图标位图是 BGRA，而配色在代码里写的是 0xRRGGBB。这两套顺序一旦写反，
// 红蓝就互换：蓝色会变成橙色、紫色会变成粉色 —— 程序不报任何错，
// 只有盯着托盘看才发现颜色不对（这一条就是这么踩出来的）。
func TestIconBitmapChannelOrder(t *testing.T) {
	const (
		size = 48
		rgb  = 0x2F80ED // 蓝：故意让 R/B 差得很远，写反了必然被抓到
	)
	wantR, wantG, wantB := byte(0x2F), byte(0x80), byte(0xED)

	bgra := iconBitmap(size, rgb, 'B')
	// 取靠近左边框、又在字模矩形之外的一点：那里是纯底色、完全不透明。
	o := (size/2*size + 2) * 4
	gotB, gotG, gotR, gotA := bgra[o], bgra[o+1], bgra[o+2], bgra[o+3]
	if gotA != 255 {
		t.Fatalf("底色像素应当是实心的，alpha = %d", gotA)
	}
	if gotR != wantR || gotG != wantG || gotB != wantB {
		t.Errorf("底色通道顺序不对：得到 R=%#02x G=%#02x B=%#02x，期望 R=%#02x G=%#02x B=%#02x",
			gotR, gotG, gotB, wantR, wantG, wantB)
	}
}

// 圆角正方形的四条边必须撑满、四个角必须是透空的。
// 角上不透空说明圆角没生效（退化成直角方块）；边上透空说明图形整体被缩小了。
func TestIconBitmapShape(t *testing.T) {
	const size = 48
	bgra := iconBitmap(size, 0x27AE60, 'E')

	alphaAt := func(x, y int) byte { return bgra[(y*size+x)*4+3] }

	// 最外一圈（边中点）是抗锯齿的边缘像素：允许半透明，但必须有覆盖，
	// 也就是说图形确实铺到了图标边界（shapeHalf 只留了 0.75 个超采样像素）。
	for _, p := range [][2]int{{size / 2, 0}, {size / 2, size - 1}, {0, size / 2}, {size - 1, size / 2}} {
		if a := alphaAt(p[0], p[1]); a == 0 {
			t.Errorf("边中点 (%d,%d) 完全透明，图形没有铺到边界", p[0], p[1])
		}
	}
	// 再往里一像素就必须是实心的
	for _, p := range [][2]int{{size / 2, 1}, {size - 2, size / 2}} {
		if a := alphaAt(p[0], p[1]); a != 255 {
			t.Errorf("(%d,%d) 应当是实心的，alpha = %d", p[0], p[1], a)
		}
	}
	// 四角：完全透明
	for _, p := range [][2]int{{0, 0}, {size - 1, 0}, {0, size - 1}, {size - 1, size - 1}} {
		if a := alphaAt(p[0], p[1]); a != 0 {
			t.Errorf("圆角处 (%d,%d) 应当全透明，alpha = %d", p[0], p[1], a)
		}
	}
}

// 字模必须留出可观的余量。余量 ≤ 0 就是笔画压到图标边缘上了 ——
// 这正是「E / Z 的直角看上去贴着外轮廓」那类返工的根因。
func TestGlyphMargin(t *testing.T) {
	for _, size := range []int{16, 20, 24, 32, 40, 48} {
		for _, g := range []rune{'E', 'B', 'G', '1', '?'} {
			m := glyphMargin(size, 0x2F80ED, g)
			if math.IsInf(m, 1) {
				t.Fatalf("%dpx %c：一个纯白像素都没有，字模没画上去", size, g)
			}
			if m < 1.0 {
				t.Errorf("%dpx %c：留白余量只有 %.2f px，笔画贴边了", size, g, m)
			}
		}
	}
}

// roundedSquareDist 是整个外形的唯一真值来源（形状判定、抗锯齿、留白都靠它），
// 所以把它的边界行为单独钉住：内部为正、外部为负、rc=0 退化成普通正方形。
func TestRoundedSquareDist(t *testing.T) {
	const (
		half = 10.0
		rc   = 4.0
	)
	if d := roundedSquareDist(0, 0, half, rc); d != half {
		t.Errorf("中心处到边界的距离应当就是半边长 %.1f，得到 %.1f", half, d)
	}
	if d := roundedSquareDist(half-1, 0, half, rc); math.Abs(d-1) > 1e-9 {
		t.Errorf("直边内侧 1 像素处应为 1，得到 %.4f", d)
	}
	if d := roundedSquareDist(half+1, 0, half, rc); math.Abs(d+1) > 1e-9 {
		t.Errorf("直边外侧 1 像素处应为 -1，得到 %.4f", d)
	}
	// 角上的点：(half-rc+rc/√2) 处正好落在圆角圆弧上 → 距离 0
	off := (half - rc) + rc/math.Sqrt2
	if d := roundedSquareDist(off, off, half, rc); math.Abs(d) > 1e-9 {
		t.Errorf("圆角圆弧上应为 0，得到 %.4f", d)
	}
	// rc = 0：圆角退化成直角，角上的点按直边算
	if d := roundedSquareDist(half, half, half, 0); math.Abs(d) > 1e-9 {
		t.Errorf("rc=0 时角点应在边界上（0），得到 %.4f", d)
	}
	if d := roundedSquareDist(half+0.5, half+0.5, half, 0); d >= 0 {
		t.Errorf("rc=0 时角外的点应为负，得到 %.4f", d)
	}
}
