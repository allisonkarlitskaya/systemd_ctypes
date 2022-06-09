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


TEST_ADDR = ('org.freedesktop.Test', '/', 'org.freedesktop.Test.Main')


class TestAPI(dbusmock.DBusTestCase):
    @classmethod
    def setUpClass(cls):
        cls.start_session_bus()
        cls.bus_user = systemd_ctypes.Bus.default_user()
        cls.bus_user.attach_event(None, 0)

    def setUp(self):
        self.mock_log = tempfile.NamedTemporaryFile()
        self.p_mock = self.spawn_server(*TEST_ADDR, stdout=self.mock_log)
        self.addCleanup(self.p_mock.wait)
        self.addCleanup(self.p_mock.terminate)

    def assertLog(self, regex):
        with open(self.mock_log.name, "rb") as f:
            self.assertRegex(f.read(), regex)

    def add_method(self, iface, name, in_sig, out_sig, code):
        result = self.bus_user.call_method('org.freedesktop.Test', '/', dbusmock.MOCK_IFACE, 'AddMethod', 'sssss',
                                           iface, name, in_sig, out_sig, code)
        self.assertEqual(result, ())

    def async_call(self, message):
        loop = systemd_ctypes.Event.create_event_loop()

        result = None
        async def _call():
            nonlocal result
            result = await self.bus_user.call_async(message)

        loop.run_until_complete(_call())
        return result

    def test_noarg_noret_sync(self):
        self.add_method('', 'Do', '', '', '')
        result = self.bus_user.call_method(*TEST_ADDR, 'Do')
        self.assertEqual(result, ())
        self.assertLog(b'^[0-9.]+ Do$')

    def test_noarg_noret_async(self):
        self.add_method('', 'Do', '', '', '')
        message = self.bus_user.message_new_method_call(*TEST_ADDR, 'Do')
        self.assertEqual(self.async_call(message).get_body(), ())
        self.assertLog(b'^[0-9.]+ Do$')

    def test_strarg_strret_sync(self):
        self.add_method('', 'Reverse', 's', 's', 'ret = "".join(reversed(args[0]))')

        result = self.bus_user.call_method(*TEST_ADDR, 'Reverse', 's', 'ab c')
        self.assertEqual(result, ('c ba',))
        self.assertLog(b'^[0-9.]+ Reverse "ab c"\n$')

    def test_strarg_strret_async(self):
        self.add_method('', 'Reverse', 's', 's', 'ret = "".join(reversed(args[0]))')
        message = self.bus_user.message_new_method_call(*TEST_ADDR, 'Reverse', 's', 'ab c')
        self.assertEqual(self.async_call(message).get_body(), ('c ba',))
        self.assertLog(b'^[0-9.]+ Reverse "ab c"\n$')

    def test_bool(self):
        self.add_method('', 'Not', 'b', 'b', 'ret = not args[0]')

        for val in [False, True]:
            result = self.bus_user.call_method(*TEST_ADDR, 'Not', 'b', val)
            self.assertEqual(result, (not val,))

    def test_int_sync(self):
        self.add_method('', 'Inc', 'yiuxt', 'yiuxt', 'ret = (args[0] + 1, args[1] + 1, args[2] + 1, args[3] + 1, args[4] + 1)')

        result = self.bus_user.call_method(*TEST_ADDR, 'Inc', 'yiuxt',
                                           0x7E, 0x7FFFFFFE, 0xFFFFFFFE, 0x7FFFFFFFFFFFFFFE, 0xFFFFFFFFFFFFFFFE)
        self.assertEqual(result, (0x7F, 0x7FFFFFFF, 0xFFFFFFFF, 0x7FFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF))

    def test_int_async(self):
        self.add_method('', 'Dec', 'yiuxt', 'yiuxt', 'ret = (args[0] - 1, args[1] - 1, args[2] - 1, args[3] - 1, args[4] - 1)')

        message = self.bus_user.message_new_method_call(*TEST_ADDR, 'Dec', 'yiuxt',
                                                        1, -0x7FFFFFFF, 1, -0x7FFFFFFFFFFFFFFF, 1)
        self.assertEqual(self.async_call(message).get_body(), (0, -0x80000000, 0, -0x8000000000000000, 0))

    def test_int_error(self):
        # int overflow
        self.add_method('', 'Inc', 'i', 'i', 'ret = args[0] + 1')
        with self.assertRaisesRegex(systemd_ctypes.BusError, 'OverflowError'):
            self.bus_user.call_method(*TEST_ADDR, 'Inc', 'i', 0x7FFFFFFF)

        # uint underflow
        self.add_method('', 'Dec', 'u', 'u', 'ret = args[0] - 1')
        with self.assertRaisesRegex(systemd_ctypes.BusError, "OverflowError: can't convert negative value to unsigned int"):
            self.bus_user.call_method(*TEST_ADDR, 'Dec', 'u', 0)

    def test_float(self):
        self.add_method('', 'Sq', 'd', 'd', 'ret = args[0] * args[0]')
        result = self.bus_user.call_method(*TEST_ADDR, 'Sq', 'd', -5.5)
        self.assertAlmostEqual(result[0], 30.25)

    def test_objpath(self):
        self.add_method('', 'Parent', 'o', 'o', "ret = '/'.join(args[0].split('/')[:-1])")
        result = self.bus_user.call_method(*TEST_ADDR, 'Parent', 'o', '/foo/bar/baz')
        self.assertEqual(result, ('/foo/bar',))

    def test_array_output(self):
        self.add_method('', 'Echo', 'u', 'as', 'ret = ["echo"] * args[0]')
        result = self.bus_user.call_method(*TEST_ADDR, 'Echo', 'u', 2)
        self.assertEqual(result, (['echo', 'echo'],))

    def test_array_input(self):
        self.add_method('', 'Count', 'as', 'u', 'ret = len(args[0])')
        result = self.bus_user.call_method(*TEST_ADDR, 'Count', 'as', ['first', 'second'])
        self.assertEqual(result, (2,))

    def test_dict_output(self):
        self.add_method('', 'GetStrs', '', 'a{ss}', "ret = {'a': 'x', 'b': 'y'}")
        result = self.bus_user.call_method(*TEST_ADDR, 'GetStrs')
        self.assertEqual(result, ({'a': 'x', 'b': 'y'},))

        self.add_method('', 'GetInts', '', 'a{ii}', "ret = {1: 42, 2: 99}")
        result = self.bus_user.call_method(*TEST_ADDR, 'GetInts')
        self.assertEqual(result, ({1: 42, 2: 99},))

        self.add_method('', 'GetVariants', '', 'a{sv}',
                        "ret = {'a': dbus.String('x', variant_level=1), 'b': dbus.Boolean(True, variant_level=1)}")
        result = self.bus_user.call_method(*TEST_ADDR, 'GetVariants')
        self.assertEqual(result, ({'a': 'x', 'b': True},))

    def test_dict_input(self):
        self.add_method('', 'CountStrs', 'a{ss}', 'u', 'ret = len(args[0])')
        result = self.bus_user.call_method(*TEST_ADDR, 'CountStrs', 'a{ss}', {'a': 'x', 'b': 'y'})
        self.assertEqual(result, (2,))

        # TODO: Add more data types once int and variants work

    def test_unknown_method_sync(self):
        with self.assertRaisesRegex(systemd_ctypes.BusError, '.*org.freedesktop.DBus.Error.UnknownMethod:.*'
                'Do is not a valid method of interface org.freedesktop.Test.Main'):
            self.bus_user.call_method(*TEST_ADDR, 'Do')

    def test_unknown_method_async(self):
        message = self.bus_user.message_new_method_call(*TEST_ADDR, 'Do')
        with self.assertRaisesRegex(systemd_ctypes.BusError, '.*org.freedesktop.DBus.Error.UnknownMethod:.*'
                'Do is not a valid method of interface org.freedesktop.Test.Main'):
            self.async_call(message).get_body()

    def test_custom_error(self):
        self.add_method('', 'Boom', '', '', 'raise dbus.exceptions.DBusException("no good", name="com.example.Error.NoGood")')
        with self.assertRaisesRegex(systemd_ctypes.BusError, 'no good'):
            self.bus_user.call_method(*TEST_ADDR, 'Boom')


if __name__ == '__main__':
    # avoid writing to stderr
    unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout))
