# 验证配置用 emoji 在「shell 托盘提示用的那个字体」下能不能真的画出字形。
#
# 为什么要测：szTip 里的文本渲染完全归 Explorer 所有，读不回来、截图也截不到
# （见 skills/win32-native-gui-probe §15）。所以退一步，用 GDI 拿**同一个字体**
# 把字符画进 DIB，再和「确定不存在的码位」画出来的豆腐块比对：
# 像素完全一致 => 这个字符在这个字体下就是豆腐块，emoji 白加了。
#
# 字体取自 SystemParametersInfoW(SPI_GETNONCLIENTMETRICS).lfStatusFont ——
# 就是状态栏/气球提示/tooltip 那一档。
import ctypes
import hashlib
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SPI_GETNONCLIENTMETRICS = 0x0029
LF_FACESIZE = 32
DT_LEFT, DT_TOP, DT_SINGLELINE, DT_NOPREFIX = 0, 0, 0x20, 0x800
TRANSPARENT = 1

CELL = 40  # 每个字符一格 40x40


class LOGFONTW(ctypes.Structure):
    _fields_ = [
        ("lfHeight", ctypes.c_long), ("lfWidth", ctypes.c_long),
        ("lfEscapement", ctypes.c_long), ("lfOrientation", ctypes.c_long),
        ("lfWeight", ctypes.c_long), ("lfItalic", ctypes.c_byte),
        ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte),
        ("lfCharSet", ctypes.c_byte), ("lfOutPrecision", ctypes.c_byte),
        ("lfClipPrecision", ctypes.c_byte), ("lfQuality", ctypes.c_byte),
        ("lfPitchAndFamily", ctypes.c_byte),
        ("lfFaceName", ctypes.c_wchar * LF_FACESIZE),
    ]


class NONCLIENTMETRICSW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("iBorderWidth", ctypes.c_int), ("iScrollWidth", ctypes.c_int),
        ("iScrollHeight", ctypes.c_int), ("iCaptionWidth", ctypes.c_int),
        ("iCaptionHeight", ctypes.c_int),
        ("lfCaptionFont", LOGFONTW),
        ("iSmCaptionWidth", ctypes.c_int), ("iSmCaptionHeight", ctypes.c_int),
        ("lfSmCaptionFont", LOGFONTW),
        ("iMenuWidth", ctypes.c_int), ("iMenuHeight", ctypes.c_int),
        ("lfMenuFont", LOGFONTW), ("lfStatusFont", LOGFONTW),
        ("lfMessageFont", LOGFONTW),
        ("iPaddedBorderWidth", ctypes.c_int),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


print("LOGFONTW =", ctypes.sizeof(LOGFONTW), "(期望 92)")
print("NONCLIENTMETRICSW =", ctypes.sizeof(NONCLIENTMETRICSW), "(期望 504)")
assert ctypes.sizeof(LOGFONTW) == 92
assert ctypes.sizeof(NONCLIENTMETRICSW) == 504

ncm = NONCLIENTMETRICSW()
ncm.cbSize = ctypes.sizeof(ncm)
if not user32.SystemParametersInfoW(SPI_GETNONCLIENTMETRICS, ctypes.sizeof(ncm), ctypes.byref(ncm), 0):
    sys.exit("SystemParametersInfoW 失败: %d" % ctypes.get_last_error())

status = ncm.lfStatusFont
print("提示字体 = %r  height=%d  charset=%d" % (status.lfFaceName, status.lfHeight, status.lfCharSet))

hdc_screen = user32.GetDC(0)
hdc = gdi32.CreateCompatibleDC(hdc_screen)
hfont = gdi32.CreateFontIndirectW(ctypes.byref(status))
old_font = gdi32.SelectObject(hdc, hfont)

bmi = BITMAPINFO()
bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bmi.bmiHeader.biWidth = CELL
bmi.bmiHeader.biHeight = -CELL          # 负高度 = 自上而下
bmi.bmiHeader.biPlanes = 1
bmi.bmiHeader.biBitCount = 32
bmi.bmiHeader.biCompression = 0

bits = ctypes.c_void_p()
hbmp = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
old_bmp = gdi32.SelectObject(hdc, hbmp)
gdi32.SetBkMode(hdc, TRANSPARENT)
gdi32.SetTextColor(hdc, 0x000000)

buf = (ctypes.c_ubyte * (CELL * CELL * 4)).from_address(bits.value)


def render(ch):
    """把单个字符画进 DIB，返回 (有墨迹的列数, 像素数组的 sha)"""
    ctypes.memset(bits, 0xFF, CELL * CELL * 4)  # 白底
    r = ctypes.create_unicode_buffer(ch)
    # ★ DrawTextW 的 nCount 是 **UTF-16 码元数**，Python 的 len() 数的是**码位**。
    # 星平面字符（🌡 U+1F321）在 UTF-16 里占 2 个码元，用 len() 会少传一个，
    # 尾部被截断 —— 表现成「emoji 画不出来」的假象。别再用 len()。
    n_units = len(ch.encode("utf-16-le")) // 2
    user32.DrawTextW(hdc, r, n_units, ctypes.byref(wintypes.RECT(1, 1, CELL, CELL)),
                     DT_LEFT | DT_TOP | DT_SINGLELINE | DT_NOPREFIX)
    gdi32.GdiFlush()
    data = bytes(buf)
    cols = sum(1 for x in range(CELL)
               if any(data[(y * CELL + x) * 4] < 0x80 for y in range(CELL)))
    return cols, hashlib.sha256(data).hexdigest()[:16]


TOFU = "\U0010FFFD"          # 私用区最后一码位，任何字体都没有 —— 画出来是空心中括号
tofu_ink, tofu_hash = render(TOFU)
ctrl_ink, ctrl_hash = render("A")
print("\n参照：不存在的码位 U+10FFFD -> 墨迹列=%d sha=%s（空心中括号）" % (tofu_ink, tofu_hash))
print("参照：普通字符 'A'        -> 墨迹列=%d sha=%s\n" % (ctrl_ink, ctrl_hash))
if tofu_ink == 0:
    sys.exit("!! 豆腐块本身画不出来，这个测法不成立")
if ctrl_hash == tofu_hash:
    sys.exit("!! 对照组就撞上了，说明这个测法本身不成立")

CASES = [
    ("温度 🌡", "\U0001F321"),
    ("功耗 ⚡", "\u26A1"),
    ("风扇 🌀", "\U0001F300"),
    ("摄氏度 ℃", "\u2103"),
    ("模式图标 🍃", "\U0001F343"),
    ("模式图标 ❄", "\u2744"),
    ("模式图标 🎮", "\U0001F3AE"),
]

bad = 0
for name, ch in CASES:
    ink, h = render(ch)
    if h == tofu_hash:
        verdict = "豆腐块 ✗"
        bad += 1
    elif ink == 0:
        verdict = "完全空白 ✗"
        bad += 1
    else:
        verdict = "有字形 ✓"
    print("  %-14s U+%05X  ink=%-4d sha=%-16s %s" % (name, ord(ch), ink, h, verdict))

gdi32.SelectObject(hdc, old_bmp)
gdi32.SelectObject(hdc, old_font)
gdi32.DeleteObject(hbmp)
gdi32.DeleteObject(hfont)
gdi32.DeleteDC(hdc)
user32.ReleaseDC(0, hdc_screen)

print()
if bad:
    sys.exit("!! 有 %d 个字符在提示字体下画不出字形，需要换图标" % bad)
print("=> 全部字符都能在提示字体下渲染出真实字形，不是豆腐块。")
