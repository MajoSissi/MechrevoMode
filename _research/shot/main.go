// shot：按窗口标题截图，用于开发期验证界面布局。
// 用法：shot.exe <窗口标题> <输出png>
package main

import (
	"encoding/binary"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"os"
	"syscall"
	"unsafe"
)

var (
	user32   = syscall.NewLazyDLL("user32.dll")
	gdi32    = syscall.NewLazyDLL("gdi32.dll")
	pFind    = user32.NewProc("FindWindowW")
	pSetFg   = user32.NewProc("SetForegroundWindow")
	pGetRect = user32.NewProc("GetWindowRect")
	pGetDC   = user32.NewProc("GetDC")
	pRelDC   = user32.NewProc("ReleaseDC")
	pPrintW  = user32.NewProc("PrintWindow")
	pShowW   = user32.NewProc("ShowWindow")
	pDpiAware = user32.NewProc("SetProcessDPIAware")

	pCreateCompatDC   = gdi32.NewProc("CreateCompatibleDC")
	pCreateCompatBmp  = gdi32.NewProc("CreateCompatibleBitmap")
	pSelectObject     = gdi32.NewProc("SelectObject")
	pGetDIBits        = gdi32.NewProc("GetDIBits")
	pDeleteDC         = gdi32.NewProc("DeleteDC")
	pDeleteObject     = gdi32.NewProc("DeleteObject")
)

type rect struct{ Left, Top, Right, Bottom int32 }

type bitmapInfoHeader struct {
	Size          uint32
	Width         int32
	Height        int32
	Planes        uint16
	BitCount      uint16
	Compression   uint32
	SizeImage     uint32
	XPelsPerMeter int32
	YPelsPerMeter int32
	ClrUsed       uint32
	ClrImportant  uint32
}

type rgbQuad struct{ Blue, Green, Red, Reserved byte }

type bitmapInfo struct {
	Header bitmapInfoHeader
	Colors [1]rgbQuad
}

func utf16Ptr(s string) *uint16 {
	p, err := syscall.UTF16PtrFromString(s)
	if err != nil {
		return nil
	}
	return p
}

// cropArg 解析形如 crop=x,y,w,h 的参数
func cropArg() (x, y, w, h int, ok bool) {
	for _, a := range os.Args[1:] {
		if len(a) > 5 && a[:5] == "crop=" {
			n, err := fmt.Sscanf(a[5:], "%d,%d,%d,%d", &x, &y, &w, &h)
			if err == nil && n == 4 {
				return x, y, w, h, true
			}
		}
	}
	return 0, 0, 0, 0, false
}

func main() {
	if len(os.Args) < 3 {
		fmt.Println("usage: shot <title> <out.png>")
		fmt.Println("   or: shot <class> <title> <out.png>   (class 用 - 占位表示不过滤)")
		os.Exit(2)
	}

	var class, title, out string
	if len(os.Args) >= 4 {
		class, title, out = os.Args[1], os.Args[2], os.Args[3]
	} else {
		title, out = os.Args[1], os.Args[2]
	}
	if class == "-" {
		class = ""
	}

	// 必须声明 DPI 感知，否则拿到的是被虚拟化（缩小）的坐标和残缺的画面
	pDpiAware.Call()

	var pClass uintptr
	if class != "" {
		pClass = uintptr(unsafe.Pointer(utf16Ptr(class)))
	}
	hwnd, _, _ := pFind.Call(pClass, uintptr(unsafe.Pointer(utf16Ptr(title))))
	if hwnd == 0 {
		fmt.Println("window not found:", title)
		os.Exit(1)
	}
	pShowW.Call(hwnd, 5) // SW_SHOW
	pSetFg.Call(hwnd)

	var r rect
	pGetRect.Call(hwnd, uintptr(unsafe.Pointer(&r)))
	w := int(r.Right - r.Left)
	h := int(r.Bottom - r.Top)
	if w <= 0 || h <= 0 {
		fmt.Println("bad window rect")
		os.Exit(1)
	}

	hdcScreen, _, _ := pGetDC.Call(0)
	hdcMem, _, _ := pCreateCompatDC.Call(hdcScreen)
	hbmp, _, _ := pCreateCompatBmp.Call(hdcScreen, uintptr(w), uintptr(h))
	pSelectObject.Call(hdcMem, hbmp)

	// PW_RENDERFULLCONTENT = 2，能抓到被遮挡的窗口
	ok, _, _ := pPrintW.Call(hwnd, hdcMem, 2)
	if ok == 0 {
		pPrintW.Call(hwnd, hdcMem, 0)
	}

	bi := bitmapInfo{}
	bi.Header.Size = uint32(unsafe.Sizeof(bitmapInfoHeader{}))
	bi.Header.Width = int32(w)
	bi.Header.Height = -int32(h) // 负数 = 自顶向下
	bi.Header.Planes = 1
	bi.Header.BitCount = 32
	bi.Header.Compression = 0 // BI_RGB

	buf := make([]byte, w*h*4)
	pGetDIBits.Call(hdcMem, hbmp, 0, uintptr(h),
		uintptr(unsafe.Pointer(&buf[0])),
		uintptr(unsafe.Pointer(&bi)), 0)

	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			i := (y*w + x) * 4
			img.Set(x, y, color.RGBA{R: buf[i+2], G: buf[i+1], B: buf[i], A: 255})
		}
	}
	_ = binary.LittleEndian

	pDeleteObject.Call(hbmp)
	pDeleteDC.Call(hdcMem)
	pRelDC.Call(0, hdcScreen)

	// 可选区域裁剪：crop=x,y,w,h
	if x, y, cw, ch, ok := cropArg(); ok && x >= 0 && y >= 0 && x+cw <= w && y+ch <= h && cw > 0 && ch > 0 {
		if sub, ok2 := img.SubImage(image.Rect(x, y, x+cw, y+ch)).(*image.RGBA); ok2 {
			img = sub
			w, h = cw, ch
		}
	}

	f, err := os.Create(out)
	if err != nil {
		fmt.Println("create failed:", err)
		os.Exit(1)
	}
	defer f.Close()
	if err := png.Encode(f, img); err != nil {
		fmt.Println("encode failed:", err)
		os.Exit(1)
	}
	fmt.Printf("saved %s (%dx%d)\n", out, w, h)}
