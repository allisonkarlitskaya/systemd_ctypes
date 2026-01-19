import asyncio
import gc
import socket
import weakref

import pytest

from systemd_ctypes import bus, run_async


class test_AsyncGC(bus.Object):
    """Test object with async methods for GC testing."""

    def __init__(self):
        super().__init__()
        self.handler_started = asyncio.Event()
        self.handler_can_finish = asyncio.Event()
        self.handler_completed = False
        self.handler_task_ref = None
        self.task_was_collected = False

    def task_collected_callback(self, ref):
        """Called when the handler task is garbage collected."""
        self.task_was_collected = True

    @bus.Interface.Method('i', 'i')
    async def track_lifecycle(self, value: int) -> int:
        """Async method that tracks its task lifecycle."""
        # Get the current task and create a weak reference with a callback
        current = asyncio.current_task()
        self.handler_task_ref = weakref.ref(current, self.task_collected_callback)

        self.handler_started.set()
        await self.handler_can_finish.wait()
        self.handler_completed = True
        return value + 1


@pytest.fixture
def dbus_connection():
    """Create a peer-to-peer D-Bus connection for testing."""
    client_socket, server_socket = socket.socketpair()
    client = bus.Bus.new(fd=client_socket.detach())
    server = bus.Bus.new(fd=server_socket.detach(), server=True)
    return client, server


def test_async_handler_task_not_collected_during_execution(dbus_connection):
    """Test that async method handler tasks are not garbage collected while running.

    This test uses weakref callbacks to detect if the server-side handler task
    is garbage collected. Without the fix in bus.py, the task created in
    reply_method_function_return_value() can be GC'd because it has no strong
    references (only a circular reference through its callback).
    """
    client, server = dbus_connection
    test_obj = test_AsyncGC()
    slot = server.add_object('/test', test_obj)

    async def test():
        # Make the D-Bus call - we keep a reference to ensure the CLIENT task doesn't get GC'd
        call_task = asyncio.create_task(
            client.call_method_async(None, '/test', 'test.AsyncGC', 'TrackLifecycle', 'i', 42)
        )

        # Wait for the handler to start and record its task
        await asyncio.wait_for(test_obj.handler_started.wait(), timeout=1.0)

        # Verify the handler task exists
        assert test_obj.handler_task_ref is not None
        assert test_obj.handler_task_ref() is not None, "Handler task was already collected"

        # Now aggressively trigger garbage collection while handler is suspended
        # This is where the bug would manifest - the task gets collected
        for _ in range(50):
            gc.collect(0)  # Collect generation 0
            gc.collect(1)  # Collect generation 1
            gc.collect(2)  # Collect generation 2 (full collection)
            await asyncio.sleep(0.01)

            # Check if task was collected mid-execution
            # Without the fix, task_was_collected becomes True
            if test_obj.task_was_collected:
                pytest.fail("Handler task was garbage collected during execution!")

        # The task should still be alive
        assert test_obj.handler_task_ref() is not None, \
            "Handler task was garbage collected before completion"

        # Allow handler to finish
        test_obj.handler_can_finish.set()

        # Wait for completion
        result = await asyncio.wait_for(call_task, timeout=2.0)
        assert result == (43,)
        assert test_obj.handler_completed, "Handler did not complete"

        # The task MAY now be collected after completion (that's fine)

    try:
        run_async(test())
    finally:
        del slot
