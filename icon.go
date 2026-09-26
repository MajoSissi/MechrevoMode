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

// 托盘图标 = 圆角正方形底色 + 中间一个白色字母。

// cornerRatio 圆角半径占**半边长**的比例。0.45 → 圆角半径 = 边长的 22.5%，
// 看上去是「带圆角的方块」，既不像直角方块那么硬，也没到胶囊或圆形。
const cornerRatio = 0.45

// roundedSquareDist 点到圆角正方形边界的距离：内部为正、外部为负。
//
// 圆角正方形 = 正方形（|x|<=half 且 |y|<=half）∩ 四个角上的圆（半径 rc）。
// 按对称性把点折到第一象限后只有两种情形：
//   - 落在「十字」区域（|x| <= half-rc 或 |y| <= half-rc）：最近的是直边，
//     距离 = half - max(|x|,|y|)；
//   - 落在角上的那块方形区域：最近的是圆角圆弧，距离 = rc - hypot(越界量)。
//
// rc = 0 时退化为普通正方形（填充区在某些尺寸下就是这种情形）。
// 用「有符号距离」而不是布尔判断，是因为字模留白和边缘抗锯齿都要用到这个数值。
func roundedSquareDist(dx, dy, half, rc float64) float64 {
	ax, ay := math.Abs(dx), math.Abs(dy)
	qx, qy := ax-(half-rc), ay-(half-rc)
	if qx > 0 && qy > 0 {
		return rc - math.Hypot(qx, qy)
	}
	return half - math.Max(ax, ay)
}

// iconSuperSample 底色超采样倍率。圆角与边缘都靠它做抗锯齿。
const iconSuperSample = 4

// shapeHalf 求超采样画布 hi×hi 上圆角正方形的半边长。
//
// 减掉 0.75 个超采样像素是给边缘抗锯齿留的余量：不留的话，抗锯齿只会把
// 轮廓往里啃，图标看着会偏小一圈。
func shapeHalf(hi int) float64 {
	h := float64(hi)/2.0 - float64(iconSuperSample)*0.75
	if h < 1 {
		return 1
	}
	return h
}

// glyphBoxInset 字模四周留出的空白，占**形状半边长**的比例。
//
// 字母的直角本来就落在字模矩形的四角上（「E」上下两条横杠的两端就在那儿），
// 不留空隙看着就像贴在图标边缘上。留太多字母又会显得小。
// 0.30 → 字母高度约占图标高度的 68%（实测 16px 上是 6×9、24px 上 11×15）。
const glyphBoxInset = 0.30

// glyphBoxFor 求 5×7 点阵在图标上占用的整数矩形（宽 w、高 h）。
//
// 以前用「5×s 宽、7×s 高」的整数倍，s 只能取整数，于是每个尺寸要么把四角
// 顶到边缘上，要么一下子小一大截，没有中间状态。
// 改成先按「离边缘留出 inset」解出目标高度，再按 5:7 取整；铺像素用最近邻，
// 每个点阵像素仍对应 1～2 个完整图标像素，笔画不会变半透明。
//
// availHalf：形状半边长；rc：形状圆角半径，单位都是图标像素。
// 除了四边不能越界，字模矩形的四角也不能戳出圆角 —— 所以四角要落在圆角内侧。
func glyphBoxFor(availHalf, rc float64) (w, h int) {
	limit := availHalf * (1 - glyphBoxInset)
	fits := func(w, h int) bool {
		ax, ay := float64(w)/2.0, float64(h)/2.0
		if ax > availHalf || ay > availHalf {
			return false
		}
		qx, qy := ax-(availHalf-rc), ay-(availHalf-rc)
		if qx > 0 && qy > 0 {
			return math.Hypot(qx, qy) <= rc
		}
		return true
	}
	for h = 48; h >= 7; h-- {
		w = int(math.Round(float64(h) * 5.0 / 7.0))
		if w < 5 {
			w = 5
		}
		if float64(h)/2.0 <= limit && fits(w, h) {
			return w, h
		}
	}
	return 5, 7
}

// iconBitmap 生成 size×size 的 32bpp BGRA（直通 alpha）位图
func iconBitmap(size int, rgb uint32, glyph rune) []byte {
	const ss = iconSuperSample // 底色超采样倍率
	hi := size * ss

	cx := float64(hi) / 2.0
	cy := float64(hi) / 2.0
	// 外形：圆角正方形（超采样坐标）
	half := shapeHalf(hi)
	rc := half * cornerRatio

	// 字模：按最近邻铺到 gw×gh 的矩形上，纯白实心、不做半透明，
	// 于是小图标上笔画依然是一根实心的白线，不会糊成灰绿。
	fm := lookupGlyph(glyph)
	gw, gh := glyphBoxFor(half/float64(ss), rc/float64(ss))
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

	// 位图是 BGRA：out[0]=蓝、out[1]=绿、out[2]=红。
	// rgb 是 0xRRGGBB，所以三个分量必须**倒着**写进去 ——
	// 顺序写反的话红蓝互换，蓝色会显示成橙色、紫色会显示成粉色。
	r8 := byte(rgb >> 16)
	g8 := byte(rgb >> 8)
	b8 := byte(rgb)

	out := make([]byte, size*size*4)
	for y := 0; y < size; y++ {
		for x := 0; x < size; x++ {
			o := (y*size + x) * 4
			if glyphOut[y*size+x] {
				out[o], out[o+1], out[o+2], out[o+3] = 255, 255, 255, 255
				continue
			}
			// 数一遍有多少个子采样点落在图形里：颜色就是纯底色，
			// alpha 取覆盖率。只按最大的那一份取色（而不是数覆盖率）的话，
			// 圆角处会留下生硬的阶梯。
			n := 0
			for sy := 0; sy < ss; sy++ {
				dy := float64(y*ss+sy) + 0.5 - cy
				for sx := 0; sx < ss; sx++ {
					dx := float64(x*ss+sx) + 0.5 - cx
					if roundedSquareDist(dx, dy, half, rc) > 0 {
						n++
					}
				}
			}
			if n == 0 {
				continue // 已是全透明
			}
			out[o], out[o+1], out[o+2] = b8, g8, r8
			out[o+3] = clampByte(float64(n) / float64(ss*ss) * 255)
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
