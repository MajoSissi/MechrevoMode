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

def send(payload):
    c.publish('Fan/Control', json.dumps(payload), qos=0)
    time.sleep(3.0)
    st = getstatus()
    return st

print('baseline:')
b = getstatus()
print('  mode=%s turboIdx=%s profile=%s table=%s' % (
    b.get('OperatingMode'), b.get('TurboProfileIndex'), b.get('ProfileName'), b.get('FAN_TableName')))

TRIALS = [
    {"Action": "OPERATING_TURBO_MODE"},
    {"Action": "OPERATING_TURBO_MODE", "SILENT": 1},
    {"Action": "OPERATING_TURBO_MODE", "SILENT": 0},
    {"Action": "OPERATING_TURBO_MODE", "EXTREME": 1},
    {"Action": "OPERATING_TURBO_MODE", "EXTREME": 0},
    {"Action": "OPERATING_TURBO_MODE", "Level": 1},
    {"Action": "OPERATING_TURBO_MODE", "Level": 0},
    {"Action": "OPERATING_TURBO_MODE", "TurboProfileIndex": 1},
    {"Action": "OPERATING_TURBO_MODE", "Silent": 1},
]

for t in TRIALS:
    st = send(t)
    print('%-58s -> mode=%s turboIdx=%-3s table=%-6s profile=%s' % (
        json.dumps(t, ensure_ascii=False), st.get('OperatingMode'),
        st.get('TurboProfileIndex'), st.get('FAN_TableName'), st.get('ProfileName')))

print()
print('restore CUSTOM')
st = send({"Action": "OPERATING_CUSTOM_MODE"})
print('  mode=%s profile=%s' % (st.get('OperatingMode'), st.get('ProfileName')))

c.loop_stop()
c.disconnect()
