# Dodeljuje radnicima opsege redova i prati izmerenu brzinu svakog radnika.
class WorkBalancer:

    # Postavlja pocetno stanje balansera.
    def __init__(self, n_total, base_batch, n_workers, epochs,
                 mode="static", min_batch=4, alpha=0.3):
        self.n_total = n_total
        self.base_batch = base_batch
        self.n_workers = n_workers
        self.mode = mode
        self.min_batch = min_batch
        self.alpha = alpha

        self.target_samples = epochs * n_total
        self.cursor = 0
        self.samples_done = 0
        self.epoch = 0

        self.spp = {}
        self.retired = {}
        self.last_alloc = {}

    # Tacno je kada je potrosen zadati budzet uzoraka.
    @property
    def exhausted(self):
        return self.samples_done >= self.target_samples

    # Vraca ukupnu velicinu batch-a za ceo klaster u jednoj rundi.
    def global_batch(self):
        return self.base_batch * self.n_workers

    # Deli globalni batch medju radnicima srazmerno njihovoj brzini.
    def _sizes(self, workers):
        total = self.global_batch()
        if not workers:
            return {}
        known = [w for w in workers if self.spp.get(w)]
        if self.mode != "dynamic" or len(known) < len(workers):
            base = max(self.min_batch, total // len(workers))
            return {w: base for w in workers}

        rates = {w: 1.0 / self.spp[w] for w in workers}
        s = sum(rates.values())
        sizes = {w: max(self.min_batch, int(round(total * rates[w] / s)))
                 for w in workers}
        drift = total - sum(sizes.values())
        if drift:
            fastest = max(workers, key=lambda w: rates[w])
            sizes[fastest] = max(self.min_batch, sizes[fastest] + drift)
        return sizes

    # Pomera kursor za zadati broj redova i prelazi u novu epohu na kraju skupa.
    def _take(self, size):
        if self.cursor >= self.n_total:
            self.cursor = 0
            self.epoch += 1
        start = self.cursor
        end = min(start + size, self.n_total)
        self.cursor = end
        if self.cursor >= self.n_total:
            self.cursor = 0
            self.epoch += 1
        self.samples_done += end - start
        return start, end

    # Vraca opseg redova za svakog radnika u sledecoj rundi.
    def assign(self, workers):
        if self.exhausted or not workers:
            return {}
        out = {}
        for w, size in self._sizes(sorted(workers)).items():
            start, end = self._take(size)
            out[w] = (start, end)
            self.last_alloc[w] = end - start
        return out

    # Vraca jedan opseg redova za jednog radnika; koristi se u asinhronom rezimu.
    def assign_single(self, worker, size=None):
        if self.exhausted:
            return None
        start, end = self._take(size or self.base_batch)
        self.last_alloc[worker] = end - start
        return start, end

    # Uklapa jedno merenje u procenu brzine radnika.
    def record(self, worker, n_samples, seconds):
        if n_samples <= 0 or seconds <= 0:
            return
        spp = seconds / n_samples
        prev = self.spp.get(worker)
        self.spp[worker] = spp if prev is None else \
            (1 - self.alpha) * prev + self.alpha * spp

    # Uklanja izbacenog radnika iz aktivnih procena i arhivira njegovu brzinu.
    def forget(self, worker):
        if worker in self.spp:
            self.retired[worker] = self.spp.pop(worker)
        self.last_alloc.pop(worker, None)

    # Vraca odnos najsporijeg i najbrzeg radnika; 1.0 znaci savrsenu ravnotezu.
    def imbalance(self):
        v = [x for x in self.spp.values() if x]
        if len(v) < 2:
            merged = dict(self.retired)
            merged.update(self.spp)
            v = [x for x in merged.values() if x]
        return (max(v) / min(v)) if len(v) > 1 else 1.0

    # Vraca pregled brzine, poslednjeg batch-a i stanja svih radnika.
    def summary(self):
        both = dict(self.retired)
        both.update(self.spp)
        return {w: {"samples_per_sec": round(1.0 / s, 1) if s else None,
                    "last_batch": self.last_alloc.get(w),
                    "state": "active" if w in self.spp else "gone"}
                for w, s in sorted(both.items())}
