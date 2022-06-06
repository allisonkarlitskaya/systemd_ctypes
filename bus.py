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

import asyncio
import itertools

from ctypes import c_char, c_uint8, c_uint, c_int16, c_uint16, c_int32, c_uint32, c_int64, c_uint64, c_double, byref
from librarywrapper import utf8
from libsystemd import sd
import signature

BASIC_TYPE_MAP = {
    'y': c_uint8, 'b': c_uint,
    'n': c_int16, 'q': c_uint16, 'i': c_int32, 'u': c_uint32, 'x': c_int64, 't': c_uint64,
    'd': c_double, 's': utf8, 'o': utf8, 'g': utf8,
}


class Message(sd.bus_message):
    def append_with_info(self, typeinfo, value):
        category, contents, child_info = typeinfo

        if basic_type := BASIC_TYPE_MAP.get(category):
            self.append_basic(ord(category), basic_type.from_param(value))
        else:
            # Containers
            child_info_iter = itertools.repeat(child_info) if category == 'a' else child_info
            value_iter = value.items() if child_info[0] == 'e' else value

            self.open_container(category, contents)
            for child_info, child in zip(child_info_iter, value_iter):
                self.append_with_info(child_info, child)
            self.close_container()

    def append(self, typestring, value):
        self.append_with_info(signature.parse_typestring(typestring), value)

    def peek_type(self):
        type_holder = c_char()
        contents_holder = utf8()
        if super().peek_type(byref(type_holder), byref(contents_holder)) == 0:
            return None
        return chr(ord(type_holder.value)), contents_holder.value

    def yield_values(self):
        while next_type := self.peek_type():
            category, contents = next_type

            if basic_type := BASIC_TYPE_MAP.get(category):
                holder = basic_type()
                self.read_basic(ord(category), byref(holder))
                yield holder.value

            else:
                # Containers
                if category == 'a':
                    constructor = dict if contents.startswith('{') else list
                elif category == 'v':
                    constructor = next
                else:
                    constructor = tuple

                self.enter_container(ord(category), contents)
                value = constructor(self.yield_values())
                self.exit_container()

                yield value

    def read_body(self):
        return list(self.yield_values())


class PendingCall:
    def __init__(self):
        self.callback = sd.bus_message_handler_t(self.done)
        self.future = asyncio.get_running_loop().create_future()

    def done(self, _message, userdata, error):
        message = Message(_message)

        if message:
            self.future.set_result(message)
        else:
            self.future.set_exception(Exception)
        return 0


class Bus(sd.bus):
    @staticmethod
    def default_user():
        bus = Bus()
        sd.bus.default_user(bus)
        return bus

    @staticmethod
    def default_system():
        bus = Bus()
        sd.bus.default_system(bus)
        return bus

    def message_new_method_call(self, destination, path, interface, member):
        message = Message()
        super().message_new_method_call(message, destination, path, interface, member)
        return message

    def call(self, message, timeout):
        reply = Message()
        error = sd.bus_error()
        super().call(message, timeout, error, reply)
        return reply

    async def call_async(self, message, timeout):
        pending = PendingCall()
        super().call_async(None, message, pending.callback, None, timeout)
        return await pending.future
