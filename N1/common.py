import os

import numpy as np

N_FEATURES = 784
N_CLASSES = 10

N_TRAIN = 10000
N_TEST = 2000

DATASET = os.environ.get("DATASET", "mnist").lower()
_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../mnist.npz")

TRUE_W_SEED = 1234
DATA_SEED = 7
TEST_SEED = 99

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
        _cached["mnist"] = (d["X"].astype(np.float64), d["y"])
    return _cached["mnist"]


# Pretvara vektor oznaka u one-hot matricu.
def _onehot(y):
    Y = np.zeros((len(y), N_CLASSES))
    Y[np.arange(len(y)), y] = 1.0
    return Y


# Vraca fiksnu matricu kojom se generisu oznake sintetickog zadatka.
def _true_w():
    return np.random.default_rng(TRUE_W_SEED).standard_normal((N_FEATURES, N_CLASSES))


# Generise sinteticki skup od n uzoraka za dato seme.
def _make(n, seed):
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, N_FEATURES))
    y = np.argmax(X @ _true_w(), axis=1)
    return X, _onehot(y)


# Vraca skup za treniranje: ulaze i one-hot oznake.
def load_train():
    if DATASET == "synthetic":
        return _make(N_TRAIN, DATA_SEED)
    X, y = _mnist()
    return X[:N_TRAIN], _onehot(y[:N_TRAIN])


# Vraca test skup: ulaze i one-hot oznake.
def load_test():
    if DATASET == "synthetic":
        return _make(N_TEST, TEST_SEED)
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
