import socket, time, sys

HOST, PORT = '127.0.0.1', 13688

def probe(payload=None, wait=2.0, label=''):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(wait)
    try:
        s.connect((HOST, PORT))
    except Exception as e:
        print(f'[{label}] connect failed: {e}')
        return
    print(f'[{label}] connected')
    if payload is not None:
        try:
            s.sendall(payload)
            print(f'[{label}] sent {len(payload)} bytes: {payload[:200]!r}')
        except Exception as e:
            print(f'[{label}] send failed: {e}')
    # read whatever comes
    s.settimeout(2.0)
    chunks = []
    t0 = time.time()
    try:
        while time.time() - t0 < wait:
            d = s.recv(65536)
            if not d:
                print(f'[{label}] peer closed after {len(chunks)} chunks')
                break
            chunks.append(d)
    except socket.timeout:
        pass
    except Exception as e:
        print(f'[{label}] recv err: {e}')
    data = b''.join(chunks)
    print(f'[{label}] received {len(data)} bytes')
    if data:
        print(f'[{label}] raw: {data[:600]!r}')
        try:
            print(f'[{label}] utf8: {data[:600].decode("utf-8","replace")}')
        except Exception:
            pass
    s.close()
    print()

if __name__ == '__main__':
    probe(None, 3.0, 'passive')
    probe(b'{}\n', 2.0, 'json-newline')
    probe(b'{}', 2.0, 'json-raw')
    probe(bytes([0,0,0,0]), 2.0, 'zeros4')
