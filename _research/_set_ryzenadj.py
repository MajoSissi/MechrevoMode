import json, io

p = r'C:/Users/Majo/AppData/Roaming/MechrevoMode/config.json'
d = json.load(io.open(p, encoding='utf-8'))
d['run_enabled'] = True
d['run_path'] = r'D:\User\OneDrive\Programm\RyzenAdj\ryzenadj.exe'
d['run_args'] = '--set-coall=-25'
d['run_delay_sec'] = 8
io.open(p, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2))
print('ok')
