# Šta se nalazi u bazi

Sve što merenja beleže završi u jednom fajlu: `results/runs.sqlite`. Nema CSV-a
pored njega, nema drugog izvora istine. Ovaj dokument je rečnik podataka — šta
koja kolona znači, u kojoj je jedinici i ko je upisuje.

Šema je definisana u `N2/store.py` (konstanta `SCHEMA`), a jedini pisac je
`N1/server.py`.

Otvaranje iz konzole:

```bash
sqlite3 results/runs.sqlite
```

ili iz Pythona, read-only, bez rizika da se pokvari run koji traje:

```python
from N2 import store
db = store.connect("results/runs.sqlite")
run = store.latest_run(db, "sync_n4")
```

---

## Četiri tabele

| Tabela | Zrno (jedan red je) | Redova u trenutnoj bazi |
|---|---|---|
| `runs` | jedan pokrenut eksperiment | 13 |
| `rounds` | run × runda | 15 684 |
| `worker_rounds` | run × runda × radnik | 33 248 |
| `events` | diskretan događaj | 76 |

Sve tri detaljne tabele pokazuju na `runs(run_id)`. `run_id` je autoinkrement,
pa je hronološki: veći `run_id` znači kasnije pokrenut run.

---

## `runs` — konfiguracija i zbirni ishod

Red se upisuje **na startu servera**, dok se još niko nije povezao, a dopunjuje
se **na kraju** kroz `Store.finish()`.

| Kolona | Jedinica | Kada se upisuje | Značenje |
|---|---|---|---|
| `run_id` | — | start | primarni ključ, autoinkrement |
| `label` | — | start | `--label`, npr. `sync_n4`. **Nije jedinstven** |
| `mode` | — | start | `sync`, `async` ili `sequential` |
| `n_workers` | broj | start | očekivan broj radnika (`--workers`) |
| `lr` | — | start | korak učenja |
| `balance` | — | start | `static` ili `dynamic` |
| `dataset` | — | start | uvek `mnist` |
| `timeout_s` | s | start | prag detektora otkaza |
| `interceptors` | tekst / JSON | start **i** kraj | vidi napomenu ispod |
| `config` | JSON | start | ceo `config` rečnik, uključujući `batch_size`, `epochs`, `zlib`, `latency` |
| `started_at` | Unix vreme | start | |
| `finished_at` | Unix vreme | kraj | **`NULL` znači da run nije završen** |
| `total_rounds` | broj | kraj | |
| `total_samples` | broj | kraj | ukupno obrađenih uzoraka |
| `total_bytes` | bajtova | kraj | primljeno na serveru, posle dekompresije lanca |

### Dve zamke u ovoj tabeli

**`interceptors` menja tip tokom života run-a.** Na startu je tu tekstualni
`repr` lanca, npr. `Chain([zlib, metrics])`, jer izveštaj još ne postoji. Na
kraju ga `finish()` prepiše JSON izveštajem sa izmerenim vrednostima:

```json
{"zlib":    {"raw_bytes": 197478978, "wire_bytes": 160074323,
             "ratio": 0.8106, "cpu_seconds": 6.486},
 "metrics": {"sent": {"?": 3136}, "recv": {"PULL": 4, "PUSH": 3132},
             "bytes_out": 160074323, "bytes_in": 54661627}}
```

Ako run nije završen, u koloni ostane tekst — zato parsiranje kao JSON mora da
podnese neuspeh. Figura 7 čita baš ovaj JSON.

**`NULL` u `finished_at` nije greška u podacima, nego podatak.** Run
`checkpoint_a` je namerno ubijen usred obuke, pa nema završne vrednosti. To je
dokaz za stavku *Rad sa fajlovima* i ne treba ga „popravljati”.

### Sekvencijalni baseline je poseban slučaj

`baseline_b128` upisuje `DOC/baseline.py`, a ne server. Kolone koje opisuju
mrežu tu nemaju smisla i to se vidi u podacima:

| Kolona | Vrednost | Zašto |
|---|---|---|
| `mode` | `sequential` | nema ni servera ni radnika |
| `timeout_s` | `NULL` | nema detektora otkaza |
| `interceptors` | `{}` | nema lanca, poruke ne postoje |
| `total_bytes` | `0` | ništa ne ide preko mreže |

Piše u istu tabelu i istim redosledom kolona namerno, da bi figura 1 mogla da
crta sekvencijalni i distribuirani run zajedno, bez posebnog puta za učitavanje.
Ali svaki upit koji sabira saobraćaj mora da ga isključi, inače deli nulom.

---

## `rounds` — vremenska serija obuke

Jedan red po završenoj rundi, upisuje ga `write_row()` u `server.py`. U `sync`
režimu red nastaje kad se barijera razreši, u `async` režimu na svaki `PUSH`.

| Kolona | Jedinica | Kumulativno? | Značenje |
|---|---|---|---|
| `round` | broj | — | redni broj runde, počinje od 1 |
| `samples` | broj | **da** | ukupno uzoraka od početka run-a |
| `train_loss` | — | ne | gubitak na batch-u, težinski prosek po radnicima |
| `test_loss` | — | ne | na izdvojenih 2000 uzoraka |
| `test_acc` | udeo 0–1 | ne | tačnost na test skupu |
| `wall_clock` | s | **da** | od trenutka kad su svi radnici prisutni |
| `active_workers` | broj | ne | koliko ih je bilo živo u toj rundi |
| `bytes_in` | bajtova | **da** | ukupno primljeno na serveru |
| `mean_staleness` | rundi | ne | koliko su gradijenti zaostajali |
| `round_seconds` | s | ne | trajanje same runde |
| `barrier_wait` | s | ne | od prvog do poslednjeg `PUSH`-a u rundi |

### Šta treba znati pre nego što se ovo crta

**`test_loss` i `test_acc` se ne računaju svake runde.** Evaluacija ide na
svakih `--eval-every` rundi (podrazumevano 5) i u prvoj rundi, jer bi inače
merenje trajalo duže od obuke. Između evaluacija se **prethodna vrednost
ponavlja**, ne upisuje se `NULL`:

| round | test_acc | train_loss |
|---|---|---|
| 4 | 0.26 | 2.13825 |
| 5 | **0.6095** | 2.11922 |
| 6 | 0.6095 | 2.05530 |
| 9 | 0.6095 | 1.98427 |
| 10 | **0.7185** | 1.90570 |

Zbog toga kriva tačnosti izgleda stepenasto na malom uvećanju. `train_loss` se,
nasuprot tome, računa svake runde iz podataka koje radnici ionako šalju.

**`samples`, `wall_clock` i `bytes_in` rastu**, ostale kolone su po rundi. Ako
se traži propusnost, deli se poslednji `samples` poslednjim `wall_clock`-om, a
ne sabiraju se kolone.

**`mean_staleness` je nula u celom `sync` režimu** — to je definicija barijere.
U `async` režimu ide do 7 u ovim merenjima. Poređenje te dve vrednosti je
figura 3.

**`barrier_wait` je u `async` režimu uvek 0**, jer barijere nema; kolona tamo
nema značenje.

---

## `worker_rounds` — podaci po pojedinačnom radniku

Ovo je tabela zbog koje CSV nije bio dovoljan: u red „po rundi” ne staje
podatak koji postoji zasebno za svakog radnika.

| Kolona | Jedinica | Značenje |
|---|---|---|
| `round` | broj | runda kojoj doprinos pripada |
| `worker` | tekst | `worker-1`, `worker-2`, … |
| `n_samples` | broj | veličina batch-a koju je server dodelio |
| `seconds` | s | od otpuštanja sa barijere do prispeća `PUSH`-a |
| `staleness` | rundi | koliko je zaostajala verzija težina |

Primer iz `balanced_n4`, runda 100 — vidi se šta balanser radi:

| worker | n_samples | seconds |
|---|---|---|
| worker-1 | 10 | 0.00502 |
| worker-2 | 40 | 0.00505 |
| worker-3 | 39 | 0.00457 |
| worker-4 | 39 | 0.00497 |

Radnik 1 je četiri puta sporiji po uzorku, pa dobija četiri puta manji batch i
stigne u isto vreme kad i ostali. `seconds` su izjednačeni, `n_samples` nisu —
to je cilj, jer barijera čeka najsporijeg.

Napomena o poravnanju: `seconds` meri **ceo obilazak**, uključujući mrežu i
čekanje, a ne samo računanje gradijenta. Zato je to prava veličina za
balansiranje, ali nije čisto vreme računanja.

---

## `events` — diskretni događaji

Jedini deo baze koji se potvrđuje odmah (`commit` posle svakog reda), da
događaj preživi i ako server bude ubijen.

| `kind` | Kada | `detail` sadrži |
|---|---|---|
| `registered` | radnik pošalje `PULL` | popunjenost klastera, npr. `3/4` |
| `evicted` | radnik ispadne | razlog: `timeout` ili `connection closed` |

U trenutnoj bazi: 39 registracija i 37 izbacivanja.

**Većina izbacivanja nisu kvarovi.** Kad se budžet uzoraka potroši, radnici
uredno zatvore vezu i server ih ukloni istim putem kojim uklanja i otkazale
čvorove — zato skoro svaki run ima izbacivanja u poslednjoj rundi. Prava
injekcija kvara se prepoznaje po tome što se dogodila **usred** run-a:

```sql
SELECT r.label, e.round, e.worker, e.detail
FROM events e JOIN runs r USING (run_id)
WHERE e.kind = 'evicted' AND r.label IN ('crash_n4', 'freeze_n4')
ORDER BY e.round LIMIT 3;
```

daje:

| label | round | worker | detail |
|---|---|---|---|
| crash_n4 | 150 | worker-1 | `connection closed` |
| freeze_n4 | 150 | worker-1 | `timeout` |
| crash_n4 | 794 | worker-4 | `connection closed` |

Prva dva reda su cela poenta otpornosti na otkaze, i to u dva različita oblika.
**Pad procesa** zatvori soket, pa ga operativni sistem prijavi odmah — razlog je
`connection closed`. **Zamrznut radnik** drži soket otvorenim i ne šalje ništa,
pa se ne može razlikovati od radnika koji samo dugo računa; njega otkriva tek
istek tajmauta i razlog je `timeout`. Treći red je normalan kraj run-a.

Da je obuka preživela, vidi se poređenjem: kvar je u rundi 150, a `rounds` za
isti run ide do 794.

---

## Čega u bazi *nema*

Namerno, da fajl ne bi rastao bez potrebe:

- **Težine modela** — one idu u `checkpoints/w.npz` preko `N2/checkpoint.py`.
- **Sami gradijenti** — postoje samo u mreži i u memoriji servera.
- **MNIST** — keširan je u `mnist.npz` i nije rezultat merenja.
- **Ispis konzole** — nije nigde snimljen; poruka `resumed from ... at round N`
  se vidi samo uživo, ali se isti podatak može pročitati iz `rounds` (vidi
  ispod).

---

## Koja figura koristi koje kolone

| Figura | Tabela | Kolone |
|---|---|---|
| 1 — ispravnost | `rounds` | `samples`, `test_acc` |
| 2 — ubrzanje | `rounds` | `wall_clock`, `test_acc`, `samples` |
| 3 — sync/async | `rounds` | `wall_clock`, `test_acc`, `mean_staleness` |
| 4 — otpornost | `rounds` | `round`, `test_acc`, `round_seconds`, `active_workers` |
| 5 — saobraćaj | `rounds` | `samples`, `bytes_in` |
| 6 — balansiranje | `worker_rounds` + `rounds` | `n_samples`, `seconds`, `barrier_wait` |
| 7 — interceptori | `runs` | `interceptors` (JSON) |

---

## Korisni upiti

**Pregled svih run-ova:**

```sql
SELECT run_id, label, mode, n_workers, total_rounds, total_samples,
       ROUND(total_bytes / 1e6, 2) AS mb
FROM runs ORDER BY run_id;
```

**Završna tačnost svakog run-a:**

```sql
SELECT r.label, MAX(d.round) AS last_round, d.test_acc
FROM rounds d JOIN runs r USING (run_id)
GROUP BY r.run_id ORDER BY r.run_id;
```

**Dokaz da je `--resume` proradio** — gde jedan run stane, drugi nastavi:

```sql
SELECT r.label, MIN(d.round) AS od, MAX(d.round) AS do_
FROM rounds d JOIN runs r USING (run_id)
WHERE r.label LIKE 'checkpoint%' GROUP BY r.run_id;
```

Daje `checkpoint_a` od 1 do 30 i `checkpoint_b` od 31 do 1565: bez preklapanja
i bez rupe.

**Prosečno čekanje na barijeri, statički prema dinamičkom** (prvih 20 rundi se
preskače jer balanser tek uči brzine):

```sql
SELECT r.label, ROUND(AVG(d.barrier_wait), 4) AS wait_s
FROM rounds d JOIN runs r USING (run_id)
WHERE r.label IN ('static_n4', 'balanced_n4') AND d.round > 20
GROUP BY r.run_id;
```

---

## Pravila kojih se treba držati

**Ista labela može da postoji više puta.** Ponovno pokretanje ne briše stari
run, nego dodaje novi. Zato svako čitanje ide kroz `store.latest_run()`, koji
uzima najveći `run_id` za datu labelu. Ako se poredi „ručno”, mora se filtrirati
po `run_id`, inače se mešaju dva različita merenja.

**Piše samo server.** Radnici bazu ne dodiruju, pa postoji tačno jedan pisac i
klasičan SQLite problem sa više pisaca se ne javlja.

**WAL režim je uključen** (`PRAGMA journal_mode=WAL`) da bi `plot.py` mogao da
čita bazu dok run još traje. Zbog toga pored `.sqlite` nastaju i
`.sqlite-wal` i `.sqlite-shm`; to su privremeni fajlovi, sadržaj im se prelije
u glavni fajl kad se poslednja veza zatvori, i zato su u `.gitignore`.

**Redovi po rundi se ne potvrđuju odmah** nego na svakih 50 rundi kroz
`flush()`, jer bi upis na disk svake runde bio skuplji od same obuke. Pre
svakog checkpointa ide dodatni `flush()`, da posle pada važi pravilo: baza
sadrži bar onu rundu koja piše u checkpointu.
