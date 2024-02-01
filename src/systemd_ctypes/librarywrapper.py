# systemd_ctypes
#
# Copyright (C) 2022 Allison Karlitskaya <allison.karlitskaya@redhat.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import ctypes
import inspect
import logging
import os
import sys
import types
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    NewType,
    NoReturn,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from . import typing

# First in 3.10, and conditional import gives type errors
NoneType = type(None)

logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    CType = TypeVar("CType", bound=ctypes._CData)
    Callback = ctypes._FuncPointer
else:
    CType = TypeVar("CType")
    Callback = ctypes.c_void_p


if typing.TYPE_CHECKING:
    class Reference(Generic[CType], ctypes._Pointer[CType]):
        pass

    def byref(x: CType) -> Reference[CType]:
        raise NotImplementedError
else:
    class Reference(Generic[CType]):
        pass

    byref = ctypes.byref


UserData = Optional[ctypes.c_void_p]


class negative_errno(ctypes.c_int):
    def errcheck(self, func: Callable[..., object], _args: Tuple[object, ...]) -> int:
        result = self.value
        if result < 0:
            raise OSError(-result, f"{func.__name__}: {os.strerror(-result)}")
        return result


class utf8(ctypes.c_char_p):
    def errcheck(self, func: Callable[..., object], _args: Tuple[object, ...]) -> str:
        assert self.value is not None
        return self.value.decode()

    @classmethod
    def from_param(cls, value: str) -> 'utf8':
        return cls(value.encode())


class utf8_or_null(ctypes.c_char_p):
    def errcheck(self,
                 func: Callable[..., object],
                 _args: Tuple[object, ...]) -> Optional[str]:
        return self.value.decode() if self.value is not None else None

    @classmethod
    def from_param(cls, value: Optional[str]) -> 'utf8_or_null':
        return cls(value.encode() if value is not None else None)


class boolint(ctypes.c_int):
    def errcheck(self, func: Callable[..., object], _args: Tuple[object, ...]) -> bool:
        return bool(self.value)


WeakReference = NewType("WeakReference", int)
Errno = typing.Annotated[NoReturn, "errno"]


type_map = {
    Union[None, Errno]: negative_errno,  # technically returns int
    Union[bool, Errno]: negative_errno,  # technically returns int
    Union[int, Errno]: negative_errno,
    bool: boolint,
    Optional[str]: utf8_or_null,
    str: utf8,
    int: ctypes.c_int,
    WeakReference: ctypes.c_void_p
}


def map_type(annotation: Any, global_vars: Dict[str, object]) -> Any:
    try:
        return type_map[annotation]
    except KeyError:
        pass  # ... and try more cases below

    if isinstance(annotation, typing.ForwardRef):
        annotation = annotation.__forward_arg__

    if isinstance(annotation, str):
        annotation = global_vars[annotation]

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is Reference:
        return ctypes.POINTER(map_type(args[0], global_vars))

    elif origin is Union and NoneType in args:
        # the C pointer types are already nullable
        other_arg, = set(args) - {NoneType}
        return map_type(other_arg, global_vars)

    elif origin is typing.Annotated:
        return args[1]

    else:
        assert origin is None, origin
        return annotation


class ReferenceType(ctypes.c_void_p):
    @classmethod
    def _install_cfuncs(cls, cdll: ctypes.CDLL) -> None:
        logger.debug('Installing stubs for %s:', cls)
        stubs = tuple(cls.__dict__.items())
        for name, stub in stubs:
            if name.startswith("__"):
                continue
            cls._wrap(cdll, stub)

        cls._wrap(cdll, cls._ref)
        cls._wrap(cdll, cls._unref)

    @classmethod
    def _wrap(cls, cdll: ctypes.CDLL, stub: object) -> None:
        stub_type = type(stub)
        if isinstance(stub, staticmethod):
            # In older Python versions, staticmethod() isn't considered
            # callable, doesn't have a name, and can't be introspected with
            # inspect.signature(). Unwrap it.
            stub = stub.__func__
        assert isinstance(stub, types.FunctionType)
        name = stub.__name__
        signature = inspect.signature(stub)
        stub_globals = sys.modules.get(cls.__module__).__dict__

        func = cdll[f'{cls.__name__}_{name.lstrip("_")}']
        func.argtypes = tuple(
            map_type(parameter.annotation, stub_globals)
            for parameter in signature.parameters.values()
        )
        func.restype = map_type(signature.return_annotation, stub_globals)
        errcheck = getattr(func.restype, 'errcheck', None)
        if errcheck is not None:
            func.errcheck = errcheck

        logger.debug('  create wrapper %s.%s%s', cls.__name__, name, signature)
        logger.debug('    args %s res %s', func.argtypes, func.restype)

        # ctypes function pointer objects don't implement the usual function
        # descriptor logic, which means they won't bind as methods.  For static
        # methods, that's good, but for instance methods, we add a wrapper as
        # the easiest and most performant way to get the binding behaviour.
        if stub_type is not staticmethod:
            setattr(cls, name, lambda *args: func(*args))
        else:
            setattr(cls, name, func)

    def _unref(self: 'ReferenceType') -> None:
        ...

    def _ref(self: 'ReferenceType') -> None:
        ...

    T = TypeVar("T", bound='ReferenceType')

    @classmethod
    def ref(cls: Type[T], origin: WeakReference) -> T:
        self = cls(origin)
        self._ref()
        return self

    def __del__(self) -> None:
        if self.value is not None:
            self._unref()
