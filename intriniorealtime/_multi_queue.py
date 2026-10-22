import queue
import threading
import time
from collections import deque


class MultiQueue:
    """A Queue-compatible collection backed by one FIFO per priority."""

    def __init__(self, priority_count, splitter, maxsize=0):
        if priority_count < 1:
            raise ValueError("priority_count must be positive")

        self.maxsize = maxsize
        self._splitter = splitter
        self._queues = [deque() for _ in range(priority_count)]
        self._mutex = threading.Lock()
        self._condition = threading.Condition(self._mutex)
        self._all_tasks_done = threading.Condition(self._mutex)
        self._unfinished_tasks = 0

    def _split(self, item):
        split_items = list(self._splitter(item))
        if not split_items:
            split_items = [(len(self._queues) - 1, item)]
        for priority, _ in split_items:
            if priority < 0 or priority >= len(self._queues):
                raise ValueError("splitter returned an invalid priority")
        return split_items

    def put(self, item, block=True, timeout=None):
        if timeout is not None and timeout < 0:
            raise ValueError("'timeout' must be a non-negative number")

        split_items = self._split(item)
        deadline = None if timeout is None else time.monotonic() + timeout
        rejected = False
        inserted = 0

        with self._condition:
            for priority, split_item in split_items:
                priority_queue = self._queues[priority]
                if self.maxsize > 0:
                    if not block:
                        if len(priority_queue) >= self.maxsize:
                            rejected = True
                            continue
                    elif timeout is None:
                        while len(priority_queue) >= self.maxsize:
                            self._condition.wait()
                    else:
                        while len(priority_queue) >= self.maxsize:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                rejected = True
                                break
                            self._condition.wait(remaining)
                        if rejected:
                            break

                priority_queue.append(split_item)
                inserted += 1

            if inserted:
                self._unfinished_tasks += inserted
                self._condition.notify_all()

        if rejected:
            raise queue.Full

    def put_nowait(self, item):
        return self.put(item, block=False)

    def get(self, block=True, timeout=None):
        if timeout is not None and timeout < 0:
            raise ValueError("'timeout' must be a non-negative number")

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not any(self._queues):
                if not block:
                    raise queue.Empty
                if timeout is None:
                    self._condition.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    self._condition.wait(remaining)

            for priority_queue in self._queues:
                if priority_queue:
                    item = priority_queue.popleft()
                    self._condition.notify_all()
                    return item

        raise queue.Empty

    def get_nowait(self):
        return self.get(block=False)

    def qsize(self):
        with self._condition:
            return sum(len(priority_queue) for priority_queue in self._queues)

    def empty(self):
        with self._condition:
            return not any(self._queues)

    def full(self):
        if self.maxsize <= 0:
            return False
        with self._condition:
            return all(len(priority_queue) >= self.maxsize
                       for priority_queue in self._queues)

    def task_done(self):
        with self._all_tasks_done:
            unfinished = self._unfinished_tasks - 1
            if unfinished < 0:
                raise ValueError("task_done() called too many times")
            self._unfinished_tasks = unfinished
            if unfinished == 0:
                self._all_tasks_done.notify_all()

    def join(self):
        with self._all_tasks_done:
            while self._unfinished_tasks:
                self._all_tasks_done.wait()
