import errno
import gc
import os
import socket
import unittest

from systemd_ctypes import bus


def fd_closed(fd: int, old_stat: os.stat_result) -> bool:
    try:
        new_stat = os.fstat(fd)
    except OSError as exc:
        # If it is EBADF then it got closed!  Good!
        if exc.errno == errno.EBADF:
            return True

    # Otherwise, maybe someone opened a new file.  Let's compare the stats.
    return new_stat != old_stat


class Router:
    slot: bus.Slot


class Exportee(bus.BaseObject):
    router: Router


# Check for proper freeing of bus objects when references go out of scope.
# Used to make sure we don't form accidental reference loops.
class TestReferences(unittest.TestCase):
    def setUp(self):
        client_socket, server_socket = socket.socketpair()
        self.client = bus.Bus.new(fd=client_socket.detach())
        self.server = bus.Bus.new(fd=server_socket.detach(), server=True)
        self.and_gc = False
        self.extra_refs = []

    def tearDown(self):
        client_fd = self.client.get_fd()
        client_stat = os.fstat(client_fd)

        server_fd = self.server.get_fd()
        server_stat = os.fstat(server_fd)

        # This should result in both ends closing.
        del self.client
        del self.server
        del self.extra_refs

        # Make sure the GC is the thing that solves this one.
        if self.and_gc:
            # At least one should still be open at this point
            assert not fd_closed(client_fd, client_stat) or not fd_closed(server_fd, server_stat)
            gc.enable()
            gc.collect()
            # ...but at this point they should both be closed

        assert fd_closed(client_fd, client_stat)
        assert fd_closed(server_fd, server_stat)

    def test_export_no_save(self):
        self.server.add_object('/foo', bus.BaseObject())

    def test_export_and_cancel(self):
        slot = self.server.add_object('/foo', bus.BaseObject())
        slot.cancel()

    def test_export_cancel_and_save(self):
        slot = self.server.add_object('/foo', bus.BaseObject())
        slot.cancel()
        self.extra_refs.append(slot)

    def test_export_save(self):
        slot = self.server.add_object('/foo', bus.BaseObject())
        self.extra_refs.append(slot)

    def test_gc_required(self) -> None:
        # Test a case of some objects referencing each other and make sure that
        #
        #   1) The cycle isn't resolved merely by unref (we disable GC for that)
        #
        #   2) Manually running the GC does indeed clear the cycle

        gc.disable()
        self.and_gc = True

        router = Router()
        exportee = Exportee()
        exportee.router = router
        router.slot = self.server.add_object('/foo', exportee)
        self.extra_refs.append(router)
