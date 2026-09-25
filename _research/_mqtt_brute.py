import time, itertools
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688

ids = []
for n in range(0, 8):
    ids.append(f'UWPClient_{n}')
ids += ['UWPClient', 'UWPClient_User_4', 'UWPSTDClient', 'UWPIntelClient', 'UWPADATAClient',
        'UWPClient_26', 'MyControlCenterUser', 'OcToolUser', 'MyControlCenter', 'MyTrayClient',
        'PluginClient', 'MyKeyboard', 'OcScannerUser', 'OpenCLUser', 'MyTPDetectorUser',
        'MyDynamicDesktopUser']

USERS = [(None, None),
         ('UWPClient_User_4', 'UWPClient_Pwd888881772688_4'),
         ('UWPClient_User_4', None),
         (None, 'UWPClient_Pwd888881772688_4')]

ok = []
for cid in ids:
    for user, pwd in USERS:
        res = {'rc': None}
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, protocol=mqtt.MQTTv311)
        if user:
            c.username_pw_set(user, pwd)

        def on_connect(client, userdata, flags, rc, properties=None, res=res):
            res['rc'] = rc
            if str(rc) in ('0', 'Success') or getattr(rc, 'value', None) == 0:
                client.subscribe('#', qos=0)

        c.on_connect = on_connect
        try:
            c.connect(HOST, PORT, 10)
        except Exception as e:
            print(f'cid={cid!r} user={user!r} connect-exception {e}')
            continue
        c.loop_start()
        time.sleep(0.8)
        c.loop_stop()
        try:
            c.disconnect()
        except Exception:
            pass
        rc = res['rc']
        status = 'OK  ' if (str(rc) in ('0', 'Success')) else 'FAIL'
        print(f'[{status}] cid={cid!r:28} user={str(user)!r:22} rc={rc}')
        if status == 'OK  ':
            ok.append((cid, user, pwd))

print()
print('WORKING COMBOS:', ok)
