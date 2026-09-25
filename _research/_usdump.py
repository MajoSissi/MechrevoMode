import sys, dnfile

path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Tools\L-Mechrevo\L-Mechrevo.dll"
pe = dnfile.dnPE(path)
us = pe.net.metadata.streams.get(b'#US')
data = bytes(us.__data__) if hasattr(us, '__data__') else None
if data is None:
    data = us.get_data() if hasattr(us, 'get_data') else None

out = []
i = 1
n = len(data)
while i < n:
    # compressed length
    b0 = data[i]
    if b0 == 0:
        i += 1
        continue
    if b0 & 0x80 == 0:
        ln = b0
        i += 1
    elif b0 & 0xC0 == 0x80:
        ln = ((b0 & 0x3F) << 8) | data[i+1]
        i += 2
    else:
        ln = ((b0 & 0x1F) << 24) | (data[i+1] << 16) | (data[i+2] << 8) | data[i+3]
        i += 4
    if ln == 0 or i + ln > n:
        i += 1
        continue
    raw = data[i:i+ln]
    try:
        s = raw.decode('utf-16le', 'replace')
    except Exception:
        s = repr(raw)
    out.append(s[:-1] if s.endswith('\x00') else s)
    i += ln

print('COUNT', len(out), file=sys.stderr)
for s in out:
    print(repr(s))
