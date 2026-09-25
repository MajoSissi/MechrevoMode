import time, json
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
N = 7

seen = {}

def on_connect(client, userdata, flags, rc, properties=None):
    print('connected rc=', rc)
    client.subscribe('#', qos=0)

def on_message(client, userdata, msg):
    try:
        p = msg.payload.decode('utf-8')
    except Exception:
        p = repr(msg.payload)
    seen[msg.topic] = p
    print(f'<< {msg.topic} | {p[:800]}')

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{N}', protocol=mqtt.MQTTv311)
c.username_pw_set(f'UWPClient_User_{N}', f'UWPClient_Pwd888881772688_{N}')
c.on_connect = on_connect
c.on_message = on_message
c.connect(HOST, PORT, 30)
c.loop_start()
time.sleep(2.0)

c.publish('Fan/Control', '{"Action":"GETSTATUS"}', qos=0)
time.sleep(3.0)

c.loop_stop()
c.disconnect()

print()
print('=== topics ===')
for t in sorted(seen):
    print(t, '=>', len(seen[t]), 'bytes')

print()
print('=== Fan/Status full ===')
fs = seen.get('Fan/Status')
if fs:
    try:
        obj = json.loads(fs)
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        print()
        print('KEYS:', sorted(obj.keys()) if isinstance(obj, dict) else type(obj))
    except Exception as e:
        print('raw:', fs, 'err', e)
else:
    print('(none)')
