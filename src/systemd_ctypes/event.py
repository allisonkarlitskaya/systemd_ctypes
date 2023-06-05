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

from ctypes import byref

from . import inotify
from . import libsystemd


class Event(libsystemd.sd_event):
    _default_instance = None

    @staticmethod
    def default():
        if Event._default_instance is None:
            Event._default_instance = Event()
            Event._default(Event._default_instance)
        return Event._default_instance

    def add_inotify(self, path, mask, handler):
        @libsystemd.sd_event_inotify_handler_t
        def wrapper(source, _event, userdata):
            event = _event.contents
            handler(inotify.Event(event.mask), event.cookie, event.name if event.len else None)
            return 0
        source = libsystemd.sd_event_source()
        source.wrapper = wrapper
        self._add_inotify(byref(source), path, mask, source.wrapper, None)
        return source

    def add_inotify_fd(self, fd, mask, handler):
        # HACK: sd_event_add_inotify_fd() got added in 250, which is too new.  Fake it.
        return self.add_inotify(f'/proc/self/fd/{fd}', mask, handler)


# This is all a bit more awkward than it should have to be: systemd's event
# loop chaining model is designed for glib's prepare/check/dispatch paradigm;
# failing to call prepare() can lead to deadlocks, for example.
#
# Hack a selector subclass which calls prepare() before sleeping and this for us.
class Selector(selectors.DefaultSelector):
    def __init__(self, event=None):
        super().__init__()
        self.sd_event = event or Event.default()
        self.key = self.register(self.sd_event.get_fd(), selectors.EVENT_READ)

    def select(self, timeout=None):
        while self.sd_event.prepare():
            self.sd_event.dispatch()
        ready = super().select(timeout)
        # workaround https://github.com/systemd/systemd/issues/23826
        # keep calling wait() until there's nothing left
        while self.sd_event.wait(0):
            self.sd_event.dispatch()
            while self.sd_event.prepare():
                self.sd_event.dispatch()
        # NB: this could return zero events with infinite timeout, but nobody seems to mind.
        return [(key, events) for (key, events) in ready if key != self.key]


class EventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self):
        return asyncio.SelectorEventLoop(Selector())


def run_async(main, debug=None):
    asyncio.set_event_loop_policy(EventLoopPolicy())

    polyfill = sys.version_info < (3, 7, 0) and not hasattr(asyncio, 'run')
    if polyfill:
        # Polyfills for Python 3.6:
        loop = asyncio.get_event_loop()

        assert not hasattr(asyncio, 'get_running_loop')
        asyncio.get_running_loop = lambda: loop

        assert not hasattr(asyncio, 'create_task')
        asyncio.create_task = loop.create_task

        assert not hasattr(asyncio, 'run')

        def run(main, debug=None):
            if debug is not None:
                loop.set_debug(debug)
            loop.run_until_complete(main)

        asyncio.run = run

        asyncio._systemd_ctypes_polyfills = True

    asyncio.run(main, debug=debug)

    if polyfill:
        del asyncio.create_task, asyncio.get_running_loop, asyncio.run
