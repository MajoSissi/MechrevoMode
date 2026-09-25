import time
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688

COMBOS = []
for cid in ['UWPClient_3', 'UWPClient_0', 'UWPClient_1', 'UWPClient_2', 'UWPClient_4',
            'UWPClient_User_4', 'UWPClient', 'UWPSTDClient', 'UWPIntelClient', 'UWPADATAClient',
            'MyControlCenterUser', 'OcToolUser']:
    COMBOS.append((cid, None, None))
    COMBOS.append((cid, 'UWPClient_User_4', 'UWPClient_Pwd888881772688_4'))

seen_ok = []
for cid, user, pwd in COMBOS:
    msgs = []
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, protocol=mqtt.MQTTv311)
    if user:
        c.username_pw_set(user, pwd)

    def on_connect(client, userdata, flags, rc, properties=None):
        try:
            ok = (rc == 0 or str(rc) == 'Success')
        except Exception:
            ok = False
        if ok:
            client.subscribe('#', qos=0)
            print(f'  >>> SUCCESS cid={cid} user={user} rc={rc}')
            seen_ok.append((cid, user))

    def on_message(client, userdata, msg):
        msgs.append(msg)
        print(f'      MSG {msg.topic!r} : {msg.payload[:300]!r}')

    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(HOST, PORT, 20)
    except Exception as e:
        print(f'  connect err cid={cid}: {e}')
        continue
    c.loop_start()
    time.sleep(1.5)
    c.loop_stop()
    try:
        c.disconnect()
    except Exception:
        pass

print()
print('WORKING:', seen_ok)
