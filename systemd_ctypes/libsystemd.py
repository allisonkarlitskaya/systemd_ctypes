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

from ctypes import *

from .librarywrapper import *


class sd(librarywrapper):
    namespace = 'sd'

    class bus_error(Structure):
        # This is ABI, so we are safe to assume it doesn't change
        # unfortunately, we lack anything like sd_bus_error_new()
        _fields_ = [
            ("name", utf8),
            ("message", utf8),
            ("_need_free", c_int)
        ]

sd.dlopen('libsystemd.so.0')

sd.register_reference_types([
    'bus',
    'bus_message',
    'bus_slot',
    'event',
])

sd.bus_message_handler_t = CFUNCTYPE(c_int, sd.bus_message_p, c_void_p, POINTER(sd.bus_error))

sd.event.register_methods([
    (staticmethod, negative_errno, 'default', [POINTER(sd.event_p)]),
    (instancemethod, negative_errno, 'prepare', []),
    (instancemethod, negative_errno, 'wait', [c_uint64]),
    (instancemethod, negative_errno, 'dispatch', []),
    (instancemethod, negative_errno, 'get_fd', []),
    (instancemethod, negative_errno, 'loop', []),
])

BASIC_TYPE_MAP = {
    'y': c_uint8, 'b': boolint,
    'n': c_int16, 'q': c_uint16, 'i': c_int32, 'u': c_uint32, 'x': c_int64, 't': c_uint64,
    'd': c_double, 's': utf8, 'o': utf8, 'g': utf8,
}

# Typesafe wrapper for functions that require the third argument to correspond
# to the type specified by the character given as the second argument.
# Raises KeyError in case the type character is not supported.
def basic_type_in(func):
    def wrapper(self, char, value):
        box = BASIC_TYPE_MAP[char](value)
        func(self, ord(char), box if isinstance(box, utf8) else byref(box))
    return wrapper
def basic_type_out(func):
    def wrapper(self, char):
        box = BASIC_TYPE_MAP[char]()
        func(self, ord(char), byref(box))
        return box.value
    return wrapper

sd.bus_message.register_methods([
    (instancemethod, POINTER(sd.bus_error), 'get_error', []),
    (instancemethod, negative_errno, 'at_end', [boolint]),
    (instancemethod, negative_errno, 'close_container', []),
    (instancemethod, negative_errno, 'enter_container', [c_char, utf8]),
    (instancemethod, negative_errno, 'exit_container', []),
    (instancemethod, negative_errno, 'is_method_error', [utf8]),
    (instancemethod, negative_errno, 'open_container', [c_char, utf8]),
    (instancemethod, negative_errno, 'peek_type', [POINTER(c_char), POINTER(utf8)]),
    (instancemethod, negative_errno, 'rewind', [boolint]),
    (basic_type_in, negative_errno, 'append_basic', [sd.bus_message_p, c_char, c_void_p]),
    (basic_type_out, negative_errno, 'read_basic', [sd.bus_message_p, c_char, c_void_p]),
    (instancemethod, utf8, 'get_signature', [boolint]),
])

sd.bus.register_methods([
    (staticmethod, negative_errno, 'default_system', [POINTER(sd.bus_p)]),
    (staticmethod, negative_errno, 'default_user', [POINTER(sd.bus_p)]),
    (instancemethod, negative_errno, 'add_match', [POINTER(sd.bus_slot), utf8, sd.bus_message_handler_t, c_void_p]),
    (instancemethod, negative_errno, 'add_match_async', [POINTER(sd.bus_slot), utf8, sd.bus_message_handler_t, sd.bus_message_handler_t, c_void_p]),
    (instancemethod, negative_errno, 'attach_event', [sd.event_p, c_int]),
    (instancemethod, negative_errno, 'call', [sd.bus_message_p, c_uint64, POINTER(sd.bus_error), POINTER(sd.bus_message_p)]),
    (instancemethod, negative_errno, 'call_async', [POINTER(sd.bus_slot), sd.bus_message_p, sd.bus_message_handler_t, c_void_p, c_uint64]),
    (instancemethod, negative_errno, 'flush', []),
    (instancemethod, negative_errno, 'message_new', [POINTER(sd.bus_message_p), c_uint8]),
    (instancemethod, negative_errno, 'message_new_method_call', [POINTER(sd.bus_message_p), utf8, utf8, utf8, utf8]),
    (instancemethod, negative_errno, 'new', [POINTER(sd.bus_p)]),
    (instancemethod, negative_errno, 'process', [c_uint64]),
    (instancemethod, negative_errno, 'wait', [c_uint64]),
])
