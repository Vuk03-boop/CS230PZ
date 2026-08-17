# Beleške za odbranu

Distribuirani okvir za mašinsko učenje — obuka modela preko više čvorova.

Dokument je organizovan po stavkama iz tabele za ocenjivanje. Za svaku stavku:
gde se nalazi u kodu, zašto je urađeno baš tako, i koja pitanja se očekuju.

Struktura foldera prati podelu iz tabele: **N1** je klijent-server deo,
**N2** je middleware sloj.

---

## Pregled arhitekture

Model je **softmax regresija** nad MNIST skupom (784 ulaza, 10 klasa, plus bias).
Model je namerno jednostavan — tema projekta je *distribucija*, a ne dubina mreže.

Topologija je **parameter server**:

```
        worker-1 ──┐
        worker-2 ──┼── TCP ──> server (drži jedinu kopiju W)
        worker-3 ──┘
```

Jedna runda:

1. Server pošalje težine `W` svakom radniku.
2. Svaki radnik računa gradijent nad svojim delom podataka.
3. Radnik šalje gradijent nazad.
4. Server kombinuje gradijente, ažurira `W`, i ciklus se ponavlja.

Ključna tvrdnja koju projekat dokazuje: **N sinhronih radnika sa batch-om B
matematički je ekvivalentno jednom čvoru sa batch-om N×B.** Zato `../DOC/baseline.py`
postoji — to je referentna kriva sa kojom se poredi svaki distribuirani run.

---

## Fajlovi i biblioteke

### Šta koji fajl radi i šta u njemu koristi

| Fajl | Uloga | Biblioteke i za šta baš tu |
|---|---|---|
| `../N1/common.py` | preuzimanje i keširanje MNIST-a, podela na train/test, model, gradijent, evaluacija | `numpy` (softmax, `Xb @ W`, one-hot, `savez_compressed` keša), `sklearn.datasets.fetch_openml` (samo pri prvom preuzimanju), `os` (putanja keša) |
| `../N1/net.py` | framing poruka preko TCP-a i poziv lanca interceptora | `struct` (`Struct("!I")`, prefiks dužine), `pickle` (serijalizacija poruke) |
| `../N1/server.py` | parameter server: `select()` petlja, barijera, detektor otkaza, dodela posla, upis metrika | `socket` (slušajući soket, `TCP_NODELAY`), `select` (multipleksiranje), `numpy` (`np.tensordot`, `np.average` za težinski prosek), `time` (merenja i timeout), `argparse` |
| `../N1/worker.py` | radni čvor: gradijent nad dodeljenim opsegom, injekcija kvara | `socket` (`create_connection`), `os` (`os._exit(1)` za tvrd pad), `time` (`sleep` za zamrzavanje i simuliranu sporost), `sys`, `argparse` |
| `../N2/interceptors.py` | middleware lanac: float32 gradijenti, deflate, metrike, veštačko kašnjenje | `zlib` (`compress`/`decompress`), `numpy` (`astype(np.float32)`, provera `dtype`), `time` (`perf_counter` za CPU cenu kompresije) |
| `../N2/store.py` | SQLite šema, upis metrika, čitanje rezultata | `sqlite3` (šema, WAL, upisi), `json` (kolone `config` i `interceptors`), `os`, `time`, `pandas` (samo u funkcijama za čitanje) |
| `../N2/balancer.py` | EWMA brzine po radniku, podela globalnog batch-a, kursor kroz skup | **nijedna** — čist Python, bez ijednog `import`-a |
| `../N2/checkpoint.py` | atomsko snimanje i učitavanje težina | `numpy` (`savez`/`load` za `.npz`), `os` (`fsync`, `replace`, `makedirs`) |
| `../DOC/baseline.py` | sekvencijalni referentni run, bez mreže | `argparse`, `time`; model i bazu uzima iz `../N1/common.py` i `../N2/store.py` |
| `../DOC/plot.py` | crta svih sedam figura iz baze | `matplotlib` (backend `Agg`), posredno `pandas` kroz `store` |
| `../DOC/run_experiments.sh` | pokreće sve run-ove iza figura | bash; bira interpreter iz `.venv` i postavlja `PYTHONPATH` |
| `smoke_test.py` | pokreće svaki režim od kraja do kraja, po jednu epohu | `subprocess` (server i radnici kao zasebni procesi), `os`, `sys`, `time` |

Da `../N2/balancer.py` nema nijedan uvoz nije slučajno: logika balansiranja ne zna
ni za mrežu ni za numpy, pa se može testirati bez klastera.

### Standardna biblioteka — gde i zašto

| Modul | Gde | Zašto baš on |
|---|---|---|
| `socket` | `server.py`, `worker.py` | Tema traži rad direktno nad soketima. `TCP_NODELAY` je uključen jer Nagle-ov algoritam spaja male pakete i u ping-pong protokolu unosi kašnjenje reda desetina milisekundi. |
| `select` | `server.py` | Jedan tok izvršavanja nad svim vezama, pa nema zaključavanja oko `W`. Timeout od 0.5 s postoji da bi se detektor otkaza izvršavao i kada nema saobraćaja. |
| `struct` | `net.py` | Fiksni binarni prefiks dužine je ono što tok bajtova pretvara u tok poruka. `"!I"` je mrežni redosled bajtova, pa protokol ne zavisi od arhitekture mašine. |
| `pickle` | `net.py` | Serijalizuje `dict` sa numpy nizom u jednom pozivu. JSON ne ume numpy niz bez ručnog pretvaranja u listu, što bi uvećalo i veličinu poruke i vreme. |
| `zlib` | `interceptors.py` | Deflate je u standardnoj biblioteci, pa kompresija ne uvodi novu zavisnost. Nivo je parametar (`--zlib 1-9`) da bi se odnos kompresije i CPU cene mogao meriti. |
| `sqlite3` | `store.py` | Baza u standardnoj biblioteci: jedan fajl, bez servera koji se instalira i pokreće. Podržava WAL, što je uslov da `plot.py` čita dok run traje. |
| `json` | `store.py` | Kolone `config` i `interceptors` menjaju oblik sa zastavicama, pa im ne odgovara fiksna šema. |
| `argparse` | `server.py`, `worker.py`, `baseline.py` | Sve nadogradnje su zastavice sa podrazumevanim vrednostima koje reprodukuju originalno ponašanje. |
| `os` | `checkpoint.py`, `common.py`, `worker.py` | `os.replace` i `os.fsync` su nosioci atomskog upisa; `os._exit(1)` je tvrd pad bez čišćenja, što je upravo ono što se testira. |
| `time` | svuda | Merenje trajanja rundi, EWMA, timeout, simulirana sporost. `perf_counter()` se koristi tamo gde se meri CPU cena, `time()` tamo gde treba apsolutan trenutak. |
| `subprocess` | `smoke_test.py` | Klaster mora da bude više procesa da bi test uopšte bio distribuiran. |

### Biblioteke trećih strana — gde i zašto

| Biblioteka | Gde | Za šta | Zašto |
|---|---|---|---|
| `numpy` | `common.py`, `server.py`, `checkpoint.py`, `interceptors.py` | matrična aritmetika modela, težinski prosek gradijenata, `.npz` checkpoint, sužavanje tipa | Petlja u čistom Pythonu nad matricom 785×10 bila bi red veličine sporija, a projekat meri vreme — sporo računanje bi zamaglilo mrežne efekte koji se ispituju. |
| `scikit-learn` | samo `common._download_mnist()` | `fetch_openml("mnist_784")` | Koristi se **isključivo** za jednokratno preuzimanje skupa, ne za učenje. Posle prvog pokretanja projekat radi i bez nje, iz keša `mnist.npz`. |
| `pandas` | `store.rounds_of()`, `store.worker_balance()`, posredno `plot.py` | `read_sql_query` vraća DataFrame nad kojim `plot.py` radi `groupby` i `rolling` | Uvozi se **lokalno u funkciji**, ne na vrhu modula, pa server i radnici ne plaćaju njen uvoz iako uvoze `store`. |
| `matplotlib` | `plot.py` | svih sedam figura | Backend je `Agg`, jer se crta u PNG bez otvaranja prozora, pa figure mogu da se generišu i iz skripte bez grafičkog okruženja. |

Verzije sa kojima su napravljeni rezultati: Python 3.14.5, numpy 2.5.1,
pandas 3.0.5, matplotlib 3.11.1, scikit-learn 1.9.0.

### Šta se namerno *ne* koristi

- **ZeroMQ, gRPC ili bilo koji RPC okvir.** Tema traži rad direktno nad
  soketima; framing, barijera i detekcija otkaza su baš ono što se ocenjuje, a
  okvir bi ih sakrio.
- **PyTorch, TensorFlow ili drugi ML okvir.** Gradijent softmax regresije je
  jedan izraz; okvir ne bi doneo ništa, a sakrio bi to što se distribuira.
- **`threading` i `asyncio` u serveru.** `select()` daje jedan tok izvršavanja i
  time izbegava zaključavanje oko jedine kopije `W`.
- **`pandas` na putanji upisa.** Server piše samo kroz `sqlite3`; pandas se
  pojavljuje tek pri čitanju rezultata.

---

## N1 — Klijent-server (3 boda)

**Fajlovi:** `../N1/net.py`, `../N1/server.py`, `../N1/worker.py`

### Zašto sopstveni protokol umesto biblioteke

TCP je **tok bajtova, a ne tok poruka**. To je najvažnija rečenica u ovom delu.
Jedan `sendall()` može da stigne kao nekoliko `recv()` poziva, a dva `sendall()`
poziva mogu da stignu kao jedan. Ne postoji granica poruke koju TCP garantuje.

Rešenje je **framing sa prefiksom dužine**: svaka poruka počinje sa 4 bajta
(`struct.Struct("!I")`, big-endian unsigned int) koji kažu koliko bajtova sledi.

```python
sock.sendall(HEADER.pack(len(data)) + data)
```

### Najčešća greška — `recv_exactly`

`sock.recv(n)` sme da vrati **manje od n bajtova u bilo kom trenutku**. To nije
greška, to je normalno ponašanje. Zato se mora petljati:

```python
def recv_exactly(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)
```

Ako se ovo napiše kao običan `sock.recv(n)`, kod radi na malim porukama i puca
na velikim — a gradijent od 785×10 float64 je 62 KB, dovoljno da se raspadne.

`if not chunk` znači da je druga strana **uredno zatvorila vezu**. To je samo po
sebi signal otkaza: proces sa druge strane više ne postoji.

### Petlja servera

Server je **jednonitni**, koristi `select()`:

```python
readable, _, _ = select.select([lsock] + list(conns), [], [], 0.5)
```

Zašto ne niti? Zato što bi svaka nit dirala isto `W`, pa bi trebalo zaključavanje.
Ovako postoji tačno jedan tok izvršavanja i nema trke za podacima. Timeout od 0.5s
postoji da bi se detektor otkaza izvršavao i kada nema saobraćaja.

### Sinhroni i asinhroni režim

| | Sinhrono (`--mode sync`) | Asinhrono (`--mode async`) |
|---|---|---|
| Ažuriranje `W` | tek kad svi pošalju gradijent | odmah po prijemu svakog gradijenta |
| Barijera | postoji (`responded == active`) | ne postoji |
| Brzina | ograničena najsporijim radnikom | niko nikoga ne čeka |
| Konzistentnost | svi računaju iz iste verzije `W` | gradijenti su **zastareli** |

**Staleness** (zastarelost) = `rnd - msg["round"]`. Radnik pošalje sa kojom
verzijom težina je računao; server vidi koliko je rundi u međuvremenu prošlo.
U sinhronom režimu je uvek 0, u asinhronom raste sa brojem radnika.

To je klasičan kompromis **konzistentnost ↔ propusnost**, i to je poenta poređenja.

### Detekcija otkaza — dva nezavisna signala

Ovo je najjači deo za odbranu, jer pokazuje razumevanje realnih distribuiranih sistema.

**Signal 1 — veza se zatvorila.** Proces je mrtav. Trenutno i tačno.

```python
if msg is None:
    drop(s, "connection closed")
```

**Signal 2 — tišina duže od `--timeout`.** Proces je zamrznut, ili je mreža
particionisana. Sporo i heurističko.

```python
dead = [w for w in active
        if w not in responded and now - last_seen[w] > args.timeout]
```

**Zašto su oba potrebna:** pravi distribuirani sistem za udaljene otkaze dobija
**samo signal 2**. Signal 1 je poklon činjenice da sve radi na jednoj mašini preko
loopback-a. Zato eksperimenti testiraju oba: `--crash-at-round` ubija proces
(signal 1), `--freeze-at-round` ga zamrzava sa otvorenom vezom (signal 2).

**Timeout je nemoguće podesiti tačno.** Kratak timeout izbacuje spore ali žive
čvorove; dugačak odlaže oporavak. To je fundamentalno ograničenje, ne propust
implementacije.

### Tri suptilnosti u barijeri (očekuj pitanja)

**1. Radnik na barijeri ćuti — zato timeout meri `released_at`, ne `last_seen`.**

Radnik koji čeka na barijeri po definiciji ne šalje ništa. Da timeout meri
"vreme od poslednje poruke", svi preživeli bi bili izbačeni odmah nakon što se
izbaci jedan. Zato `broadcast()` osvežava `last_seen` u trenutku *otpuštanja*:

```python
if send_to(w, payload):
    last_seen[w] = now
    released_at[w] = now
```

Timeout meri *"vreme otkad sam ti dao posao"*, a ne *"vreme otkad si se javio"*.

**2. Detektor ne radi dok se klaster ne formira.**

`started` postaje `True` tek kad se svi radnici registruju. Inače bi prvi radnik
bio izbačen dok se čeka poslednji. Iz istog razloga se i merenje vremena (`t0`)
resetuje tek tada — inače je svaki run pomeren za dužinu startovanja.

**3. PUSH od izbačenog radnika mora da prekine vezu.**

```python
if wid not in active:
    drop(s, "push from evicted worker")
```

Radnik može biti izbačen po timeout-u a da je i dalje živ. Da mu se dozvoli da
uđe u `responded`, barijera bi se zaključala zauvek — jer `responded` više nikad
ne bi bilo jednako `active`.

### Injekcija otkaza — dva različita eksperimenta

`--crash-at-round` poziva `os._exit(1)`: bez čišćenja, OS zatvara soket → signal 1.

`--freeze-at-round` ulazi u `while True: time.sleep(3600)`: veza ostaje otvorena,
poruke prestaju → signal 2.

Ovo **nisu isti eksperiment** i ne smeju se predstaviti kao jedan.

---

## N1 — Rad sa fajlovima (2 boda)

**Fajlovi:** `../N2/checkpoint.py`, `../N1/common.py`

### Dve vrste rada sa fajlovima u projektu

1. **Keširanje skupa podataka** — `common.py` preuzme MNIST jednom sa OpenML-a i
   snimi ga kao `mnist.npz`. Fiksni `DATA_SEED` znači da je permutacija uvek ista,
   pa je podela na train/test reproducibilna.
2. **Checkpoint težina** — najzanimljiviji deo, objašnjen ispod.

### Zašto checkpoint uopšte postoji

Priča o otpornosti na otkaze je bila **jednostrana**: radnik može da padne i
obuka se nastavlja, ali **server drži jedinu kopiju `W`**. Server je bio jedina
tačka otkaza u projektu čija je tema tolerancija otkaza.

Checkpoint pretvara *"server je pao, run je izgubljen"* u *"server je pao,
pokreni ga ponovo i izgubi najviše K rundi"*.

### Atomsko pisanje — ključni argument

Checkpoint koji je napola upisan kad proces padne je **gori od nepostojećeg**,
jer sledeće pokretanje pročita skraćen fajl i pukne na način koji liči na grešku
u modelu, a ne na oštećen fajl.

Postupak:

```python
tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.tmp.npz")
np.savez(tmp, W=W, ...)
with open(tmp, "rb") as f:
    os.fsync(f.fileno())      # bajtovi stvarno na disku
os.replace(tmp, path)         # atomska zamena
```

Tri stvari koje treba znati da se objasne:

- **`os.replace` je atomsko** unutar jednog fajl sistema. Čitalac vidi ili ceo
  stari checkpoint ili ceo novi — nikad mešavinu.
- **Isti direktorijum je bitan.** Preko granice fajl sistema `os.replace` se
  svodi na kopiranje i atomičnost se gubi.
- **`fsync` pre preimenovanja.** Bez njega nestanak struje može da ostavi
  direktorijum koji pokazuje na prazan fajl.

`load()` tretira oštećen fajl kao nepostojeći, a ne kao fatalnu grešku — napola
upisan checkpoint od ranijeg pada ne sme da zaustavi run koji pokušava da se
oporavi od njega.

---

## N2 — Middleware / Wrapperi / Brokeri / Interceptori (3 boda)

**Fajl:** `../N2/interceptors.py`

### Definicija

**Interceptor** je objekat koji stoji na putu poruke i sme da je pregleda ili
izmeni, a da ni pošiljalac ni primalac ne znaju da postoji. Termin je iz
Tanenbaum & Van Steen; isti obrazac je servlet filter u Javi ili gRPC interceptor.

Konkretna korist ovde: kompresija gradijenata, brojanje bajtova i veštačko
kašnjenje — **bez ijednog `if args.compress` unutar petlje za obuku**.

### Dva nivoa kuka

| Nivo | Metode | Šta vidi | Ko ga koristi |
|---|---|---|---|
| **Objektni** | `before_send(obj)` / `after_recv(obj)` | Python `dict` | `Float32Gradients` — mora da zna da je `msg["grads"]` niz |
| **Bajtni** | `encode(data)` / `decode(data)` | pickle payload | `Zlib` — ne zna niti ga zanima šta bajtovi znače |

Postoje dva nivoa jer dve korisne transformacije prirodno žive na različitim
slojevima.

### Simetričnost lanca — obavezno pitanje

Lanac je **chain of responsibility** i **mora** biti simetričan:

```
odlazak:  napred → nazad     [Float32, Zlib]  →  prvo float32, pa deflate
dolazak:  nazad → napred     [Zlib, Float32]  →  prvo inflate, pa objekat
```

```python
def apply_encode(self, data, ctx):
    for i in self.items:              # napred
        data = i.encode(data, ctx)

def apply_decode(self, data, ctx):
    for i in reversed(self.items):    # nazad
        data = i.decode(data, ctx)
```

Ako se ovo obrne, zlib-u se prosledi pickle koji ne ume da pročita.

### Jednostrani i dvostrani interceptori

Ovo je fina distinkcija koja ostavlja dobar utisak:

- **Bajtni interceptori moraju biti na obe strane.** Radnik koji deflate-uje a
  server koji ne — greška pri odpakivanju na prvoj poruci.
- **Objektni interceptori su bezbedni jednostrano.** `Float32Gradients` šalje
  `float32`; server dobije niz užeg tipa i numpy ga sam promoviše nazad pri prvoj
  aritmetičkoj operaciji. Serveru **ne treba** odgovarajući interceptor.

Zato `--compress` postoji samo na radniku, a `--zlib` mora na oba.

### Zašto se zlib meri, a ne pretpostavlja

Gust gradijent malih float brojeva je **blizu nekompresibilnog**, a na loopback-u
CPU cena kompresije može da premaši uštedu u prenosu. `Zlib.stats()` beleži
`raw_bytes`, `wire_bytes`, `ratio` i `cpu_seconds` baš zato — to je **rezultat za
rad, a ne unapred poznat zaključak.**

### Redosled u `build()`

```python
if compress_f32: items.append(Float32Gradients())   # prvo suzi tip
if zlib_level:   items.append(Zlib(zlib_level))     # pa komprimuj
items.append(Metrics())                             # pa broji
```

`Metrics` je poslednji da bi brojao ono što je **stvarno prešlo preko soketa**,
a ne veličinu pickle-a.

---

## N2 — Rad sa bazom (3 boda)

**Fajl:** `../N2/store.py`

### Zašto baza, a ne samo CSV

Tri razloga, svi se pojave čim postoji više od jednog run-a:

1. **Jedan CSV je jedan run.** Svako pitanje koje poredi run-ove ("ubrzanje 4
   radnika u odnosu na 1 pri istoj tačnosti") je *join*, a join nad direktorijumom
   fajlova je način da se slučajno uporede dva run-a sa različitim parametrima.
2. **Redovi po rundi i konfiguracija run-a su različitog oblika.** CSV mora ili da
   ponavlja konfiguraciju u svakom redu ili da je izostavi. Prva verzija je
   izostavljala — zato stariji rezultati ne beleže sa kojim `--compute-delay` su
   napravljeni.
3. **Diskretni događaji nemaju kolonu** u tabeli "po rundi". Registracija radnika
   i izbacivanje radnika se ne uklapaju u red koji opisuje rundu.

### Šema — četiri tabele

| Tabela | Šta drži | Zrno |
|---|---|---|
| `runs` | konfiguracija i zbirni rezultat | jedan red po run-u |
| `rounds` | metrike po rundi | run × runda |
| `events` | registracija, izbacivanje i razlog | proizvoljan broj |
| `worker_rounds` | koliko je koji radnik obradio i za koje vreme | run × runda × radnik |

`worker_rounds` je tabela koja **omogućava grafik load balancinga** — u CSV-u
nema mesta za podatak po radniku.

Pun rečnik podataka — svaka kolona, jedinica, ko je upisuje i koje su zamke pri
čitanju — je u **`BAZA.md`**.

### Konkurentnost

**Piše samo server.** Radnici ne diraju bazu. Znači postoji tačno jedan pisac i
klasičan SQLite problem sa više pisaca se ne javlja.

WAL režim je ipak uključen:

```python
self.db.execute("PRAGMA journal_mode=WAL")
```

da bi `plot.py` mogao da čita bazu **dok run još traje**. Čitalac se otvara kao
read-only (`file:{path}?mode=ro`).

`synchronous=NORMAL` je kompromis: brže od `FULL`, a i dalje bezbedno u WAL režimu.

### CSV je uklonjen

Ranije je server pisao i CSV i bazu, sa istim redovima na oba mesta. To je značilo
dva izvora istine koji mogu da se raziđu: CSV se prepisuje pri svakom pokretanju, a
baza dodaje novi red za svaki run. `plot.py` je pritom davao prednost CSV-u, pa je
run koji padne na pola mogao tiho da nacrta **stari** CSV, dok tačni podaci stoje u
bazi. Sada sve piše samo u bazu, a `plot.py` čita `load_run`, koji uzima poslednji
run sa datom labelom.

---

## N2 — Load balancer (2 boda)

**Fajl:** `../N2/balancer.py`

### Problem koji rešava

Problem je specifičan za **sinhronu** obuku. Sa statičkim deljenjem svaki radnik
dobija tačno `len(X)/n` uzoraka i isti batch, pa **svaka runda traje koliko i
najsporiji radnik**. Jedan čvor koji je 3× sporiji čini ceo klaster 3× sporijim,
dok ostalih n−1 radnika stoji besposleno na barijeri.

**To besposleno vreme je merljivo i to je ono što ide u rad** — kolona
`barrier_wait` je razlika između dolaska prvog i poslednjeg gradijenta u rundi.

### Kako radi

Server drži **eksponencijalno otežanu procenu** (EWMA) sekundi po uzorku za
svakog radnika:

```python
self.spp[worker] = spp if prev is None else \
    (1 - self.alpha) * prev + self.alpha * spp
```

Merenje se radi na jedinom mestu gde je moguće — između otpuštanja radnika i
odgovora od njega:

```python
elapsed = now - released_at[wid]
BAL.record(wid, msg["n"], elapsed)
```

Zatim se fiksni globalni batch deli **srazmerno brzini**: brzi radnici dobijaju
više uzoraka, spori manje, svi završe otprilike u isto vreme, i `barrier_wait`
pada.

### Dva svojstva koja treba odbraniti

**1. Globalni batch ostaje konstantan.**

Klaster i dalje troši tačno `n_workers × base_batch` uzoraka po rundi. Zato su
dinamički i statički run **uporedivi pri istom broju obrađenih uzoraka**, i x-osa
svake postojeće figure i dalje znači isto. Greška zaokruživanja se gura na
najbržeg radnika baš da bi zbir bio tačno konstantan.

**2. Prosek gradijenata mora postati težinski.** ← *najvažnije pitanje*

Svaki radnik vraća gradijent koji je **srednja vrednost nad njegovim batch-om**.
Prosečiti te srednje vrednosti sa jednakom težinom je ispravno **samo ako su
batch-evi jednaki**. Sa nejednakim batch-evima ispravna kombinacija je:

$$\bar{g} = \frac{\sum_i n_i g_i}{\sum_i n_i}$$

što se teleskopski svodi na srednju vrednost nad **unijom** batch-eva.

```python
ns = np.array([m[2] for m in buf_meta], dtype=np.float64)
combined = np.tensordot(ns, stacked, axes=(0, 0)) / ns.sum()
```

Običan `np.mean` bi tiho **precenio** manje batch-eve sporih radnika i promenio
funkciju cilja. Ovako ekvivalencija *"N radnika = jedan čvor sa N-strukim batch-om"*
preživljava i uz load balancing.

### Detalji koje vredi znati

- **Dok svaki radnik nije bar jednom izmeren, deli se ravnomerno.** Pogađanje na
  osnovu nepotpune slike je gore od nepogađanja.
- **`forget()` pri izbacivanju.** Procena mrtvog radnika ne sme da nastavi da
  usmerava podelu za preživele. Procena se **arhivira**, ne briše — i dalje je
  pošten odgovor na pitanje "koliko je taj čvor bio brz pre nego što je pao".
- **U asinhronom režimu se ne balansira** (`assign_single` daje osnovni batch).
  Nema barijere, pa nema ni zastoja koji bi se ispravljao — poenta asinhronog
  režima je da spor radnik nikoga ne zadržava.

### Ograničenje tehnike — reci ovo sam, pre nego što te pitaju

Zato postoje **dva** parametra za simulaciju sporosti:

- `--compute-delay` — **fiksno** vreme po batch-u, bez obzira na veličinu.
- `--delay-per-sample` — vreme **po uzorku**.

Load balancer može da pomeri **samo onaj deo cene koji raste sa količinom posla**.
Sa fiksnim kašnjenjem po batch-u, server može da smanji batch sporog radnika na
minimum i **sporiji radnik i dalje traje isto** — ništa se ne poboljša.

Zato eksperiment za balansiranje koristi `--delay-per-sample`. To je granica
tehnike i vredi je pomenuti u radu.

---

## Kvalitet koda (1 bod)

- **Razdvojeni slojevi.** `net.py` ne zna ništa o obuci; `interceptors.py` ne zna
  ništa o soketima; `balancer.py` ne zna ništa o mreži. Svaki se može testirati sam.
- **Sve nadogradnje su opcione.** Podrazumevane zastavice reprodukuju originalno
  ponašanje bajt po bajt, pa stariji rezultati i dalje važe.
- **`smoke_test.py`** pokreće svaki režim od kraja do kraja, po jednu epohu, za
  oko minut. Za demonstraciju uživo: `python smoke_test.py sync`.

---

## Očekivana pitanja i kratki odgovori

**Zašto sam pišeš framing umesto da koristiš biblioteku?**
Jer TCP je tok bajtova. Bez prefiksa dužine ne postoji granica poruke. Prefiks je
4 bajta big-endian, a `recv_exactly` petlja jer `recv(n)` sme da vrati manje od n.

**Kako razlikuješ pad procesa od zamrznutog čvora?**
Pad zatvara TCP vezu → `recv` vrati prazno → trenutna detekcija. Zamrzavanje
ostavlja vezu otvorenom → detekcija samo po timeout-u. Pravi distribuirani sistem
za udaljene otkaze ima samo drugi signal.

**Zašto timeout meri vreme od otpuštanja, a ne od poslednje poruke?**
Jer radnik na barijeri po definiciji ćuti. Inače bi svi preživeli bili izbačeni
odmah posle prvog izbacivanja.

**Zašto je prosek gradijenata težinski?**
Jer svaki radnik vraća srednju vrednost nad *svojim* batch-om. Sa nejednakim
batch-evima jedino `Σnᵢgᵢ / Σnᵢ` daje srednju vrednost nad unijom. `np.mean` bi
promenio funkciju cilja.

**Zašto `os.replace`, a ne obično pisanje?**
Jer je atomsko unutar fajl sistema. Napola upisan checkpoint je gori od
nepostojećeg. Zato: temp fajl u istom direktorijumu → `fsync` → `os.replace`.

**Zašto se interceptori na prijemu primenjuju obrnutim redosledom?**
Jer je transformacija kompozicija funkcija — mora se odmotati suprotnim
redosledom od motanja. Inače se zlib-u prosledi pickle.

**Zašto SQLite, a ne CSV?**
Poređenje run-ova je join; konfiguracija i metrike po rundi su različitog oblika;
diskretni događaji nemaju kolonu u tabeli po rundi.

**Šta je staleness i kada je različit od nule?**
Broj rundi između verzije težina iz koje je gradijent računat i tekuće verzije.
U sinhronom režimu uvek 0; u asinhronom raste sa brojem radnika.

**Koje je ograničenje tvog load balancera?**
Može da pomeri samo deo cene koji skalira sa količinom posla. Fiksni trošak po
rundi (pokretanje kernela, dispatch) ostaje bez obzira na veličinu batch-a.

**Zašto je server jednonitni?**
Da ne bi bilo zaključavanja oko `W`. `select()` daje jedan tok izvršavanja i nema
trke za podacima. Za ovaj broj čvorova propusnost nije usko grlo.
