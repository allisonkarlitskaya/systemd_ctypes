#
# Copyright (C) 2022  Allison Karlitskaya
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

from ctypes import *
import types
import os


class negative_errno(c_int):
    @staticmethod
    def errcheck(self, func, args):
        result = self.value
        if result < 0:
            raise OSError(-result, os.strerror(-result))
        return result


class utf8(c_char_p):
    @classmethod
    def from_param(cls, value):
        return cls(value.encode('utf-8'))
    @property
    def value(self):
        data = super().value
        return None if data is None else data.decode('utf-8')
    #@staticmethod
    #def errcheck(self, func, args):
    #    return self.value


class instancemethod:
    def __init__(self, function):
        self.__func__ = function

    def __get__(self, obj, objtype=None):
        if not obj:
            return self
        return types.MethodType(self.__func__, obj)


class typewrapper(c_void_p):
    @classmethod
    def register_methods(cls, methods, private=False):
        prefix = '_' if private else ''
        for decorator, restype, name, argtypes in methods:
            func = getattr(cls._library, f'{cls.namespace}_{name}')
            func.restype = restype
            if hasattr(restype, 'errcheck'):
                func.errcheck = func.restype.errcheck
            if decorator is instancemethod:
                func.argtypes = [cls, *argtypes]
            else:
                func.argtypes = argtypes
            setattr(cls, f'{prefix}{name}', decorator(func))

    @classmethod
    def ref(cls, obj):
        assert cls._owns_reference
        return cls(obj._ref().value)

    def __del__(self):
        if self._owns_reference and self.value is not None:
            self._unref()

class librarywrapper:
    @classmethod
    def dlopen(cls, soname):
        cls._library = cdll.LoadLibrary(soname)

    @classmethod
    def register_reference_types(cls, type_names):
        overrides = {key: cls.__dict__[key] for key in ['__module__', '_library']}
        for name in type_names:
            # the {name}_p types are (weak) pointers
            # the {name} types own their value
            weak_ref = type(f'{name}_p', (typewrapper,), overrides)
            weak_ref.namespace = f'{cls.namespace}_{name}'
            weak_ref._owns_reference = False
            weak_ref.register_methods([
                (instancemethod, weak_ref, 'ref', []),
                (instancemethod, None, 'unref', [])
            ], private=True)
            ref = type(name, (weak_ref,), overrides)
            ref._owns_reference = True
            setattr(cls, f'{name}_p', weak_ref)
            setattr(cls, f'{name}', ref)
