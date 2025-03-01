from __future__ import annotations

import builtins
import os
import sys
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, TypeVar, overload

import pyarrow as pa

from daft import context
from daft.daft import CountMode, ImageFormat
from daft.daft import PyExpr as _PyExpr
from daft.daft import col as _col
from daft.daft import date_lit as _date_lit
from daft.daft import decimal_lit as _decimal_lit
from daft.daft import lit as _lit
from daft.daft import series_lit as _series_lit
from daft.daft import time_lit as _time_lit
from daft.daft import timestamp_lit as _timestamp_lit
from daft.daft import udf as _udf
from daft.datatype import DataType, TimeUnit
from daft.expressions.testing import expr_structurally_equal
from daft.logical.schema import Field, Schema
from daft.series import Series, item_to_series

if sys.version_info < (3, 8):
    from typing_extensions import Literal
else:
    from typing import Literal

if TYPE_CHECKING:
    from daft.io import IOConfig


# Implementation taken from: https://github.com/pola-rs/polars/blob/main/py-polars/polars/utils/various.py#L388-L399
# This allows Sphinx to correctly work against our "namespaced" accessor functions by overriding @property to
# return a class instance of the namespace instead of a property object.
accessor_namespace_property: type[property] = property
if os.getenv("DAFT_SPHINX_BUILD") == "1":
    from typing import Any

    # when building docs (with Sphinx) we need access to the functions
    # associated with the namespaces from the class, as we don't have
    # an instance; @sphinx_accessor is a @property that allows this.
    NS = TypeVar("NS")

    class sphinx_accessor(property):  # noqa: D101
        def __get__(  # type: ignore[override]
            self,
            instance: Any,
            cls: type[NS],
        ) -> NS:
            try:
                return self.fget(instance if isinstance(instance, cls) else cls)  # type: ignore[misc]
            except (AttributeError, ImportError):
                return self  # type: ignore[return-value]

    accessor_namespace_property = sphinx_accessor


def lit(value: object) -> Expression:
    """Creates an Expression representing a column with every value set to the provided value

    Example:
        >>> col("x") + lit(1)

    Args:
        val: value of column

    Returns:
        Expression: Expression representing the value provided
    """
    if isinstance(value, datetime):
        # pyo3 datetime (PyDateTime) is not available when running in abi3 mode, workaround
        pa_timestamp = pa.scalar(value)
        i64_value = pa_timestamp.cast(pa.int64()).as_py()
        time_unit = TimeUnit.from_str(pa_timestamp.type.unit)._timeunit
        tz = pa_timestamp.type.tz
        lit_value = _timestamp_lit(i64_value, time_unit, tz)
    elif isinstance(value, date):
        # pyo3 date (PyDate) is not available when running in abi3 mode, workaround
        epoch_time = value - date(1970, 1, 1)
        lit_value = _date_lit(epoch_time.days)
    elif isinstance(value, time):
        # pyo3 time (PyTime) is not available when running in abi3 mode, workaround
        pa_time = pa.scalar(value)
        i64_value = pa_time.cast(pa.int64()).as_py()
        time_unit = TimeUnit.from_str(pa.type_for_alias(str(pa_time.type)).unit)._timeunit
        lit_value = _time_lit(i64_value, time_unit)
    elif isinstance(value, Decimal):
        sign, digits, exponent = value.as_tuple()
        lit_value = _decimal_lit(sign == 1, digits, exponent)
    elif isinstance(value, Series):
        lit_value = _series_lit(value._series)
    else:
        lit_value = _lit(value)
    return Expression._from_pyexpr(lit_value)


def col(name: str) -> Expression:
    """Creates an Expression referring to the column with the provided name

    Example:
        >>> col("x")

    Args:
        name: Name of column

    Returns:
        Expression: Expression representing the selected column
    """
    return Expression._from_pyexpr(_col(name))


class Expression:
    _expr: _PyExpr = None  # type: ignore

    def __init__(self) -> None:
        raise NotImplementedError("We do not support creating a Expression via __init__ ")

    @accessor_namespace_property
    def str(self) -> ExpressionStringNamespace:
        """Access methods that work on columns of strings"""
        return ExpressionStringNamespace.from_expression(self)

    @accessor_namespace_property
    def dt(self) -> ExpressionDatetimeNamespace:
        """Access methods that work on columns of datetimes"""
        return ExpressionDatetimeNamespace.from_expression(self)

    @accessor_namespace_property
    def float(self) -> ExpressionFloatNamespace:
        """Access methods that work on columns of floats"""
        return ExpressionFloatNamespace.from_expression(self)

    @accessor_namespace_property
    def url(self) -> ExpressionUrlNamespace:
        """Access methods that work on columns of URLs"""
        return ExpressionUrlNamespace.from_expression(self)

    @accessor_namespace_property
    def list(self) -> ExpressionListNamespace:
        """Access methods that work on columns of lists"""
        return ExpressionListNamespace.from_expression(self)

    @accessor_namespace_property
    def struct(self) -> ExpressionStructNamespace:
        """Access methods that work on columns of structs"""
        return ExpressionStructNamespace.from_expression(self)

    @accessor_namespace_property
    def image(self) -> ExpressionImageNamespace:
        """Access methods that work on columns of images"""
        return ExpressionImageNamespace.from_expression(self)

    @accessor_namespace_property
    def partitioning(self) -> ExpressionPartitioningNamespace:
        """Access methods that support partitioning operators"""
        return ExpressionPartitioningNamespace.from_expression(self)

    @accessor_namespace_property
    def json(self) -> ExpressionJsonNamespace:
        """Access methods that work on columns of json"""
        return ExpressionJsonNamespace.from_expression(self)

    @staticmethod
    def _from_pyexpr(pyexpr: _PyExpr) -> Expression:
        expr = Expression.__new__(Expression)
        expr._expr = pyexpr
        return expr

    @staticmethod
    def _to_expression(obj: object) -> Expression:
        if isinstance(obj, Expression):
            return obj
        else:
            return lit(obj)

    @staticmethod
    def udf(func: Callable, expressions: builtins.list[Expression], return_dtype: DataType) -> Expression:
        return Expression._from_pyexpr(_udf(func, [e._expr for e in expressions], return_dtype._dtype))

    def __bool__(self) -> bool:
        raise ValueError(
            "Expressions don't have a truth value. "
            "If you used Python keywords `and` `not` `or` on an expression, use `&` `~` `|` instead."
        )

    def __abs__(self) -> Expression:
        """Absolute of a numeric expression (``abs(expr)``)"""
        return self.abs()

    def abs(self) -> Expression:
        """Absolute of a numeric expression (``expr.abs()``)"""
        return Expression._from_pyexpr(abs(self._expr))

    def __add__(self, other: object) -> Expression:
        """Adds two numeric expressions or concatenates two string expressions (``e1 + e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr + expr._expr)

    def __radd__(self, other: object) -> Expression:
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(expr._expr + self._expr)

    def __sub__(self, other: object) -> Expression:
        """Subtracts two numeric expressions (``e1 - e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr - expr._expr)

    def __rsub__(self, other: object) -> Expression:
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(expr._expr - self._expr)

    def __mul__(self, other: object) -> Expression:
        """Multiplies two numeric expressions (``e1 * e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr * expr._expr)

    def __rmul__(self, other: object) -> Expression:
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(expr._expr * self._expr)

    def __truediv__(self, other: object) -> Expression:
        """True divides two numeric expressions (``e1 / e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr / expr._expr)

    def __rtruediv__(self, other: object) -> Expression:
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(expr._expr / self._expr)

    def __mod__(self, other: Expression) -> Expression:
        """Takes the mod of two numeric expressions (``e1 % e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr % expr._expr)

    def __rmod__(self, other: Expression) -> Expression:
        """Takes the mod of two numeric expressions (``e1 % e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(expr._expr % self._expr)

    def __and__(self, other: Expression) -> Expression:
        """Takes the logical AND of two boolean expressions (``e1 & e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr & expr._expr)

    def __rand__(self, other: Expression) -> Expression:
        """Takes the logical reverse AND of two boolean expressions (``e1 & e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(expr._expr & self._expr)

    def __or__(self, other: Expression) -> Expression:
        """Takes the logical OR of two boolean expressions (``e1 | e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr | expr._expr)

    def __ror__(self, other: Expression) -> Expression:
        """Takes the logical reverse OR of two boolean expressions (``e1 | e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(expr._expr | self._expr)

    def __lt__(self, other: Expression) -> Expression:
        """Compares if an expression is less than another (``e1 < e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr < expr._expr)

    def __le__(self, other: Expression) -> Expression:
        """Compares if an expression is less than or equal to another (``e1 <= e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr <= expr._expr)

    def __eq__(self, other: Expression) -> Expression:  # type: ignore
        """Compares if an expression is equal to another (``e1 == e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr == expr._expr)

    def __ne__(self, other: Expression) -> Expression:  # type: ignore
        """Compares if an expression is not equal to another (``e1 != e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr != expr._expr)

    def __gt__(self, other: Expression) -> Expression:
        """Compares if an expression is greater than another (``e1 > e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr > expr._expr)

    def __ge__(self, other: Expression) -> Expression:
        """Compares if an expression is greater than or equal to another (``e1 >= e2``)"""
        expr = Expression._to_expression(other)
        return Expression._from_pyexpr(self._expr >= expr._expr)

    def __invert__(self) -> Expression:
        """Inverts a boolean expression (``~e``)"""
        expr = self._expr.__invert__()
        return Expression._from_pyexpr(expr)

    def alias(self, name: builtins.str) -> Expression:
        """Gives the expression a new name, which is its column's name in the DataFrame schema and the name
        by which subsequent expressions can refer to the results of this expression.

        Example:
            >>> col("x").alias("y")

        Args:
            name: New name for expression

        Returns:
            Expression: Renamed expression
        """
        assert isinstance(name, str)
        expr = self._expr.alias(name)
        return Expression._from_pyexpr(expr)

    def cast(self, dtype: DataType) -> Expression:
        """Casts an expression to the given datatype if possible

        Example:

            >>> # [1.0, 2.5, None]: float32 -> [1, 2, None]: int64
            >>> col("float").cast(DataType.int64())
            >>>
            >>> # [Path("/tmp1"), Path("/tmp2"), Path("/tmp3")]: Python -> ["/tmp1", "/tmp1", "/tmp1"]: utf8
            >>> col("path_obj_col").cast(DataType.string())

        Returns:
            Expression: Expression with the specified new datatype
        """
        assert isinstance(dtype, DataType)
        expr = self._expr.cast(dtype._dtype)
        return Expression._from_pyexpr(expr)

    def ceil(self) -> Expression:
        """The ceiling of a numeric expression (``expr.ceil()``)"""
        expr = self._expr.ceil()
        return Expression._from_pyexpr(expr)

    def floor(self) -> Expression:
        """The floor of a numeric expression (``expr.floor()``)"""
        expr = self._expr.floor()
        return Expression._from_pyexpr(expr)

    def sign(self) -> Expression:
        """The sign of a numeric expression (``expr.sign()``)"""
        expr = self._expr.sign()
        return Expression._from_pyexpr(expr)

    def round(self, decimals: int = 0) -> Expression:
        """The round of a numeric expression (``expr.round(decimals = 0)``)

        Args:
            decimals: number of decimal places to round to. Defaults to 0.
        """
        assert isinstance(decimals, int)
        expr = self._expr.round(decimals)
        return Expression._from_pyexpr(expr)

    def count(self, mode: CountMode = CountMode.Valid) -> Expression:
        """Counts the number of values in the expression.

        Args:
            mode: whether to count all values, non-null (valid) values, or null values. Defaults to CountMode.Valid.
        """
        expr = self._expr.count(mode)
        return Expression._from_pyexpr(expr)

    def sum(self) -> Expression:
        """Calculates the sum of the values in the expression"""
        expr = self._expr.sum()
        return Expression._from_pyexpr(expr)

    def mean(self) -> Expression:
        """Calculates the mean of the values in the expression"""
        expr = self._expr.mean()
        return Expression._from_pyexpr(expr)

    def min(self) -> Expression:
        """Calculates the minimum value in the expression"""
        expr = self._expr.min()
        return Expression._from_pyexpr(expr)

    def max(self) -> Expression:
        """Calculates the maximum value in the expression"""
        expr = self._expr.max()
        return Expression._from_pyexpr(expr)

    def any_value(self, ignore_nulls=False) -> Expression:
        """Returns any value in the expression

        Args:
            ignore_nulls: whether to ignore null values when selecting the value. Defaults to False.
        """
        expr = self._expr.any_value(ignore_nulls)
        return Expression._from_pyexpr(expr)

    def agg_list(self) -> Expression:
        """Aggregates the values in the expression into a list"""
        expr = self._expr.agg_list()
        return Expression._from_pyexpr(expr)

    def agg_concat(self) -> Expression:
        """Aggregates the values in the expression into a single string by concatenating them"""
        expr = self._expr.agg_concat()
        return Expression._from_pyexpr(expr)

    def _explode(self) -> Expression:
        expr = self._expr.explode()
        return Expression._from_pyexpr(expr)

    def if_else(self, if_true: Expression, if_false: Expression) -> Expression:
        """Conditionally choose values between two expressions using the current boolean expression as a condition

        Example:
            >>> # x = [2, 2, 2]
            >>> # y = [1, 2, 3]
            >>> # a = ["a", "a", "a"]
            >>> # b = ["b", "b", "b"]
            >>> # if_else_result = ["a", "b", "b"]
            >>> (col("x") > col("y")).if_else(col("a"), col("b"))

        Args:
            if_true (Expression): Values to choose if condition is true
            if_false (Expression): Values to choose if condition is false

        Returns:
            Expression: New expression where values are chosen from `if_true` and `if_false`.
        """
        if_true = Expression._to_expression(if_true)
        if_false = Expression._to_expression(if_false)
        return Expression._from_pyexpr(self._expr.if_else(if_true._expr, if_false._expr))

    def apply(self, func: Callable, return_dtype: DataType) -> Expression:
        """Apply a function on each value in a given expression

        .. NOTE::
            This is just syntactic sugar on top of a UDF and is convenient to use when your function only operates
            on a single column, and does not benefit from executing on batches. For either of those other use-cases,
            use a UDF instead.

        Example:
            >>> def f(x_val: str) -> int:
            >>>     return int(x_val) if x_val.isnumeric() else 0
            >>>
            >>> col("x").apply(f, return_dtype=DataType.int64())

        Args:
            func: Function to run per value of the expression
            return_dtype: Return datatype of the function that was ran

        Returns:
            Expression: New expression after having run the function on the expression
        """
        from daft.udf import UDF

        def batch_func(self_series):
            return [func(x) for x in self_series.to_pylist()]

        return UDF(func=batch_func, return_dtype=return_dtype)(self)

    def is_null(self) -> Expression:
        """Checks if values in the Expression are Null (a special value indicating missing data)

        Example:
            >>> # [1., None, NaN] -> [False, True, False]
            >>> col("x").is_null()

        Returns:
            Expression: Boolean Expression indicating whether values are missing
        """
        expr = self._expr.is_null()
        return Expression._from_pyexpr(expr)

    def not_null(self) -> Expression:
        """Checks if values in the Expression are not Null (a special value indicating missing data)

        Example:
            >>> # [1., None, NaN] -> [True, False, True]
            >>> col("x").not_null()

        Returns:
            Expression: Boolean Expression indicating whether values are not missing
        """
        expr = self._expr.not_null()
        return Expression._from_pyexpr(expr)

    def is_in(self, other: Any) -> Expression:
        """Checks if values in the Expression are in the provided list

        Example:
            >>> # [1, 2, 3] -> [True, False, True]
            >>> col("x").is_in([1, 3])

        Returns:
            Expression: Boolean Expression indicating whether values are in the provided list
        """

        if not isinstance(other, Expression):
            series = item_to_series("items", other)
            other = Expression._to_expression(series)

        expr = self._expr.is_in(other._expr)
        return Expression._from_pyexpr(expr)

    def name(self) -> builtins.str:
        return self._expr.name()

    def __repr__(self) -> builtins.str:
        return repr(self._expr)

    def _to_sql(self, db_scheme: builtins.str) -> builtins.str:
        return self._expr.to_sql(db_scheme)

    def _to_field(self, schema: Schema) -> Field:
        return Field._from_pyfield(self._expr.to_field(schema._schema))

    def __hash__(self) -> int:
        return self._expr.__hash__()

    def __reduce__(self) -> tuple:
        return Expression._from_pyexpr, (self._expr,)

    ###
    # Helper methods required by optimizer:
    # These should be removed from the Python API for Expressions when logical plans and optimizer are migrated to Rust
    ###

    def _input_mapping(self) -> builtins.str | None:
        return self._expr._input_mapping()

    def _required_columns(self) -> set[builtins.str]:
        return self._expr._required_columns()

    def _is_column(self) -> bool:
        return self._expr._is_column()


SomeExpressionNamespace = TypeVar("SomeExpressionNamespace", bound="ExpressionNamespace")


class ExpressionNamespace:
    _expr: _PyExpr

    def __init__(self) -> None:
        raise NotImplementedError("We do not support creating a ExpressionNamespace via __init__ ")

    @classmethod
    def from_expression(cls: type[SomeExpressionNamespace], expr: Expression) -> SomeExpressionNamespace:
        ns = cls.__new__(cls)
        ns._expr = expr._expr
        return ns


class ExpressionUrlNamespace(ExpressionNamespace):
    def download(
        self,
        max_connections: int = 32,
        on_error: Literal["raise"] | Literal["null"] = "raise",
        io_config: IOConfig | None = None,
        use_native_downloader: bool = True,
    ) -> Expression:
        """Treats each string as a URL, and downloads the bytes contents as a bytes column

        .. NOTE::
            If you are observing excessive S3 issues (such as timeouts, DNS errors or slowdown errors) during URL downloads,
            you may wish to reduce the value of ``max_connections`` (defaults to 32) to reduce the amount of load you are placing
            on your S3 servers.

            Alternatively, if you are running on machines with lower number of cores but very high network bandwidth, you can increase
            ``max_connections`` to get higher throughput with additional parallelism

        Args:
            max_connections: The maximum number of connections to use per thread to use for downloading URLs. Defaults to 32.
            on_error: Behavior when a URL download error is encountered - "raise" to raise the error immediately or "null" to log
                the error but fallback to a Null value. Defaults to "raise".
            io_config: IOConfig to use when accessing remote storage. Note that the S3Config's `max_connections` parameter will be overridden
                with `max_connections` that is passed in as a kwarg.
            use_native_downloader (bool): Use the native downloader rather than python based one.
                Defaults to True.

        Returns:
            Expression: a Binary expression which is the bytes contents of the URL, or None if an error occured during download
        """
        if use_native_downloader:
            raise_on_error = False
            if on_error == "raise":
                raise_on_error = True
            elif on_error == "null":
                raise_on_error = False
            else:
                raise NotImplemented(f"Unimplemented on_error option: {on_error}.")

            if not (isinstance(max_connections, int) and max_connections > 0):
                raise ValueError(f"Invalid value for `max_connections`: {max_connections}")

            # Use the `max_connections` kwarg to override the value in S3Config
            # This is because the max parallelism is actually `min(S3Config's max_connections, url_download's max_connections)` under the hood.
            # However, default max_connections on S3Config is only 8, and even if we specify 32 here we are bottlenecked there.
            # Therefore for S3 downloads, we override `max_connections` kwarg to have the intended effect.
            io_config = context.get_context().daft_planning_config.default_io_config if io_config is None else io_config
            io_config = io_config.replace(s3=io_config.s3.replace(max_connections=max_connections))

            using_ray_runner = context.get_context().is_ray_runner
            return Expression._from_pyexpr(
                self._expr.url_download(max_connections, raise_on_error, not using_ray_runner, io_config)
            )
        else:
            from daft.udf_library import url_udfs

            return url_udfs.download_udf(
                Expression._from_pyexpr(self._expr),
                max_worker_threads=max_connections,
                on_error=on_error,
            )


class ExpressionFloatNamespace(ExpressionNamespace):
    def is_nan(self) -> Expression:
        """Checks if values are NaN (a special float value indicating not-a-number)

        .. NOTE::
            Nulls will be propagated! I.e. this operation will return a null for null values.

        Example:
            >>> # [1., None, NaN] -> [False, None, True]
            >>> col("x").is_nan()

        Returns:
            Expression: Boolean Expression indicating whether values are invalid.
        """
        return Expression._from_pyexpr(self._expr.is_nan())


class ExpressionDatetimeNamespace(ExpressionNamespace):
    def date(self) -> Expression:
        """Retrieves the date for a datetime column

        Example:
            >>> col("x").dt.date()

        Returns:
            Expression: a Date expression
        """
        return Expression._from_pyexpr(self._expr.dt_date())

    def day(self) -> Expression:
        """Retrieves the day for a datetime column

        Example:
            >>> col("x").dt.day()

        Returns:
            Expression: a UInt32 expression with just the day extracted from a datetime column
        """
        return Expression._from_pyexpr(self._expr.dt_day())

    def hour(self) -> Expression:
        """Retrieves the day for a datetime column

        Example:
            >>> col("x").dt.day()

        Returns:
            Expression: a UInt32 expression with just the day extracted from a datetime column
        """
        return Expression._from_pyexpr(self._expr.dt_hour())

    def month(self) -> Expression:
        """Retrieves the month for a datetime column

        Example:
            >>> col("x").dt.month()

        Returns:
            Expression: a UInt32 expression with just the month extracted from a datetime column
        """
        return Expression._from_pyexpr(self._expr.dt_month())

    def year(self) -> Expression:
        """Retrieves the year for a datetime column

        Example:
            >>> col("x").dt.year()

        Returns:
            Expression: a UInt32 expression with just the year extracted from a datetime column
        """
        return Expression._from_pyexpr(self._expr.dt_year())

    def day_of_week(self) -> Expression:
        """Retrieves the day of the week for a datetime column, starting at 0 for Monday and ending at 6 for Sunday

        Example:
            >>> col("x").dt.day_of_week()

        Returns:
            Expression: a UInt32 expression with just the day_of_week extracted from a datetime column
        """
        return Expression._from_pyexpr(self._expr.dt_day_of_week())


class ExpressionStringNamespace(ExpressionNamespace):
    def contains(self, substr: str | Expression) -> Expression:
        """Checks whether each string contains the given pattern in a string column

        Example:
            >>> col("x").str.contains(col("foo"))

        Args:
            pattern: pattern to search for as a literal string, or as a column to pick values from

        Returns:
            Expression: a Boolean expression indicating whether each value contains the provided pattern
        """
        substr_expr = Expression._to_expression(substr)
        return Expression._from_pyexpr(self._expr.utf8_contains(substr_expr._expr))

    def match(self, pattern: str | Expression) -> Expression:
        """Checks whether each string matches the given regular expression pattern in a string column

        Example:
            >>> df = daft.from_pydict({"x": ["foo", "bar", "baz"]})
            >>> df.with_column("match", df["x"].str.match("ba.")).collect()
            ╭─────────╮
            │ match   │
            │ ---     │
            │ Boolean │
            ╞═════════╡
            │ false   │
            ├╌╌╌╌╌╌╌╌╌┤
            │ true    │
            ├╌╌╌╌╌╌╌╌╌┤
            │ true    │
            ╰─────────╯

        Args:
            pattern: Regex pattern to search for as string or as a column to pick values from

        Returns:
            Expression: a Boolean expression indicating whether each value matches the provided pattern
        """
        pattern_expr = Expression._to_expression(pattern)
        return Expression._from_pyexpr(self._expr.utf8_match(pattern_expr._expr))

    def endswith(self, suffix: str | Expression) -> Expression:
        """Checks whether each string ends with the given pattern in a string column

        Example:
            >>> col("x").str.endswith(col("foo"))

        Args:
            pattern: pattern to search for as a literal string, or as a column to pick values from

        Returns:
            Expression: a Boolean expression indicating whether each value ends with the provided pattern
        """
        suffix_expr = Expression._to_expression(suffix)
        return Expression._from_pyexpr(self._expr.utf8_endswith(suffix_expr._expr))

    def startswith(self, prefix: str | Expression) -> Expression:
        """Checks whether each string starts with the given pattern in a string column

        Example:
            >>> col("x").str.startswith(col("foo"))

        Args:
            pattern: pattern to search for as a literal string, or as a column to pick values from

        Returns:
            Expression: a Boolean expression indicating whether each value starts with the provided pattern
        """
        prefix_expr = Expression._to_expression(prefix)
        return Expression._from_pyexpr(self._expr.utf8_startswith(prefix_expr._expr))

    def split(self, pattern: str | Expression, regex: bool = False) -> Expression:
        r"""Splits each string on the given literal or regex pattern, into a list of strings.

        Example:
            >>> df = daft.from_pydict({"data": ["foo.bar.baz", "a.b.c", "1.2.3"]})
            >>> df.with_column("split", df["data"].str.split(".")).collect()
            ╭─────────────┬─────────────────╮
            │ data        ┆ split           │
            │ ---         ┆ ---             │
            │ Utf8        ┆ List[Utf8]      │
            ╞═════════════╪═════════════════╡
            │ foo.bar.baz ┆ [foo, bar, baz] │
            ├╌╌╌╌╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┤
            │ a.b.c       ┆ [a, b, c]       │
            ├╌╌╌╌╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┤
            │ 1.2.3       ┆ [1, 2, 3]       │
            ╰─────────────┴─────────────────╯

            Split on a regex pattern

            >>> df = daft.from_pydict({"data": ["foo.bar...baz", "a.....b.c", "1.2...3.."]})
            >>> df.with_column("split", df["data"].str.split(r"\.+", regex=True)).collect()
            ╭───────────────┬─────────────────╮
            │ data          ┆ split           │
            │ ---           ┆ ---             │
            │ Utf8          ┆ List[Utf8]      │
            ╞═══════════════╪═════════════════╡
            │ foo.bar...baz ┆ [foo, bar, baz] │
            ├╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┤
            │ a.....b.c     ┆ [a, b, c]       │
            ├╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┤
            │ 1.2...3..     ┆ [1, 2, 3, ]     │
            ╰───────────────┴─────────────────╯


        Args:
            pattern: The pattern on which each string should be split, or a column to pick such patterns from.
            regex: Whether the pattern is a regular expression. Defaults to False.

        Returns:
            Expression: A List[Utf8] expression containing the string splits for each string in the column.
        """
        pattern_expr = Expression._to_expression(pattern)
        return Expression._from_pyexpr(self._expr.utf8_split(pattern_expr._expr, regex))

    def concat(self, other: str) -> Expression:
        """Concatenates two string expressions together

        .. NOTE::
            Another (easier!) way to invoke this functionality is using the Python `+` operator which is
            aliased to using `.str.concat`. These are equivalent:

            >>> col("x").str.concat(col("y"))
            >>> col("x") + col("y")

        Args:
            other (Expression): a string expression to concatenate with

        Returns:
            Expression: a String expression which is `self` concatenated with `other`
        """
        # Delegate to + operator implementation.
        return Expression._from_pyexpr(self._expr) + other

    def extract(self, pattern: str | Expression, index: int = 0) -> Expression:
        r"""Extracts the specified match group from the first regex match in each string in a string column.

        Notes:
            If index is 0, the entire match is returned.
            If the pattern does not match or the group does not exist, a null value is returned.

        Example:
            >>> regex = r"(\d)(\d*)"
            >>> df = daft.from_pydict({"x": ["123-456", "789-012", "345-678"]})
            >>> df.with_column("match", df["x"].str.extract(regex))
            ╭─────────┬─────────╮
            │ x       ┆ match   │
            │ ---     ┆ ---     │
            │ Utf8    ┆ Utf8    │
            ╞═════════╪═════════╡
            │ 123-456 ┆ 123     │
            ├╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌┤
            │ 789-012 ┆ 789     │
            ├╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌┤
            │ 345-678 ┆ 345     │
            ╰─────────┴─────────╯

            Extract the first capture group

            >>> df.with_column("match", df["x"].str.extract(regex, 1)).collect()
            ╭─────────┬─────────╮
            │ x       ┆ match   │
            │ ---     ┆ ---     │
            │ Utf8    ┆ Utf8    │
            ╞═════════╪═════════╡
            │ 123-456 ┆ 1       │
            ├╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌┤
            │ 789-012 ┆ 7       │
            ├╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌┤
            │ 345-678 ┆ 3       │
            ╰─────────┴─────────╯

        Args:
            pattern: The regex pattern to extract
            index: The index of the regex match group to extract

        Returns:
            Expression: a String expression with the extracted regex match

        See also:
            `extract_all`
        """
        pattern_expr = Expression._to_expression(pattern)
        return Expression._from_pyexpr(self._expr.utf8_extract(pattern_expr._expr, index))

    def extract_all(self, pattern: str | Expression, index: int = 0) -> Expression:
        r"""Extracts the specified match group from all regex matches in each string in a string column.

        Notes:
            This expression always returns a list of strings.
            If index is 0, the entire match is returned. If the pattern does not match or the group does not exist, an empty list is returned.

        Example:
            >>> regex = r"(\d)(\d*)"
            >>> df = daft.from_pydict({"x": ["123-456", "789-012", "345-678"]})
            >>> df.with_column("match", df["x"].str.extract_all(regex))
            ╭─────────┬────────────╮
            │ x       ┆ matches    │
            │ ---     ┆ ---        │
            │ Utf8    ┆ List[Utf8] │
            ╞═════════╪════════════╡
            │ 123-456 ┆ [123, 456] │
            ├╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌╌╌╌┤
            │ 789-012 ┆ [789, 012] │
            ├╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌╌╌╌┤
            │ 345-678 ┆ [345, 678] │
            ╰─────────┴────────────╯

            Extract the first capture group

            >>> df.with_column("match", df["x"].str.extract_all(regex, 1)).collect()
            ╭─────────┬────────────╮
            │ x       ┆ matches    │
            │ ---     ┆ ---        │
            │ Utf8    ┆ List[Utf8] │
            ╞═════════╪════════════╡
            │ 123-456 ┆ [1, 4]     │
            ├╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌╌╌╌┤
            │ 789-012 ┆ [7, 0]     │
            ├╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌╌╌╌┤
            │ 345-678 ┆ [3, 6]     │
            ╰─────────┴────────────╯

        Args:
            pattern: The regex pattern to extract
            index: The index of the regex match group to extract

        Returns:
            Expression: a List[Utf8] expression with the extracted regex matches

        See also:
            `extract`
        """
        pattern_expr = Expression._to_expression(pattern)
        return Expression._from_pyexpr(self._expr.utf8_extract_all(pattern_expr._expr, index))

    def replace(self, pattern: str | Expression, replacement: str | Expression, regex: bool = False) -> Expression:
        """Replaces all occurrences of a pattern in a string column with a replacement string. The pattern can be a literal string or a regex pattern.

        Example:
            >>> df = daft.from_pydict({"data": ["foo", "bar", "baz"]})
            >>> df.with_column("replace", df["data"].str.replace("ba", "123")).collect()
            ╭──────┬─────────╮
            │ data ┆ replace │
            │ ---  ┆ ---     │
            │ Utf8 ┆ Utf8    │
            ╞══════╪═════════╡
            │ foo  ┆ foo     │
            ├╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌┤
            │ bar  ┆ 123r    │
            ├╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌┤
            │ baz  ┆ 123z    │
            ╰──────┴─────────╯

            Replace with a regex pattern

            >>> df = daft.from_pydict({"data": ["foo", "fooo", "foooo"]})
            >>> df.with_column("replace", df["data"].str.replace(r"o+", "a", regex=True)).collect()
            ╭───────┬─────────╮
            │ data  ┆ replace │
            │ ---   ┆ ---     │
            │ Utf8  ┆ Utf8    │
            ╞═══════╪═════════╡
            │ foo   ┆ fa      │
            ├╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌┤
            │ fooo  ┆ fa      │
            ├╌╌╌╌╌╌╌┼╌╌╌╌╌╌╌╌╌┤
            │ foooo ┆ fa      │
            ╰───────┴─────────╯

        Args:
            pattern: The pattern to replace
            replacement: The replacement string
            regex: Whether the pattern is a regex pattern or an exact match. Defaults to False.

        Returns:
            Expression: a String expression with patterns replaced by the replacement string
        """
        pattern_expr = Expression._to_expression(pattern)
        replacement_expr = Expression._to_expression(replacement)
        return Expression._from_pyexpr(self._expr.utf8_replace(pattern_expr._expr, replacement_expr._expr, regex))

    def length(self) -> Expression:
        """Retrieves the length for a UTF-8 string column

        Example:
            >>> col("x").str.length()

        Returns:
            Expression: an UInt64 expression with the length of each string
        """
        return Expression._from_pyexpr(self._expr.utf8_length())

    def lower(self) -> Expression:
        """Convert UTF-8 string to all lowercase

        Example:
            >>> col("x").str.lower()

        Returns:
            Expression: a String expression which is `self` lowercased
        """
        return Expression._from_pyexpr(self._expr.utf8_lower())

    def upper(self) -> Expression:
        """Convert UTF-8 string to all upper

        Example:
            >>> col("x").str.upper()

        Returns:
            Expression: a String expression which is `self` uppercased
        """
        return Expression._from_pyexpr(self._expr.utf8_upper())

    def lstrip(self) -> Expression:
        """Strip whitespace from the left side of a UTF-8 string

        Example:
            >>> col("x").str.lstrip()

        Returns:
            Expression: a String expression which is `self` with leading whitespace stripped
        """
        return Expression._from_pyexpr(self._expr.utf8_lstrip())

    def rstrip(self) -> Expression:
        """Strip whitespace from the right side of a UTF-8 string

        Example:
            >>> col("x").str.rstrip()

        Returns:
            Expression: a String expression which is `self` with trailing whitespace stripped
        """
        return Expression._from_pyexpr(self._expr.utf8_rstrip())

    def reverse(self) -> Expression:
        """Reverse a UTF-8 string

        Example:
            >>> col("x").str.reverse()

        Returns:
            Expression: a String expression which is `self` reversed
        """
        return Expression._from_pyexpr(self._expr.utf8_reverse())

    def capitalize(self) -> Expression:
        """Capitalize a UTF-8 string

        Example:
            >>> col("x").str.capitalize()

        Returns:
            Expression: a String expression which is `self` uppercased with the first character and lowercased the rest
        """
        return Expression._from_pyexpr(self._expr.utf8_capitalize())

    def left(self, nchars: int | Expression) -> Expression:
        """Gets the n (from nchars) left-most characters of each string

        Example:
            >>> col("x").str.left(3)

        Returns:
            Expression: a String expression which is the `n` left-most characters of `self`
        """
        nchars_expr = Expression._to_expression(nchars)
        return Expression._from_pyexpr(self._expr.utf8_left(nchars_expr._expr))

    def right(self, nchars: int | Expression) -> Expression:
        """Gets the n (from nchars) right-most characters of each string

        Example:
            >>> col("x").str.right(3)

        Returns:
            Expression: a String expression which is the `n` right-most characters of `self`
        """
        nchars_expr = Expression._to_expression(nchars)
        return Expression._from_pyexpr(self._expr.utf8_right(nchars_expr._expr))

    def find(self, substr: str | Expression) -> Expression:
        """Returns the index of the first occurrence of the substring in each string

        .. NOTE::
            The returned index is 0-based.
            If the substring is not found, -1 is returned.

        Example:
            >>> col("x").str.find("foo")

        Returns:
            Expression: an Int64 expression with the index of the first occurrence of the substring in each string
        """
        substr_expr = Expression._to_expression(substr)
        return Expression._from_pyexpr(self._expr.utf8_find(substr_expr._expr))


class ExpressionListNamespace(ExpressionNamespace):
    def join(self, delimiter: str | Expression) -> Expression:
        """Joins every element of a list using the specified string delimiter

        Args:
            delimiter (str | Expression): the delimiter to use to join lists with

        Returns:
            Expression: a String expression which is every element of the list joined on the delimiter
        """
        delimiter_expr = Expression._to_expression(delimiter)
        return Expression._from_pyexpr(self._expr.list_join(delimiter_expr._expr))

    def lengths(self) -> Expression:
        """Gets the length of each list

        Returns:
            Expression: a UInt64 expression which is the length of each list
        """
        return Expression._from_pyexpr(self._expr.list_lengths())

    def get(self, idx: int | Expression, default: object = None) -> Expression:
        """Gets the element at an index in each list

        Args:
            idx: index or indices to retrieve from each list
            default: the default value if the specified index is out of bounds

        Returns:
            Expression: an expression with the type of the list values
        """
        idx_expr = Expression._to_expression(idx)
        default_expr = lit(default)
        return Expression._from_pyexpr(self._expr.list_get(idx_expr._expr, default_expr._expr))


class ExpressionStructNamespace(ExpressionNamespace):
    def get(self, name: str) -> Expression:
        """Retrieves one field from a struct column

        Args:
            name: the name of the field to retrieve

        Returns:
            Expression: the field expression
        """
        return Expression._from_pyexpr(self._expr.struct_get(name))


class ExpressionsProjection(Iterable[Expression]):
    """A collection of Expressions that can be projected onto a Table to produce another Table

    Invariants:
        1. All Expressions have names
        2. All Expressions have unique names
    """

    def __init__(self, exprs: list[Expression]) -> None:
        # Check invariants
        seen: set[str] = set()
        for e in exprs:
            if e.name() in seen:
                raise ValueError(f"Expressions must all have unique names; saw {e.name()} twice")
            seen.add(e.name())

        self._output_name_to_exprs = {e.name(): e for e in exprs}

    @classmethod
    def from_schema(cls, schema: Schema) -> ExpressionsProjection:
        return cls([col(field.name) for field in schema])

    def __len__(self) -> int:
        return len(self._output_name_to_exprs)

    def __iter__(self) -> Iterator[Expression]:
        return iter(self._output_name_to_exprs.values())

    @overload
    def __getitem__(self, idx: slice) -> list[Expression]:
        ...

    @overload
    def __getitem__(self, idx: int) -> Expression:
        ...

    def __getitem__(self, idx: int | slice) -> Expression | list[Expression]:
        # Relies on the fact that Python dictionaries are ordered
        return list(self._output_name_to_exprs.values())[idx]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExpressionsProjection):
            return False

        return len(self._output_name_to_exprs) == len(other._output_name_to_exprs) and all(
            (s.name() == o.name()) and expr_structurally_equal(s, o)
            for s, o in zip(self._output_name_to_exprs.values(), other._output_name_to_exprs.values())
        )

    def required_columns(self) -> set[str]:
        """Column names required to run this ExpressionsProjection"""
        result: set[str] = set()
        for e in self._output_name_to_exprs.values():
            result |= e._required_columns()
        return result

    def union(self, other: ExpressionsProjection, rename_dup: str | None = None) -> ExpressionsProjection:
        """Unions two Expressions. Output naming conflicts are handled with keyword arguments.

        Args:
            other (ExpressionsProjection): other ExpressionsProjection to union with this one
            rename_dup (Optional[str], optional): when conflicts in naming happen, append this string to the conflicting column in `other`. Defaults to None.
        """
        unioned: dict[str, Expression] = {}
        for expr in list(self) + list(other):
            name = expr.name()

            # Handle naming conflicts
            if name in unioned:
                if rename_dup is not None:
                    while name in unioned:
                        name = f"{rename_dup}{name}"
                    expr = expr.alias(name)
                else:
                    raise ValueError(
                        f"Duplicate name found with different expression. name: {name}, seen: {unioned[name]}, current: {expr}"
                    )

            unioned[name] = expr
        return ExpressionsProjection(list(unioned.values()))

    def to_name_set(self) -> set[str]:
        return {e.name() for e in self}

    def input_mapping(self) -> dict[str, str]:
        """Returns a map of {output_name: input_name} for all expressions that are just no-ops/aliases of an existing input"""
        result = {}
        for e in self:
            input_map = e._input_mapping()
            if input_map is not None:
                result[e.name()] = input_map
        return result

    def to_column_expressions(self) -> ExpressionsProjection:
        return ExpressionsProjection([col(e.name()) for e in self])

    def get_expression_by_name(self, name: str) -> Expression:
        if name not in self._output_name_to_exprs:
            raise ValueError(f"{name} not found in ExpressionsProjection")
        return self._output_name_to_exprs[name]

    def to_inner_py_exprs(self) -> list[_PyExpr]:
        return [expr._expr for expr in self]

    def resolve_schema(self, schema: Schema) -> Schema:
        fields = [e._to_field(schema) for e in self]
        return Schema._from_field_name_and_types([(f.name, f.dtype) for f in fields])

    def __repr__(self) -> str:
        return f"{self._output_name_to_exprs.values()}"


class ExpressionImageNamespace(ExpressionNamespace):
    """Expression operations for image columns."""

    def decode(self, on_error: Literal["raise"] | Literal["null"] = "raise") -> Expression:
        """
        Decodes the binary data in this column into images.

        This can only be applied to binary columns that contain encoded images (e.g. PNG, JPEG, etc.)

        Args:
            on_error: Whether to raise when encountering an error, or log a warning and return a null

        Returns:
            Expression: An Image expression represnting an image column.
        """
        raise_on_error = False
        if on_error == "raise":
            raise_on_error = True
        elif on_error == "null":
            raise_on_error = False
        else:
            raise NotImplemented(f"Unimplemented on_error option: {on_error}.")

        return Expression._from_pyexpr(self._expr.image_decode(raise_error_on_failure=raise_on_error))

    def encode(self, image_format: str | ImageFormat) -> Expression:
        """
        Encode an image column as the provided image file format, returning a binary column
        of encoded bytes.

        Args:
            image_format: The image file format into which the images will be encoded.

        Returns:
            Expression: A Binary expression representing a binary column of encoded image bytes.
        """
        if isinstance(image_format, str):
            image_format = ImageFormat.from_format_string(image_format.upper())
        if not isinstance(image_format, ImageFormat):
            raise ValueError(f"image_format must be a string or ImageFormat variant, but got: {image_format}")
        return Expression._from_pyexpr(self._expr.image_encode(image_format))

    def resize(self, w: int, h: int) -> Expression:
        """
        Resize image into the provided width and height.

        Args:
            w: Desired width of the resized image.
            h: Desired height of the resized image.

        Returns:
            Expression: An Image expression representing an image column of the resized images.
        """
        if not isinstance(w, int):
            raise TypeError(f"expected int for w but got {type(w)}")
        if not isinstance(h, int):
            raise TypeError(f"expected int for h but got {type(h)}")
        return Expression._from_pyexpr(self._expr.image_resize(w, h))

    def crop(self, bbox: tuple[int, int, int, int] | Expression) -> Expression:
        """
        Crops images with the provided bounding box

        Args:
            bbox (tuple[float, float, float, float] | Expression): Either a tuple of (x, y, width, height)
                parameters for cropping, or a List Expression where each element is a length 4 List
                which represents the bounding box for the crop

        Returns:
            Expression: An Image expression representing the cropped image
        """
        if not isinstance(bbox, Expression):
            if len(bbox) != 4 or not all([isinstance(x, int) for x in bbox]):
                raise ValueError(
                    f"Expected `bbox` to be either a tuple of 4 ints or an Expression but received: {bbox}"
                )
            bbox = Expression._to_expression(bbox).cast(DataType.fixed_size_list(DataType.uint64(), 4))
        assert isinstance(bbox, Expression)
        return Expression._from_pyexpr(self._expr.image_crop(bbox._expr))


class ExpressionPartitioningNamespace(ExpressionNamespace):
    def days(self) -> Expression:
        """Partitioning Transform that returns the number of days since epoch (1970-01-01)

        Returns:
            Expression: Date Type Expression
        """
        return Expression._from_pyexpr(self._expr.partitioning_days())

    def hours(self) -> Expression:
        """Partitioning Transform that returns the number of hours since epoch (1970-01-01)

        Returns:
            Expression: Int32 Expression in hours
        """
        return Expression._from_pyexpr(self._expr.partitioning_hours())

    def months(self) -> Expression:
        """Partitioning Transform that returns the number of months since epoch (1970-01-01)

        Returns:
            Expression: Int32 Expression in months
        """

        return Expression._from_pyexpr(self._expr.partitioning_months())

    def years(self) -> Expression:
        """Partitioning Transform that returns the number of years since epoch (1970-01-01)

        Returns:
            Expression: Int32 Expression in years
        """

        return Expression._from_pyexpr(self._expr.partitioning_years())

    def iceberg_bucket(self, n: int) -> Expression:
        """Partitioning Transform that returns the Hash Bucket following the Iceberg Specification of murmur3_32_x86
        https://iceberg.apache.org/spec/#appendix-b-32-bit-hash-requirements

        Args:
            n (int): Number of buckets

        Returns:
            Expression: Int32 Expression with the Hash Bucket
        """
        return Expression._from_pyexpr(self._expr.partitioning_iceberg_bucket(n))

    def iceberg_truncate(self, w: int) -> Expression:
        """Partitioning Transform that truncates the input to a standard width `w` following the Iceberg Specification.
        https://iceberg.apache.org/spec/#truncate-transform-details

        Args:
            w (int): width of the truncation

        Returns:
            Expression: Expression of the Same Type of the input
        """
        return Expression._from_pyexpr(self._expr.partitioning_iceberg_truncate(w))


class ExpressionJsonNamespace(ExpressionNamespace):
    def query(self, jq_query: str) -> Expression:
        """Query JSON data in a column using a JQ-style filter https://jqlang.github.io/jq/manual/
        This expression uses jaq as the underlying executor, see https://github.com/01mf02/jaq for the full list of supported filters.

        Example:
            >>> df = daft.from_pydict({"col": ['{"a": 1}', '{"a": 2}', '{"a": 3}']})
            >>> df.with_column("res", df["col"].json.query(".a")).collect()
            ╭──────────┬──────╮
            │ col      ┆ res  │
            │ ---      ┆ ---  │
            │ Utf8     ┆ Utf8 │
            ╞══════════╪══════╡
            │ {"a": 1} ┆ 1    │
            ├╌╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌┤
            │ {"a": 2} ┆ 2    │
            ├╌╌╌╌╌╌╌╌╌╌┼╌╌╌╌╌╌┤
            │ {"a": 3} ┆ 3    │
            ╰──────────┴──────╯

        Args:
            jq_query (str): JQ query string

        Returns:
            Expression: Expression representing the result of the JQ query as a column of JSON-compatible strings
        """

        return Expression._from_pyexpr(self._expr.json_query(jq_query))
