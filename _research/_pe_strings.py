# 从 PE 文件里抽出可打印字符串（ASCII + UTF-16LE），输出到 txt。
#
# 环境里没有 binutils 的 strings，自己扫一遍就够了：
# 连续 >=minlen 个可打印字符算一个字符串。UTF-16LE 那趟要把低字节和高字节拆开看。
import re
import sys

MINLEN = 5


def ascii_strings(data, minlen=MINLEN):
    out = []
    cur = bytearray()
    for b in data:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= minlen:
                out.append(cur.decode("ascii", "replace"))
            cur = bytearray()
    if len(cur) >= minlen:
        out.append(cur.decode("ascii", "replace"))
    return out


def utf16_strings(data, minlen=MINLEN):
    out = []
    cur = []
    for i in range(0, len(data) - 1, 2):
        lo, hi = data[i], data[i + 1]
        if hi == 0 and 32 <= lo < 127:
            cur.append(chr(lo))
        else:
            if len(cur) >= minlen:
                out.append("".join(cur))
            cur = []
    if len(cur) >= minlen:
        out.append("".join(cur))
    return out


def main():
    path = sys.argv[1]
    out_path = sys.argv[2]
    minlen = int(sys.argv[3]) if len(sys.argv) > 3 else MINLEN
    data = open(path, "rb").read()
    lines = []
    lines.append("# ASCII strings from %s" % path)
    lines += ascii_strings(data, minlen)
    lines.append("")
    lines.append("# UTF-16LE strings from %s" % path)
    lines += utf16_strings(data, minlen)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("写入 %s（%d 行）" % (out_path, len(lines)))


if __name__ == "__main__":
    main()
