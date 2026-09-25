# 列出 PE 文件的**静态导入**（硬依赖）DLL 名单。
#
# 用途：判断 ryzenadj.exe 到底是「硬依赖 WinRing0x64.dll」还是「运行时动态加载」。
# 二者的区别很关键：硬依赖缺失 → 进程起不来（0xC0000135 DLL_NOT_FOUND）；
# 动态加载缺失 → 进程能起，只是功能退化。实测里前者出现了，
# 说明把 WinRing0x64.dll 拿掉就根本跑不起来，跟「优先用哪个驱动」是两回事。
#
# 只解析导入描述符，不解析导出表，够用且比上第三方库轻。
import struct
import sys


def imports(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"MZ":
        return ["<不是 PE 文件>"]
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        return ["<PE 头异常>"]
    coff = e_lfanew + 4
    machine, = struct.unpack_from("<H", data, coff)
    opt_off = coff + 20
    magic, = struct.unpack_from("<H", data, opt_off)
    pe32p = (magic == 0x20B)
    # DataDirectory 偏移：PE32 在可选头 +96，PE32+ 在 +112
    dd_off = opt_off + (112 if pe32p else 96)
    import_rva, import_size = struct.unpack_from("<II", data, dd_off + 8)

    # RVA -> 文件偏移
    nsec, = struct.unpack_from("<H", data, coff + 2)
    optsize, = struct.unpack_from("<H", data, coff + 16)
    sec_off = opt_off + optsize
    secs = []
    for i in range(nsec):
        s = sec_off + i * 40
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, s)
        secs.append((vaddr, vsize, raddr, rsize))

    def rva2off(rva):
        for vaddr, vsize, raddr, rsize in secs:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return raddr + (rva - vaddr)
        return None

    out = []
    off = rva2off(import_rva)
    if off is None:
        return ["<没有导入表>"]
    # 导入目录以「全零的 20 字节描述符」结束。除了判零，还必须加边界保护：
    # 万一 RVA 映射算错，光靠判零会一直读下去直到越界（实测就撞到了）。
    end_off = off + max(import_size, 20)
    while off + 20 <= min(end_off, len(data)):
        entry = struct.unpack_from("<IIIII", data, off)
        if entry[3] == 0:
            break
        noff = rva2off(entry[3])
        if noff is None:
            out.append("<名称 RVA 映射失败 0x%X>" % entry[3])
            off += 20
            continue
        z = data.index(b"\0", noff)
        out.append(data[noff:z].decode("ascii", "replace"))
        off += 20
    return out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print("=== %s" % p)
        for n in imports(p):
            print("   ", n)
