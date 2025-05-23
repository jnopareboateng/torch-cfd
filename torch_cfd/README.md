## TODO

- [x] add native PyTorch implementation for applying `torch.linalg` and `torch.fft` function directly on `GridArray`.
- [x] add discrete Helmholtz decomposition in both spatial and spectral domains.
- [x] adjust the function to act on `(batch, time, *spatial)` tensor, currently only `(*spatial)` is supported.
- [x] add native vorticity computation, instead of taking FDM for pseudo-spectral.

## Changelog

### 0.1.0
- Implemented the FVM method on a staggered MAC grid (pressure on cell centers).
- Added native PyTorch implementation for applying `torch.linalg` and `torch.fft` functions directly on `GridArray` and `GridVariable`.
- Added native implementation of arithmetic manipulation directly on `GridVariableVector`.
- Added several helper functions `consistent_grid` to replace `consistent_grid_arrays`.
- Removed dependence of `from torch.utils._pytree import register_pytree_node`
- Minor notes:
  - Added native PyTorch dense implementation of `scipy.linalg.circulant`: for a 1d array `column`
    ```python
    # scipy version
    mat = scipy.linalg.circulant(column)

    # torch version
    idx = (n - torch.arange(n)[None].T + torch.arange(n)[None]) % n
    mat = torch.gather(column[None, ...].expand(n, -1), 1, idx)
    ```


### 0.0.8
- Starting from PyTorch 2.6.0, if data are saved using serialization (for loop with `pickle` or `dill`), then `torch.load` will raise an error, if you want to load the data, you can either add this in the imports or re-generate the data using this version.
    ```python
    torch.serialization.add_safe_globals([defaultdict])
    torch.serialization.add_safe_globals([list])
    ```

### 0.0.6
- Minor changes in function names, added `sfno` directory and moved `get_trajectory_imex` and `get_trajectory_rk4` to the data generation folder.

### 0.0.5
- added a batch dimension in solver matching. By default, the solver should work for input shapes `(batch, kx, ky)` or `(kx, ky)`. `get_trajectory()` output is either `(n_t, kx, ky)` or `(batch, n_t, kx, ky)`.


### 0.0.4
- The forcing functions are now implemented as `nn.Module` and utilize a wrapper decorator for the potential function.
- Added some common time stepping schemes, additional ones that Jax-CFD did not have includes the commonly used Crank-Nicholson IMEX.
- Combined the implementation for step size satisfying the CFL condition.


### 0.0.1
- `grids.GridArray` is implemented as a subclass of `torch.Tensor`, not the original jax implentation uses the inheritance from `np.lib.mixins.NDArrayOperatorsMixin`. `__array_ufunc__()` is replaced by `__torch_function__()`.
- The padding of `torch.nn.functional.pad()` is different from `jax.numpy.pad()`, PyTorch's pad starts from the last dimension, while Jax's pad starts from the first dimension. For example, `F.pad(x, (0, 0, 1, 0, 1, 1))` is equivalent to `jax.numpy.pad(x, ((1, 1), (1, 0), (0, 0)))` for an array of size `(*, t, h, w)`.
- A handy outer sum, which is usefully in getting the n-dimensional Laplacian in the frequency domain, is implemented as follows to replace `reduce(np.add.outer, eigenvalues)`
    ```python
    def outer_sum(x: Union[List[torch.Tensor], Tuple[torch.Tensor]]) -> torch.Tensor:
        """
        Returns the outer sum of a list of one dimensional arrays
        Example:
        x = [a, b, c]
        out = a[..., None, None] + b[..., None] + c
        """

        def _sum(a, b):
            return a[..., None] + b

        return reduce(_sum, x)
    ```