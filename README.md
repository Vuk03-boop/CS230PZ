# Distribuirani okvir za mašinsko učenje

Projekat iz predmeta CS230, tema 20. Softmax regresija nad MNIST skupom,
obučavana preko više procesa po *parameter server* topologiji, nad golim TCP
soketima — bez ZeroMQ-a i bez ML okvira.

## Mogućnosti

- Parameter server i N radnika, `select()` petlja, sopstveni protokol sa
  prefiksom dužine
- Sinhroni i asinhroni režim obuke
- Detekcija otkaza na dva nezavisna signala (prekinuta veza i istek tajmauta)
- Injekcija kvara: pad procesa i zamrznut čvor
- Middleware lanac interceptora: float32 gradijenti, deflate, metrike, veštačko
  kašnjenje
- Dinamičko balansiranje opterećenja na osnovu izmerene brzine radnika
- Atomski checkpoint težina i nastavak obuke sa `--resume`
- SQLite skladište metrika

## Zahtevi

Python 3.11+ (rezultati su pravljeni na 3.14), uz `numpy`, `pandas`,
`matplotlib` i `scikit-learn`.

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install numpy pandas matplotlib scikit-learn
```

`scikit-learn` je potreban samo za jednokratno preuzimanje MNIST-a.

## Pokretanje

Sve se pokreće iz korena projekta, uz koren na `PYTHONPATH`-u, da bi paketi
`N1` i `N2` bili uvozivi.

```bash
export PYTHONPATH=.
python -c "from N1 import common; common.load_train()"   # preuzmi MNIST jednom
bash DOC/run_experiments.sh                              # svi eksperimenti
python DOC/plot.py                                       # figure u figures/
```

Jedna konfiguracija ručno:

```bash
export PYTHONPATH=.
python N1/server.py --workers 4 --mode sync --balance dynamic \
       --epochs 10 --label my_run &
for i in 1 2 3 4; do python N1/worker.py --id $i --delay-per-sample 0.0001 & done
```

Brza provera svih režima, po jedna epoha, bez preuzimanja MNIST-a:

```bash
python smoke_test.py          # svih sedam scenarija
python smoke_test.py sync     # samo jedan
```

## Struktura

```
N1/     klijent-server sloj: model, protokol, server, radnik
N2/     middleware sloj: interceptori, balanser, checkpoint, baza
DOC/    sekvencijalni baseline, crtanje figura, skripta eksperimenata
results/  runs.sqlite — sve izmerene metrike
figures/  generisane figure (PNG)
```

| Fajl | Uloga |
|---|---|
| `N1/common.py` | MNIST, model, gradijent, evaluacija |
| `N1/net.py` | framing poruka preko TCP-a |
| `N1/server.py` | parameter server: barijera, detekcija otkaza, dodela posla |
| `N1/worker.py` | radni čvor i injekcija kvara |
| `N2/interceptors.py` | lanac interceptora |
| `N2/balancer.py` | procena brzine i dodela veličine batch-a |
| `N2/checkpoint.py` | atomski checkpoint težina |
| `N2/store.py` | SQLite šema i čitanje |
| `DOC/baseline.py` | sekvencijalni referentni run |
| `DOC/plot.py` | sve figure |
| `DOC/run_experiments.sh` | svi run-ovi iza figura |
| `smoke_test.py` | provera svih režima od kraja do kraja |

## Rezultati

MNIST, 4 radnika, jedan sporiji sa 4× cenom po uzorku:

| | statička podela | dinamičko balansiranje |
|---|---|---|
| prosečno čekanje na barijeri | 0.0099 s | 0.0007 s |
| prosečno trajanje runde | 0.0157 s | 0.0068 s |

Kompresija, 4 radnika, isti budžet uzoraka (783 runde):

| | bajtova primljenih na serveru | stvarno vreme |
|---|---|---|
| bez interceptora | 197.49 MB | 5.6 s |
| float32 gradijenti | 99.26 MB | 6.6 s |
| float32 + deflate 6 | 54.67 MB | 11.3 s |

Sve nadogradnje su podrazumevano isključene, pa run bez dodatnih zastavica daje
istu putanju kao osnovna verzija.

## Dokumentacija

- [ODBRANA.md](ODBRANA.md) — objašnjenje po stavkama iz tabele za ocenjivanje,
  korišćene biblioteke i očekivana pitanja
- [BAZA.md](BAZA.md) — rečnik podataka za `results/runs.sqlite`
