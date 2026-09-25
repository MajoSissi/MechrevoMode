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
time.sleep(1.2)

def getstatus(timeout=6.0):
    state.pop('last', None)
    c.publish('Fan/Control', '{"Action":"GETSTATUS"}', qos=0)
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.2)
        if 'last' in state:
            return state['last']
    return {}

def send(payload, topic='Fan/Control'):
    c.publish(topic, json.dumps(payload), qos=0)
    time.sleep(3.0)
    return getstatus()

# enter turbo first
send({"Action": "OPERATING_TURBO_MODE"})
b = getstatus()
print('turbo baseline: mode=%s turboIdx=%s table=%s profile=%s' % (
    b.get('OperatingMode'), b.get('TurboProfileIndex'), b.get('FAN_TableName'), b.get('ProfileName')))
print()

TRIALS = [
    {"Action": "SilentPerformance"},
    {"Action": "OverClocking"},
    {"Action": "SILENT"},
    {"Action": "EXTREME"},
    {"Action": "SilentPerformanceMode"},
    {"Action": "OPERATING_TURBO_SILENT_MODE"},
    {"Action": "OPERATING_TURBO_EXTREME_MODE"},
    {"Action": "UserSet_TurboMode_Lev", "Level": 1},
    {"Action": "SetTurboModeLevel", "Level": 1},
    {"Action": "SET_TURBO_LEVEL", "Level": 1},
    {"Action": "OPERATING_TURBO_MODE", "TurboSubMode": 1},
    {"Action": "OPERATING_TURBO_MODE", "SubMode": 1},
    {"Action": "OPERATING_TURBO_MODE", "ProfileIndex": 1},
]

hits = []
for t in TRIALS:
    st = send(t)
    tag = f"mode={st.get('OperatingMode')} turboIdx={st.get('TurboProfileIndex')} table={st.get('FAN_TableName')}"
    print('%-58s -> %s' % (json.dumps(t), tag))
    if st.get('TurboProfileIndex') not in (b.get('TurboProfileIndex'), None):
        hits.append((json.dumps(t), tag))

print()
print('HITS:', hits)

print()
print('restore CUSTOM')
st = send({"Action": "OPERATING_CUSTOM_MODE"})
print('  mode=%s profile=%s' % (st.get('OperatingMode'), st.get('ProfileName')))
c.loop_stop()
c.disconnect()
