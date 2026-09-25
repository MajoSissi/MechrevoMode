import json, io

p = r'C:/Users/Majo/AppData/Roaming/MechrevoMode/config.json'
d = json.load(io.open(p, encoding='utf-8'))
d['run_enabled'] = True
d['run_path'] = r'C:\Windows\System32\cmd.exe'
d['run_args'] = '/c echo mechrevo-run-ok > "%APPDATA%\\MechrevoMode\\run_test.txt"'
d['run_delay_sec'] = 2
io.open(p, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2))
print('run_path =', d['run_path'])
print('run_args =', d['run_args'])
