//go:build windows

package main

import (
	"encoding/binary"
	"math"
	"unsafe"
)

// 5x7 点阵字模，每行低 5 位有效（bit4 在最左）
var font5x7 = map[rune][7]uint8{
	'A': {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},
	'B': {0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E},
	'C': {0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E},
	'D': {0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E},
	'E': {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F},
	'F': {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10},
	'G': {0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F},
	'H': {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},
	'I': {0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E},
	'J': {0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C},
	'K': {0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11},
	'L': {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F},
	'M': {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11},
	'N': {0x11, 0x19, 0x19, 0x15, 0x13, 0x13, 0x11},
	'O': {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},
	'P': {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10},
	'Q': {0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D},
	'R': {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11},
	'S': {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E},
	'T': {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04},
	'U': {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},
	'V': {0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04},
	'W': {0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11},
	'X': {0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11},
	'Y': {0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04},
	'Z': {0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F},
	'0': {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E},
	'1': {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E},
	'2': {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F},
	'3': {0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E},
	'4': {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02},
	'5': {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E},
	'6': {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E},
	'7': {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08},
	'8': {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E},
	'9': {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C},
	'?': {0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04},
	'-': {0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00},
	'+': {0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00},
	'.': {0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C},
}

// lookupGlyph 取字模：小写自动转大写，找不到用 '?'
func lookupGlyph(g rune) [7]uint8 {
	if fm, ok := font5x7[g]; ok {
		return fm
	}
	if g >= 'a' && g <= 'z' {
		if fm, ok := font5x7[g-'a'+'A']; ok {
			return fm
		}
	}
	return font5x7['?']
}

// 未连接 GCU 时的图标配色
const colUnknown = 0x7F8C9A

// cos30 = √3/2。正六边形的内切半径 = 外接半径 × cos30。
const cos30 = 0.8660254037844386

// insideHexagon 判断点 (dx, dy)（相对中心）是否落在尖角朝上（顶点在 12 点钟方向）
// 的正六边形内，外接半径为 r。
//
// 顶点依次位于 -90°、-30°、30°、90°、150°、210°。
// 六条边给出六个半平面约束，利用对称性可以化简成下面两个不等式：
//
//	|x| <= 内切半径                      （左右两条竖直边）
//	0.5|x| + cos30*|y| <= 内切半径       （四条斜边）
func insideHexagon(dx, dy, r float64) bool {
	apothem := r * cos30
	ax, ay := math.Abs(dx), math.Abs(dy)
	return ax <= apothem && 0.5*ax+cos30*ay <= apothem
}

// glyphBoxInset 字模四周留给六边形轮廓的空白，占内切半径的比例。
//
// 字母的直角本来就落在字模矩形的四角上（「E」上下两条横杠的两端就在那儿），
// 而六边形的斜边正好从四角旁边掠过，不留空隙看着就像压在轮廓线上。
const glyphBoxInset = 0.20

// glyphBoxFor 求 5×7 点阵在图标上占用的整数矩形（宽 w、高 h）。
//
// 以前用「5×s 宽、7×s 高」的整数倍，s 只能取整数，于是每个尺寸要么把四角
// 顶到六边形斜边上，要么一下子小一大截，没有中间状态。
// 改成先按「四角离斜边留出 inset」解出目标高度，再按 5:7 取整；铺像素用最近邻，
// 每个点阵像素仍对应 1～2 个完整图标像素，笔画不会变半透明。
//
// 约束来自斜边：矩形四角 (w/2, h/2) 必须落在六边形内侧，
//
//	0.5*(w/2) + cos30*(h/2) <= apothem*(1-inset)
func glyphBoxFor(apothem float64) (w, h int) {
	limit := apothem * (1 - glyphBoxInset)
	for h = 48; h >= 7; h-- {
		w = int(math.Round(float64(h) * 5.0 / 7.0))
		if w < 5 {
			w = 5
		}
		if 0.25*float64(w)+cos30*0.5*float64(h) <= limit {
			return w, h
		}
	}
	return 5, 7
}

// iconSuperSample 底色超采样倍率。六边形边缘靠它做抗锯齿。
const iconSuperSample = 4

// hexagonRadius 求超采样画布 hi×hi 上六边形的外接半径。
//
// 减掉 0.75 个超采样像素是给边缘抗锯齿留的余量：不留的话，抗锯齿只会把
// 轮廓往里啃，图标看着会偏小一圈。
func hexagonRadius(hi int) float64 {
	r := float64(hi)/2.0 - float64(iconSuperSample)*0.75
	if r < 1 {
		return 1
	}
	return r
}

// iconBitmap 生成 size×size 的 32bpp BGRA（直通 alpha）位图
func iconBitmap(size int, rgb uint32, glyph rune) []byte {
	const ss = iconSuperSample // 底色超采样倍率
	hi := size * ss

	cx := float64(hi) / 2.0
	cy := float64(hi) / 2.0
	// 尖角朝上的六边形：高度 = 2r，宽度 = √3·r。高度占满图标，宽度自然收窄。
	radius := hexagonRadius(hi)

	// 底色掩码（高分辨率，用于边缘抗锯齿）
	hexHi := make([]bool, hi*hi)
	for y := 0; y < hi; y++ {
		dy := float64(y) + 0.5 - cy
		for x := 0; x < hi; x++ {
			dx := float64(x) + 0.5 - cx
			if insideHexagon(dx, dy, radius) {
				hexHi[y*hi+x] = true
			}
		}
	}

	// 字模：按最近邻铺到 gw×gh 的矩形上，纯白实心、不做半透明，
	// 于是小图标上笔画依然是一根实心的白线，不会糊成灰绿。
	fm := lookupGlyph(glyph)
	gw, gh := glyphBoxFor(radius * cos30 / float64(ss))
	ox := (size - gw) / 2
	oy := (size - gh) / 2
	glyphOut := make([]bool, size*size)
	for gy := 0; gy < gh; gy++ {
		ry := int((float64(gy) + 0.5) * 7.0 / float64(gh))
		if ry > 6 {
			ry = 6
		}
		bits := fm[ry]
		for gx := 0; gx < gw; gx++ {
			rx := int((float64(gx) + 0.5) * 5.0 / float64(gw))
			if rx > 4 {
				rx = 4
			}
			if bits&(1<<uint(4-rx)) == 0 {
				continue
			}
			y, x := oy+gy, ox+gx
			if y < 0 || y >= size || x < 0 || x >= size {
				continue
			}
			glyphOut[y*size+x] = true
		}
	}

	br := byte(rgb >> 16)
	bg := byte(rgb >> 8)
	bb := byte(rgb)

	out := make([]byte, size*size*4)
	for y := 0; y < size; y++ {
		for x := 0; x < size; x++ {
			o := (y*size + x) * 4
			if glyphOut[y*size+x] {
				out[o], out[o+1], out[o+2], out[o+3] = 255, 255, 255, 255
				continue
			}
			var cov float64
			for sy := 0; sy < ss; sy++ {
				for sx := 0; sx < ss; sx++ {
					if hexHi[(y*ss+sy)*hi+x*ss+sx] {
						cov++
					}
				}
			}
			if cov <= 0 {
				continue // 已是全透明
			}
			// 直通 alpha：颜色保持纯模式色，只让 alpha 递减
			out[o] = bb
			out[o+1] = bg
			out[o+2] = br
			out[o+3] = clampByte(cov / float64(ss*ss) * 255)
		}
	}
	return out
}

func clampByte(v float64) byte {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return byte(v + 0.5)
}

// makeHIcon 把 BGRA 位图封装成 RT_ICON 资源格式并创建 HICON
func makeHIcon(size int, rgb uint32, glyph rune) uintptr {
	bgra := iconBitmap(size, rgb, glyph)

	const bihSize = 40
	maskRow := ((size + 31) / 32) * 4
	xorSize := size * size * 4
	andSize := maskRow * size
	buf := make([]byte, bihSize+xorSize+andSize)

	// BITMAPINFOHEADER，高度为 2*size（XOR + AND）
	binary.LittleEndian.PutUint32(buf[0:], bihSize)
	binary.LittleEndian.PutUint32(buf[4:], uint32(int32(size)))
	binary.LittleEndian.PutUint32(buf[8:], uint32(int32(size*2)))
	binary.LittleEndian.PutUint16(buf[12:], 1)
	binary.LittleEndian.PutUint16(buf[14:], 32)
	binary.LittleEndian.PutUint32(buf[16:], 0) // BI_RGB

	// XOR 位图：自下而上
	for y := 0; y < size; y++ {
		srcRow := (size - 1 - y) * size * 4
		dst := bihSize + y*size*4
		copy(buf[dst:dst+size*4], bgra[srcRow:srcRow+size*4])
	}

	// AND 掩码：全透明处打 1
	for y := 0; y < size; y++ {
		srcY := size - 1 - y
		for x := 0; x < size; x++ {
			if bgra[(srcY*size+x)*4+3] == 0 {
				idx := bihSize + xorSize + y*maskRow + x/8
				buf[idx] |= 1 << uint(7-(x%8))
			}
		}
	}

	h, _, _ := pCreateIconFromResource.Call(
		uintptr(unsafe.Pointer(&buf[0])),
		uintptr(len(buf)),
		1,          // fIcon = TRUE
		0x00030000, // 版本
		0, 0,
		0, // LR_DEFAULTCOLOR
	)
	return h
}
