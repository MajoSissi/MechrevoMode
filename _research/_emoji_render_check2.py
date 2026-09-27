# 复核上一个探测：是不是我自己的画法有问题？
#
# 三条正交验证：
#   1. 换三个 API（DrawTextW / ExtTextOutW / TextOutW）—— 排除某个 API 不做字体链接
#   2. 换四个字体（提示字体 / 菜单字体 / Segoe UI Emoji / Segoe UI）—— 排除字体选错
#   3. 用哨兵字符包起来画 "X🌡X"，看墨迹落在哪几列 —— 直接看出 emoji 那一格是不是空的
import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SPI_GETNONCLIENTMETRICS = 0x0029
ASCII_W = 64      # 画布宽度
CH = 40           # 画布高度
TRANSPARENT = 1


class LOGFONTW(ctypes.Structure):
    _fields_ = [("lfHeight", ctypes.c_long), ("lfWidth", ctypes.c_long),
                ("lfEscapement", ctypes.c_long), ("lfOrientation", ctypes.c_long),
                ("lfWeight", ctypes.c_long), ("lfItalic", ctypes.c_byte),
                ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte),
                ("lfCharSet", ctypes.c_byte), ("lfOutPrecision", ctypes.c_byte),
                ("lfClipPrecision", ctypes.c_byte), ("lfQuality", ctypes.c_byte),
                ("lfPitchAndFamily", ctypes.c_byte),
                ("lfFaceName", ctypes.c_wchar * 32)]


class NONCLIENTMETRICSW(ctypes.Structure):
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


ncm = NONCLIENTMETRICSW()
ncm.cbSize = ctypes.sizeof(ncm)
user32.SystemParametersInfoW(SPI_GETNONCLIENTMETRICS, ctypes.sizeof(ncm), ctypes.byref(ncm), 0)

FONTS = [("提示(lfStatusFont)", ncm.lfStatusFont),
         ("菜单(lfMenuFont)", ncm.lfMenuFont),
         ("消息(lfMessageFont)", ncm.lfMessageFont)]
for name in ("Segoe UI Emoji", "Segoe UI", "Segoe UI Symbol"):
    lf = LOGFONTW()
    lf.lfHeight = -12
    lf.lfCharSet = 1
    lf.lfFaceName = name
    FONTS.append(("显式 " + name, lf))

hdc_screen = user32.GetDC(0)
hdc = gdi32.CreateCompatibleDC(hdc_screen)
bmi = BMI()
bmi.bmiHeader.biSize = ctypes.sizeof(BMIH)
bmi.bmiHeader.biWidth = ASCII_W
bmi.bmiHeader.biHeight = -CH
bmi.bmiHeader.biPlanes = 1
bmi.bmiHeader.biBitCount = 32
bits = ctypes.c_void_p()
hbmp = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
old_bmp = gdi32.SelectObject(hdc, hbmp)
gdi32.SetBkMode(hdc, TRANSPARENT)
gdi32.SetTextColor(hdc, 0x000000)
buf = (ctypes.c_ubyte * (ASCII_W * CH * 4)).from_address(bits.value)


def ink_columns(text, api):
    ctypes.memset(bits, 0xFF, ASCII_W * CH * 4)
    r = ctypes.create_unicode_buffer(text)
    if api == "DrawTextW":
        user32.DrawTextW(hdc, r, len(text), ctypes.byref(wintypes.RECT(0, 0, ASCII_W, CH)), 0x20)
    elif api == "ExtTextOutW":
        gdi32.ExtTextOutW(hdc, 0, 0, 0, None, r, len(text), None)
    else:
        gdi32.TextOutW(hdc, 0, 0, r, len(text))
    gdi32.GdiFlush()
    data = bytes(buf)
    cols = []
    for x in range(ASCII_W):
        hit = False
        for y in range(CH):
            if data[(y * ASCII_W + x) * 4] < 0x80:
                hit = True
                break
        cols.append(1 if hit else 0)
    total = sum(cols)
    spans, start = [], None
    for x, c in enumerate(cols + [0]):
        if c and start is None:
            start = x
        elif not c and start is not None:
            spans.append((start, x - 1))
            start = None
    return total, spans


TARGETS = [("🌡 U+1F321", "\U0001F321", 2), ("🌀 U+1F300", "\U0001F300", 2),
           ("⚡ U+26A1", "\u26A1", 1), ("℃ U+2103", "\u2103", 1)]

for api in ("DrawTextW", "ExtTextOutW", "TextOutW"):
    print("=" * 78)
    print("API =", api)
    for fname, lf in FONTS:
        hf = gdi32.CreateFontIndirectW(ctypes.byref(lf))
        old = gdi32.SelectObject(hdc, hf)
        # "X<ch>X"：X 一定画得出来，用来定位 emoji 那一格
        line = []
        for label, ch, _ in TARGETS:
            tot, spans = ink_columns("X" + ch + "X", api)
            # 三个字形：X / ch / X。如果只有 2 段且总宽接近两个 X，说明中间是空的
            line.append("%s ink=%d spans=%s" % (label.split()[0], tot, spans))
        print("  %-22s | %s" % (fname, "  |  ".join(line)))
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(hf)
    print()

gdi32.SelectObject(hdc, old_bmp)
gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(hdc)
user32.ReleaseDC(0, hdc_screen)
print("提示字体 = %r" % ncm.lfStatusFont.lfFaceName)
print("菜单字体 = %r" % ncm.lfMenuFont.lfFaceName)
