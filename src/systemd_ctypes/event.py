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
import selectors
import sys
from typing import Callable, ClassVar, Coroutine, List, Optional, Tuple, TypeVar

from . import inotify, libsystemd
from .librarywrapper import Reference, UserData, byref

T = TypeVar('T')


class Event(libsystemd.sd_event):
    class Source(libsystemd.sd_event_source):
        def cancel(self) -> None:
            self._unref()
            self.value = None

    _default_instance: ClassVar[Optional['Event']] = None

    @staticmethod
    def default() -> 'Event':
        if Event._default_instance is None:
            Event._default_instance = Event()
            Event._default(byref(Event._default_instance))
        return Event._default_instance

    InotifyHandler = Callable[[inotify.Event, int, Optional[bytes]], None]

    class InotifySource(Source):
        def __init__(self, handler: 'Event.InotifyHandler') -> None:
            def callback(source: libsystemd.sd_event_source,
                         _event: Reference[inotify.inotify_event],
                         userdata: UserData) -> int:
                event = _event.contents
                handler(inotify.Event(event.mask), event.cookie, event.name)
                return 0
            self.trampoline = libsystemd.sd_event_inotify_handler_t(callback)

    def add_inotify(self, path: str, mask: inotify.Event, handler: InotifyHandler) -> InotifySource:
        source = Event.InotifySource(handler)
        self._add_inotify(byref(source), path, mask, source.trampoline, source.userdata)
        return source

    def add_inotify_fd(self, fd: int, mask: inotify.Event, handler: InotifyHandler) -> InotifySource:
        source = Event.InotifySource(handler)
        self._add_inotify_fd(byref(source), fd, mask, source.trampoline, source.userdata)
        return source


# This is all a bit more awkward than it should have to be: systemd's event
# loop chaining model is designed for glib's prepare/check/dispatch paradigm;
# failing to call prepare() can lead to deadlocks, for example.
#
# Hack a selector subclass which calls prepare() before sleeping and this for us.
class Selector(selectors.DefaultSelector):
    def __init__(self, event: Optional[Event] = None) -> None:
        super().__init__()
        self.sd_event = event or Event.default()
        self.key = self.register(self.sd_event.get_fd(), selectors.EVENT_READ)

    def select(
            self, timeout: Optional[float] = None
    ) -> List[Tuple[selectors.SelectorKey, int]]:
        # It's common to drop the last reference to a Source or Slot object on
        # a dispatch of that same source/slot from the main loop.  If we happen
        # to garbage collect before returning, the trampoline could be
        # destroyed before we're done using it.  Provide a mechanism to defer
        # the destruction of trampolines for as long as we might be
        # dispatching.  This gets cleared again at the bottom, before return.
        libsystemd.Trampoline.deferred = []

        while self.sd_event.prepare():
            self.sd_event.dispatch()
        ready = super().select(timeout)
        # workaround https://github.com/systemd/systemd/issues/23826
        # keep calling wait() until there's nothing left
        while self.sd_event.wait(0):
            self.sd_event.dispatch()
            while self.sd_event.prepare():
                self.sd_event.dispatch()

        # We can be sure we're not dispatching callbacks anymore
        libsystemd.Trampoline.deferred = None

        # This could return zero events with infinite timeout, but nobody seems to mind.
        return [(key, events) for (key, events) in ready if key != self.key]


def selector_event_loop_factory() -> asyncio.AbstractEventLoop:
    """Factory function to create an asyncio event loop using Selector."""
    return asyncio.SelectorEventLoop(Selector())


def run_async(main: Coroutine[None, None, T], debug: Optional[bool] = None) -> T:
    if sys.version_info >= (3, 12):
        # Python 3.12+: asyncio.run() supports loop_factory
        return asyncio.run(main, debug=debug, loop_factory=selector_event_loop_factory)

    elif sys.version_info >= (3, 7):
        # Python 3.7-3.11: inject via EventLoopPolicy
        class EventLoopPolicy(asyncio.DefaultEventLoopPolicy):
            def new_event_loop(self) -> asyncio.AbstractEventLoop:
                return selector_event_loop_factory()
        asyncio.set_event_loop_policy(EventLoopPolicy())
        return asyncio.run(main, debug=debug)

    else:
        # Python 3.6: no asyncio.run(), polyfill get_running_loop and create_task
        loop = selector_event_loop_factory()
        asyncio.set_event_loop(loop)

        assert not hasattr(asyncio, 'get_running_loop')
        asyncio.get_running_loop = lambda: loop  # type: ignore[attr-defined]

        assert not hasattr(asyncio, 'create_task')
        asyncio.create_task = loop.create_task  # type: ignore[attr-defined]

        try:
            if debug is not None:
                loop.set_debug(debug)
            return loop.run_until_complete(main)
        finally:
            try:
                # Cancel all pending tasks
                pending = asyncio.Task.all_tasks(loop)  # type: ignore[attr-defined]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                # shutdown_default_executor() not available until 3.9
            finally:
                asyncio.set_event_loop(None)
                loop.close()
                del asyncio.create_task, asyncio.get_running_loop
