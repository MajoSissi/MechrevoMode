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
    return getstatus()

for t in [
    {"Action": "OPERATING_CUSTOM_MODE", "ProfileIndex": 0},
    {"Action": "OPERATING_CUSTOM_MODE", "ProfileIndex": 1},
    {"Action": "OPERATING_CUSTOM_MODE", "ProfileIndex": 4},
    {"Action": "OPERATING_GAMING_MODE", "ProfileIndex": 1},
    {"Action": "OPERATING_OFFICE_MODE", "ProfileIndex": 1},
    {"Action": "OPERATING_TURBO_MODE", "ProfileIndex": 0},
]:
    st = send(t)
    print('%-58s -> mode=%s G=%s O=%s T=%s C=%s table=%-6s profile=%s' % (
        json.dumps(t), st.get('OperatingMode'), st.get('GamingProfileIndex'),
        st.get('OfficeProfileIndex'), st.get('TurboProfileIndex'), st.get('CustomProfileIndex'),
        st.get('FAN_TableName'), st.get('ProfileName')))

print()
print('restore CUSTOM ProfileIndex 0')
st = send({"Action": "OPERATING_CUSTOM_MODE", "ProfileIndex": 0})
print('  mode=%s customIdx=%s profile=%s table=%s' % (
    st.get('OperatingMode'), st.get('CustomProfileIndex'), st.get('ProfileName'), st.get('FAN_TableName')))
c.loop_stop()
c.disconnect()
