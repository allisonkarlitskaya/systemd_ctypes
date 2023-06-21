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
from enum import IntFlag, auto
from typing import Optional


class inotify_event(ctypes.Structure):
    _fields_ = (
        ('wd', ctypes.c_int32),
        ('mask', ctypes.c_uint32),
        ('cookie', ctypes.c_uint32),
        ('len', ctypes.c_uint32),
    )

    @property
    def name(self) -> Optional[bytes]:
        if self.len == 0:
            return None

        class event_with_name(ctypes.Structure):
            _fields_ = (*inotify_event._fields_, ('name', ctypes.c_char * self.len))

        name = ctypes.cast(ctypes.addressof(self), ctypes.POINTER(event_with_name)).contents.name
        assert isinstance(name, bytes)
        return name


class Event(IntFlag):
    ACCESS = auto()
    MODIFY = auto()
    ATTRIB = auto()
    CLOSE_WRITE = auto()
    CLOSE_NOWRITE = auto()
    OPEN = auto()
    MOVED_FROM = auto()
    MOVED_TO = auto()
    CREATE = auto()
    DELETE = auto()
    DELETE_SELF = auto()
    MOVE_SELF = auto()

    UNMOUNT = 1 << 13
    Q_OVERFLOW = auto()
    IGNORED = auto()

    ONLYDIR = 1 << 24
    DONT_FOLLOW = auto()
    EXCL_UNLINK = auto()

    MASK_CREATE = 1 << 28
    MASK_ADD = auto()
    ISDIR = auto()
    ONESHOT = auto()

    CLOSE = CLOSE_WRITE | CLOSE_NOWRITE
    MOVE = MOVED_FROM | MOVED_TO
    CHANGED = (MODIFY | ATTRIB | CLOSE_WRITE | MOVE |
               CREATE | DELETE | DELETE_SELF | MOVE_SELF)
