# Distribuirani okvir za mašinsko učenje (CS230, tema 20)

Parameter server + N radnika, goli TCP soketi i `select()`, bez ZeroMQ-a i bez
ML biblioteke. Softmax regresija nad MNIST-om, obučavana sinhrono ili
asinhrono preko više procesa, sa injekcijom otkaza, balansiranjem opterećenja
na strani servera, middleware lancem interceptora i SQLite skladištem metrika.

## Fajlovi

| fajl | uloga |
|---|---|
| `N1/common.py` | učitavanje i keširanje MNIST-a, model, gradijenti |
| `N1/net.py` | framing sa prefiksom dužine preko TCP-a; propušta poruke kroz lanac interceptora |
| `N2/interceptors.py` | middleware: float32 gradijenti, deflate, metrike, veštačko kašnjenje |
| `N1/server.py` | parameter server: barijera, detekcija otkaza, dodela posla, logovanje |
| `N1/worker.py` | radni čvor: lokalni gradijenti, injekcija otkaza, raspored koji vodi server |
| `N2/balancer.py` | procena propusnosti i dodela veličine batch-a |
| `N2/checkpoint.py` | atomski checkpoint težina, tako da je i sam server oporavljiv |
| `N2/store.py` | SQLite šema i pomoćne funkcije za čitanje |
| `DOC/baseline.py` | sekvencijalni referentni run, bez mreže |
| `DOC/plot.py` | sve figure |
| `DOC/run_experiments.sh` | svi run-ovi iza svih figura |
| `smoke_test.py` | provera svih režima od kraja do kraja, po jedna epoha (~1 min) |
| `BAZA.md` | rečnik podataka: svaka tabela i kolona u `results/runs.sqlite` |

## Pokretanje

Sve se pokreće iz korena projekta, uz koren na `PYTHONPATH`-u, da bi `N1` i `N2`
bili uvozivi. `run_experiments.sh` to radi umesto tebe; ako proces pokrećeš
ručno, moraš sam:

```bash
export PYTHONPATH=.                                      # ili koristi: python -m N1.server
python -c "from N1 import common; common.load_train()"   # preuzmi MNIST jednom, pre svega paralelnog
bash DOC/run_experiments.sh
python DOC/plot.py
```

Ili jedna konfiguracija ručno:

```bash
export PYTHONPATH=.
python N1/server.py --workers 4 --mode sync --balance dynamic \
       --epochs 10 --label my_run &
for i in 1 2 3 4; do python N1/worker.py --id $i --delay-per-sample 0.0001 & done
```

Bez `PYTHONPATH`-a, `python N1/server.py` puca sa
`ModuleNotFoundError: No module named 'N2'`, jer Python na `sys.path` stavlja
direktorijum *skripte*, a ne direktorijum iz kog si je pokrenuo.

Brza provera svih režima od kraja do kraja, bez preuzimanja MNIST-a:

```bash
python smoke_test.py          # svih sedam scenarija
python smoke_test.py sync     # samo jedan
```

## Kako se delovi preslikavaju na tabelu za ocenjivanje

**N1 — klijent-server (3).** `N1/server.py` + `N1/worker.py`. Jedan soket koji
sluša, `select()` nad svim vezama, eksplicitan framing poruka u `N1/net.py` jer
je TCP tok bajtova i `recv(n)` sme da vrati manje od `n` bajtova.

**N1 — rad sa fajlovima (2).** `N2/checkpoint.py` upisuje `W` na disk na svakih
K rundi, a `--resume` ga vraća. Upis ide
temp fajl → `fsync` → `os.replace`, što je atomsko unutar fajl sistema, pa pad
usred pisanja ne može da ostavi napola čitljiv checkpoint. Time se zatvara rupa
u originalnom dizajnu: radnici su bili preživljivi, server nije.

**N2 — middleware / interceptori (3).** `N2/interceptors.py`. Lanac se izvršava
napred-nazad pri slanju i nazad-napred pri prijemu, na dva nivoa: objektni nivo
(pre pickle-a) i bajtni nivo (posle). Sužavanje gradijenata na float32 je
objektni nivo i jednostrano je; deflate je bajtni nivo i obe strane moraju da se
slože. Brojanje bajtova i veštačko WAN kašnjenje su takođe interceptori, i zato
ih ne pominju ni petlja za obuku ni kod za soket.

**N2 — rad sa bazom (3).** `N2/store.py`, SQLite, četiri tabele: `runs` (jedan
red po run-u sa punom konfiguracijom), `rounds` (metrike po rundi), `events`
(registracije i izbacivanja sa razlozima), `worker_rounds` (koji radnik je
obradio koliko uzoraka i za koje vreme). Piše samo server, pa postoji jedan
pisac i nema problema sa zaključavanjem; WAL je uključen da bi `DOC/plot.py`
mogao da čita dok run traje. Ovo je jedino mesto gde se metrike upisuju — nema
paralelnog CSV-a koji bi se razišao sa bazom.

**N2 — load balancer (2).** `N2/balancer.py`, uključuje se sa `--balance
dynamic`. Server drži EWMA sekundi po uzorku za svakog radnika i deli konstantan
globalni batch srazmerno izmerenoj brzini, pa spor čvor dobija manji batch
umesto da zadržava barijeru.

## Jedan detalj ispravnosti koji vredi spremiti za odbranu

Svaki radnik vraća gradijent koji je **srednja vrednost nad njegovim batch-om**.
Prosečiti te srednje vrednosti sa jednakom težinom je ispravno samo kad su
batch-evi jednake veličine. Uz dinamičko balansiranje nisu, pa
`resolve_barrier()` koristi težinsku kombinaciju

    sum(n_i * g_i) / sum(n_i)

koja se teleskopski svodi na srednju vrednost nad unijom batch-eva. Običan
`np.mean` bi precenio manje batch-eve sporih radnika i tiho promenio funkciju
cilja koja se optimizuje. Zato i tvrdnja „N sinhronih radnika sa batch-om B
ponaša se kao jedan čvor sa batch-om N·B” i dalje važi kad je balansiranje
uključeno.

## Izmereni rezultati (MNIST, 4 radnika, jedan sporiji sa 4× cenom po uzorku)

| | statička podela | dinamičko balansiranje |
|---|---|---|
| uzoraka po radniku po rundi | 32 / 32 / 32 / 32 | 10 / 39 / 40 / 38 |
| sekundi po radniku po rundi | 0.0142 / 0.0047 / 0.0046 / 0.0046 | 0.0052 / 0.0051 / 0.0052 / 0.0052 |
| prosečno čekanje na barijeri | 0.0099 s | 0.0007 s |
| prosečno trajanje runde | 0.0157 s | 0.0068 s |

Balanser konvergira ka udelima kod kojih svakom radniku treba istih ~5.2 ms, a
to je cela poenta: barijera se otpušta čim stigne poslednji radnik, pa su bitna
jednaka *vremena*, a ne jednaki *batch-evi*. Čekanje na barijeri pada 14×, a
trajanje runde više nego prepolovljeno.

Kompresija, 4 radnika, isti budžet uzoraka (783 runde):

| | bajtova primljenih na serveru | stvarno vreme (2 run-a) |
|---|---|---|
| ništa | 197.49 MB | 5.6 s / 5.5 s |
| float32 gradijenti | 99.26 MB | 6.6 s / 5.3 s |
| float32 + deflate nivo 6 | 54.67 MB | 11.3 s / 12.9 s |

Brojevi bajtova su tačni i reprodukuju se bit po bit; stvarna vremena ne, pa su
prikazana dva run-a umesto jednog. Sužavanje na float32 prepolovljava saobraćaj
uz CPU cenu koja se gubi u šumu — vredi ga uključiti bezuslovno. Deflate povrh
toga skida još 45%, ali košta 6–7 s na run-u koji inače traje 5 s, pa se isplati
tek kad je veza dovoljno spora da tih 45 MB koje uštedi putuje duže od toga —
otprilike ispod 60 Mbit/s. Na loopback-u je jasan neto gubitak; to treba
prijaviti kao negativan rezultat, a ne kriti, jer je merenje kompromisa i bila
poenta. Uz to, 45% je daleko više nego što deflate postiže nad slučajnim
podacima: pravi gradijenti su vrlo kompresibilni, pa bi testiranje ovoga na
sintetičkom zadatku potcenilo interceptor.

## Reprodukcija ranijeg rezultata

Sve što je dodato je podrazumevano isključeno: `--balance static`, bez
interceptora osim brojanja bajtova, `--db` upisuje novi red za run umesto da
dira stare. Run bez novih zastavica daje istu putanju kao originalni kod.
