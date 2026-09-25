//go:build windows

package main

import (
	"fmt"
	"image"
	"image/color"
	"image/png"
	"math"
	"os"
	"path/filepath"
	"strings"
)

// glyphMargin 量出字模离六边形轮廓还有多少余量（单位：图标像素，可为小数）。
//
// 直接从渲染结果里取纯白像素当字模真值，而不是照着重算一遍字模矩形——
// 这样量到的就是眼睛真正看到的东西。
//
// 六边形的六个半平面里，左右两条竖直边的法向量是 (1,0)，四条斜边的法向量是
// (0.5, cos30)，恰好都是单位向量，所以「到边的距离」直接就是
// apothem-|x| 和 apothem-0.5|x|-cos30|y|，不用再除以模长。
func glyphMargin(size int, rgb uint32, glyph rune) float64 {
	apothem := hexagonRadius(size*iconSuperSample) * cos30 / float64(iconSuperSample)
	c := float64(size) / 2.0

	bgra := iconBitmap(size, rgb, glyph)
	margin := math.Inf(1)
	for y := 0; y < size; y++ {
		for x := 0; x < size; x++ {
			o := (y*size + x) * 4
			// 纯白不透明的像素就是字模笔画
			if bgra[o] != 255 || bgra[o+1] != 255 || bgra[o+2] != 255 || bgra[o+3] != 255 {
				continue
			}
			dx := math.Abs(float64(x) + 0.5 - c)
			dy := math.Abs(float64(y) + 0.5 - c)
			m := apothem - dx // 左右竖直边
			if m2 := apothem - 0.5*dx - cos30*dy; m2 < m {
				m = m2 // 斜边
			}
			if m < margin {
				margin = m
			}
		}
	}
	return margin
}

// dumpIcons 自检：把托盘图标各种尺寸渲染成一张放大对照图，
// 输出到 %APPDATA%\MechrevoMode\icons\sheet.png，用来肉眼确认形状与字模比例；
// 同时打印每个尺寸下字模的留白余量，用来量化确认字母没贴到六边形轮廓上。
func dumpIcons() {
	dir := filepath.Join(configDir(), "icons")
	_ = os.MkdirAll(dir, 0o755)

	type sample struct {
		name  string
		color uint32
		glyph rune
	}
	samples := []sample{
		{"静音E", 0x27AE60, 'E'},
		{"均衡B", 0x2F80ED, 'B'},
		{"狂暴G", 0xE2445C, 'G'},
		{"自定义1", 0x9B51E0, '1'},
		{"未连接", colUnknown, '?'},
	}
	sizes := []int{16, 20, 24, 32, 40, 48}

	const cell = 256
	sheetW := cell * len(sizes)
	sheetH := cell * len(samples)
	sheet := image.NewNRGBA(image.Rect(0, 0, sheetW, sheetH))

	// 中性灰底：既能看清图案轮廓，也能一眼看出白字和彩色底
	bg := color.NRGBA{R: 0x5A, G: 0x5A, B: 0x5A, A: 0xFF}
	for y := 0; y < sheetH; y++ {
		for x := 0; x < sheetW; x++ {
			sheet.SetNRGBA(x, y, bg)
		}
	}

	for r, s := range samples {
		for c, size := range sizes {
			bgra := iconBitmap(size, s.color, s.glyph)
			zoom := cell / size
			ox := c*cell + (cell-size*zoom)/2
			oy := r*cell + (cell-size*zoom)/2
			for y := 0; y < size; y++ {
				for x := 0; x < size; x++ {
					o := (y*size + x) * 4
					a := bgra[o+3]
					if a == 0 {
						continue // 全透明处保留灰底，便于看清轮廓
					}
					px := color.NRGBA{R: bgra[o+2], G: bgra[o+1], B: bgra[o], A: a}
					for dy := 0; dy < zoom; dy++ {
						for dx := 0; dx < zoom; dx++ {
							sx, sy := ox+x*zoom+dx, oy+y*zoom+dy
							if sx < sheetW && sy < sheetH {
								sheet.SetNRGBA(sx, sy, px)
							}
						}
					}
				}
			}
		}
	}

	p := filepath.Join(dir, "sheet.png")
	f, err := os.Create(p)
	if err != nil {
		return
	}
	defer f.Close()
	_ = png.Encode(f, sheet)
	app.logf("图标预览已输出: %s", p)

	// 量化自检：字母离六边形轮廓的余量。
	// 余量必须是正的、而且要有点分量，否则角度大的字母（E/Z/1）就会贴着轮廓。
	app.logf("字模留白余量（图标像素，越大越安全；inset=%.2f）:", glyphBoxInset)
	for _, size := range sizes {
		apothem := hexagonRadius(size*iconSuperSample) * cos30 / float64(iconSuperSample)
		gw, gh := glyphBoxFor(apothem)
		worst, worstName := math.Inf(1), ""
		parts := make([]string, 0, len(samples))
		for _, s := range samples {
			m := glyphMargin(size, s.color, s.glyph)
			parts = append(parts, fmt.Sprintf("%c=%.2f", s.glyph, m))
			if m < worst {
				worst, worstName = m, string(s.glyph)
			}
		}
		app.logf("  %2dpx 字模%2d×%-2d 内切半径=%.1f 最小余量=%.2f(%s)  %s",
			size, gw, gh, apothem, worst, worstName, strings.Join(parts, " "))
	}
}
