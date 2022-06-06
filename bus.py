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

from libsystemd import *
from event import Event
import signature
import asyncio


BASIC_TYPE_MAP = {
    'y': c_uint8, 'b': c_uint,
    'n': c_int16, 'q': c_uint16, 'i': c_int32, 'u': c_uint32, 'x': c_int64, 't': c_uint64,
    'd': c_double, 's': utf8, 'o': utf8, 'g': utf8,
}

class Message(sd_bus_message_ref):
    def append_with_info(self, typeinfo, value):
        category, contents, child_info = typeinfo

        if basic_type := BASIC_TYPE_MAP.get(category):
            sd.bus_message_append_basic(self, ord(category), basic_type.from_param(value))
        else:
            # Containers
            child_info_iter = itertools.repeat(child_info) if category == 'a' else child_info
            value_iter = value.items() if child_info[0] == 'e' else value

            sd.bus_open_container(category, contents)
            for child_info, child in zip(child_info, value):
                self.append_with_info(child_info, child)
            sd.bus_close_container()

    def append(self, typestring, value):
        self.append_with_info(signature.parse_typestring(typestring), value)

    def peek_type(self):
        type_holder = c_char()
        contents_holder = utf8()
        if sd.bus_message_peek_type(self, byref(type_holder), byref(contents_holder)) == 0:
            return None
        return chr(ord(type_holder.value)), contents_holder.value

    def yield_values(self):
        while next_type := self.peek_type():
            category, contents = next_type

            if basic_type := BASIC_TYPE_MAP.get(category):
                holder = basic_type()
                sd.bus_message_read_basic(self, ord(category), byref(holder))
                yield holder.value

            else:
                # Containers
                if category == 'a':
                    constructor = dict if contents.startswith('{') else list
                elif category == 'v':
                    constructor = next
                else:
                    constructor = tuple

                sd.bus_message_enter_container(self, ord(category), contents)
                value = constructor(self.yield_values())
                sd.bus_message_exit_container(self)

                yield value

    def read_body(self):
        return list(self.yield_values())


class PendingCall:
    def __init__(self):
        self.callback = sd_bus_message_handler_t(self.done)
        self.future = asyncio.get_running_loop().create_future()

    def done(self, _message, userdata, error):
        message = Message(_message)

        if message:
            self.future.set_result(message)
        else:
            self.future.set_exception(Exception)
        return 0

class Bus(sd_bus_ref):
    @staticmethod
    def default_user():
        bus = Bus()
        sd.bus_default_user(bus)
        return bus

    @staticmethod
    def default_system():
        bus = Bus()
        sd.bus_default_system(bus)
        return bus

    def flush(self):
        sd.bus_flush(self)

    def message_new_method_call(self, destination, path, interface, member):
        message = Message()
        sd.bus_message_new_method_call(self, message, destination, path, interface, member)
        return message

    def call(self, message, timeout):
        reply = Message()
        error = sd_bus_error()
        sd.bus_call(self, message, timeout, error, reply)
        return reply

    async def call_async(self, message, timeout):
        pending = PendingCall()
        sd.bus_call_async(self, None, message, pending.callback, None, timeout)
        return await pending.future

    def attach_event(self, event=None, priority=0):
        sd.bus_attach_event(self, event, priority)
