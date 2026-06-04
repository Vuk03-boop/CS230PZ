import os

import numpy as np

N_FEATURES = 784
N_CLASSES = 10

N_TRAIN = 10000
N_TEST = 2000

DATASET = "mnist"
_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../mnist.npz")

DATA_SEED = 7

_cached = {}


# Preuzima MNIST sa OpenML-a i cuva ga u lokalni npz kes.
def _download_mnist():
    from sklearn.datasets import fetch_openml
    print("[*] downloading MNIST from OpenML (once, ~11 MB compressed)...")
    X, y = fetch_openml("mnist_784", version=1, return_X_y=True, as_frame=False)
    X = np.asarray(X, dtype=np.float64) / 255.0
    y = np.asarray(y, dtype=np.int64)
    rng = np.random.default_rng(DATA_SEED)
    perm = rng.permutation(len(X))
    np.savez_compressed(_CACHE, X=X[perm].astype(np.float32), y=y[perm])
    print(f"[*] cached in {_CACHE}")


# Ucitava kesirani MNIST, a preuzima ga ako kes ne postoji.
def _mnist():
    if "mnist" not in _cached:
        if not os.path.exists(_CACHE):
            _download_mnist()
        d = np.load(_CACHE)
        # Isecanje ide pre pretvaranja u float64. Od 70000 uzoraka koriste se
        # samo prvih N_TRAIN + N_TEST, a cela matrica u float64 zauzima 439 MB
        # po procesu; ovako je 75 MB, sto puta pet procesa u klasteru cini
        # razliku od skoro dva gigabajta.
        n = N_TRAIN + N_TEST
        _cached["mnist"] = (d["X"][:n].astype(np.float64), d["y"][:n])
    return _cached["mnist"]


# Pretvara vektor oznaka u one-hot matricu.
def _onehot(y):
    Y = np.zeros((len(y), N_CLASSES))
    Y[np.arange(len(y)), y] = 1.0
    return Y


# Vraca skup za treniranje: ulaze i one-hot oznake.
def load_train():
    X, y = _mnist()
    return X[:N_TRAIN], _onehot(y[:N_TRAIN])


# Vraca test skup: ulaze i one-hot oznake.
def load_test():
    X, y = _mnist()
    return X[N_TRAIN:N_TRAIN + N_TEST], _onehot(y[N_TRAIN:N_TRAIN + N_TEST])


# Inicijalizuje matricu tezina na nule; poslednji red je bias.
def init_weights():
    return np.zeros((N_FEATURES + 1, N_CLASSES))


# Dodaje ulazima kolonu jedinica zbog bias clana.
def _augment(X):
    return np.hstack([X, np.ones((len(X), 1))])


# Racuna softmax po redovima na numericki stabilan nacin.
def softmax(z):
    z = z - np.max(z, axis=1, keepdims=True)
    e = np.exp(z)
    return e / np.sum(e, axis=1, keepdims=True)


# Racuna gradijent, gubitak i tacnost za jedan batch.
def forward_backward(W, X, Y):
    Xb = _augment(X)
    P = softmax(Xb @ W)
    loss = -np.mean(np.sum(Y * np.log(P + 1e-9), axis=1))
    acc = np.mean(np.argmax(P, axis=1) == np.argmax(Y, axis=1))
    grad = Xb.T @ (P - Y) / len(X)
    return grad, loss, acc


# Racuna gubitak i tacnost na izdvojenom test skupu.
def evaluate(W, X, Y):
    P = softmax(_augment(X) @ W)
    loss = -np.mean(np.sum(Y * np.log(P + 1e-9), axis=1))
    acc = np.mean(np.argmax(P, axis=1) == np.argmax(Y, axis=1))
    return loss, acc
