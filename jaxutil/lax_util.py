import jax
from jax import numpy as jnp, vmap, eval_shape, lax
from jax.experimental.host_callback import id_tap
from tqdm.auto import tqdm
from inspect import signature
from jax.tree_util import tree_leaves, tree_flatten, tree_unflatten
from jaxopt.tree_util import *

tree_len = lambda tree: len(tree_leaves(tree)[0])


def tree_stack(trees):
    _, treedef = tree_flatten(trees[0])
    leaf_list = [tree_flatten(tree)[0] for tree in trees]
    leaf_stacked = [jnp.stack(leaves) for leaves in zip(*leaf_list)]
    return tree_unflatten(treedef, leaf_stacked)


def batch_split(
    batch,
    n_batch: int = None,
    batch_size: int = None,
    devices: tuple = None,
    strict=True,
):
    if devices is not None:
        batches = batch_split(batch, n_batch=len(devices))
        batches = tree_map(lambda x: jax.device_put_sharded(list(x), devices), batches)
        return batches

    n = tree_len(batch)

    if n_batch is not None:
        batch_size = n // n_batch
    elif batch_size is not None:
        n_batch = n // batch_size

    if strict:
        assert n_batch * batch_size == n
    else:
        batch = tree_map(lambda x: x[: n_batch * batch_size], batch)

    batches = tree_map(lambda x: x.reshape((n_batch, batch_size, *x.shape[1:])), batch)
    return batches


def fold(
    f,
    state,
    data=None,
    steps=None,
    backend="python",
    jit=False,
    show_progress=False,
    save_every=1,
):
    if len(signature(f).parameters) == 1:
        save_step = lambda state, x: f(state)
    elif len(signature(f).parameters) == 2:
        save_step = f
    else:
        raise Exception("Fold function must take either one or two arguments.")

    step = lambda state, x: (save_step(state, x)[0], None)
    if jit:
        save_step = jax.jit(save_step)
        step = jax.jit(step)

    n = tree_len(data) if data is not None else steps
    n_batch = n // save_every

    if backend == "python":
        n = tree_len(data) if data is not None else steps
        if show_progress:
            pbar = tqdm(total=n_batch)
        save_list = []
        for i in range(n_batch * save_every):
            batch = tree_map(lambda x: x[i], data) if data is not None else None
            if i % save_every == 0:
                state, save = save_step(state, batch)
                save_list.append(save)
                if show_progress:
                    pbar.update()
            else:
                state, _ = step(state, batch)
        save_list = tree_stack(save_list)
        if show_progress:
            pbar.close()
        return state, save_list

    elif backend == "lax":
        n = tree_len(data) if data is not None else steps
        n_batch = n // save_every
        if show_progress:
            pbar = tqdm(total=n_batch)
        if data is not None:
            if save_every > 1:
                data = batch_split(data, batch_size=save_every, strict=False)

                def epoch(state, batch):
                    x0 = tree_map(lambda x: x[0], batch)
                    state, save = save_step(state, x0)

                    sub_batch = tree_map(lambda x: x[1:], batch)
                    state = lax.scan(step, state, sub_batch)[0]

                    if show_progress:
                        id_tap(lambda *_: pbar.update(), None)

                    return state, save

            else:

                def epoch(state, x):
                    state, save = save_step(state, x)
                    if show_progress:
                        id_tap(lambda *_: pbar.update(), None)
                    return state, save

            output = lax.scan(epoch, state, data)
        else:

            def epoch(state, _):
                state, save = save_step(state, None)
                if save_every > 1:
                    state = lax.scan(step, state, None, length=save_every - 1)[0]
                if show_progress:
                    id_tap(lambda *_: pbar.update(), None)
                return state, save

            output = lax.scan(epoch, state, None, n_batch)
        if show_progress:
            pbar.close()
        return output


def laxmap(f, data, batch_size=None, **kwargs):
    if batch_size == None:
        return fold(lambda _, x: (None, f(x)), None, data, **kwargs)[1]
    else:
        batches = batch_split(data, batch_size=batch_size)
        batched_out = fold(
            lambda _, batch: (None, vmap(f)(batch)), None, batches, **kwargs
        )[1]
        flat_out = tree_map(lambda x: x.reshape(-1, *x.shape[2:]), batched_out)
        return flat_out


def laxsum(f, data, batch_size=None, **kwargs):
    x0 = tree_map(lambda x: x[0], data)
    sum_init = tree_map(lambda x: jnp.zeros(x.shape, dtype=x.dtype), eval_shape(f, x0))
    if batch_size == None:
        return fold(
            lambda avg, x: (tree_add(avg, f(x)), None), sum_init, data, **kwargs
        )[0]
    else:

        def batched_f(batch):
            out_tree = vmap(f)(batch)
            return tree_map(lambda x: x.sum(0), out_tree)

        batches = batch_split(data, batch_size=batch_size)
        return fold(
            lambda avg, batch: (tree_add(avg, batched_f(batch)), None),
            sum_init,
            batches,
            **kwargs
        )[0]


def laxmean(f, data, *args, **kwargs):
    n = tree_len(data)
    _f = lambda *args: tree_scalar_mul(1 / n, f(*args))
    return laxsum(_f, data, *args, **kwargs)
