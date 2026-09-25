import re, sys, os

def strings(path, minlen=5, limit=400):
    with open(path, 'rb') as f:
        data = f.read()
    out = []
    # ASCII
    for m in re.finditer(rb'[\x20-\x7e]{%d,}' % minlen, data):
        out.append(m.group().decode('ascii', 'replace'))
    # UTF-16LE
    for m in re.finditer(rb'(?:[\x20-\x7e]\x00){%d,}' % minlen, data):
        out.append(m.group().decode('utf-16le', 'replace'))
    return out

if __name__ == '__main__':
    p = sys.argv[1]
    kw = sys.argv[2].split(',') if len(sys.argv) > 2 else None
    ss = strings(p)
    print('total strings:', len(ss))
    if kw:
        seen = set()
        for s in ss:
            sl = s.lower()
            if any(k.lower() in sl for k in kw):
                if s not in seen:
                    seen.add(s)
                    print(repr(s[:300]))
    else:
        for s in ss[:300]:
            print(repr(s[:300]))
