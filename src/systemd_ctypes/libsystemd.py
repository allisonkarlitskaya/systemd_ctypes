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
import os
import sys
from typing import ClassVar, List, Optional, Tuple, Union

from .inotify import inotify_event
from .librarywrapper import (
    Callback,
    Errno,
    Reference,
    ReferenceType,
    UserData,
    WeakReference,
    byref,
)
from .typing import Annotated


class Trampoline(ReferenceType):
    deferred: 'ClassVar[list[Callback] | None]' = None
    trampoline: Callback
    userdata: UserData = None

    def cancel(self) -> None:
        self._unref()
        self.value = None

    def __del__(self) -> None:
        # This might be the currently-dispatching callback — make sure we don't
        # destroy the trampoline before we return.  We drop the deferred list
        # from the event loop when we're sure we're not doing any dispatches.
        if Trampoline.deferred is not None:
            Trampoline.deferred.append(self.trampoline)
        if self.value is not None:
            self._unref()


class sd_bus_error(ctypes.Structure):
    # This is ABI, so we are safe to assume it doesn't change.
    # Unfortunately, we lack anything like sd_bus_error_new().
    _fields_ = (
        ("name", ctypes.c_char_p),
        ("message", ctypes.c_char_p),
        ("_need_free", ctypes.c_int),
    )

    def get(self) -> Tuple[str, str]:
        return self.name.decode(), self.message.decode()

    def set(self, name: str, message: str) -> None:
        result = libsystemd.sd_bus_error_set(byref(self), name, message)
        if result < 0:
            raise OSError(-result, f"sd_bus_error_set: {os.strerror(-result)}")

    def __del__(self) -> None:
        if self._b_needsfree_:
            libsystemd.sd_bus_error_free(byref(self))


class sd_id128(ctypes.Structure):
    # HACK: Pass-by-value of array-containing-structs is broken on Python
    # 3.6. See https://bugs.python.org/issue22273
    _fields_: List[Tuple[str, type]] = (
        [("bytes", ctypes.c_uint8 * 16)]
        if sys.version_info >= (3, 7, 0)
        else [("one", ctypes.c_uint64), ("two", ctypes.c_uint64)]
    )


class sd_event_source(Trampoline):
    ...


class sd_event(ReferenceType):
    def _add_inotify(
        self: 'sd_event',
        source: Reference[sd_event_source],
        path: str,
        event: int,
        callback: Callback,
        user_data: UserData,
    ) -> Union[None, Errno]:
        ...

    def dispatch(self: 'sd_event') -> Union[None, Errno]:
        ...

    def get_fd(self: 'sd_event') -> Union[int, Errno]:
        raise NotImplementedError

    def get_state(self: 'sd_event') -> Union[int, Errno]:
        raise NotImplementedError

    def loop(self: 'sd_event') -> Union[None, Errno]:
        ...

    def prepare(self: 'sd_event') -> Union[None, Errno]:
        ...

    def wait(
        self: 'sd_event', timeout: Annotated[int, ctypes.c_uint64]
    ) -> Union[None, Errno]:
        ...

    @staticmethod
    def _default(ret: Reference['sd_event']) -> Union[None, Errno]:
        ...


class sd_bus_slot(Trampoline):
    ...


class sd_bus_message(ReferenceType):
    def rewind(self: 'sd_bus_message', complete: bool) -> Union[None, Errno]:
        ...

    def _get_error(self: 'sd_bus_message') -> Reference[sd_bus_error]:
        raise NotImplementedError

    def has_signature(self: 'sd_bus_message', signature: str) -> Union[bool, Errno]:
        raise NotImplementedError

    def is_method_error(self: 'sd_bus_message', name: str) -> Union[bool, Errno]:
        raise NotImplementedError

    def _new_method_errnof(
            self: 'sd_bus_message',
            message: Reference['sd_bus_message'],
            error: int,
            format_str: str,
            first_arg: str
    ) -> Union[None, Errno]:
        ...

    def _new_method_errorf(
        self: 'sd_bus_message',
        m: Reference['sd_bus_message'],
        name: str,
        format_str: str,
        first_arg: str
    ) -> Union[None, Errno]:
        ...

    def _new_method_return(
        self: 'sd_bus_message', m: Reference['sd_bus_message']
    ) -> Union[None, Errno]:
        ...

    def seal(
        self: 'sd_bus_message',
        cookie: Annotated[int, ctypes.c_uint64],
        timeout: Annotated[int, ctypes.c_uint64],
    ) -> Union[None, Errno]:
        ...

    def _get_bus(self: 'sd_bus_message') -> WeakReference:
        raise NotImplementedError

    def get_destination(self: 'sd_bus_message') -> str:
        raise NotImplementedError

    def get_interface(self: 'sd_bus_message') -> str:
        raise NotImplementedError

    def get_member(self: 'sd_bus_message') -> str:
        raise NotImplementedError

    def get_path(self: 'sd_bus_message') -> str:
        raise NotImplementedError

    def get_sender(self: 'sd_bus_message') -> Optional[str]:
        raise NotImplementedError

    def get_signature(self: 'sd_bus_message', complete: bool) -> str:
        raise NotImplementedError


class sd_bus(ReferenceType):
    def _add_match(
        self: 'sd_bus',
        slot: Reference[sd_bus_slot],
        match: str,
        handler: Callback,
        user_data: UserData,
    ) -> Union[None, Errno]:
        ...

    def _add_match_async(
        self: 'sd_bus',
        slot: Reference[sd_bus_slot],
        match: str,
        callback: Callback,
        install_callback: Callback,
        user_data: UserData,
    ) -> Union[None, Errno]:
        ...

    def _add_object(
        self: 'sd_bus',
        slot: Reference[sd_bus_slot],
        path: str,
        callback: Callback,
        user_data: UserData,
    ) -> Union[None, Errno]:
        ...

    def attach_event(
        self: 'sd_bus', event: Optional[sd_event], priority: int
    ) -> Union[None, Errno]:
        ...

    def _call(
        self: 'sd_bus',
        message: sd_bus_message,
        timeout: Annotated[int, ctypes.c_uint64],
        ret_error: Reference[sd_bus_error],
        reply: Reference[sd_bus_message],
    ) -> Union[None, Errno]:
        ...

    def _call_async(
        self: 'sd_bus',
        slot: Reference[sd_bus_slot],
        message: sd_bus_message,
        callback: Callback,
        user_data: UserData,
        timeout_usec: Annotated[int, ctypes.c_uint64],
    ) -> Union[None, Errno]:
        ...

    def flush(self: 'sd_bus') -> Union[None, Errno]:
        ...

    def get_fd(self: 'sd_bus') -> Union[int, Errno]:
        raise NotImplementedError

    def _message_new_method_call(
        self: 'sd_bus',
        message: Reference[sd_bus_message],
        destination: Optional[str],
        path: str,
        interface: str,
        member: str,
    ) -> Union[None, Errno]:
        ...

    def _message_new_signal(
        self: 'sd_bus',
        message: Reference[sd_bus_message],
        path: str,
        interface: str,
        member: str,
    ) -> Union[None, Errno]:
        ...

    def release_name(self: 'sd_bus', name: str) -> Union[None, Errno]:
        ...

    def request_name(
        self: 'sd_bus', name: str, flags: Annotated[int, ctypes.c_uint64]
    ) -> Union[None, Errno]:
        ...

    def set_address(self: 'sd_bus', address: str) -> Union[None, Errno]:
        ...

    def set_bus_client(self: 'sd_bus', b: bool) -> Union[None, Errno]:
        ...

    def set_fd(self: 'sd_bus', input_fd: int, output_fd: int) -> Union[None, Errno]:
        ...

    def set_server(self: 'sd_bus', b: bool, bus_d: sd_id128) -> Union[None, Errno]:
        ...

    def start(self: 'sd_bus') -> Union[None, Errno]:
        ...

    def wait(
        self: 'sd_bus', timeout_usec: Annotated[int, ctypes.c_uint64]
    ) -> Union[None, Errno]:
        ...

    def send(
        self: 'sd_bus', message: sd_bus_message, cookie: Optional[Reference[ctypes.c_uint64]]
    ) -> Union[None, Errno]:
        ...

    @staticmethod
    def _default_system(ret: Reference['sd_bus']) -> Union[None, Errno]:
        ...

    @staticmethod
    def _default_user(ret: Reference['sd_bus']) -> Union[None, Errno]:
        ...

    @staticmethod
    def _new(ret: Reference['sd_bus']) -> Union[None, Errno]:
        ...


sd_bus_message_handler_t = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(sd_bus_error))
sd_event_inotify_handler_t = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(inotify_event), ctypes.c_void_p)


libsystemd = ctypes.CDLL("libsystemd.so.0")
for cls in {
    sd_bus,
    sd_bus_message,
    sd_bus_slot,
    sd_event,
    sd_event_source,
}:
    cls._install_cfuncs(libsystemd)
