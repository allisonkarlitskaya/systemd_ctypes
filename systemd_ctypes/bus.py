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

import asyncio
import base64
import itertools
import logging
import sys
from ctypes import c_char, byref

from .librarywrapper import utf8
from .libsystemd import sd
from .signature import parse_signature, parse_typestring

logger = logging.getLogger(__name__)

class BusMessage(sd.bus_message):
    def new_method_return(self):
        reply = BusMessage()
        super().new_method_return(reply)
        return reply

    def append_with_info(self, typeinfo, value):
        category, contents, child_info = typeinfo

        try:
            # Basic types
            self.append_basic(category, value)
            return
        except KeyError:
            pass

        # Support base64 encoding of binary blobs
        if category == 'a' and contents == 'y' and isinstance(value, str):
            value = base64.b64decode(value)

        # Containers
        child_info_iter = itertools.repeat(child_info) if category == 'a' else child_info
        value_iter = value.items() if child_info[0] == 'e' else value

        self.open_container(ord(category), contents)
        for child_info, child in zip(child_info_iter, value_iter):
            self.append_with_info(child_info, child)
        self.close_container()

    def append_arg(self, typestring, arg):
        self.append_with_info(parse_typestring(typestring), arg)

    def append(self, signature, *args):
        infos = parse_signature(signature)
        assert len(infos) == len(args)
        for info, arg in zip(infos, args):
            self.append_with_info(info, arg)

    def yield_values(self):
        category_holder, contents_holder = c_char(), utf8()
        while self.peek_type(byref(category_holder), byref(contents_holder)):
            category, contents = chr(ord(category_holder.value)), contents_holder.value

            try:
                # Basic types
                yield self.read_basic(category)
                continue
            except KeyError:
                pass

            # Containers
            if category == 'a':
                constructor = dict if contents.startswith('{') else list
            elif category == 'v':
                constructor = lambda i: {"t": contents, "v": next(i)}
            else:
                constructor = tuple

            self.enter_container(ord(category), contents)
            value = constructor(self.yield_values())
            self.exit_container()

            # base64 encode binary blobs
            if category == 'a' and contents == 'y':
                value = base64.b64encode(bytes(value)).decode('ascii')

            yield value

    def get_body(self):
        self.rewind(True)
        return tuple(self.yield_values())


class BusError(Exception):
    def __init__(self, code, description, message=None):
        super().__init__(description)
        self.code = code
        self.description = description
        self.message = message


class Slot(sd.bus_slot):
    def __init__(self, callback=None):
        self.__func__ = callback
        self.callback = sd.bus_message_handler_t(self._callback)
        self.userdata = None

    def _callback(self, _message, _userdata, _ret_error):
        # If this throws an exception, ctypes will log the message and return
        # -1 which is actually more or less exactly what we want.
        return 1 if self.__func__(BusMessage.ref(_message)) else 0


class PendingCall(Slot):
    def __init__(self):
        super().__init__(self.done)

        get_loop = asyncio.get_running_loop if sys.version_info >= (3, 7, 0) else asyncio.get_event_loop
        self.future = get_loop().create_future()

    def done(self, message):
        if message.is_method_error(None):
            error = message.get_error()[0]
            self.future.set_exception(BusError(error.name.value, error.message.value, message))
        else:
            self.future.set_result(message)
        return True


class Bus(sd.bus):
    _default_system = None
    _default_user = None

    @staticmethod
    def default_system(attach_event=False):
        if not Bus._default_system:
            Bus._default_system = Bus()
            sd.bus.default_system(Bus._default_system)
            if attach_event:
                Bus._default_system.attach_event(None, 0)
        return Bus._default_system

    @staticmethod
    def default_user(attach_event=False):
        if not Bus._default_user:
            Bus._default_user = Bus()
            sd.bus.default_user(Bus._default_user)
            if attach_event:
                Bus._default_user.attach_event(None, 0)
        return Bus._default_user

    def message_new_method_call(self, destination, path, interface, member, types='', *args):
        message = BusMessage()
        super().message_new_method_call(message, destination, path, interface, member)
        message.append(types, *args)
        return message

    def call(self, message, timeout=None):
        reply = BusMessage()
        error = sd.bus_error()
        try:
            super().call(message, timeout or 0, byref(error), reply)
            return reply
        except OSError as exc:
            raise BusError(error.name.value, error.message.value, reply) from exc

    def call_method(self, destination, path, interface, member, types='', *args, timeout=None):
        logger.debug('Doing sync method call %s %s %s %s %s %s',
                destination, path, interface, member, types, args)
        message = self.message_new_method_call(destination, path, interface, member, types, *args)
        message = self.call(message, timeout)
        return message.get_body()

    async def call_async(self, message, timeout=None):
        pending = PendingCall()
        super().call_async(pending, message, pending.callback, pending.userdata, timeout or 0)
        return await pending.future

    async def call_method_async(self, destination, path, interface, member, types='', *args, timeout=None):
        logger.debug('Doing async method call %s %s %s %s %s %s',
                destination, path, interface, member, types, args)
        message = self.message_new_method_call(destination, path, interface, member, types, *args)
        message = await self.call_async(message, timeout)
        return message.get_body()

    def add_match(self, rule, handler):
        slot = Slot(handler)
        super().add_match(byref(slot), rule, slot.callback, slot.userdata)
        return slot
