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

from librarywrapper import librarywrapper, negative_errno, utf8


class sd_bus_error(Structure):
    # This is ABI, so we are safe to assume it doesn't change
    # unfortunately, we lack anything like sd_bus_error_new()
    __fields__ = [
        ("name", str),
        ("message", str),
        ("_need_free", int)
    ]


# the _p types are (weak) pointers
# the _ref types own their value

class sd_bus_message_p(c_void_p):
    def ref(self):
        return sd.bus_message_ref(self).value


class sd_bus_message_ref(sd_bus_message_p):
    def __init__(self, message=None):
        super().__init__(message and message.ref())

    def __del__(self):
        sd.bus_message_unref(self)


class sd_bus_p(c_void_p):
    def ref(self):
        return sd.bus_ref(self).value


class sd_bus_ref(sd_bus_p):
    def __del__(self):
        sd.bus_unref(self)


class sd_event_p(c_void_p):
    def ref(self):
        return sd.bus_ref(self).value


class sd_event_ref(sd_event_p):
    def __del__(self):
        sd.event_unref(self)


class sd_bus_slot_p(c_void_p):
    pass


sd_bus_message_handler_t = CFUNCTYPE(c_int, sd_bus_message_p, c_void_p, POINTER(sd_bus_error))


class libsystemd(librarywrapper):
    soname = 'libsystemd.so.0'
    namespace = 'sd'
    functions = {
        'bus_attach_event': (negative_errno, [sd_bus_p, sd_event_p, c_int]),
        'bus_call': (negative_errno, [sd_bus_p, sd_bus_message_p, c_uint64, POINTER(sd_bus_error), POINTER(sd_bus_message_p)]),
        'bus_call_async': (negative_errno, [sd_bus_p, POINTER(sd_bus_slot_p), sd_bus_message_p, sd_bus_message_handler_t, c_void_p, c_uint64]),
        'bus_default_system': (negative_errno, [POINTER(sd_bus_p)]),
        'bus_default_user': (negative_errno, [POINTER(sd_bus_p)]),
        'bus_flush': (negative_errno, [sd_bus_p]),
        'bus_message_append_basic': (negative_errno, [sd_bus_message_p, c_char, c_void_p]),
        'bus_message_at_end': (negative_errno, [sd_bus_message_p, c_int]),
        'bus_message_close_container': (negative_errno, [sd_bus_message_p]),
        'bus_message_enter_container': (negative_errno, [sd_bus_message_p, c_char, utf8]),
        'bus_message_exit_container': (negative_errno, [sd_bus_message_p]),
        'bus_message_get_signature': (utf8, [sd_bus_message_p, c_int]),
        'bus_message_new': (negative_errno, [sd_bus_p, POINTER(sd_bus_message_p), c_uint8]),
        'bus_message_new_method_call': (negative_errno, [sd_bus_p, POINTER(sd_bus_message_p), utf8, utf8, utf8, utf8]),
        'bus_message_open_container': (negative_errno, [sd_bus_message_p, c_char, utf8]),
        'bus_message_peek_type': (negative_errno, [sd_bus_message_p, POINTER(c_char), POINTER(utf8)]),
        'bus_message_read_basic': (negative_errno, [sd_bus_message_p, c_char, c_void_p]),
        'bus_message_ref': (sd_bus_message_p, [sd_bus_message_p]),
        'bus_message_unref': (None, [sd_bus_message_p]),
        'bus_new': (negative_errno, [POINTER(sd_bus_p)]),
        'bus_process': (negative_errno, [sd_bus_p, c_uint64]),
        'bus_unref': (None, [sd_bus_p]),
        'bus_wait': (negative_errno, [sd_bus_p, c_uint64]),

        'event_default': (negative_errno, [POINTER(sd_event_p)]),
        'event_prepare': (negative_errno, [sd_event_p]),
        'event_wait': (negative_errno, [sd_event_p, c_uint64]),
        'event_dispatch': (negative_errno, [sd_event_p]),
        'event_run': (negative_errno, [sd_event_p]),
        'event_get_fd': (negative_errno, [sd_event_p]),
        'event_loop': (negative_errno, [sd_event_p]),
        'event_unref': (None, [sd_event_p]),
    }


sd = libsystemd()
