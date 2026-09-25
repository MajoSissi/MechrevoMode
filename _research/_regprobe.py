import winreg, sys

def walk(root, path, depth=0, maxdepth=4):
    try:
        k = winreg.OpenKey(root, path, 0, winreg.KEY_READ)
    except OSError as e:
        print('  ' * depth + f'[{path}] OPEN FAIL {e}')
        return
    # values
    i = 0
    while True:
        try:
            name, val, typ = winreg.EnumValue(k, i)
        except OSError:
            break
        print('  ' * depth + f'  {name} = {val!r}  (type {typ})')
        i += 1
    if depth >= maxdepth:
        return
    j = 0
    while True:
        try:
            sub = winreg.EnumKey(k, j)
        except OSError:
            break
        print('  ' * depth + f'[{path}\\{sub}]')
        walk(root, path + '\\' + sub, depth + 1, maxdepth)
        j += 1

ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\MyControlCenter'),
    (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\MyControlCenter'),
    (winreg.HKEY_CURRENT_USER, r'SOFTWARE\MyControlCenter'),
    (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\L-Mechrevo'),
    (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\AISTONE'),
    (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Mechrevo'),
]

names = {winreg.HKEY_LOCAL_MACHINE: 'HKLM', winreg.HKEY_CURRENT_USER: 'HKCU'}
for root, path in ROOTS:
    print('=' * 70)
    print(f'{names[root]}\\{path}')
    # first list subkeys of parent to see what exists
    try:
        parent = path.rsplit('\\', 1)[0]
        leaf = path.rsplit('\\', 1)[1] if '\\' in path else path
        pk = winreg.OpenKey(root, parent, 0, winreg.KEY_READ)
        subs = []
        j = 0
        while True:
            try:
                subs.append(winreg.EnumKey(pk, j))
            except OSError:
                break
            j += 1
        print(f'  (siblings of {leaf}: {[s for s in subs if "control" in s.lower() or "gcu" in s.lower() or "mech" in s.lower() or "aistone" in s.lower() or "oem" in s.lower()]})')
    except OSError as e:
        print(f'  parent open fail: {e}')
    walk(root, path, 0, 3)
