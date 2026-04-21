import asyncio
import unittest

from connection_manager import ConnectionManager


class TestConnectionManagerBroadcaster(unittest.IsolatedAsyncioTestCase):

    async def test_broadcaster_restarts_after_task_completes_unexpectedly(self):
        """If _broadcast_task is done (not None), start_background_broadcaster must create a new one."""
        manager = ConnectionManager()

        async def noop():
            return

        manager._running = False
        manager._loop = asyncio.get_event_loop()
        manager.message_queue = asyncio.Queue()
        manager._broadcast_task = asyncio.create_task(noop())
        await asyncio.sleep(0)  # let noop() complete so task.done() == True

        self.assertTrue(manager._broadcast_task.done())

        manager.start_background_broadcaster()

        self.assertIsNotNone(manager._broadcast_task)
        self.assertFalse(manager._broadcast_task.done())

        await manager.stop_background_broadcaster()

    async def test_broadcaster_does_not_double_start_when_already_running(self):
        """Calling start_background_broadcaster twice must not create a second task."""
        manager = ConnectionManager()
        manager._loop = asyncio.get_event_loop()
        manager.message_queue = asyncio.Queue()

        manager.start_background_broadcaster()
        first_task = manager._broadcast_task

        manager.start_background_broadcaster()
        second_task = manager._broadcast_task

        self.assertIs(first_task, second_task)

        await manager.stop_background_broadcaster()

    async def test_stop_resets_message_queue(self):
        """stop_background_broadcaster must reset message_queue to None."""
        manager = ConnectionManager()
        manager._loop = asyncio.get_event_loop()

        manager.start_background_broadcaster()
        self.assertIsNotNone(manager.message_queue)

        await manager.stop_background_broadcaster()

        self.assertIsNone(manager.message_queue)

    async def test_fresh_queue_created_on_restart(self):
        """After stop + start, message_queue must be a new empty Queue instance."""
        manager = ConnectionManager()
        manager._loop = asyncio.get_event_loop()

        manager.start_background_broadcaster()
        first_queue = manager.message_queue
        await manager.stop_background_broadcaster()

        manager.start_background_broadcaster()
        second_queue = manager.message_queue

        self.assertIsNotNone(second_queue)
        self.assertIsNot(first_queue, second_queue)

        await manager.stop_background_broadcaster()
