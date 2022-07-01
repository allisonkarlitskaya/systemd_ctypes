# systemd_ctypes
#
# Copyright (C) 2022 Martin Pitt <martin@piware.de>
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
import errno
import os
import sys
import tempfile
import unittest

from unittest.mock import MagicMock

import systemd_ctypes
from systemd_ctypes.inotify import Event


with open("/etc/os-release") as f:
    os_release = f.read()


class TestPathWatch(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)

    def async_wait_cond(self, cond):
        asyncio.set_event_loop_policy(systemd_ctypes.EventLoopPolicy())

        async def _call():
            for retry in range(50):
                if cond():
                    break
                await asyncio.sleep(0.1)
            else:
                self.fail('Timed out waiting for condition')

        if sys.version_info >= (3, 7, 0):
            asyncio.run(_call())
        else:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(_call())

    def testSingleDirectory(self):
        listener = MagicMock()
        watch = systemd_ctypes.PathWatch(self.base.name, listener)
        self.addCleanup(watch.close)
        events = 0

        def wait_event(num=1):
            nonlocal events
            events += num
            self.async_wait_cond(lambda: len(listener.mock_calls) == events)

        # initialization event; the first argument is the fd, which varies
        wait_event()
        listener.do_identity_changed.assert_called_once()
        self.assertGreater(listener.do_identity_changed.call_args_list[-1][0][0], 0)
        self.assertEqual(listener.do_identity_changed.call_args_list[-1][0][1], None)

        # create file
        with open(os.path.join(self.base.name, 'file.txt'), 'w'):
            wait_event()
            listener.do_inotify_event.assert_called_with(Event.CREATE, 0, b'file.txt')

        # close file
        wait_event()
        listener.do_inotify_event.assert_called_with(Event.CLOSE_WRITE, 0, b'file.txt')

        # rename
        os.rename(os.path.join(self.base.name, 'file.txt'), os.path.join(self.base.name, 'datei.txt'))
        wait_event(2)
        self.assertEqual(listener.do_inotify_event.call_args_list[-2][0][0], Event.MOVED_FROM)
        inode = listener.do_inotify_event.call_args_list[-2][0][1]
        self.assertGreater(inode, 0)
        self.assertEqual(listener.do_inotify_event.call_args_list[-2][0][2], b'file.txt')
        listener.do_inotify_event.assert_called_with(Event.MOVED_TO, inode, b'datei.txt')

        # remove file
        os.remove(os.path.join(self.base.name, 'datei.txt'))
        wait_event()
        listener.do_inotify_event.assert_called_with(Event.DELETE, 0, b'datei.txt')

        # create directory
        os.mkdir(os.path.join(self.base.name, 'somedir'))
        wait_event()
        listener.do_inotify_event.assert_called_with(Event.CREATE | Event.ISDIR, 0, b'somedir')

        # symlink to directory
        os.symlink('somedir', os.path.join(self.base.name, 'dirlink'))
        wait_event()
        listener.do_inotify_event.assert_called_with(Event.CREATE, 0, b'dirlink')

        # rename symlink
        os.rename(os.path.join(self.base.name, 'dirlink'), os.path.join(self.base.name, 'pointer'))
        wait_event(2)
        self.assertEqual(listener.do_inotify_event.call_args_list[-2][0][0], Event.MOVED_FROM)
        inode = listener.do_inotify_event.call_args_list[-2][0][1]
        self.assertGreater(inode, 0)
        self.assertEqual(listener.do_inotify_event.call_args_list[-2][0][2], b'dirlink')
        listener.do_inotify_event.assert_called_with(Event.MOVED_TO, inode, b'pointer')

    # on systemd 239 on RHEL 8: Assertion 'sz <= d->buffer_filled' failed at ../src/libsystemd/sd-event/sd-event.c:3185, function event_inotify_data_drop().
    @unittest.skipIf('PLATFORM_ID="platform:el8"' in os_release, 'crashes on RHEL 8 systemd')
    @unittest.skipIf('Focal Fossa' in os_release, 'crashes on Ubuntu 20.04')
    def testRootDirectoryIdentity(self):
        listener = MagicMock()
        listen_root = os.path.join(self.base.name, 'root')

        watch = systemd_ctypes.PathWatch(listen_root, listener)
        self.addCleanup(watch.close)

        def wait_event(num=1):
            self.async_wait_cond(lambda: len(listener.mock_calls) == num)

        # do_identity_changed init event; but no fd yet, as the root dir does not exist
        wait_event()
        listener.do_identity_changed.assert_called_once_with(None, errno.ENOENT)
        listener.reset_mock()

        # create the root directory
        os.mkdir(listen_root)
        wait_event()
        listener.do_identity_changed.assert_called_once()
        self.assertGreater(listener.do_identity_changed.call_args_list[-1][0][0], 0)
        self.assertEqual(listener.do_identity_changed.call_args_list[-1][0][1], None)
        listener.reset_mock()

        # now picks up events in it
        os.symlink('nothing', os.path.join(listen_root, 'somelink'))
        wait_event()
        listener.do_inotify_event.assert_called_once_with(Event.CREATE, 0, b'somelink')
        listener.reset_mock()

        # remove root directory
        os.unlink(os.path.join(listen_root, 'somelink'))
        wait_event()
        listener.reset_mock()

        os.rmdir(listen_root)
        wait_event()
        listener.do_identity_changed.assert_called_once_with(None, errno.ENOENT)
        listener.reset_mock()

        # root is a symlink to another dir
        os.mkdir(os.path.join(self.base.name, 'realroot'))
        os.symlink('realroot', listen_root)
        # the mkdir should *not* create an event, as it's outside of the watched hierarchy; just the symlink
        wait_event()
        listener.do_identity_changed.assert_called_once()
        self.assertGreater(listener.do_identity_changed.call_args_list[-1][0][0], 0)
        self.assertEqual(listener.do_identity_changed.call_args_list[-1][0][1], None)
        listener.reset_mock()

        # picks up events in real root
        os.symlink('nothing', os.path.join(listen_root, 'somelink'))
        wait_event()
        listener.do_inotify_event.assert_called_once_with(Event.CREATE, 0, b'somelink')
        listener.reset_mock()

    def testHierarchy(self):
        listener = MagicMock()
        listen_root = os.path.join(self.base.name, 'root')
        os.makedirs(os.path.join(listen_root, 'subdir'))

        watch = systemd_ctypes.PathWatch(listen_root, listener)
        self.addCleanup(watch.close)

        def wait_event(num=1):
            self.async_wait_cond(lambda: len(listener.mock_calls) == num)

        # do_identity_changed init event
        wait_event()
        listener.do_identity_changed.assert_called_once()
        listener.reset_mock()

        # file in subdir does not get an event
        with open(os.path.join(listen_root, 'subdir', 'subfile.txt'), 'w'):
            pass
        # change outside of root directory does not get an event
        with open(os.path.join(self.base.name, 'unrelated'), 'w'):
            pass

        # only this one; this is the canary for "saw events up to this"
        with open(os.path.join(listen_root, 'file.txt'), 'w'):
            wait_event()
            listener.do_inotify_event.assert_called_once_with(Event.CREATE, 0, b'file.txt')

        listener.reset_mock()
        wait_event()
        listener.do_inotify_event.assert_called_once_with(Event.CLOSE_WRITE, 0, b'file.txt')


if __name__ == '__main__':
    unittest.main()
