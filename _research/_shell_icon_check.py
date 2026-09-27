"""Ask Windows itself what icon it sees in the built exe.

ExtractIconExW is the same call the Shell uses to populate Explorer's file icon,
so a positive count here is the end-to-end proof that the icon is embedded and
readable -- not just "a resource directory exists".

Also dumps the icon back out as a PNG for eyeballing.
Run:  python _research/_shell_icon_check.py [exe]
"""
import ctypes
import struct
import sys
from ctypes import wintypes

EXE = sys.argv[1] if len(sys.argv) > 1 else r"build\MechrevoMode.exe"

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                   ctypes.POINTER(wintypes.HICON),
                                   ctypes.POINTER(wintypes.HICON), wintypes.UINT]
shell32.ExtractIconExW.restype = wintypes.UINT

# How many icons does the Shell see in this file?
total = shell32.ExtractIconExW(EXE, -1, None, None, 0)
print(f"{EXE}: Shell reports {total} icon(s)")

if total:
    big = wintypes.HICON()
    small = wintypes.HICON()
    got = shell32.ExtractIconExW(EXE, 0, ctypes.byref(big), ctypes.byref(small), 1)
    print(f"  ExtractIconExW(index=0) -> {got}   large={big.value} small={small.value}")

    # Redraw the large icon into a 32x32 DIB and dump it so it can be eyeballed.
    w = h = 32
    hdc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmi = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc, bmi, 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(memdc, hbmp)
    ok = user32.DrawIconEx(memdc, 0, 0, wintypes.HICON(big.value), w, h, 0, None, 3)
    gdi32.SelectObject(memdc, old)

    if ok and bits:
        raw = ctypes.string_at(bits, w * h * 4)
        px = bytearray(raw)
        for i in range(0, len(px), 4):          # BGRA -> RGBA
            px[i], px[i + 2], px[i + 2] = px[i + 2], px[i], px[i]
        try:
            from PIL import Image
            Image.frombytes("RGBA", (w, h), bytes(px)).save("build/_icon_preview.png")
            print("  dumped build/_icon_preview.png (32x32, for eyeballing)")
        except ImportError:
            # Fall back to an ASCII preview: unambiguously shows "not blank".
            ink = sum(1 for i in range(3, len(px), 4) if px[i] > 8)
            print(f"  {ink}/{w*h} pixels non-transparent")
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(0, hdc)
    user32.DestroyIcon(big)
    user32.DestroyIcon(small)
else:
    print("  FAIL: the Shell sees no icon in this executable")
    sys.exit(1)
