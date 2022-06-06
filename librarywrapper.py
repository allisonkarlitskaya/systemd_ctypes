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
        value = c_char_p.value.__get__(self)
        return None if value is None else value.decode('utf-8')
    #@staticmethod
    #def errcheck(self, func, args):
    #    return self.value


class librarywrapper:
    def __init__(self):
        library = cdll.LoadLibrary(self.__class__.soname)

        for name, signature in self.__class__.functions.items():
            func = library.__getattr__(f'{self.__class__.namespace}_{name}')
            func.restype, func.argtypes = signature
            if hasattr(func.restype, 'errcheck'):
                func.errcheck = func.restype.errcheck
            self.__setattr__(name, func)
