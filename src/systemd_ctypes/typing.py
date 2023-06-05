import typing
from typing import TYPE_CHECKING

# The goal here is to continue to work on Python 3.6 while pretending to have
# access to some modern typing features.  The shims provided here are only
# enough for what we need for systemd_ctypes to work at runtime.


if TYPE_CHECKING:
    # See https://github.com/python/mypy/issues/1153 for why we do this separately
    from typing import Annotated, ForwardRef, TypeGuard, get_args, get_origin

else:
    # typing.get_args() and .get_origin() appeared in Python 3.8 but Annotated
    # arrived in 3.9.  Unfortunately, it's difficult to implement a mocked up
    # version of Annotated which works with the real typing.get_args() and
    # .get_origin() in Python 3.8, so we use our own versions there as well.
    try:
        from typing import Annotated, get_args, get_origin
    except ImportError:
        class AnnotatedMeta(type):
            def __getitem__(cls, params):
                class AnnotatedType:
                    __origin__ = Annotated
                    __args__ = params
                return AnnotatedType

        class Annotated(metaclass=AnnotatedMeta):
            pass

        def get_args(annotation: typing.Any) -> typing.Tuple[typing.Any]:
            return getattr(annotation, '__args__', ())

        def get_origin(annotation: typing.Any) -> typing.Any:
            return getattr(annotation, '__origin__', None)

    try:
        from typing import ForwardRef
    except ImportError:
        from typing import _ForwardRef as ForwardRef

    try:
        from typing import TypeGuard
    except ImportError:
        T = typing.TypeVar('T')

        class TypeGuard(typing.Generic[T]):
            pass


__all__ = (
    'Annotated',
    'ForwardRef',
    'TypeGuard',
    'get_args',
    'get_origin',
    'TYPE_CHECKING',
)
