# 把「到底有没有画出字形」直接画成 ASCII 位图，不再靠间接推断。
#
# 前面两次探测给出了互相矛盾的结论（单字符 ink=0，但 "X🌡X" 中间那格却有 12 列墨迹），
# 必然是画法上有差别。这里统一成**一条**代码路径，把每个样例的墨迹图打出来，
# 一眼就能看出字形是真的、空白、还是豆腐块。
import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SPI_GETNONCLIENTMETRICS = 0x0029
W, H = 96, 20
TRANSPARENT = 1
DT_SINGLELINE, DT_NOPREFIX = 0x20, 0x800


class LOGFONTW(ctypes.Structure):
    _fields_ = [("lfHeight", ctypes.c_long), ("lfWidth", ctypes.c_long),
                ("lfEscapement", ctypes.c_long), ("lfOrientation", ctypes.c_long),
                ("lfWeight", ctypes.c_long), ("lfItalic", ctypes.c_byte),
                ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte),
                ("lfCharSet", ctypes.c_byte), ("lfOutPrecision", ctypes.c_byte),
                ("lfClipPrecision", ctypes.c_byte), ("lfQuality", ctypes.c_byte),
                ("lfPitchAndFamily", ctypes.c_byte), ("lfFaceName", ctypes.c_wchar * 32)]


class NCM(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT),
                ("iBorderWidth", ctypes.c_int), ("iScrollWidth", ctypes.c_int),
                ("iScrollHeight", ctypes.c_int), ("iCaptionWidth", ctypes.c_int),
                ("iCaptionHeight", ctypes.c_int), ("lfCaptionFont", LOGFONTW),
                ("iSmCaptionWidth", ctypes.c_int), ("iSmCaptionHeight", ctypes.c_int),
                ("lfSmCaptionFont", LOGFONTW),
                ("iMenuWidth", ctypes.c_int), ("iMenuHeight", ctypes.c_int),
                ("lfMenuFont", LOGFONTW), ("lfStatusFont", LOGFONTW),
                ("lfMessageFont", LOGFONTW), ("iPaddedBorderWidth", ctypes.c_int)]


class BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", BMIH), ("bmiColors", wintypes.DWORD * 3)]


ncm = NCM()
ncm.cbSize = ctypes.sizeof(ncm)
user32.SystemParametersInfoW(SPI_GETNONCLIENTMETRICS, ctypes.sizeof(ncm), ctypes.byref(ncm), 0)
lf_status = ncm.lfStatusFont
print("提示字体 %r height=%d" % (lf_status.lfFaceName, lf_status.lfHeight))

screen = user32.GetDC(0)
memdc = gdi32.CreateCompatibleDC(screen)
bmi = BMI()
bmi.bmiHeader.biSize = ctypes.sizeof(BMIH)
bmi.bmiHeader.biWidth = W
bmi.bmiHeader.biHeight = -H
bmi.bmiHeader.biPlanes = 1
bmi.bmiHeader.biBitCount = 32
bits = ctypes.c_void_p()
hbmp = gdi32.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
gdi32.SelectObject(memdc, hbmp)
gdi32.SetBkMode(memdc, TRANSPARENT)
gdi32.SetTextColor(memdc, 0x000000)
buf = (ctypes.c_ubyte * (W * H * 4)).from_address(bits.value)


def draw(text):
    ctypes.memset(bits, 0xFF, W * H * 4)
    r = ctypes.create_unicode_buffer(text)
    # ★ 坑：DrawTextW 的 nCount 是 **UTF-16 码元数**，而 Python 的 len() 数的是**码位**。
    # 星平面字符（🌡 U+1F321 之类）在 UTF-16 里占 2 个码元，用 len() 就会少传一个，
    # 字符串被从尾部截断 —— 表现是「emoji 画不出来」的假象，害我白查两轮。
    # 正确算法：按 utf-16-le 编出来的字节数除以 2。
    n_units = len(text.encode("utf-16-le")) // 2
    rect = wintypes.RECT(0, 0, W, H)
    user32.DrawTextW(memdc, r, n_units, ctypes.byref(rect), DT_SINGLELINE | DT_NOPREFIX)
    gdi32.GdiFlush()
    data = bytes(buf)
    rows = []
    for y in range(H):
        line = "".join("#" if data[(y * W + x) * 4] < 0x80 else "." for x in range(W))
        rows.append(line)
    inkcols = [x for x in range(W) if any(rows[y][x] == "#" for y in range(H))]
    return rows, len(inkcols)


def show(title, text):
    rows, n = draw(text)
    # 裁掉尾部空白行，只留 y=0..maxink
    last = max((y for y in range(H) if "#" in rows[y]), default=-1)
    print("\n--- %s  文字=%r  有墨迹的列数=%d" % (title, text, n))
    for y in range(0, last + 1):
        print("   " + rows[y][:60])


TOFU = "\U0010FFFD"
show("对照组：普通字符 A", "A")
show("对照组：不存在码位 U+10FFFD 单独画", TOFU)
show("对照组：X + 不存在码位 + X", "X" + TOFU + "X")
show("待测：🌡 单独画", "\U0001F321")
show("待测：X + 🌡 + X", "X\U0001F321X")
show("待测：🌀 单独画", "\U0001F300")
show("待测：X + 🌀 + X", "X\U0001F300X")
show("待测：⚡ 单独画", "\u26A1")
show("待测：℃ 单独画", "\u2103")

gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(memdc)
user32.ReleaseDC(0, screen)
