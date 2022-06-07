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

import tempfile
import unittest
import sys

import dbusmock
import systemd_ctypes

class TestAPI(dbusmock.DBusTestCase):
    @classmethod
    def setUpClass(cls):
        cls.start_session_bus()
        cls.bus_user = systemd_ctypes.Bus.default_user()
        cls.bus_user.attach_event(None, 0)

    def setUp(self):
        self.mock_log = tempfile.NamedTemporaryFile()
        self.p_mock = self.spawn_server('org.freedesktop.Test',
                                        '/',
                                        'org.freedesktop.Test.Main',
                                        stdout=self.mock_log)
        self.addCleanup(self.p_mock.wait)
        self.addCleanup(self.p_mock.terminate)

    def assertLog(self, regex):
        with open(self.mock_log.name, "rb") as f:
            self.assertRegex(f.read(), regex)

    def add_method(self, iface, name, in_sig, out_sig, code):
        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', dbusmock.MOCK_IFACE, 'AddMethod')
        message.append('s', iface)
        message.append('s', name)
        message.append('s', in_sig)
        message.append('s', out_sig)
        message.append('s', code)
        result = self.bus_user.call(message, -1)
        self.assertEqual(result.get_body(), ())

    def async_call(self, message):
        loop = systemd_ctypes.Event.create_event_loop()

        result = None
        async def _call():
            nonlocal result
            result = await self.bus_user.call_async(message, 1000000)

        loop.run_until_complete(_call())
        return result

    def test_noarg_noret_sync(self):
        self.add_method('', 'Do', '', '', '')

        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Do')
        result = self.bus_user.call(message, -1).get_body()
        self.assertEqual(result, ())

        self.assertLog(b'^[0-9.]+ Do$')

    def test_noarg_noret_async(self):
        self.add_method('', 'Do', '', '', '')

        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Do')
        result = self.async_call(message).get_body()
        self.assertEqual(result, ())

        self.assertLog(b'^[0-9.]+ Do$')

    def test_strarg_strret_sync(self):
        self.add_method('', 'Reverse', 's', 's', 'ret = "".join(reversed(args[0]))')

        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Reverse')
        message.append('s', 'ab c')
        result = self.bus_user.call(message, -1).get_body()
        self.assertEqual(result, ('c ba',))

        self.assertLog(b'^[0-9.]+ Reverse "ab c"\n$')

    def test_strarg_strret_async(self):
        self.add_method('', 'Reverse', 's', 's', 'ret = "".join(reversed(args[0]))')

        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Reverse')
        message.append('s', 'ab c')
        result = self.async_call(message).get_body()
        self.assertEqual(result, ('c ba',))

        self.assertLog(b'^[0-9.]+ Reverse "ab c"\n$')

    def test_bool_sync(self):
        self.add_method('', 'Not', 'b', 'b', 'ret = not args[0]')

        for val in [False, True]:
            message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Not')
            message.append('b', val)
            result = self.bus_user.call(message, -1).get_body()
            self.assertEqual(result, (not val,))

    def test_int_sync(self):
        self.add_method('', 'Inc', 'yiuxt', 'yiuxt', 'ret = (args[0] + 1, args[1] + 1, args[2] + 1, args[3] + 1, args[4] + 1)')

        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Inc')
        message.append('y', 0x7E)
        message.append('i', 0x7FFFFFFE)
        message.append('u', 0xFFFFFFFE)
        message.append('x', 0x7FFFFFFFFFFFFFFE)
        message.append('t', 0xFFFFFFFFFFFFFFFE)
        result = self.bus_user.call(message, -1).get_body()
        self.assertEqual(result, (0x7F, 0x7FFFFFFF, 0xFFFFFFFF, 0x7FFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF))

    def test_int_async(self):
        self.add_method('', 'Dec', 'yiuxt', 'yiuxt', 'ret = (args[0] - 1, args[1] - 1, args[2] - 1, args[3] - 1, args[4] - 1)')

        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Dec')
        message.append('y', 1)
        message.append('i', -0x7FFFFFFF)
        message.append('u', 1)
        message.append('x', -0x7FFFFFFFFFFFFFFF)
        message.append('t', 1)
        result = self.async_call(message).get_body()
        self.assertEqual(result, (0, -0x80000000, 0, -0x8000000000000000, 0))

    def test_int_error(self):
        # int overflow
        self.add_method('', 'Inc', 'i', 'i', 'ret = args[0] + 1')
        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Inc')
        message.append('i', 0x7FFFFFFF)
        with self.assertRaisesRegex(systemd_ctypes.BusError, 'OverflowError'):
            self.bus_user.call(message, -1)

        # uint underflow
        self.add_method('', 'Dec', 'u', 'u', 'ret = args[0] - 1')
        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Dec')
        message.append('u', 0)
        with self.assertRaisesRegex(systemd_ctypes.BusError, "OverflowError: can't convert negative value to unsigned int"):
            self.bus_user.call(message, -1)

    def test_float(self):
        self.add_method('', 'Sq', 'd', 'd', 'ret = args[0] * args[0]')
        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Sq')
        message.append('d', -5.5)
        self.assertAlmostEqual(self.async_call(message).get_body()[0], 30.25)

    def test_objpath(self):
        self.add_method('', 'Parent', 'o', 'o', "ret = '/'.join(args[0].split('/')[:-1])")
        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Parent')
        message.append('o', '/foo/bar/baz')
        self.assertEqual(self.async_call(message).get_body(), ('/foo/bar',))

    def test_unknown_method_sync(self):
        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Do')
        with self.assertRaisesRegex(systemd_ctypes.BusError, '.*org.freedesktop.DBus.Error.UnknownMethod:.*'
                'Do is not a valid method of interface org.freedesktop.Test.Main'):
            self.bus_user.call(message, -1)

    def test_unknown_method_async(self):
        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Do')
        with self.assertRaisesRegex(systemd_ctypes.BusError, '.*org.freedesktop.DBus.Error.UnknownMethod:.*'
                'Do is not a valid method of interface org.freedesktop.Test.Main'):
            self.async_call(message).get_body()

    def test_custom_error(self):
        self.add_method('', 'Boom', '', '', 'raise dbus.exceptions.DBusException("no good", name="com.example.Error.NoGood")')
        message = self.bus_user.message_new_method_call('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main', 'Boom')
        with self.assertRaisesRegex(systemd_ctypes.BusError, 'no good'):
            self.bus_user.call(message, -1)


if __name__ == '__main__':
    # avoid writing to stderr
    unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout))
