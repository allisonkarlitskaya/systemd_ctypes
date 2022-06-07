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
import os


class negative_errno(ctypes.c_int):
    def errcheck(self, func, _args):
        result = self.value
        if result < 0:
            raise OSError(-result, f'{func.__name__}: {os.strerror(-result)}')
        return result


class utf8(ctypes.c_char_p):
    def __init__(self, value=None):
        if value is not None:
            value = value.encode('utf-8')
        super().__init__(value)

    @classmethod
    def from_param(cls, value):
        return cls(value)

    @property
    def value(self):
        value = super().value
        if value is not None:
            value = value.decode('utf-8')
        return value


class boolint(ctypes.c_int):
    @property
    def value(self):
        # https://github.com/python/cpython/issues/73456
        return bool(ctypes.c_int.value.__get__(self))


def instancemethod(func):
    func.argtypes = [ctypes.c_void_p, *func.argtypes]
    def wrapper(*args):
        return func(*args)
    return wrapper


class librarywrapper:
    def __getattr__(self, name):
        # This is mostly to silence pylint complaining about unknown attributes
        raise AttributeError(f'{self.namespace}_{name} is not registered')

    @classmethod
    def dlopen(cls, soname):
        cls._library = ctypes.cdll.LoadLibrary(soname)

    @classmethod
    def register_reference_types(cls, type_names):
        for name in type_names:
            class pointer_type(ctypes.c_void_p):
                __qualname__ = f'{cls.__qualname__}.{name}_p'
                __module__ = cls.__module__
                _library = cls._library
                namespace = f'{cls.namespace}_{name}'

                @classmethod
                def register_methods(cls, methods, private=False):
                    prefix = '_' if private else ''
                    for decorator, restype, name, argtypes in methods:
                        func = getattr(cls._library, f'{cls.namespace}_{name}')
                        func.restype, func.argtypes = restype, argtypes
                        if hasattr(restype, 'errcheck'):
                            func.errcheck = func.restype.errcheck
                        setattr(cls, f'{prefix}{name}', decorator(func))

            pointer_type.register_methods([(instancemethod, pointer_type, 'ref', [])], private=True)
            pointer_type.__name__ = f'{name}_p'
            setattr(cls, pointer_type.__name__, pointer_type)

            class reference_type(pointer_type):
                __qualname__ = f'{cls.__qualname__}.{name}'
                __module__ = cls.__module__

                @classmethod
                def ref(cls, obj):
                    return cls(obj._ref().value)

                def __del__(self):
                    if self.value is not None:
                        self._unref()

            reference_type.register_methods([(instancemethod, None, 'unref', [])], private=True)
            reference_type.__name__ = name
            setattr(cls, reference_type.__name__, reference_type)
