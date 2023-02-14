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
import enum


class inotify_event(ctypes.Structure):
    _fields_ = [
        ('wd', ctypes.c_int32),
        ('mask', ctypes.c_uint32),
        ('cookie', ctypes.c_uint32),
        ('len', ctypes.c_uint32),
    ]

    @property
    def name(self):
        class event_with_name(ctypes.Structure):
            _fields_ = [*inotify_event._fields_, ('name', ctypes.c_char * self.len)]
        return ctypes.cast(ctypes.addressof(self), ctypes.POINTER(event_with_name)).contents.name


Event = enum.IntFlag('inotify.Event', [
    'ACCESS', 'MODIFY', 'ATTRIB', 'CLOSE_WRITE',
    'CLOSE_NOWRITE', 'OPEN', 'MOVED_FROM', 'MOVED_TO',
    'CREATE', 'DELETE', 'DELETE_SELF', 'MOVE_SELF',
    '_unused_0x1000', 'UNMOUNT', 'Q_OVERFLOW', 'IGNORED',
    '_unused_0x10000', '_unused_0x20000', '_unused_0x40000', '_unused_0x80000',
    '_unused_0x100000', '_unused_0x200000', '_unused_0x400000', '_unused_0x800000',
    'ONLYDIR', 'DONT_FOLLOW', 'EXCL_UNLINK', '_unused_0x8000000',
    'MASK_CREATE', 'MASK_ADD', 'ISDIR', 'ONESHOE'

])
Event.CLOSE = Event.CLOSE_WRITE | Event.CLOSE_NOWRITE
Event.MOVE = Event.MOVED_FROM | Event.MOVED_TO

# non-standard.  All "change" events (ie: excluding read-only events)
Event.CHANGED = (Event.MODIFY | Event.ATTRIB | Event.CLOSE_WRITE | Event.MOVE |
                 Event.CREATE | Event.DELETE | Event.DELETE_SELF | Event.MOVE_SELF)
