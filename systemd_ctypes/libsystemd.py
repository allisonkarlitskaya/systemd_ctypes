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

import sys

from ctypes import Structure, byref, \
        c_uint8, c_uint16, c_uint32, c_uint64, \
        c_char, c_int, c_int16, c_int32, c_int64, \
        c_double, c_void_p, \
        CFUNCTYPE, POINTER

from .inotify import inotify_event
from .librarywrapper import librarywrapper, utf8, negative_errno, instancemethod, boolint


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

    class id128(Structure):
        # HACK: Pass-by-value of array-containing-structs is broken on Python
        # 3.6. See https://bugs.python.org/issue22273
        _fields_ = [
            ("bytes", c_uint8 * 16)
        ] if sys.version_info >= (3, 7, 0) else [
            ("one", c_uint64), ("two", c_uint64)
        ]


sd.dlopen('libsystemd.so.0')

sd.register_reference_types([
    'bus',
    'bus_message',
    'bus_slot',
    'event',
    'event_source',
])

sd.bus_message_handler_t = CFUNCTYPE(c_int, sd.bus_message_p, c_void_p, POINTER(sd.bus_error))
sd.event_inotify_handler_t = CFUNCTYPE(c_int, sd.event_source_p, POINTER(inotify_event), c_void_p)

sd.event.register_methods([
    (instancemethod, negative_errno, 'add_inotify', [POINTER(sd.event_source), utf8, c_uint32, sd.event_inotify_handler_t, c_void_p]),
    (instancemethod, negative_errno, 'dispatch', []),
    (instancemethod, negative_errno, 'get_fd', []),
    (instancemethod, negative_errno, 'get_state', []),
    (instancemethod, negative_errno, 'loop', []),
    (instancemethod, negative_errno, 'prepare', []),
    (instancemethod, negative_errno, 'wait', [c_uint64]),
    (staticmethod, negative_errno, 'default', [POINTER(sd.event_p)]),
])

class InvalidArgsError(Exception):
    pass

class utf8_object_path(utf8):
    def __init__(self, value=None):
        super().__init__(value)
        if self.value is not None:
            # TODO - check all the requirements
            if len(self.value) == 0 or self.value[0] != "/":
                raise InvalidArgsError(f"Invalid object path '{self.value}'")

class utf8_signature(utf8):
    def __init__(self, value=None):
        super().__init__(value)
        if self.value is not None:
            # TODO - check all the requirements
            if " " in self.value:
                raise InvalidArgsError(f"Invalid signature '{self.value}'")

BASIC_TYPE_MAP = {
    'y': c_uint8, 'b': boolint,
    'n': c_int16, 'q': c_uint16, 'i': c_int32, 'u': c_uint32, 'x': c_int64, 't': c_uint64,
    'd': c_double, 's': utf8, 'o': utf8_object_path, 'g': utf8_signature,
}

# Typesafe wrapper for functions that require the third argument to correspond
# to the type specified by the character given as the second argument.
# Raises KeyError in case the type character is not supported.
def basic_type_in(func):
    def wrapper(self, char, value):
        try:
            box = BASIC_TYPE_MAP[char](value)
        except TypeError:
            box = None
        if box is None or box.value != value:
            raise InvalidArgsError(f"{value} does not fit in a '{char}'")
        func(self, ord(char), box if isinstance(box, utf8) else byref(box))
    return wrapper
def basic_type_out(func):
    def wrapper(self, char):
        box = BASIC_TYPE_MAP[char]()
        func(self, ord(char), byref(box))
        return box.value
    return wrapper

sd.bus_message.register_methods([
    (basic_type_in, negative_errno, 'append_basic', [sd.bus_message_p, c_char, c_void_p]),
    (basic_type_out, negative_errno, 'read_basic', [sd.bus_message_p, c_char, c_void_p]),
    (instancemethod, POINTER(sd.bus_error), 'get_error', []),
    (instancemethod, negative_errno, 'at_end', [boolint]),
    (instancemethod, negative_errno, 'close_container', []),
    (instancemethod, negative_errno, 'enter_container', [c_char, utf8]),
    (instancemethod, negative_errno, 'exit_container', []),
    (instancemethod, negative_errno, 'has_signature', [utf8]),
    (instancemethod, negative_errno, 'is_method_error', [utf8]),
    (instancemethod, negative_errno, 'new_method_return', [POINTER(sd.bus_message_p)]),
    (instancemethod, negative_errno, 'open_container', [c_char, utf8]),
    (instancemethod, negative_errno, 'peek_type', [POINTER(c_char), POINTER(utf8)]),
    (instancemethod, negative_errno, 'rewind', [boolint]),
    (instancemethod, negative_errno, 'seal', [c_uint64, c_uint64]),
    (instancemethod, sd.bus_p, 'get_bus', []),
    (instancemethod, utf8, 'get_destination', []),
    (instancemethod, utf8, 'get_interface', []),
    (instancemethod, utf8, 'get_member', []),
    (instancemethod, utf8, 'get_path', []),
    (instancemethod, utf8, 'get_sender', []),
    (instancemethod, utf8, 'get_signature', [boolint]),
])

sd.bus.register_methods([
    (instancemethod, negative_errno, 'add_match', [POINTER(sd.bus_slot), utf8, sd.bus_message_handler_t, c_void_p]),
    (instancemethod, negative_errno, 'add_match_async', [POINTER(sd.bus_slot), utf8, sd.bus_message_handler_t, sd.bus_message_handler_t, c_void_p]),
    (instancemethod, negative_errno, 'add_object', [POINTER(sd.bus_slot), utf8, sd.bus_message_handler_t, c_void_p]),
    (instancemethod, negative_errno, 'attach_event', [sd.event_p, c_int]),
    (instancemethod, negative_errno, 'call', [sd.bus_message_p, c_uint64, POINTER(sd.bus_error), POINTER(sd.bus_message_p)]),
    (instancemethod, negative_errno, 'call_async', [POINTER(sd.bus_slot), sd.bus_message_p, sd.bus_message_handler_t, c_void_p, c_uint64]),
    (instancemethod, negative_errno, 'flush', []),
    (instancemethod, negative_errno, 'message_new', [POINTER(sd.bus_message_p), c_uint8]),
    (instancemethod, negative_errno, 'message_new_method_call', [POINTER(sd.bus_message_p), utf8, utf8, utf8, utf8]),
    (instancemethod, negative_errno, 'new', [POINTER(sd.bus_p)]),
    (instancemethod, negative_errno, 'set_fd', [c_int, c_int]),
    (instancemethod, negative_errno, 'set_server', [boolint, sd.id128]),
    (instancemethod, negative_errno, 'start', []),
    (instancemethod, negative_errno, 'wait', [c_uint64]),
    (instancemethod, negative_errno, 'send', [sd.bus_message_p, POINTER(c_uint64)]),
    (staticmethod, negative_errno, 'default_system', [POINTER(sd.bus_p)]),
    (staticmethod, negative_errno, 'default_user', [POINTER(sd.bus_p)]),
    (staticmethod, negative_errno, 'new', [POINTER(sd.bus_p)]),
])
