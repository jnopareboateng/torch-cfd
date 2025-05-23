# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Modifications copyright (C) 2024 S.Cao
# ported Google's Jax-CFD functional template to PyTorch's tensor ops

"""Fast diagonalization method for inverting linear operators."""
from functools import reduce
from typing import Callable, Optional, Sequence, Union, List, Tuple

import numpy as np

import torch
import torch.fft as fft



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


def transform(
    func: Callable[[torch.Tensor], torch.Tensor],
    operators: Sequence[torch.Tensor],
    dtype: torch.dtype,
    *,
    hermitian: bool = False,
    circulant: bool = False,
    implementation: Optional[str] = None,
) -> Callable[[torch.Tensor], torch.Tensor]:
    """Apply a linear operator written as a sum of operators on each axis.

    Such linear operators are *separable*, and can be written as a sum of tensor
    products, e.g., `operators = [A, B]` corresponds to the linear operator
    A ⊗ I + I ⊗ B, where the tensor product ⊗ indicates a separation between
    operators applied along the first and second axis.

    This function computes matrix-valued functions of such linear operators via
    the "fast diagonalization method" [1]:
      F(A ⊗ I + I ⊗ B)
      = (X(A) ⊗ X(B)) F(Λ(A) ⊗ I + I ⊗ Λ(B)) (X(A)^{-1} ⊗ X(B)^{-1})

    where X(A) denotes the matrix of eigenvectors of A and Λ(A) denotes the
    (diagonal) matrix of eigenvalues. The function `F` is easy to compute in
    this basis, because matrix Λ(A) ⊗ I + I ⊗ Λ(B) is diagonal.

    The current implementation directly diagonalizes dense matrices for each
    linear operator, which limits it's applicability to grids with less than
    1e3-1e4 elements per side (~1 second to several minutes of setup time).

    Example: The Laplacian operator can be written as a sum of 1D Laplacian
    operators along each axis, i.e., as a sum of 1D convolutions along each axis.
    This can be seen mathematically (∇² = ∂²/∂x² + ∂²/∂y² + ∂²/∂z²) or by
    decomposing the 2D kernel:

      [0  1  0]               [ 1]
      [1 -4  1] = [1 -2  1] + [-2]
      [0  1  0]               [ 1]

    Args:
      func: NumPy function applied in the diagonal basis that is passed the
        N-dimensional array of eigenvalues (one dimension for each linear
        operator).
      operators: forward linear operators as matrices, applied along each axis.
        Each of these matrices is diagonalized.
      dtype: dtype of the right-hand-side.
      hermitian: whether or not all linear operator are Hermitian (i.e., symmetric
        in the real valued case).
      circulant: whether or not all linear operators are circulant.
      implementation: how to implement fast diagonalization. Default uses 'rfft'
        for grid size larger than 1024 and 'matmul' otherwise:
        - 'matmul': scales like O(N**(d+1)) for d N-dimensional operators, but
          makes good use of matmul hardware. Requires hermitian=True.
        - 'fft': scales like O(N**d * log(N)) for d N-dimensional operators.
          Requires circulant=True.
        - 'rfft': use the RFFT instead of the FFT. This is a little faster than
          'fft' but also has slightly larger error. It currently requires an even
          sized last axis and circulant=True.
      precision: numerical precision for matrix multplication. Only relevant on
        TPUs with implementation='matmul'.

    Returns:
      A function that computes the transformation of the indicated operator.

    References:
    [1] Lynch, R. E., Rice, J. R. & Thomas, D. H. Direct solution of partial
        difference equations by tensor product methods. Numer. Math. 6, 185–199
        (1964). https://paperpile.com/app/p/b7fdea4e-b2f7-0ada-b056-a282325c3ecf
    """
    if any(op.ndim != 2 or op.shape[0] != op.shape[1] for op in operators):
        raise ValueError(
            "operators are not all square matrices. Shapes are "
            + ", ".join(str(op.shape) for op in operators)
        )

    if implementation is None:
        implementation = "rfft"
    if implementation == "rfft" and operators[-1].shape[0] % 2:
        implementation = "matmul"

    if implementation == "matmul":
        if not hermitian:
            raise ValueError(
                "non-hermitian operators not yet supported with "
                'implementation="matmul"'
            )
        return _hermitian_matmul_transform(func, operators, dtype)
    elif implementation == "fft":
        if not circulant:
            raise ValueError(
                "non-circulant operators not yet supported with " 'implementation="fft"'
            )
        return _circulant_fft_transform(func, operators, dtype)
    elif implementation == "rfft":
        if not circulant:
            raise ValueError(
                "non-circulant operators not yet supported with "
                'implementation="rfft"'
            )
        return _circulant_rfft_transform(func, operators, dtype)
    else:
        raise ValueError(f"invalid implementation: {implementation}")


def _hermitian_matmul_transform(
    func: Callable[[torch.Tensor], torch.Tensor],
    operators: Sequence[torch.Tensor],
    dtype: torch.dtype,
) -> Callable[[torch.Tensor], torch.Tensor]:
    """Fast diagonalization by matrix multiplication along each axis."""
    eigenvalues, eigenvectors = zip(*map(torch.linalg.eigh, operators))

    # Example: if eigenvalues=[a, b, c], then:
    #   summed_eigenvalues[i, j, k] == a[i] + b[j] + c[k]
    # for all i, j, k.
    summed_eigenvalues = outer_sum(eigenvalues)
    diagonals = torch.asarray(func(summed_eigenvalues), dtype)
    eigenvectors = [torch.asarray(vector, dtype) for vector in eigenvectors]

    shape = summed_eigenvalues.shape
    if diagonals.shape != shape:
        raise ValueError(
            "output shape from func() does not match input shape: "
            f"{diagonals.shape} vs {shape}"
        )

    def apply(rhs: torch.Tensor) -> torch.Tensor:
        if rhs.shape != shape:
            raise ValueError(f"rhs.shape={rhs.shape} does not match shape={shape}")
        if rhs.dtype != dtype:
            raise ValueError(f"rhs.dtype={rhs.dtype} does not match dtype={dtype}")

        # Use tensordot so we have more control over the underlying XLA operations.
        out = rhs
        for vectors in eigenvectors:
            out = torch.tensordot(out, vectors, dims=(0, 0))
        out *= diagonals
        for vectors in eigenvectors:
            out = torch.tensordot(out, vectors, dims=(0, 1))
        return out

    return apply


def _circulant_fft_transform(
    func: Callable[[torch.Tensor], torch.Tensor],
    operators: Sequence[torch.Tensor],
    dtype: torch.dtype,
) -> Callable[[torch.Tensor], torch.Tensor]:
    """Fast diagonalization by Fast Fourier Transform."""
    # https://en.wikipedia.org/wiki/Circulant_matrix#Eigenvectors_and_eigenvalues
    eigenvalues = [fft.fft(op[:, 0]) for op in operators]
    summed_eigenvalues = outer_sum(eigenvalues)  # e[m,n,k] = m^2 + n^2 + k^2
    diagonals = torch.asarray(func(summed_eigenvalues))

    shape = tuple(op.shape[0] for op in operators)
    if diagonals.shape != shape:
        raise ValueError(
            "output shape from func() does not match input shape: "
            f"{diagonals.shape} vs {shape}"
        )

    def apply(rhs: torch.Tensor) -> torch.Tensor:
        if rhs.shape != shape:
            raise ValueError(f"rhs.shape={rhs.shape} does not match shape={shape}")
        return fft.ifftn(diagonals * fft.fftn(rhs)).to(dtype)

    return apply


def _circulant_rfft_transform(
    func: Callable[[torch.Tensor], torch.Tensor],
    operators: Sequence[torch.Tensor],
    dtype: torch.dtype,
) -> Callable[[torch.Tensor], torch.Tensor]:
    """Fast diagonalization by real-valued Fast Fourier Transform."""
    # https://en.wikipedia.org/wiki/Circulant_matrix#Eigenvectors_and_eigenvalues
    if operators[-1].shape[0] % 2:
        raise ValueError(
            'implementation="rfft" currently requires an even size ' "for the last axis"
        )
    # Use `rfft()` only on the last operator so the shape of `diagonals` matches
    # the shape of the output from `rfftn()` without any extra wrangling.
    eigenvalues = [fft.fft(op[:, 0]) for op in operators[:-1]] + [
        fft.rfft(operators[-1][:, 0])
    ]
    summed_eigenvalues = outer_sum(eigenvalues)
    diagonals = torch.asarray(func(summed_eigenvalues))

    if diagonals.shape != summed_eigenvalues.shape:
        raise ValueError(
            "output shape from func() does not match input shape: "
            f"{diagonals.shape} vs {summed_eigenvalues.shape}"
        )

    def apply(rhs: torch.Tensor) -> torch.Tensor:
        if rhs.dtype != dtype:
            raise ValueError(f"rhs.dtype={rhs.dtype} does not match dtype={dtype}")
        return fft.irfftn(diagonals * fft.rfftn(rhs)).to(dtype)

    return apply


def pseudoinverse(
    v: torch.Tensor,
    operators: Sequence[torch.Tensor],
    dtype: torch.dtype,
    *,
    hermitian: bool = False,
    circulant: bool = False,
    implementation: Optional[str] = None,
    cutoff: Optional[float] = None,
) -> Callable[[torch.Tensor], torch.Tensor]:
    """Invert a linear operator written as a sum of operators on each axis.

    Args:
      operators: forward linear operators as matrices, applied along each axis.
        Each of these matrices is diagonalized.
      dtype: dtype of the right-hand-side.
      hermitian: whether or not all linear operator are Hermitian (i.e., symmetric
        in the real valued case).
      circulant: whether or not all linear operators are circulant.
      implementation: how to implement fast diagonalization.
      precision: numerical precision for matrix multplication. Only relevant on
        TPUs.
      cutoff: eigenvalues with absolute value smaller than this number are
        discarded rather than being inverted. By default, uses 10 times floating
        point epsilon.

    Returns:
      A function that computes the pseudo-inverse of the indicated operator.
    """
    if cutoff is None:
        cutoff = 10 * torch.finfo(dtype).eps

    def func(v):
        return torch.where(abs(v) > cutoff, 1 / v, 0)
    
    t = transform(
        func,
        operators,
        dtype,
        hermitian=hermitian,
        circulant=circulant,
        implementation=implementation,
    )

    return t(v)
