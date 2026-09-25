import time, json, sys, re
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
N = 0

state = {}

def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe('#', qos=0)

def on_message(client, userdata, msg):
    if msg.topic in ('Fan/Status', 'Tray/Status'):
        try:
            j = json.loads(msg.payload.decode('utf-8'))
        except Exception:
            return
        if msg.topic == 'Fan/Status':
            state['last'] = j
            print(f"      [Fan/Status] OperatingMode={j.get('OperatingMode')} "
                  f"ProfileName={j.get('ProfileName')} Table={j.get('FAN_TableName')} "
                  f"Gaming={j.get('GamingProfileIndex')} Office={j.get('OfficeProfileIndex')} "
                  f"Turbo={j.get('TurboProfileIndex')} Custom={j.get('CustomProfileIndex')}")

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{N}', protocol=mqtt.MQTTv311)
c.username_pw_set(f'UWPClient_User_{N}', f'UWPClient_Pwd888881772688_{N}')
c.on_connect = on_connect
c.on_message = on_message
c.connect(HOST, PORT, 30)
c.loop_start()
time.sleep(1.5)

def getstatus():
    c.publish('Fan/Control', '{"Action":"GETSTATUS"}', qos=0)
    time.sleep(1.2)
    return state.get('last', {})

print('=== initial ===')
init = getstatus()
print('  OperatingMode =', init.get('OperatingMode'))

CANDIDATES = [
    ('UserSet_Mode1', 'Fan/Control'),
    ('UserSet_Mode2', 'Fan/Control'),
    ('UserSet_Mode3', 'Fan/Control'),
    ('UserSet_Mode4', 'Fan/Control'),
]

for action, topic in CANDIDATES:
    payload = json.dumps({"Action": action})
    print(f'--- publish {topic} <- {payload}')
    c.publish(topic, payload, qos=0)
    time.sleep(3)
    st = getstatus()
    print(f'    -> OperatingMode = {st.get("OperatingMode")}  ProfileName={st.get("ProfileName")} Table={st.get("FAN_TableName")}')

print()
print('=== restore ===')
c.publish('Fan/Control', json.dumps({"Action": "UserSet_Mode4"}), qos=0)
time.sleep(3)
getstatus()

c.loop_stop()
c.disconnect()
