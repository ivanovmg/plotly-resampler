# -*- coding: utf-8 -*-
"""Compatible implementation for various aggregation/downsample methods.

.. |br| raw:: html

   <br>

"""

from __future__ import annotations

__author__ = "Jonas Van Der Donckt"


import math
from typing import Tuple

import numpy as np
from tsdownsample import LTTBDownsampler, MinMaxDownsampler, MinMaxLTTBDownsampler

from ..aggregation.aggregation_interface import DataAggregator, DataPointSelector

try:
    # The efficient c version of the LTTB algorithm
    from .algorithms.lttb_c import LTTB_core_c as LTTB_core
except (ImportError, ModuleNotFoundError):
    import warnings

    warnings.warn("Could not import lttbc; will use a (slower) python alternative.")
    from .algorithms.lttb_py import LTTB_core_py as LTTB_core


class LTTB(DataPointSelector):
    """Largest Triangle Three Buckets (LTTB) aggregation method.

    This is arguably the most widely used aggregation method. It is based on the
    effective area of a triangle (inspired from the line simplification domain).
    The algorithm has $O(n)$ complexity, however, for large datasets, it can be much
    slower than other algorithms (e.g. MinMax) due to the higher cost of calculating
    the areas of triangles.

    Thesis: https://skemman.is/bitstream/1946/15343/3/SS_MSthesis.pdf
    Details on visual representativeness & stability: https://arxiv.org/abs/2304.00900

    .. Tip::
        `LTTB` doesn't scale super-well when moving to really large datasets, so when
        dealing with more than 1 million samples, you might consider using
        :class:`MinMaxLTTB <MinMaxLTTB>`.

    Note
    ----
    * This class is mainly designed to operate on numerical data as LTTB calculates
      distances on the values. |br|
      When dealing with categories, the data is encoded into its numeric codes,
      these codes are the indices of the category array.
    * To aggregate category data with LTTB, your ``pd.Series`` must be of dtype
      'category'. |br|
      **Tip**: if there is an order in your categories, order them that way, LTTB uses
      the ordered category codes values (see bullet above) to calculate distances and
      make aggregation decisions.
      .. code::
        >>> import pandas as pd
        >>> s = pd.Series(["a", "b", "c", "a"])
        >>> cat_type = pd.CategoricalDtype(categories=["b", "c", "a"], ordered=True)
        >>> s_cat = s.astype(cat_type)

    """

    def __init__(self, interleave_gaps: bool = True):
        """
        Parameters
        ----------
        interleave_gaps: bool, optional
            Whether None values should be added when there are gaps / irregularly
            sampled data. A quantile-based approach is used to determine the gaps /
            irregularly sampled data. By default, True.

        """
        super().__init__(
            interleave_gaps,
            y_dtype_regex_list=[rf"{dtype}\d*" for dtype in ("float", "int", "uint")]
            + ["category", "bool"],
        )
        # TODO: when integrating with tsdownsample add x & y dtype regex list

    def _arg_downsample(
        self,
        x: np.ndarray | None,
        y: np.ndarray,
        n_out: int,
        **_,
    ) -> np.ndarray:
        # Use the Core interface to perform the downsampling
        # return LTTB_core.downsample(x, y, n_out)
        if x is None:
            return LTTBDownsampler().downsample(y, n_out=n_out)
        return LTTBDownsampler().downsample(x, y, n_out=n_out)


class MinMaxOverlapAggregator(DataPointSelector):
    """Aggregation method which performs binned min-max aggregation over 50% overlapping
    windows.

    .. image:: _static/minmax_operator.png

    In the above image, **bin_size**: represents the size of *(len(series) / n_out)*.
    As the windows have 50% overlap and are consecutive, the min & max values are
    calculated on a windows with size (2x bin-size).

    This is *very* similar to the MinMaxAggregator, emperical results showed no
    observable difference between both approaches.

    .. note::
        This method is rather efficient when scaling to large data sizes and can be used
        as a data-reduction step before feeding it to the :class:`LTTB <LTTB>`
        algorithm, as :class:`EfficientLTTB <EfficientLTTB>` does.

    """

    def __init__(self, interleave_gaps: bool = True):
        """
        Parameters
        ----------
        interleave_gaps: bool, optional
            Whether None values should be added when there are gaps / irregularly
            sampled data. A quantile-based approach is used to determine the gaps /
            irregularly sampled data. By default, True.

        """
        # this downsampler supports all dtypes
        super().__init__(interleave_gaps)

    def _arg_downsample(
        self,
        x: np.ndarray | None,
        y: np.ndarray,
        n_out: int,
        **kwargs,
    ) -> np.ndarray:
        # The block size 2x the bin size we also perform the ceil-operation
        # to ensure that the block_size * n_out / 2 < len(x)
        block_size = math.ceil(y.shape[0] / (n_out + 1) * 2)
        argmax_offset = block_size // 2

        # Calculate the offset range which will be added to the argmin and argmax pos
        offset = np.arange(
            0, stop=y.shape[0] - block_size - argmax_offset, step=block_size
        )

        # Calculate the argmin & argmax on the reshaped view of `y` &
        # add the corresponding offset
        argmin = (
            y[: block_size * offset.shape[0]].reshape(-1, block_size).argmin(axis=1)
            + offset
        )
        argmax = (
            y[argmax_offset : block_size * offset.shape[0] + argmax_offset]
            .reshape(-1, block_size)
            .argmax(axis=1)
            + offset
            + argmax_offset
        )

        # Sort the argmin & argmax (where we append the first and last index item)
        return np.unique(np.concatenate((argmin, argmax, [0, y.shape[0] - 1])))


class MinMaxAggregator(DataPointSelector):
    """Aggregation method which performs binned min-max aggregation over fully
    overlapping windows.

    This is arguably the most computational efficient downsampling method, as it only
    performs (non-expendable) comparisons on the data in a single pass.

    Details on visual representativeness & stability: https://arxiv.org/abs/2304.00900

    .. note::
        This method is rather efficient when scaling to large data sizes and can be used
        as a data-reduction step before feeding it to the :class:`LTTB <LTTB>`
        algorithm, as :class:`EfficientLTTB <EfficientLTTB>` does with the
        :class:`MinMaxOverlapAggregator <MinMaxOverlapAggregator>`.

    """

    def __init__(self, interleave_gaps: bool = True):
        """
        Parameters
        ----------
        interleave_gaps: bool, optional
            Whether None values should be added when there are gaps / irregularly
            sampled data. A quantile-based approach is used to determine the gaps /
            irregularly sampled data. By default, True.

        """
        # this downsampler supports all dtypes
        super().__init__(interleave_gaps)

    def _arg_downsample(
        self,
        x: np.ndarray | None,
        y: np.ndarray,
        n_out: int,
        **kwargs,
    ) -> np.ndarray:
        if x is None:
            return MinMaxDownsampler().downsample(y, n_out=n_out)
        return MinMaxDownsampler().downsample(x, y, n_out=n_out)


class MinMaxLTTB(DataPointSelector):
    """Efficient version off LTTB by first reducing really large datasets with
    the :class:`MinMaxAggregator <MinMaxAggregator>` and then further aggregating the
    reduced result with :class:`LTTB <LTTB>`.

    Starting from 10M data points, this method performs the MinMax-prefetching of data
    points to enhance computational efficiency.

    Inventors: Jonas & Jeroen Van Der Donckt - 2022

    Paper: pending
    """

    def __init__(self, interleave_gaps: bool = True):
        """
        Parameters
        ----------
        interleave_gaps: bool, optional
            Whether None values should be added when there are gaps / irregularly
            sampled data. A quantile-based approach is used to determine the gaps /
            irregularly sampled data. By default, True.

        """
        self.lttb = LTTB(interleave_gaps=False)
        self.minmax = MinMaxAggregator(interleave_gaps=False)
        super().__init__(
            interleave_gaps,
            y_dtype_regex_list=[rf"{dtype}\d*" for dtype in ("float", "int", "uint")]
            + ["category", "bool"],
        )
        # TODO: when integrating with tsdownsample add x & y dtype regex list

    def _arg_downsample(
        self,
        x: np.ndarray | None,
        y: np.ndarray,
        n_out: int,
        **kwargs,
    ) -> np.ndarray:
        size_threshold = 10_000_000
        ratio_threshold = 100
        downsampler = LTTBDownsampler()
        if y.shape[0] > size_threshold and y.shape[0] / n_out > ratio_threshold:
            downsampler = MinMaxLTTBDownsampler()

        if x is None:
            return downsampler.downsample(y, n_out=n_out)
        return downsampler.downsample(x, y, n_out=n_out)


class EveryNthPoint(DataPointSelector):
    """Naive (but fast) aggregator method which returns every N'th point."""

    def __init__(self, interleave_gaps: bool = True):
        """
        Parameters
        ----------
        interleave_gaps: bool, optional
            Whether None values should be added when there are gaps / irregularly
            sampled data. A quantile-based approach is used to determine the gaps /
            irregularly sampled data. By default, True.

        """
        # this downsampler supports all dtypes
        super().__init__(interleave_gaps)

    def _arg_downsample(
        self,
        x: np.ndarray | None,
        y: np.ndarray,
        n_out: int,
        **kwargs,
    ) -> np.ndarray:
        # TODO: check the "-1" below
        # TODO: add equidistant version using searchsorted on a linspace
        return np.arange(step=max(1, math.ceil(len(y) / n_out)), stop=len(y) - 1)


class FuncAggregator(DataAggregator):
    """Aggregator instance which uses the passed aggregation func.

    .. warning::
        The user has total control which `aggregation_func` is passed to this method,
        hence the user should be careful to not make copies of the data, nor write to
        the data. Furthermore, the user should beware of performance issues when
        using more complex aggregation functions.

    .. attention::
        The user has total control which `aggregation_func` is passed to this method,
        hence it is the users' responsibility to handle categorical and bool-based
        data types.

    """

    def __init__(
        self,
        aggregation_func,
        interleave_gaps: bool = True,
        x_dtype_regex_list=None,
        y_dtype_regex_list=None,
    ):
        """
        Parameters
        ----------
        aggregation_func: Callable
            The aggregation function which will be applied on each pin.
        interleave_gaps: bool, optional
            Whether None values should be added when there are gaps / irregularly
            sampled data. A quantile-based approach is used to determine the gaps /
            irregularly sampled data. By default, True.

        """
        self.aggregation_func = aggregation_func
        super().__init__(
            interleave_gaps,
            x_dtype_regex_list=x_dtype_regex_list,
            y_dtype_regex_list=y_dtype_regex_list,
        )

    def _aggregate(
        self,
        x: np.ndarray | None,
        y: np.ndarray,
        n_out: int,
        **kwargs,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Aggregate the data using the object's aggregation function.

        Parameters
        ----------
        x: np.ndarray | None
            The x-values of the data. Can be None if no x-values are available.
        y: np.ndarray
            The y-values of the data.
        n_out: int
            The number of output data points.
        **kwargs
            Additional keyword arguments, which are passed to the aggregation function.

        Returns
        -------
        Tuple[np.ndarray, np.ndarray]
            The aggregated x & y values.
            If `x` is None, then the indices of the first element of each bin is
            returned as x-values.

        """
        # Create an index-estimation for real-time data
        # Add one to the index so it's pointed at the end of the window
        # Note: this can be adjusted to .5 to center the data
        # Multiply it with the group size to get the real index-position
        # TODO: add option to select start / middle / end as index
        if x is None:
            # no time index -> use the every nth heuristic
            group_size = max(1, np.ceil(len(y) / n_out))
            idxs = (np.arange(n_out) * group_size).astype(int)
        else:
            xdt = x.dtype
            if np.issubdtype(xdt, np.datetime64) or np.issubdtype(xdt, np.timedelta64):
                x = x.view("int64")
            # Thanks to `linspace`, the data is evenly distributed over the index-range
            # The searchsorted function returns the index positions
            idxs = np.searchsorted(x, np.linspace(x[0], x[-1], n_out + 1))

        y_agg = np.array(
            [
                self.aggregation_func(y[t0:t1], **kwargs)
                for t0, t1 in zip(idxs[:-1], idxs[1:])
            ]
        )

        if x is not None:
            x_agg = x[idxs[:-1]]
        else:
            # x is None -> return the indices of the first element of each bin
            # Note that groupsize * n_out can be larger than the length of the data
            idxs[-1] = len(y) - 1
            x_agg = idxs

        return x_agg, y_agg
