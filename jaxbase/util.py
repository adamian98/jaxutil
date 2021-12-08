import jax
from jax import numpy as jnp
import numpy as np
from jax import lax
from functools import reduce
from jax.experimental.host_callback import id_tap
from tqdm.auto import tqdm, trange


def clean_dict(d):
    return {
        key: val.item() if isinstance(val, (np.ndarray, jnp.ndarray)) else val
        for key, val in d.items()
    }


def tree_stack(trees):
    _, treedef = jax.tree_flatten(trees[0])
    leaf_list = [jax.tree_flatten(tree)[0] for tree in trees]
    leaf_stacked = [jnp.stack(leaves) for leaves in zip(*leaf_list)]
    return jax.tree_unflatten(treedef, leaf_stacked)


def zeros_like_output(f, x):
    pytree = jax.eval_shape(f, x)
    return jax.tree_map(
        lambda leaf: jnp.zeros(shape=leaf.shape, dtype=leaf.dtype), pytree
    )


def batch_split(batch, n_batch: int = None, batch_size: int = None, strict=True):
    n = len(jax.tree_leaves(batch)[0])

    if n_batch is not None and batch_size is not None:
        raise Exception("Cannot specify both n_batch and batch_size")
    elif n_batch is not None:
        batch_size = n // n_batch
    elif batch_size is not None:
        n_batch = n // batch_size
    else:
        raise Exception("Need to specify either n_batch or batch_size")

    if strict:
        assert n_batch * batch_size == n
    else:
        batch = jax.tree_map(lambda x: x[: n_batch * batch_size], batch)
    batches = jax.tree_map(
        lambda x: x.reshape((n_batch, batch_size, *x.shape[1:])), batch
    )
    return batches


def _fold_lax(f, state, data, show_progress=False):
    n = len(jax.tree_leaves(data)[0])
    first_batch = jax.tree_map(lambda x: x[0], data)
    output_tree = jax.eval_shape(lambda args: f(*args), (state, first_batch))
    assert len(output_tree) == 3
    avg_init = jax.tree_map(
        lambda leaf: jnp.zeros(shape=leaf.shape, dtype=leaf.dtype), output_tree[2]
    )

    if show_progress:
        pbar = tqdm(total=n)

    def step(carry, batch):
        state, avg = carry
        batch_state, batch_out, batch_avg = f(state, batch)
        avg = jax.tree_multimap(lambda si, fi: si + fi / n, avg, batch_avg)
        if show_progress:
            id_tap(lambda *_: pbar.update(), None)
        return (batch_state, avg), batch_out

    (state, average), outputs = lax.scan(step, (state, avg_init), data)
    if show_progress:
        pbar.close()
    return state, outputs, average


def _fold_python(f, state, data, show_progress=False):
    n = len(jax.tree_leaves(data)[0])
    first_batch = jax.tree_map(lambda x: x[0], data)
    output_tree = jax.eval_shape(lambda args: f(*args), (state, first_batch))
    assert len(output_tree) == 3
    average = jax.tree_map(
        lambda leaf: jnp.zeros(shape=leaf.shape, dtype=leaf.dtype), output_tree[2]
    )
    outputs = []

    if show_progress:
        iterator = trange(n)
    else:
        iterator = range(n)

    for i in iterator:
        batch = jax.tree_map(lambda x: x[i], data)
        state, batch_out, batch_avg = f(state, batch)
        outputs.append(batch_out)
        average = jax.tree_multimap(lambda si, fi: si + fi / n, average, batch_avg)
    outputs = tree_stack(outputs)
    return state, outputs, average


def fold(f, state, data, show_progress=False, backend="lax"):
    if backend == "lax":
        return _fold_lax(f, state, data, show_progress)
    else:
        return _fold_python(f, state, data, show_progress)


def laxmean(f, data, show_progress=False, backend="lax"):
    _f = lambda _, batch: (None, None, f(batch))
    fold(_f, None, data, show_progress=show_progress, backend=backend)
