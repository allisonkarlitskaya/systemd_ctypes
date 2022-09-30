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
import functools
import itertools
import logging
import socket
from ctypes import c_char, byref
from typing import Any, Callable, Optional

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

    def new_method_return(self, signature='', *args):
        """Creates a new method return message as a reply to this message.

        :signature: The signature of the result, as a string.
        :args: The values to send, conforming to the signature string.

        :returns: the reply message
        """
        reply = BusMessage()
        super().new_method_return(reply)
        reply.append(signature, *args)
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

    def send(self):
        """Sends a message on the bus that it was created for.

        :returns: True
        """
        self.get_bus().send(self, None)
        return True

    def reply_method_error(self, error):
        """Sends an error as a reply to a method call message.

        :error: A BusError

        :returns: True
        """
        return self.new_method_error(error).send()

    def reply_method_return(self, signature='', *args):
        """Sends a return value as a reply to a method call message.

        :signature: The signature of the result, as a string.
        :args: The values to send, conforming to the signature string.

        :returns: True
        """
        return self.new_method_return(signature, *args).send()

    def _coroutine_task_complete(self, out_types, task):
        try:
            self.reply_method_return(out_types, task.result())
        except BusError as exc:
            return self.reply_method_error(exc)

    def reply_method_function_return_value(self, out_types, return_value):
        """Sends the result of a function call as a reply to a method call message.

        This call does a bit of magic: it adapts from the usual Python return
        value conventions (where the return value is ``None``, a single value,
        or a tuple) to the normal D-Bus return value conventions (where the
        result is always a tuple).

        Additionally, if the value is found to be a coroutine, a task is
        created to run the coroutine to completion and return the result
        (including exception handling).

        :out_types: The types of the return values, as an iterable.
        :return_value: The return value of a Python function call.

        :returns: True
        """
        if asyncio.coroutines.iscoroutine(return_value):
            task = asyncio.create_task(return_value)
            task.add_done_callback(functools.partial(self._coroutine_task_complete, out_types))
            return True

        reply = self.new_method_return()
        # In the general case, a function returns an n-tuple, but...
        if len(out_types) == 0:
            # Functions with no return value return None.
            assert return_value is None
        elif len(out_types) == 1:
            # Functions with a single return value return that value.
            reply.append_arg(out_types[0], return_value)
        else:
            # (general case) n return values are handled as an n-tuple.
            assert len(out_types) == len(return_value)
            for out_type, value in zip(out_types, return_value):
                reply.append_arg(out_type, value)
        return reply.send()


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
        self.future = asyncio.get_running_loop().create_future()

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

    def message_new_signal(self, path, interface, member, types='', *args):
        message = BusMessage()
        super().message_new_signal(message, path, interface, member)
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
        obj.registered_on_bus(self, path)
        return slot


class BaseObject:
    """Base object type for exporting objects on the bus

    This is the lowest-level class that can be passed to Bus.add_object().

    If you want to directly subclass this, you'll need to implement
    `message_received()`.

    Subclassing from `bus.Object` is probably a better choice.
    """
    __dbus_bus: Optional[Bus] = None
    __dbus_path: Optional[str] = None
    _dbus_interface: Optional[str] = None

    def registered_on_bus(self, bus: Bus, path: str) -> None:
        """Report that an instance was exported on a given bus and path.

        This is used so that the instance knows where to send signals.
        Bus.add_object() calls this: you probably shouldn't call this on your
        own.
        """
        self.__dbus_bus = bus
        self.__dbus_path = path

        self.registered()

    def registered(self) -> None:
        """Called after an object has been registered on the bus

        This is the correct method to implement to do some initial work that
        needs to be done after registration.  The default implementation does
        nothing.
        """
        pass

    def emit_signal(self, name: str, signature: str, *args: Any, interface: Optional[str] = None) -> bool:
        """Emit a D-Bus signal on this object

        The object must have been exported on the bus with Bus.add_object().
        Additionally, the object must have a defined interface name, or the
        interface kwarg must be provided.

        :name: the 'member' name of the signal to emit
        :signature: the type signature, as a string
        :args: the arguments, according to the signature
        :interface: (optional) override the interface of the signal
        :returns: True
        """
        assert self.__dbus_bus is not None
        return self.__dbus_bus.message_new_signal(
            self.__dbus_path,
            interface or self._dbus_interface,
            name,
            signature, *args
        ).send()

    def message_received(self, message: BusMessage) -> bool:
        """Called when a message is received for this object

        This is the lowest level interface to the BaseObject.  You need to
        handle method calls, properties, and introspection.

        You are expected to handle the message and return True.  Normally this
        means that you send a reply.  If you don't want to handle the message,
        return False and other handlers will have a chance to run.  If no
        handler handles the message, systemd will generate a suitable error
        message and send that, instead.

        :message: the message that was received
        :returns: True if the message was handled
        """
        raise NotImplementedError


class Object(BaseObject):
    """The high-level base class for exporting objects on D-Bus

    This class provides high-level APIs for defining methods, properties, and
    signals, as well as implementing introspection.

    This class works well with these helper classes:
      - Object.Method
      - Object.Property
      - Object.Signal
      - Object.Interface

    An example class might look like:

        @bus.Object.Interface('com.example.MyObject')
        class MyObject(bus.Object):
            created = bus.Object.Signal('s', 'i')
            renames = bus.Object.Property('u', value=0)
            name = bus.Object.Property('s', 'undefined')

            @bus.Object.Method(out_types=(), in_types='s')
            def rename(self, name):
                self.renames += 1
                self.name = name

            def registered(self):
                self.created('Hello', 42)

    See the documentation for the Method, Property, Signal and Interface
    classes for more information.
    """

    # Class variables
    _dbus_members: Optional[dict[str, dict[str, dict[str, dict[str, Any]]]]] = None

    # Instance variables
    _dbus_property_values: Optional[dict[str, Any]] = None

    class Interface:
        """Define the default interface name of the object

        This is meant to be used as a decorator, in order to set the default
        interface name of methods, properties, and signals associated with an
        Object.

            @bus.Object.Interface('com.example.MyObject')
            class MyObject:
                ...

        This decorator impacts calls to .emit_signal() when the interface=
        kwarg isn't given, and also impacts the definition of all Property,
        Signal, and Method declarations which lack an interface= kwarg.
        """

        def __init__(self, interface):
            self.interface = interface

        def __call__(self, cls):  # decorator
            cls._dbus_interface = self.interface
            # The members that didn't override the interface name become the
            # members of the default interface, now.
            cls._dbus_members[cls._dbus_interface] = cls._dbus_members[None]
            del cls._dbus_members[None]
            return cls

    class _Member:
        _category: str  # filled in from subclasses

        _python_name: Optional[str] = None
        _name: Optional[str] = None
        _interface: Optional[str] = None

        def __init__(self, name: Optional[str] = None, interface: Optional[str] = None):
            self._python_name = None
            self._interface = interface
            self._name = name

        def __set_name__(self, cls: Any, name: str) -> None:
            self._python_name = name  # for error messages
            if self._name is None:
                self._name = ''.join(word.title() for word in name.split('_'))
            if cls._dbus_members is None:
                cls._dbus_members = {}
            interface = cls._dbus_members.setdefault(self._interface, {})
            members = interface.setdefault(self._category, {})
            members[self._name] = self

        def _describe(self) -> dict[str, Any]:
            raise NotImplementedError

        def __getitem__(self, key):
            # Acts as an adaptor for dict accesses from introspection.to_xml()
            return self._describe()[key]


    class Property(_Member):
        """Defines a D-Bus property on an object

        There are two main ways to define properties: with and without getters.
        If you define a property without a getter, then you must provide a
        value (via the value= kwarg).  In this case, the property value is
        maintained internally and can be accessed from Python in the usual way.
        Change signals are sent automatically.

            class MyObject(bus.Object):
                counter = Property('i', value=0)

            a = MyObject()
            a.counter = 5
            a.counter += 1
            print(a.counter)

        The other way to define properties is with a getter function.  In this
        case, you can read from the property in the normal way, but not write
        to it.  You are responsible for emitting change signals for yourself.
        You must not provide the value= kwarg.

            class MyObject(bus.Object):
                _counter = 0

                counter = Property('i')
                @counter.getter
                def get_counter(self):
                    return self._counter

                def set_counter(self, value):
                    self._counter = value
                    self.property_changed('Counter')

        In either case, you can provide a setter function.  This function has
        no impact on Python code, but makes the property writable from the view
        of D-Bus.  Your setter will be called when a Properties.Set() call is
        made, and no other action will be performed.  A trivial implementation
        might look like:

            class MyObject(bus.Object):
                counter = Property('i', value=0)
                @counter.setter
                def set_counter(self, value):
                    # we got a request to set the counter from D-Bus
                    self.counter = value

        In all cases, the first (and only mandatory) argument to the
        constructor is the D-Bus type of the property.

        Your getter and setter functions can be provided by kwarg to the
        constructor.  You can also give a name= kwarg (to override the default
        name conversion scheme) or an interface= kwarg (to override the default
        interface name).
        """
        _category = 'properties'

        _getter: Optional[Callable[[Any], Any]]
        _setter: Optional[Callable[[Any, Any], None]]
        _type_string: str
        _value: Any

        def __init__(self, type_string: str,
                     value: Any = None,
                     name: Optional[str] = None,
                     interface: Optional[str] = None,
                     getter: Optional[Callable[[Any], Any]] = None,
                     setter: Optional[Callable[[Any, Any], None]] = None):
            assert value is None or getter is None, 'A property cannot have both a value and a getter'

            super().__init__(name=name, interface=interface)
            self._getter = getter
            self._setter = setter
            self._type_string = type_string
            self._value = value

        def _describe(self) -> dict[str, Any]:
            return {'type': self._type_string, 'flags': 'r' if self._setter is None else 'w'}

        def __get__(self, obj: object, cls: Optional[type] = None) -> Any:
            if self._getter is not None:
                return self._getter.__get__(obj, cls)()
            elif self._value is not None:
                if obj._dbus_property_values is not None:
                    return obj._dbus_property_values.get(self._name, self._value)
                else:
                    return self._value
            else:
                raise AttributeError(f"'{obj.__class__.__name__}' property '{self._python_name}' "
                                     f"was not properly initialised: use either the 'value=' kwarg or "
                                     f"the @'{self._python_name}.getter' decorator")

        def __set__(self, obj: Any, value: Any) -> None:
            if self._getter is not None:
                raise AttributeError(f"'{obj.__class__.__name__}' property '{self._python_name}' is read-only")
            if obj._dbus_property_values is None:
                obj._dbus_property_values = {}
            obj._dbus_property_values[self._name] = value
            obj.org_freedesktop_DBus_Properties_PropertiesChanged(self._interface or obj._dbus_interface,
                                                                  {self._name: {"t": self._type_string, "v": value}},
                                                                  [])

        def to_dbus(self, obj, reply):
            reply.open_container(ord('v'), self._type_string)
            reply.append_arg(self._type_string, self.__get__(obj))
            reply.close_container()
            return True

        def from_dbus(self, obj, value):
            if self._setter is None or self._type_string != value["t"]:
                return False
            self._setter.__get__(obj)(value["v"])
            return True

        def getter(self, getter: Callable[[Any], Any]) -> Callable[[Any], Any]:
            if self._value is not None:
                raise ValueError('A property cannot have both a value and a getter')
            if self._getter is not None:
                raise ValueError('A property cannot have both a value and a getter')
            self._getter = getter
            return getter

        def setter(self, setter: Callable[[Any, Any], None]) -> Callable[[Any, Any], None]:
            self._setter = setter
            return setter


    class Signal(_Member):
        _category = 'signals'

        def __init__(self, *out_types: str, name: Optional[str] = None) -> None:
            super().__init__(name=name)
            self._out_types = list(out_types)
            self._signature = ''.join(out_types)

        def _describe(self) -> dict[str, Any]:
            return {'in': self._out_types}

        def __get__(self, obj: Any, cls: Optional[type] = None) -> Callable[..., None]:
            def emitter(obj: Object, *args: Any) -> None:
                obj.emit_signal(self._name, self._signature, *args, interface=self._interface)
            return emitter.__get__(obj, cls)


    class Method(_Member):
        _category = 'methods'

        def __init__(self, out_types=(), in_types=(), name=None):
            super().__init__(name=name)
            self._out_types = list(out_types)
            self._in_types = list(in_types)
            self._in_signature = ''.join(in_types)
            self._func = None

        def __get__(self, obj, cls=None):
            return self._func.__get__(obj)

        def __call__(self, *args, **kwargs):
            # decorator
            self._func, = args
            return self

        def _describe(self) -> dict[str, Any]:
            return {'in': self._in_types, 'out': self._out_types}

        def invoke(self, obj, message):
            if not message.has_signature(self._in_signature):
                return False
            try:
                result = self._func.__get__(obj)(*message.get_body())
            except BusError as error:
                return message.reply_method_error(error)

            return message.reply_method_function_return_value(self._out_types, result)


    def message_received(self, message):
        interface = message.get_interface(message)
        method = message.get_member()

        if interface == 'org.freedesktop.DBus.Introspectable':
            if method == 'Introspect' and message.has_signature(''):
                return self.org_freedesktop_DBus_Introspectable_Introspect(message)

        elif interface == 'org.freedesktop.DBus.Properties':
            if method == 'GetAll' and message.has_signature('s'):
                return self.org_freedesktop_DBus_Properties_GetAll(message, *message.get_body())

            elif method == 'Get' and message.has_signature('ss'):
                return self.org_freedesktop_DBus_Properties_Get(message, *message.get_body())

            elif method == 'Set' and message.has_signature('ssv'):
                return self.org_freedesktop_DBus_Properties_Get(message, *message.get_body())

        try:
            method = self._dbus_members[interface]['methods'][method]
        except KeyError:
            return False
        return method.invoke(self, message)

    @classmethod
    def org_freedesktop_DBus_Introspectable_Introspect(cls, message):
        message.reply_method_return('s', introspection.to_xml(cls._dbus_members or {}))

    def org_freedesktop_DBus_Properties_PropertiesChanged(self, interface_name: str,
                                                          changed_properties: list[dict[str, dict[str, Any]]],
                                                          invalidated_properties: list[str]) -> None:
        self.emit_signal('PropertiesChanged', 'sa{sv}as',
                         interface_name, changed_properties, invalidated_properties,
                         interface='org.freedesktop.DBus.Properties')

    def org_freedesktop_DBus_Properties_Get(self, message, interface, name):
        try:
            prop = self._dbus_members[interface]['properties'][name]
        except KeyError:
            return False

        reply = message.new_method_return()
        prop.to_dbus(self, reply)
        return reply.send()

    def org_freedesktop_DBus_Properties_GetAll(self, message, interface):
        try:
            properties = self._dbus_members[interface].get('properties') or {}
        except KeyError:
            return False

        reply = message.new_method_return()
        reply.open_container(ord('a'), '{sv}')
        for name, prop in properties.items():
            reply.open_container(ord('e'), 'sv')
            reply.append_arg('s', name)
            prop.to_dbus(self, reply)
            reply.close_container()
        reply.close_container()
        return reply.send()

    def org_freedesktop_DBus_Properties_Set(self, message, interface, name, value):
        try:
            prop = self._dbus_members[interface]['properties'][name]
        except KeyError:
            return False

        reply = message.new_method_return()
        prop.from_dbus(self, value)
        reply.send()
"""
class MyObj(Object):
    _dbus_interface = 'cockpit.Test'

    def __init__(self) -> None:
        super().__init__()
        self._path='/test'
        self._only_getter = 'old'
        self._getter_setter = 'old'

    simple = Property('s', value='old')

    only_getter = Property('s')
    @only_getter.getter
    def get_only_getter(self) -> str:
        return self._only_getter

    only_setter = Property('s', value='old')
    @only_setter.setter
    def set_only_setter(self, value: str) -> None:
        self.only_setter = value

    getter_setter = Property('s')
    @getter_setter.getter
    def get_getter_setter(self) -> str:
        return self._getter_setter
    @getter_setter.setter
    def set_getter_setter(self, value: str) -> None:
        self._getter_setter = value
        self.property_changed('GetterSetter')

    everything_changed = Signal('')


class Dumb:
    indent = 0

    def open_container(self, *args):
        print('  ' * self.indent, 'open', args)
        self.indent += 1

    def close_container(self, *args):
        print()
        self.indent -= 1

    def append_arg(self, sig, val):
        print('  ' * self.indent, 'append', sig, val)

a = MyObj()
assert a.simple == 'old'
a.simple = 'new'
assert a.simple == 'new'
assert a.org_freedesktop_DBus_Properties_Set(Dumb(), 'cockpit.Test', 'Simple', 123) == False
assert a.simple == 'new'

assert a.only_getter == 'old'
try:
    a.only_getter = 'new'
except AttributeError:
    pass
assert a.only_getter == 'old'
assert a.org_freedesktop_DBus_Properties_Set(Dumb(), 'cockpit.Test', 'OnlyGetter', 123) == False
assert a.only_getter == 'old'

assert a.only_setter == 'old'
a.only_setter = 'new'
assert a.only_setter == 'new'
assert a.org_freedesktop_DBus_Properties_Set(Dumb(), 'cockpit.Test', 'OnlySetter', Variant('s', 'from-bus')) == True
assert a.only_setter == 'from-bus'

print (a.org_freedesktop_DBus_Properties_GetAll(Dumb(), 'cockpit.Test'))

a.everything_changed()
"""
