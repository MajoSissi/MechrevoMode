//go:build windows

package main

import (
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"math"
	"os"
	"path/filepath"
	"strings"
)

// glyphMargin 量出字模笔画离**图标边缘**还有多少余量（单位：图标像素，可为小数）。
//
// 直接从渲染结果里取纯白像素当字模真值，而不是照着重算一遍字模矩形——
// 这样量到的就是眼睛真正看到的东西。
//
// 图标只有一层底色，白色像素只可能来自字模本身，所以不用再排除任何东西。
// （早先图标带一圈白色外轮廓时，那一圈同样是纯白不透明像素，必须显式跳过，
// 否则量到的是轮廓离外沿的距离 ≈ 0，自检会永远报「贴边」。）
func glyphMargin(size int, rgb uint32, glyph rune) float64 {
	half := shapeHalf(size*iconSuperSample) / float64(iconSuperSample)
	rc := half * cornerRatio
	c := float64(size) / 2.0

	bgra := iconBitmap(size, rgb, glyph)
	margin := math.Inf(1)
	for y := 0; y < size; y++ {
		for x := 0; x < size; x++ {
			o := (y*size + x) * 4
			if bgra[o] != 255 || bgra[o+1] != 255 || bgra[o+2] != 255 || bgra[o+3] != 255 {
				continue
			}
			dx := float64(x) + 0.5 - c
			dy := float64(y) + 0.5 - c
			if d := roundedSquareDist(dx, dy, half, rc); d < margin {
				margin = d
			}
		}
	}
	return margin
}

// iconNRGBA 把 iconBitmap 的 BGRA 字节流包成 image.NRGBA，方便用 draw.Over 做 1:1 合成。
func iconNRGBA(size int, rgb uint32, glyph rune) *image.NRGBA {
	bgra := iconBitmap(size, rgb, glyph)
	img := image.NewNRGBA(image.Rect(0, 0, size, size))
	for y := 0; y < size; y++ {
		for x := 0; x < size; x++ {
			o := (y*size + x) * 4
			img.SetNRGBA(x, y, color.NRGBA{R: bgra[o+2], G: bgra[o+1], B: bgra[o], A: bgra[o+3]})
		}
	}
	return img
}

// dumpIcons 自检：把托盘图标各种尺寸渲染成一张对照图，
// 输出到 数据目录\icons\sheet.png，用来肉眼确认形状与字模比例；
// 同时打印每个尺寸下字模的留白余量，用来量化确认字母没贴到图标边缘上。
func dumpIcons() {
	dir := filepath.Join(dataDir(), "icons")
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

	// 上面是放大网格（肉眼看形状、圆角、边缘细节），
	// 下面是两条 1:1 原尺寸对照带 —— 放大图会骗人，真正要判断的是 16/24px 下的实际观感，
	// 而且托盘底色可能是浅色也可能是深色，两种都要过一遍。
	const cell = 256
	const bandPad = 14
	const iconGap = 10
	const groupGap = 26
	bandH := 48 + bandPad*2

	sheetW := cell * len(sizes)
	sheetH := cell*len(samples) + bandH*2
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

	// 1:1 对照带：浅色 / 深色任务栏各一条，图标按真实像素落在底色上（draw.Over 做 alpha 合成）。
	bands := []struct {
		name string
		bg   color.NRGBA
	}{
		{"浅色任务栏", color.NRGBA{R: 0xF2, G: 0xF2, B: 0xF2, A: 0xFF}},
		{"深色任务栏", color.NRGBA{R: 0x1E, G: 0x1E, B: 0x1E, A: 0xFF}},
	}
	rowSpan := 0
	for _, size := range sizes {
		rowSpan += size + iconGap
	}
	rowSpan += (len(samples) - 1) * groupGap
	for bi, band := range bands {
		y0 := cell*len(samples) + bi*bandH
		for y := y0; y < y0+bandH; y++ {
			for x := 0; x < sheetW; x++ {
				sheet.SetNRGBA(x, y, band.bg)
			}
		}
		x := (sheetW - rowSpan) / 2
		for _, s := range samples {
			for _, size := range sizes {
				icon := iconNRGBA(size, s.color, s.glyph)
				py := y0 + (bandH-size)/2
				draw.Draw(sheet, image.Rect(x, py, x+size, py+size), icon, image.Point{}, draw.Over)
				x += size + iconGap
			}
			x += groupGap - iconGap
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

	// 量化自检：字母离图标边缘的余量。
	// 余量必须是正的、而且要有点分量，否则角度大的字母（E/Z/1）就会贴着边缘。
	app.logf("字模留白余量（图标像素，越大越安全；inset=%.2f）:", glyphBoxInset)
	for _, size := range sizes {
		half := shapeHalf(size*iconSuperSample) / float64(iconSuperSample)
		rc := half * cornerRatio
		gw, gh := glyphBoxFor(half, rc)
		worst, worstName := math.Inf(1), ""
		parts := make([]string, 0, len(samples))
		for _, s := range samples {
			m := glyphMargin(size, s.color, s.glyph)
			parts = append(parts, fmt.Sprintf("%c=%.2f", s.glyph, m))
			if m < worst {
				worst, worstName = m, string(s.glyph)
			}
		}
		app.logf("  %2dpx 字模%2d×%-2d 半边长=%.2f 圆角=%.2f 最小余量=%.2f(%s)  %s",
			size, gw, gh, half, rc, worst, worstName, strings.Join(parts, " "))
	}
}
