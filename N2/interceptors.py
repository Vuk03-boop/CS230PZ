import time
import zlib

import numpy as np


# Osnovna klasa presretaca; sve kuke su podrazumevano prazne.
class Interceptor:

    name = "interceptor"

    # Obradjuje objekat poruke pre serijalizacije.
    def before_send(self, obj, ctx):
        return obj

    # Obradjuje bajtove pre slanja na mrezu.
    def encode(self, data, ctx):
        return data

    # Obradjuje primljene bajtove pre deserijalizacije.
    def decode(self, data, ctx):
        return data

    # Obradjuje objekat poruke posle deserijalizacije.
    def after_recv(self, obj, ctx):
        return obj

    # Vraca statistiku koju je presretac prikupio.
    def stats(self):
        return {}


# Uredjena lista presretaca koja se primenjuje simetricno u oba smera.
class Chain:

    # Pravi lanac od zadate liste presretaca.
    def __init__(self, interceptors=None):
        self.items = list(interceptors or [])

    # Dodaje presretac na kraj lanca.
    def add(self, interceptor):
        self.items.append(interceptor)
        return self

    # Vraca prvi presretac date klase ili None.
    def get(self, cls):
        for i in self.items:
            if isinstance(i, cls):
                return i
        return None

    # Primenjuje before_send redom, od pocetka ka kraju lanca.
    def apply_before_send(self, obj, ctx):
        for i in self.items:
            obj = i.before_send(obj, ctx)
        return obj

    # Primenjuje encode redom, od pocetka ka kraju lanca.
    def apply_encode(self, data, ctx):
        for i in self.items:
            data = i.encode(data, ctx)
        return data

    # Primenjuje decode obrnutim redosledom, od kraja ka pocetku lanca.
    def apply_decode(self, data, ctx):
        for i in reversed(self.items):
            data = i.decode(data, ctx)
        return data

    # Primenjuje after_recv obrnutim redosledom, od kraja ka pocetku lanca.
    def apply_after_recv(self, obj, ctx):
        for i in reversed(self.items):
            obj = i.after_recv(obj, ctx)
        return obj

    # Skuplja statistiku svih presretaca u lancu.
    def report(self):
        out = {}
        for i in self.items:
            s = i.stats()
            if s:
                out[i.name] = s
        return out

    # Tekstualni prikaz lanca sa imenima presretaca.
    def __repr__(self):
        return "Chain([" + ", ".join(i.name for i in self.items) + "])"


# Prepolovljava saobracaj tako sto gradijente salje kao float32 umesto float64.
class Float32Gradients(Interceptor):

    name = "float32"

    # Postavlja brojace poruka i ustedjenih bajtova.
    def __init__(self):
        self.n = 0
        self.saved = 0

    # Pretvara gradijent iz poruke u float32 pre slanja.
    def before_send(self, obj, ctx):
        g = obj.get("grads") if isinstance(obj, dict) else None
        if isinstance(g, np.ndarray) and g.dtype == np.float64:
            obj = dict(obj)
            obj["grads"] = g.astype(np.float32)
            self.n += 1
            self.saved += g.nbytes // 2
        return obj

    # Vraca broj obradjenih poruka i ustedu u bajtovima.
    def stats(self):
        return {"messages": self.n, "bytes_saved": self.saved}


# Deflate kompresija na nivou bajtova; mora biti ukljucena na obe strane veze.
class Zlib(Interceptor):

    name = "zlib"

    # Postavlja nivo kompresije i brojace.
    def __init__(self, level=6):
        self.level = level
        self.raw = 0
        self.wire = 0
        self.seconds = 0.0

    # Komprimuje bajtove pre slanja i meri utroseno vreme.
    def encode(self, data, ctx):
        t = time.perf_counter()
        out = zlib.compress(data, self.level)
        self.seconds += time.perf_counter() - t
        self.raw += len(data)
        self.wire += len(out)
        return out

    # Dekomprimuje primljene bajtove.
    def decode(self, data, ctx):
        return zlib.decompress(data)

    # Vraca odnos kompresije i utroseno procesorsko vreme.
    def stats(self):
        ratio = (self.wire / self.raw) if self.raw else 0.0
        return {"raw_bytes": self.raw, "wire_bytes": self.wire,
                "ratio": round(ratio, 4), "cpu_seconds": round(self.seconds, 4)}


# Broji poruke i bajtove razvrstane po tipu poruke.
class Metrics(Interceptor):

    name = "metrics"

    # Postavlja brojace poruka i bajtova.
    def __init__(self):
        self.sent = {}
        self.recv = {}
        self.bytes_out = 0
        self.bytes_in = 0
        self._pending_type = None

    # Broji poslatu poruku po njenom tipu.
    def before_send(self, obj, ctx):
        t = obj.get("type", "?") if isinstance(obj, dict) else "?"
        self.sent[t] = self.sent.get(t, 0) + 1
        ctx["type"] = t
        return obj

    # Sabira broj poslatih bajtova.
    def encode(self, data, ctx):
        self.bytes_out += len(data)
        return data

    # Sabira broj primljenih bajtova.
    def decode(self, data, ctx):
        self.bytes_in += len(data)
        return data

    # Broji primljenu poruku po njenom tipu.
    def after_recv(self, obj, ctx):
        t = obj.get("type", "?") if isinstance(obj, dict) else "?"
        self.recv[t] = self.recv.get(t, 0) + 1
        return obj

    # Vraca brojace poslatih i primljenih poruka i bajtova.
    def stats(self):
        return {"sent": dict(self.sent), "recv": dict(self.recv),
                "bytes_out": self.bytes_out, "bytes_in": self.bytes_in}


# Dodaje fiksno vestacko kasnjenje pre svakog slanja poruke.
class LatencyInjector(Interceptor):

    name = "latency"

    # Postavlja duzinu kasnjenja i brojac odlozenih poruka.
    def __init__(self, seconds=0.0):
        self.seconds = seconds
        self.n = 0

    # Odlaze slanje za zadati broj sekundi.
    def encode(self, data, ctx):
        if self.seconds > 0:
            time.sleep(self.seconds)
            self.n += 1
        return data

    # Vraca zadato kasnjenje i broj odlozenih poruka.
    def stats(self):
        return {"one_way_seconds": self.seconds, "delayed_messages": self.n}


# Sastavlja lanac presretaca na osnovu argumenata komandne linije.
def build(compress_f32=False, zlib_level=0, latency=0.0):
    items = []
    if compress_f32:
        items.append(Float32Gradients())
    if zlib_level:
        items.append(Zlib(zlib_level))
    items.append(Metrics())
    if latency:
        items.append(LatencyInjector(latency))
    return Chain(items)
