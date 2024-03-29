import asyncio
import socket
import unittest
from tempfile import TemporaryDirectory

import pytest

from systemd_ctypes import BusError, bus, introspection, run_async


class CommonTests:
    def setUp(self):
        class cockpit_Test(bus.Object):
            answer = bus.Interface.Property('i', value=42)
            level = bus.Interface.Property('u')

            def __init__(self):
                self._level = 0

            @level.getter
            def get_level(self):
                return self._level

            @level.setter
            def set_level(self, value):
                self._level = value

            @bus.Interface.Method('i', 'ii')
            def divide(self, top, bottom):
                try:
                    return top // bottom
                except ZeroDivisionError as exc:
                    raise BusError('cockpit.Error.ZeroDivisionError', 'Divide by zero') from exc

            @bus.Interface.Method('i', 'ii')
            async def divide_slowly(self, top, bottom):
                await asyncio.sleep(0.1)
                return self.divide(top, bottom)

            @bus.Interface.Method('sss', 'ss')
            def partition(self, string, sep):
                return string.partition(sep)

            @bus.Interface.Method('sss', 'ss')
            async def partition_slowly(self, string, sep):
                await asyncio.sleep(0.1)
                return string.partition(sep)

            @bus.Interface.Method('')
            def do_nothing(self):
                pass

            @bus.Interface.Method('')
            async def do_nothing_slowly(self):
                await asyncio.sleep(0.1)

            @bus.Interface.Method('s', 's')
            def read_file(self, filename: str) -> str:
                with open(filename) as fp:
                    return fp.read()

            everything_changed = bus.Interface.Signal('i', 's')

        self.test_object = cockpit_Test()
        self.test_object_slot = self.server.add_object('/test', self.test_object)

        self.received_signals = None

    def signals_queue(self):
        if not self.received_signals:
            self.received_signals = asyncio.Queue()
            self.signals_watch = self.client.add_match("type='signal'",
                                                       lambda msg: self.received_signals.put_nowait(msg))
        return self.received_signals

    def tearDown(self):
        del self.test_object_slot

    def test_introspect(self):
        async def test():
            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Introspectable',
                                                         'Introspect')
            info = introspection.parse_xml(reply)

            assert info == {
                'org.freedesktop.DBus.Introspectable': {
                    'methods': {
                        'Introspect': {'in': [], 'out': ['s']},
                    },
                    'properties': {},
                    'signals': {},
                },
                'org.freedesktop.DBus.Peer': {
                    'methods': {
                        'GetMachineId': {'in': [], 'out': ['s']},
                        'Ping': {'in': [], 'out': []},
                    },
                    'properties': {},
                    'signals': {},
                },
                'org.freedesktop.DBus.Properties': {
                    'methods': {
                        'Get': {'in': ['s', 's'], 'out': ['v']},
                        'GetAll': {'in': ['s'], 'out': ['a{sv}']},
                        'Set': {'in': ['s', 's', 'v'], 'out': []},
                    },
                    'properties': {},
                    'signals': {
                        'PropertiesChanged': {'in': ['s', 'a{sv}', 'as']}
                    },
                },
                'cockpit.Test': {
                    'methods': {
                        'Divide': {'in': ['i', 'i'], 'out': ['i']},
                        'DivideSlowly': {'in': ['i', 'i'], 'out': ['i']},
                        'Partition': {'in': ['s', 's'], 'out': ['s', 's', 's']},
                        'PartitionSlowly': {'in': ['s', 's'], 'out': ['s', 's', 's']},
                        'DoNothing': {'in': [], 'out': []},
                        'DoNothingSlowly': {'in': [], 'out': []},
                        'ReadFile': {'in': ['s'], 'out': ['s']},
                    },
                    'properties': {
                        'Answer': {'type': 'i', 'flags': 'r'},
                        'Level': {'type': 'u', 'flags': 'w'},
                    },
                    'signals': {
                        'EverythingChanged': {'in': ['i', 's']},
                    },
                },
            }
        run_async(test())

    def test_properties(self):
        async def test():
            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Properties',
                                                         'Get', 'ss', 'cockpit.Test', 'Answer')
            self.assertEqual(reply, {"t": "i", "v": 42})

            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Properties',
                                                         'GetAll', 's', 'cockpit.Test')
            self.assertEqual(reply, {"Answer": {"t": "i", "v": 42}, "Level": {"t": "u", "v": 0}})

            signals = self.signals_queue()
            self.test_object.answer = 6 * 9
            message = await signals.get()

            self.assertEqual(message.get_path(), "/test")
            self.assertEqual(message.get_interface(), "org.freedesktop.DBus.Properties")
            self.assertEqual(message.get_member(), "PropertiesChanged")
            (iface, props, invalid) = message.get_body()
            self.assertEqual(iface, "cockpit.Test")
            self.assertEqual(props, {'Answer': {'t': 'i', 'v': 54}})
            self.assertEqual(invalid, [])

            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Properties', 'Get',
                                                         'ss', 'cockpit.Test', 'Level')
            self.assertEqual(reply, {'t': 'u', 'v': 0})
            await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Properties', 'Set',
                                                'ssv', 'cockpit.Test', 'Level', {'t': 'u', 'v': 12})
            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Properties', 'Get',
                                                         'ss', 'cockpit.Test', 'Level')
            self.assertEqual(reply, {'t': 'u', 'v': 12})
        run_async(test())

    def test_method(self):
        async def test():
            reply = await self.client.call_method_async(None, '/test', 'cockpit.Test', 'DoNothing')
            self.assertEqual(reply, ())

            reply, = await self.client.call_method_async(None, '/test', 'cockpit.Test',
                                                         'Divide', 'ii', 1554, 37)
            self.assertEqual(reply, 42)

            reply = await self.client.call_method_async(None, '/test', 'cockpit.Test',
                                                        'Partition', 'ss', 'start:end', ':')
            self.assertEqual(reply, ('start', ':', 'end'))

        run_async(test())

    def test_method_throws(self):
        async def test():
            with pytest.raises(BusError, match='cockpit.Error.ZeroDivisionError: Divide by zero'):
                await self.client.call_method_async(None, '/test', 'cockpit.Test', 'Divide', 'ii', 1554, 0)
        run_async(test())

    def test_method_throws_oserror(self):
        async def test():
            with pytest.raises(BusError, match='org.freedesktop.DBus.Error.FileNotFound: .*notthere.*'):
                await self.client.call_method_async(None, '/test', 'cockpit.Test', 'ReadFile', 's', 'notthere')
        run_async(test())

    def test_async_method(self):
        async def test():
            reply = await self.client.call_method_async(None, '/test', 'cockpit.Test', 'DoNothingSlowly')
            self.assertEqual(reply, ())

            reply, = await self.client.call_method_async(None, '/test', 'cockpit.Test',
                                                         'DivideSlowly', 'ii', 1554, 37)
            self.assertEqual(reply, 42)

            reply = await self.client.call_method_async(None, '/test', 'cockpit.Test',
                                                        'PartitionSlowly', 'ss', 'start:end', ',')
            self.assertEqual(reply, ('start:end', '', ''))

        run_async(test())

    def test_async_method_throws(self):
        async def test():
            with pytest.raises(BusError, match='cockpit.Error.ZeroDivisionError: Divide by zero'):
                await self.client.call_method_async(None, '/test', 'cockpit.Test', 'DivideSlowly', 'ii', 1554, 0)
        run_async(test())

    @pytest.mark.filterwarnings('error')
    def test_cancel_async_method(self):
        async def test():
            pending = self.client.call_method_async(None, '/test', 'cockpit.Test', 'DivideSlowly', 'ii', 1554, 37)
            task = asyncio.create_task(pending)
            await asyncio.sleep(0)  # let the call start running
            task.cancel()
            await asyncio.sleep(0.5)  # wait for the error to throw on method completion
        run_async(test())

    def test_signal(self):
        async def test():
            # HACK - https://github.com/systemd/systemd/pull/24875
            #
            # Without this initial pointless method call, the signal
            # below will not be received.
            #
            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Properties', 'Get',
                                                         'ss', 'cockpit.Test', 'Answer')

            signals = self.signals_queue()
            self.test_object.everything_changed(11, 'noise')
            message = await signals.get()

            self.assertEqual(message.get_sender(), None)
            self.assertEqual(message.get_path(), "/test")
            self.assertEqual(message.get_interface(), "cockpit.Test")
            self.assertEqual(message.get_member(), "EverythingChanged")
            (level, info) = message.get_body()
            self.assertEqual(level, 11)
            self.assertEqual(info, "noise")

        run_async(test())

    def test_unexport(self):
        async def test():
            # Make sure that dropping the slot results in the object being un-exported
            self.test_object_slot = None

            with pytest.raises(BusError, match="org.freedesktop.DBus.Error.UnknownObject: Unknown object '/test'."):
                await self.client.call_method_async(None, '/test', 'cockpit.Test', 'Divide', 'ii', 1554, 37)
        run_async(test())


class TestAddress(CommonTests, unittest.TestCase):
    def setUp(self):
        with TemporaryDirectory() as tmpdir:
            socket_path = f'{tmpdir}/listener'

            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(socket_path)
            listener.listen()

            self.client = bus.Bus.new(address=f'unix:path={socket_path}')
            server_socket, _ = listener.accept()
            listener.close()

        self.server = bus.Bus.new(fd=server_socket.detach(), server=True)
        super().setUp()


class TestSocketPair(CommonTests, unittest.TestCase):
    def setUp(self):
        client_socket, server_socket = socket.socketpair()
        self.client = bus.Bus.new(fd=client_socket.detach())
        self.server = bus.Bus.new(fd=server_socket.detach(), server=True)
        super().setUp()


if __name__ == '__main__':
    unittest.main()
