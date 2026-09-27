"""Verify the icon embedded in a PE (generated from img/logo.ico by rsrc).

Checks, all read-only:
  1. the PE has a non-empty resource directory with an RT_ICON (3) type
  2. the resource directory actually contains RT_GROUP_ICON (14)
  3. every image inside img/logo.ico appears byte-for-byte inside the exe
     (proves the icon was embedded, not that a directory entry merely exists)

Run:  python _research/_verify_exe_icon.py [exe] [ico]
"""
import struct
import sys

EXE = sys.argv[1] if len(sys.argv) > 1 else "build/MechrevoMode.exe"
ICO = sys.argv[2] if len(sys.argv) > 2 else "img/logo.ico"

d = open(EXE, "rb").read()
pe = struct.unpack("<I", d[0x3C:0x40])[0]
assert d[pe:pe + 4] == b"PE\0\0", "not a PE file"
nsec = struct.unpack("<H", d[pe + 6:pe + 8])[0]
opt = pe + 24
magic = struct.unpack("<H", d[opt:opt + 2])[0]
optlen = 240 if magic == 0x20B else 224
dd = opt + (112 if magic == 0x20B else 96)
rva, sz = struct.unpack("<II", d[dd + 2 * 8: dd + 2 * 8 + 8])
print(f"{EXE}")
print(f"  resource directory: rva={rva:#x}  size={sz}")
assert rva and sz, "FAIL: resource directory is empty -> no icon in the exe"

secs = []
for i in range(nsec):
    so = opt + optlen + i * 40
    name = d[so:so + 8].rstrip(b"\0").decode(errors="replace")
    vs, va, rs, ro = struct.unpack("<IIII", d[so + 8:so + 24])
    secs.append((name, vs, va, rs, ro))


def rva2off(r):
    for name, vs, va, rs, ro in secs:
        if va <= r < va + max(vs, rs):
            return ro + (r - va)
    return None


base = rva2off(rva)
root_va = rva

# Resource tree: type (level 0) -> id (level 1) -> lang (level 2, leaf).
types = set()
groups = 0


def walk(off, level, type_id):
    global groups
    if level > 2:
        return
    nn, ni = struct.unpack("<HH", d[off + 12:off + 16])
    for k in range(nn + ni):
        e = off + 16 + k * 8
        nid, sub = struct.unpack("<II", d[e:e + 8])
        cur = nid & 0x7FFFFFFF
        if sub & 0x80000000:                       # subdirectory
            walk(base + (sub & 0x7FFFFFFF) - root_va, level + 1,
                 cur if level == 0 else type_id)
        else:                                      # leaf -> data entry
            if level == 0:
                types.add(cur)
            if level == 1 and type_id == 14:
                groups += 1


walk(base, 0, None)
print(f"  RT_ICON present:      {3 in types}")
print(f"  RT_GROUP_ICON present:{14 in types}  (groups={groups})")
assert 3 in types, "FAIL: no RT_ICON (3) resource"
assert 14 in types, "FAIL: no RT_GROUP_ICON (14) -- Explorer needs the group"

# Ground truth: every image inside the .ico must be present verbatim.
ico = open(ICO, "rb").read()
cnt = struct.unpack("<H", ico[4:6])[0]
print(f"{ICO}: {cnt} images")
ok = 0
for i in range(cnt):
    o = 6 + i * 16
    w, h, _, _, _, bpp, size, offset = struct.unpack("<BBBBHHII", ico[o:o + 16])
    blob = ico[offset:offset + size]
    hit = d.find(blob)
    tag = f"{w or 256}x{h or 256}/{bpp}bpp"
    if hit >= 0:
        ok += 1
        print(f"  {tag:12s} {size:6d} bytes  found at {hit:#x}  OK")
    else:
        print(f"  {tag:12s} {size:6d} bytes  NOT FOUND  <-- embedded icon differs")

assert ok == cnt, f"FAIL: only {ok}/{cnt} icon images are embedded"
print(f"\nPASS: resource directory present, RT_ICON+RT_GROUP_ICON set, "
      f"all {cnt} images byte-identical to {ICO}")
