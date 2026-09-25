# 复现 makeHIcon 的资源格式：把 sheet 里的 24px 位图包成 RT_ICON，
# 交给 CreateIconFromResourceEx 建 HICON，再用 DrawIconEx 画回 DIB，
# 看字模在 HICON 这条路上是否被破坏。
import ctypes
import struct
from ctypes import wintypes

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

SIZE = 24

# --- 1) 从 sheet.png 里取出 24px 静音E 的 BGRA ---------------------------------
im = Image.open(r"C:\Users\Majo\AppData\Roaming\MechrevoMode\icons\sheet.png").convert("RGBA")
zoom = 256 // SIZE
cell = 256
ox = 2 * cell + (cell - SIZE * zoom) // 2      # sizes=[16,20,24,32,40,48] -> 24 是第 3 列
oy = 0 * cell + (cell - SIZE * zoom) // 2      # 第 1 行 = 静音E
src = []
for y in range(SIZE):
    for x in range(SIZE):
        src.append(im.getpixel((ox + x * zoom + zoom // 2, oy + y * zoom + zoom // 2)))
# Sheet 里全透明处保留了灰底，这里按 alpha 还原
bgra = bytearray()
for (r, g, b, a) in src:
    if a == 255 and (r, g, b) == (0x5A, 0x5A, 0x5A):
        bgra += bytes((0, 0, 0, 0))
    else:
        bgra += bytes((b, g, r, a))


def ascii_map(px):
    """px(x,y) -> (r,g,b,a)"""
    out = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            r, g, b, a = px(x, y)
            if a == 0:
                row.append(".")
            elif r > 200 and g > 200 and b > 200:
                row.append("#")
            else:
                row.append("o")
        out.append("".join(row))
    return "\n".join(out)


print("=== 源位图（sheet 里取出的 24px 静音E）===")
print(ascii_map(lambda x, y: (bgra[(y * SIZE + x) * 4 + 2], bgra[(y * SIZE + x) * 4 + 1],
                              bgra[(y * SIZE + x) * 4 + 0], bgra[(y * SIZE + x) * 4 + 3])))

# --- 2) 按 makeHIcon 的方式打包资源 ------------------------------------------
BIH = 40
mask_row = ((SIZE + 31) // 32) * 4
xor_size = SIZE * SIZE * 4
and_size = mask_row * SIZE
buf = bytearray(BIH + xor_size + and_size)

struct.pack_into("<IiiHHIIiiII", buf, 0, BIH, SIZE, SIZE * 2, 1, 32, 0, 0, 0, 0, 0, 0)

for y in range(SIZE):                      # XOR 自下而上
    src_row = (SIZE - 1 - y) * SIZE * 4
    dst = BIH + y * SIZE * 4
    buf[dst:dst + SIZE * 4] = bgra[src_row:src_row + SIZE * 4]

for y in range(SIZE):                      # AND 掩码
    sy = SIZE - 1 - y
    for x in range(SIZE):
        if bgra[(sy * SIZE + x) * 4 + 3] == 0:
            idx = BIH + xor_size + y * mask_row + x // 8
            buf[idx] |= 1 << (7 - (x % 8))

# --- 3) CreateIconFromResourceEx --------------------------------------------
hicon = user32.CreateIconFromResourceEx(ctypes.c_char_p(bytes(buf)), len(buf),
                                        1, 0x00030000, 0, 0, 0)
print("\nHICON =", hicon)

# --- 4) DrawIconEx 到 32bpp DIB ---------------------------------------------
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


hdc_screen = user32.GetDC(0)
hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)

bi = BITMAPINFO()
bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bi.bmiHeader.biWidth = SIZE
bi.bmiHeader.biHeight = -SIZE
bi.bmiHeader.biPlanes = 1
bi.bmiHeader.biBitCount = 32
bi.bmiHeader.biCompression = 0

bits = ctypes.c_void_p()
hbmp = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
gdi32.SelectObject(hdc_mem, hbmp)

# 先用洋红铺底，便于识别「没画到」的地方
buf2 = (ctypes.c_ubyte * (SIZE * SIZE * 4)).from_address(bits.value)
for i in range(SIZE * SIZE):
    buf2[i * 4 + 0] = 0xFF
    buf2[i * 4 + 1] = 0x00
    buf2[i * 4 + 2] = 0xFF
    buf2[i * 4 + 3] = 0xFF

DI_NORMAL = 0x0003
ok = user32.DrawIconEx(hdc_mem, 0, 0, hicon, SIZE, SIZE, 0, None, DI_NORMAL)
print("DrawIconEx ok =", ok)


def px_dib(x, y):
    o = (y * SIZE + x) * 4
    return (buf2[o + 2], buf2[o + 1], buf2[o], buf2[o + 3])


print("\n=== HICON 经 DrawIconEx 画出来的结果（# = 白字，o = 色底，M = 洋红=未绘制）===")
for y in range(SIZE):
    row = []
    for x in range(SIZE):
        r, g, b, a = px_dib(x, y)
        if (r, g, b) == (0xFF, 0x00, 0xFF):
            row.append("M")
        elif r > 200 and g > 200 and b > 200:
            row.append("#")
        else:
            row.append("o")
    print("".join(row))

gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(0, hdc_screen)
