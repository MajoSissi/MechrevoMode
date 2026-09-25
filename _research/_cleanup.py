import json, io, os, glob

p = r'C:/Users/Majo/AppData/Roaming/MechrevoMode/config.json'
d = json.load(io.open(p, encoding='utf-8'))
d['run_enabled'] = False
d['run_path'] = ''
d['run_args'] = ''
d['run_delay_sec'] = 5
io.open(p, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2))
print('已清理测试用启动命令（run_enabled=False, 延迟默认 5s）')

t = r'C:/Users/Majo/AppData/Roaming/MechrevoMode/run_test.txt'
if os.path.exists(t):
    os.remove(t)
    print('已删除 run_test.txt')

print()
print('=== 搜索 ryzenadj.exe ===')
roots = [
    r'C:/Users/Majo/Desktop',
    r'C:/Users/Majo/Downloads',
    r'C:/Users/Majo/Documents',
    r'D:/Tools',
    r'D:/',
    r'C:/Tools',
]
found = []
for r in roots:
    if not os.path.isdir(r):
        continue
    for pat in ('ryzenadj.exe', 'RyzenAdj.exe', 'RYZENADJ.EXE'):
        for f in glob.glob(os.path.join(r, '**', pat), recursive=True)[:5]:
            found.append(f)
for f in sorted(set(found)):
    print(' ', f)
if not found:
    print(' （常见目录里没找到）')
print()
print('PATH 中的 ryzenadj:')
import shutil
print(' ', shutil.which('ryzenadj') or '（不在 PATH 中）')
