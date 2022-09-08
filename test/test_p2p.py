import unittest

from systemd_ctypes import bus, introspection, run_async

class TestPeerToPeer(unittest.TestCase):
    def test_p2p(self):
        async def test():
            client, server = bus.Bus.socketpair(attach_event=True)

            class Object(bus.BaseObject):
                def handle_method_call(self, message, reply):
                    reply.append('i', 42)

            @bus.Object.interface('cockpit.Test')
            class NiceObject(bus.Object):
                @bus.Object.property('i')
                def answer(self):
                    return 42

                @bus.Object.method('i')
                def z(self):
                    return 4

            slot = server.add_object('/foo', NiceObject())

            reply, = await client.call_method_async(None, '/foo', 'cockpit.Test', 'Z')
            self.assertEqual(reply, 4)

            reply, = await client.call_method_async(None, '/foo', 'org.freedesktop.DBus.Properties', 'Get', 'ss', 'cockpit.Test', 'Answer')
            self.assertEqual(reply, {"t": "i", "v": 42})

            reply, = await client.call_method_async(None, '/foo', 'org.freedesktop.DBus.Properties', 'GetAll', 's', 'cockpit.Test')
            self.assertEqual(reply, {"Answer": {"t": "i", "v": 42}})

            reply, = await client.call_method_async(None, '/foo', 'org.freedesktop.DBus.Introspectable', 'Introspect')
            info = introspection.parse_xml(reply)
            assert info == {
                'cockpit.Test': {
                    'methods': {
                        'Z': {'in': [], 'out': ['i']},
                    },
                    'properties': {
                        'Answer': {'type': 'i', 'flags': 'r'},
                    },
                    'signals': {
                    },
                },
            }

            del slot

        run_async(test())


if __name__ == '__main__':
    unittest.main()
