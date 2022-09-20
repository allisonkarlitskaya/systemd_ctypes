import asyncio
import unittest

from systemd_ctypes import bus, introspection, run_async, BusError


class TestPeerToPeer(unittest.TestCase):
    def setUp(self):
        self.client, self.server = bus.Bus.socketpair(attach_event=True)

        @bus.Object.interface('cockpit.Test')
        class TestObject(bus.Object):
            @bus.Object.property('i')
            def answer(self):
                return 42

            @bus.Object.method('i', 'ii')
            def divide(self, top, bottom):
                try:
                    return top // bottom
                except ZeroDivisionError as exc:
                    raise BusError('cockpit.Error.ZeroDivisionError', 'Divide by zero') from exc

            @bus.Object.method('i', 'ii')
            async def divide_slowly(self, top, bottom):
                await asyncio.sleep(0.1)
                return self.divide(top, bottom)

        self.test_object = self.server.add_object('/test', TestObject())

    def tearDown(self):
        del self.test_object

    def test_introspect(self):
        async def test():
            reply, = await self.client.call_method_async(None, '/test', 'org.freedesktop.DBus.Introspectable', 'Introspect')
            info = introspection.parse_xml(reply)

            assert info == {
                'cockpit.Test': {
                    'methods': {
                        'Divide': {'in': ['i', 'i'], 'out': ['i']},
                        'DivideSlowly': {'in': ['i', 'i'], 'out': ['i']},
                    },
                    'properties': {
                        'Answer': {'type': 'i', 'flags': 'r'},
                    },
                    'signals': {
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


if __name__ == '__main__':
    unittest.main()
