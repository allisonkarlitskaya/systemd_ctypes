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

import os
from typing import Any


class Handle(int):
    """An integer subclass that makes it easier to work with file descriptors"""

    def __new__(cls, fd: int = -1) -> 'Handle':
        return super(Handle, cls).__new__(cls, fd)

    # separate __init__() to set _needs_close mostly to keep pylint quiet
    def __init__(self, fd: int = -1):
        super().__init__()
        self._needs_close = fd != -1

    def __bool__(self) -> bool:
        return self != -1

    def close(self) -> None:
        if self._needs_close:
            self._needs_close = False
            os.close(self)

    def __eq__(self, value: object) -> bool:
        if int.__eq__(self, value):  # also handles both == -1
            return True

        if not isinstance(value, int):  # other object is not an int
            return False

        if not self or not value:  # when only one == -1
            return False

        return os.path.sameopenfile(self, value)

    def __del__(self) -> None:
        if self._needs_close:
            self.close()

    def __enter__(self) -> 'Handle':
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @classmethod
    def open(cls, *args: Any, **kwargs: Any) -> 'Handle':
        return cls(os.open(*args, **kwargs))

    def steal(self) -> 'Handle':
        self._needs_close = False
        return self.__class__(int(self))

    def fileno(self) -> int:
        return int(self)
