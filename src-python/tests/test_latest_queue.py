import os
import sys
import unittest
from threading import Thread


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.latest_queue import LatestQueue, QueueClosed


class LatestQueueTests(unittest.TestCase):
    def test_offer_replaces_oldest_without_exceeding_capacity(self):
        queue = LatestQueue[int](maxsize=4)
        for value in range(4):
            self.assertIsNone(queue.offer(value).dropped)

        result = queue.offer(4)

        self.assertTrue(result.accepted)
        self.assertEqual(result.dropped, 0)
        self.assertEqual(result.depth, 4)
        self.assertEqual(queue.qsize(), 4)
        self.assertEqual(queue.drain(), [1, 2, 3, 4])

    def test_get_nowait_returns_next_item(self):
        queue = LatestQueue[str](maxsize=2)
        queue.offer("first")
        queue.offer("second")

        self.assertEqual(queue.get_nowait(), "first")
        self.assertEqual(queue.get_nowait(), "second")
        self.assertTrue(queue.empty())

    def test_close_wakes_waiting_consumer(self):
        queue = LatestQueue[int](maxsize=1)
        observed = []

        def consume():
            try:
                queue.get()
            except QueueClosed as exc:
                observed.append(exc)

        worker = Thread(target=consume)
        worker.start()
        queue.close()
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertIsInstance(observed[0], QueueClosed)


if __name__ == "__main__":
    unittest.main()
