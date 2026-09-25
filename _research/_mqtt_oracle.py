import time, json
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
N = 0
events = []

def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe('#', qos=0)

def on_message(client, userdata, msg):
    events.append((msg.topic, msg.payload.decode('utf-8', 'replace')))

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{N}', protocol=mqtt.MQTTv311)
c.username_pw_set(f'UWPClient_User_{N}', f'UWPClient_Pwd888881772688_{N}')
c.on_connect = on_connect
c.on_message = on_message
c.connect(HOST, PORT, 30)
c.loop_start()
time.sleep(1.5)

def trial(topic, payload, wait=2.0):
    del events[:]
    c.publish(topic, json.dumps(payload), qos=0)
    time.sleep(wait)
    echo = [(t, p) for (t, p) in events if t in ('Fan/Control', 'System/Control', 'Setting/Control')]
    status = [p for (t, p) in events if t == 'Fan/Status']
    return echo, status

print('--- VALID: GETSTATUS on Fan/Control')
echo, status = trial('Fan/Control', {"Action": "GETSTATUS"})
print('   echo:', echo)
print('   status msgs:', len(status))

print('--- BOGUS: ZZZ_BOGUS_ACTION on Fan/Control')
echo, status = trial('Fan/Control', {"Action": "ZZZ_BOGUS_ACTION"})
print('   echo:', echo)
print('   status msgs:', len(status))

print('--- BOGUS2: {"Action":"SetFanMode"} on Fan/Control')
echo, status = trial('Fan/Control', {"Action": "SetFanMode"})
print('   echo:', echo)
print('   status msgs:', len(status))

print('--- payload format check: raw C# style')
for raw in ['{"Action":"GETSTATUS"}', "{ Action = GETSTATUS }", '{"action":"GETSTATUS"}', '{"Action":"getstatus"}']:
    del events[:]
    c.publish('Fan/Control', raw, qos=0)
    time.sleep(2)
    st = [1 for (t, p) in events if t == 'Fan/Status']
    print(f'   {raw!r:34} -> status msgs = {len(st)}')

c.loop_stop()
c.disconnect()
