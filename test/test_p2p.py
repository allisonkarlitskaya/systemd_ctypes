import asyncio
import unittest

from systemd_ctypes import bus, introspection, run_async, BusError


class TestPeerToPeer(unittest.TestCase):
    def setUp(self):
        self.client, self.server = bus.Bus.socketpair()

        class cockpit_Test(bus.Object):
            answer = bus.Interface.Property('i', value=42)

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
            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Introspectable', 'Introspect')
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
                    },
                    'properties': {
                        'Answer': {'type': 'i', 'flags': 'r'},
                    },
                    'signals': {
                        'EverythingChanged': {'in': ['i', 's']},
                    },
                },
            }
        run_async(test())

    def test_properties(self):
        async def test():
            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Properties', 'Get', 'ss', 'cockpit.Test', 'Answer')
            self.assertEqual(reply, {"t": "i", "v": 42})

            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Properties', 'GetAll', 's', 'cockpit.Test')
            self.assertEqual(reply, {"Answer": {"t": "i", "v": 42}})

            signals = self.signals_queue()
            self.test_object.answer = 6 * 9
            message = await signals.get()

            self.assertEqual(message.get_path(), "/test")
            self.assertEqual(message.get_interface(), "org.freedesktop.DBus.Properties")
            self.assertEqual(message.get_member(), "PropertiesChanged")
            (iface, props, invalid) = message.get_body()
            self.assertEqual(iface, "cockpit.Test")
            self.assertEqual(props, { 'Answer': { 't': 'i', 'v': 54 } })
            self.assertEqual(invalid, [])
        run_async(test())

    def test_method(self):
        async def test():
            reply, = await self.client.call_method_async(None, '/test', 'cockpit.Test', 'Divide', 'ii', 1554, 37)
            self.assertEqual(reply, 42)
        run_async(test())

    def test_method_throws(self):
        async def test():
            with self.assertRaisesRegex(BusError, 'cockpit.Error.ZeroDivisionError: Divide by zero'):
                await self.client.call_method_async(None, '/test', 'cockpit.Test', 'Divide', 'ii', 1554, 0)
        run_async(test())

    def test_async_method(self):
        async def test():
            reply, = await self.client.call_method_async(None, '/test', 'cockpit.Test', 'DivideSlowly', 'ii', 1554, 37)
            self.assertEqual(reply, 42)
        run_async(test())

    def test_async_method_throws(self):
        async def test():
            with self.assertRaisesRegex(BusError, 'cockpit.Error.ZeroDivisionError: Divide by zero'):
                await self.client.call_method_async(None, '/test', 'cockpit.Test', 'DivideSlowly', 'ii', 1554, 0)
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

            self.assertEqual(message.get_path(), "/test")
            self.assertEqual(message.get_interface(), "cockpit.Test")
            self.assertEqual(message.get_member(), "EverythingChanged")
            (level, info) = message.get_body()
            self.assertEqual(level, 11)
            self.assertEqual(info, "noise")

        run_async(test())

if __name__ == '__main__':
    unittest.main()
