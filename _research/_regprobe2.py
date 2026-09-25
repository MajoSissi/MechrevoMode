import winreg, sys, ctypes, ctypes.wintypes as w

print('########## HKLM\\SOFTWARE\\OEM (full) ##########')
def walk(root, path, depth=0, maxdepth=5):
    try:
        k = winreg.OpenKey(root, path, 0, winreg.KEY_READ)
    except OSError as e:
        print('  ' * depth + f'  <open fail {e}>')
        return
    i = 0
    while True:
        try:
            name, val, typ = winreg.EnumValue(k, i)
        except OSError:
            break
        print('  ' * depth + f'  {name} = {val!r}')
        i += 1
    if depth >= maxdepth:
        return
    j = 0
    while True:
        try:
            sub = winreg.EnumKey(k, j)
        except OSError:
            break
        print('  ' * depth + f'[{sub}]')
        walk(root, path + '\\' + sub, depth + 1, maxdepth)
        j += 1

for base in [r'SOFTWARE\OEM', r'SOFTWARE\OEM\MyControlCenter', r'SOFTWARE\OEM\GCUService']:
    print('=' * 70)
    print('HKLM\\' + base)
    walk(winreg.HKEY_LOCAL_MACHINE, base, 0, 4)

print()
print('########## process paths ##########')
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
k32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)

import subprocess
out = subprocess.run(['tasklist', '/fo', 'csv', '/nh'], capture_output=True, text=True).stdout
for line in out.splitlines():
    parts = [p.strip('"') for p in line.split('","')]
    if not parts:
        continue
    name = parts[0].strip('"')
    if any(x in name.lower() for x in ('gcu', 'control', 'aistone', 'systraycomponent', 'osdtp')):
        try:
            pid = int(parts[1])
        except Exception:
            continue
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            buf = ctypes.create_unicode_buffer(32768)
            size = w.DWORD(32768)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                print(f'{name} (pid {pid}) -> {buf.value}')
            k32.CloseHandle(h)
        else:
            print(f'{name} (pid {pid}) -> <access denied>')
