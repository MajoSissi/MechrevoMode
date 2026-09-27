"""Dump the 32x32 icon two ways for a side-by-side eyeball check.

  left :  img/logo.ico's own 32x32 image
  right:  what Windows renders from build/MechrevoMode.exe (via DrawIconEx)

Pure stdlib: a tiny PNG writer (zlib + struct), no Pillow needed.
Run:  python _research/_dump_icon_compare.py
"""
import ctypes
import struct
import sys
import zlib
from ctypes import wintypes

ICO = "img/logo.ico"
EXE = sys.argv[1] if len(sys.argv) > 1 else "build/MechrevoMode.exe"
SIZE = 32


def write_png(path, w, h, rgba):
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    open(path, "wb").write(png)


# ---- 1. the .ico's own 32x32 image (bottom-up BGRA) -------------------------
ico = open(ICO, "rb").read()
cnt = struct.unpack("<H", ico[4:6])[0]
target = None
for i in range(cnt):
    o = 6 + i * 16
    w, h, _, _, _, bpp, size, offset = struct.unpack("<BBBBHHII", ico[o:o + 16])
    if (w or 256) == SIZE and (h or 256) == SIZE:
        target = (offset, size, bpp)
assert target, "no 32x32 image in the .ico"
offset, size, bpp = target
px = ico[offset + 40:offset + 40 + SIZE * SIZE * 4]      # skip BITMAPINFOHEADER
out = bytearray(SIZE * SIZE * 4)
for y in range(SIZE):                                     # flip bottom-up
    src = (SIZE - 1 - y) * SIZE * 4
    for x in range(SIZE):
        b, g, r, a = px[src + x * 4: src + x * 4 + 4]
        d = (y * SIZE + x) * 4
        out[d:d + 4] = bytes((r, g, b, a))
write_png("build/_ico_source_32.png", SIZE, SIZE, bytes(out))
print("wrote build/_ico_source_32.png   (img/logo.ico @32x32)")

# ---- 2. what Windows renders from the exe (DrawIconEx) ----------------------
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                   ctypes.POINTER(wintypes.HICON),
                                   ctypes.POINTER(wintypes.HICON), wintypes.UINT]
big = wintypes.HICON()
small = wintypes.HICON()
n = shell32.ExtractIconExW(EXE, 0, ctypes.byref(big), ctypes.byref(small), 1)
print(f"ExtractIconExW({EXE}, 0) -> {n}")

w = h = SIZE
hdc = user32.GetDC(0)
memdc = gdi32.CreateCompatibleDC(hdc)
bmi = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
bits = ctypes.c_void_p()
hbmp = gdi32.CreateDIBSection(hdc, bmi, 0, ctypes.byref(bits), None, 0)
old = gdi32.SelectObject(memdc, hbmp)
gdi32.PatBlt(memdc, 0, 0, w, h, 0x00FF0062)              # WHITENESS
user32.DrawIconEx(memdc, 0, 0, big, w, h, 0, None, 3)
gdi32.SelectObject(memdc, old)
raw = bytearray(ctypes.string_at(bits, w * h * 4))
for i in range(0, len(raw), 4):
    raw[i], raw[i + 2] = raw[i + 2], raw[i]
write_png("build/_ico_from_exe_32.png", SIZE, SIZE, bytes(raw))
print("wrote build/_ico_from_exe_32.png (rendered from the exe)")

ink_src = sum(1 for p in range(0, len(out), 4) if out[p + 3] > 8)
ink_exe = sum(1 for p in range(3, len(raw), 4) if raw[p] > 8)
print(f"opaque pixels: ico={ink_src}/1024  exe={ink_exe}/1024")

gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(memdc)
user32.ReleaseDC(0, hdc)
user32.DestroyIcon(big)
user32.DestroyIcon(small)
