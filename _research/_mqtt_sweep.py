import time, json
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
N = 0
state = {}

def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe('#', qos=0)

def on_message(client, userdata, msg):
    if msg.topic == 'Fan/Status':
        try:
            state['last'] = json.loads(msg.payload.decode('utf-8'))
        except Exception:
            pass

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{N}', protocol=mqtt.MQTTv311)
c.username_pw_set(f'UWPClient_User_{N}', f'UWPClient_Pwd888881772688_{N}')
c.on_connect = on_connect
c.on_message = on_message
c.connect(HOST, PORT, 30)
c.loop_start()
time.sleep(1.5)

def getstatus():
    state.pop('last', None)
    c.publish('Fan/Control', '{"Action":"GETSTATUS"}', qos=0)
    for _ in range(12):
        time.sleep(0.25)
        if 'last' in state:
            break
    return state.get('last', {})

base = getstatus()
print('BASE OperatingMode =', base.get('OperatingMode'))
base_mode = base.get('OperatingMode')

payloads = []
for i in (1, 2, 3, 4):
    payloads.append({"Action": f"UserSet_Mode{i}"})
for i in (1, 2, 3):
    payloads.append({"Action": f"SetSysPowerMode{i}"})
for v in (0, 1, 2, 3, 4):
    payloads.append({"Action": "SetFanMode", "FanMode": v})
    payloads.append({"Action": "SetFanMode", "Mode": v})
    payloads.append({"Action": "SET_FAN_MODE", "FanMode": v})
payloads += [
    {"Action": "SetOperatingMode", "OperatingMode": 0},
    {"Action": "SetOperatingMode", "OperatingMode": 1},
    {"Action": "SetOperatingMode", "OperatingMode": 2},
    {"Action": "SetOperatingMode", "OperatingMode": 3},
    {"Action": "UserSet_Mode_Detail"},
    {"Action": "SET_BALANCE_SMART"},
    {"Action": "SetFanMode"},
    {"Action": "SetSysPowerMode", "Mode": 1},
    {"Action": "SetSysPowerMode", "Mode": 2},
    {"Action": "SetSysPowerMode", "Mode": 3},
]

topics = ['Fan/Control', 'System/Control', 'Setting/Control']

changed = []
for topic in topics:
    for p in payloads:
        txt = json.dumps(p)
        before = getstatus().get('OperatingMode')
        c.publish(topic, txt, qos=0)
        time.sleep(1.6)
        after = getstatus()
        am = after.get('OperatingMode')
        mark = ''
        if am != before:
            mark = f'  <<<<<< CHANGED {before} -> {am}'
            changed.append((topic, txt, before, am))
        print(f'{topic:18} {txt[:60]:62} {before}->{am}{mark}')

print()
print('CHANGED:', changed)

# restore to base
print('restoring to', base_mode)
c.publish('Fan/Control', json.dumps({"Action": "UserSet_Mode4"}), qos=0)
time.sleep(3)
print('now', getstatus().get('OperatingMode'))
c.loop_stop()
c.disconnect()
