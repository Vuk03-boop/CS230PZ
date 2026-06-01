import pickle
import struct

HEADER = struct.Struct("!I")


# Serijalizuje i salje jednu poruku; vraca broj bajtova korisnog sadrzaja.
def send_msg(sock, obj, chain=None):
    ctx = {"direction": "out"}
    if chain is not None:
        obj = chain.apply_before_send(obj, ctx)
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    if chain is not None:
        data = chain.apply_encode(data, ctx)
    sock.sendall(HEADER.pack(len(data)) + data)
    return len(data)


# Cita tacno n bajtova sa soketa ili vraca None ako je druga strana zatvorila vezu.
def recv_exactly(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


# Prima jednu poruku i vraca objekat i broj bajtova koji su stigli sa mreze.
def recv_msg(sock, chain=None):
    raw = recv_exactly(sock, HEADER.size)
    if raw is None:
        return None, 0
    length = HEADER.unpack(raw)[0]
    body = recv_exactly(sock, length)
    if body is None:
        return None, 0
    ctx = {"direction": "in"}
    if chain is not None:
        body = chain.apply_decode(body, ctx)
    obj = pickle.loads(body)
    if chain is not None:
        obj = chain.apply_after_recv(obj, ctx)
    return obj, length + HEADER.size
