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

def switch(action):
    c.publish('Fan/Control', json.dumps({"Action": action}), qos=0)
    time.sleep(3.0)
    return getstatus()

print('BASELINE:')
b = getstatus()
print('  OperatingMode=%s Profile=%s Table=%s PL1=%s' % (
    b.get('OperatingMode'), b.get('ProfileName'), b.get('FAN_TableName'), b.get('CPU_PL1')))

ACTIONS = [
    'OPERATING_OFFICE_MODE',
    'OPERATING_GAMING_MODE',
    'OPERATING_TURBO_MODE',
    'OPERATING_CUSTOM_MODE',
]

results = []
for a in ACTIONS:
    st = switch(a)
    row = (a, st.get('OperatingMode'), st.get('ProfileName'), st.get('FAN_TableName'),
           st.get('CPU_PL1'), st.get('GPU_ConfigurableTGPTarget'), st.get('OverClockingSwitch'))
    results.append(row)
    print('%-26s -> OperatingMode=%-4s Profile=%-16s Table=%-6s PL1=%-5s TGP=%s OC=%s' % row)

print()
print('=== MAPPING ===')
for a, m, p, t, pl1, tgp, oc in results:
    print(f'{a:26} = OperatingMode {m}')

# restore to custom (user's original)
print()
print('restoring to CUSTOM ...')
st = switch('OPERATING_CUSTOM_MODE')
print('  now OperatingMode=%s Profile=%s Table=%s' % (
    st.get('OperatingMode'), st.get('ProfileName'), st.get('FAN_TableName')))

c.loop_stop()
c.disconnect()
