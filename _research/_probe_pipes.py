import os
try:
    pipes = os.listdir('\\\\.\\pipe\\')
except Exception as e:
    print('ERR', e)
    raise SystemExit
print('TOTAL', len(pipes))
kw = ('gcu', 'mech', 'control', 'gaming', 'aistone', 'fan', 'mycontrol', 'oem', 'bridge')
for p in sorted(pipes):
    print(p)
print('---- MATCHES ----')
for p in sorted(pipes):
    if any(k in p.lower() for k in kw):
        print('  *', p)
