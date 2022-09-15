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
import socket
import sys
from ctypes import c_char, byref

from . import introspection
from .librarywrapper import utf8
from .libsystemd import sd
from .signature import parse_signature, parse_typestring

logger = logging.getLogger(__name__)


class BusError(Exception):
    def __init__(self, name, message):
        super().__init__(f'{name}: {message}')
        self.name = name
        self.message = message


class BusMessage(sd.bus_message):
    def get_bus(self):
        return Bus.ref(super().get_bus())

    def get_error(self):
        error = super().get_error()
        if error:
            return BusError(*error.contents.get())
        else:
            return None

    def new_method_return(self):
        reply = BusMessage()
        super().new_method_return(reply)
        return reply

    def new_method_error(self, error):
        reply = BusMessage()
        super().new_method_errorf(reply, error.name, "%s", error.message)
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

        # Variants
        if category == 'v':
            self.open_container(ord(category), value['t'])
            self.append_with_info(parse_typestring(value['t']), value['v'])
            self.close_container()
            return

        # Other containers
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
        error = message.get_error()
        if error is not None:
            self.future.set_exception(error)
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

    @staticmethod
    def from_fd(fd, server_id=None, attach_event=False):
        bus = Bus()
        sd.bus.new(bus)
        if server_id is not None:
            bus.set_server(True, server_id)
        bus.set_fd(fd, fd)
        bus.start()
        if attach_event:
            bus.attach_event(None, 0)
        return bus

    @staticmethod
    def socketpair(attach_event=False):
        client_socket, server_socket = socket.socketpair()
        client = Bus.from_fd(client_socket.detach(), attach_event=attach_event)
        server = Bus.from_fd(server_socket.detach(), sd.id128(), attach_event=attach_event)
        return client, server

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
            raise BusError(*error.get()) from exc

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

    def add_object(self, path, obj):
        slot = Slot(obj.message_received)
        super().add_object(byref(slot), path, slot.callback, slot.userdata)
        return slot


class BaseObject:
    def message_received(self, message):
        try:
            reply = message.new_method_return()
            handled = self.handle_method_call(message, reply)
        except BusError:
            assert False  # needs proper exception handling

        if handled:
            # message_send() is not available in RHEL 8
            message.get_bus().send(reply, None)
            return 1
        else:
            return 0

    def handle_method_call(self, message, reply):
        raise NotImplementedError

class Object(BaseObject):
    @classmethod
    def prepare(cls, iface):
        cls._dbus_interface = iface
        cls._dbus_members = {'properties': {}, 'methods': {}}
        for key, value in cls.__dict__.items():
            if not key.startswith('_') and hasattr(value, '_dbus_info'):
                category, member, info = value._dbus_info
                if member is None:
                    member = ''.join(part.title() for part in key.split('_'))
                cls._dbus_members[category][member] = value, info
        cls._dbus_xml = introspection.to_xml({
            cls._dbus_interface: {
                'methods': {
                    name: {
                        'in': list(in_args),
                        'out': list(out_args),
                    } for name, (_, (out_args, in_args)) in cls._dbus_members['methods'].items()
                },
                'properties': {
                    name: {
                        'flags': 'r',
                        'type': dbus_type,
                    } for name, (_, (dbus_type,)) in cls._dbus_members['properties'].items()
                },
                'signals': {
                },
            }
        })
        return cls

    @staticmethod
    def _add_info(category, member, *args):
        def wrapper(obj):
            obj._dbus_info = category, member, args
            return obj
        return wrapper

    @staticmethod
    def interface(name):
        return lambda cls: cls.prepare(name)

    @staticmethod
    def property(dbus_type, member=None):
        return Object._add_info('properties', member, dbus_type)

    @staticmethod
    def method(out_types=(), in_types=(), member=None):
        return Object._add_info('methods', member, out_types, in_types)

    def append_property(self, reply, info):
        func, (dbus_type,) = info
        reply.open_container(ord('v'), dbus_type)
        reply.append_arg(dbus_type, func(self))
        reply.close_container()

    def handle_method_call(self, message, reply):
        interface = message.get_interface(message)
        method = message.get_member()

        if interface == 'org.freedesktop.DBus.Introspectable':
            if method == 'Introspect' and message.has_signature(''):
                reply.append_arg('s', self._dbus_xml)
                return True

        elif interface == 'org.freedesktop.DBus.Properties':
            if method == 'GetAll' and message.has_signature('s'):
                iface, = message.get_body()
                if iface == self._dbus_interface:
                    reply.open_container(ord('a'), '{sv}')
                    for name, info in self._dbus_members['properties'].items():
                        reply.open_container(ord('e'), 'sv')
                        reply.append_arg('s', name)
                        self.append_property(reply, info)
                        reply.close_container()
                    reply.close_container()
                    return True

            elif method == 'Get' and message.has_signature('ss'):
                iface, name = message.get_body()
                if iface == self._dbus_interface:
                    info = self._dbus_members['properties'].get(name)
                    if info:
                        self.append_property(reply, info)
                        return True

        elif interface == self._dbus_interface:
            info = self._dbus_members['methods'].get(message.get_member())
            if info:
                handler, (out_types, in_types) = info
                if message.has_signature(''.join(in_types)):
                    result = handler(self, *message.get_body())

                    # In the general case, a function returns an n-tuple, but
                    # we special-case n=0 and n=1 to be more human-friendly.
                    if len(out_types) == 0:
                        assert result is None
                        result = ()
                    elif len(out_types) == 1:
                        result = (result,)

                    for out_type, arg in zip(out_types, result):
                        reply.append_arg(out_types, arg)
                    return True

        return False
