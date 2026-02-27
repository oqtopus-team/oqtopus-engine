import pytest

from oqtopus_engine_core.framework.buffer import Buffer

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

class GlobalContext:
    pass


class JobContext:
    pass


class Job:
    pass


class DummyBuffer(Buffer):
    """Simple in-memory buffer for testing Buffer's abstract interface."""

    def __init__(self, max_concurrency: int = 1):
        self._items = []
        self._max_concurrency = max_concurrency

    async def put(self, gctx: GlobalContext, jctx: JobContext, job: Job):
        # Store a tuple exactly as the Buffer contract specifies.
        self._items.append((gctx, jctx, job))

    async def get(self):
        # Retrieve FIFO for simplicity.
        return self._items.pop(0)

    def size(self) -> int:
        return len(self._items)

    def max_concurrency(self) -> int:
        return self._max_concurrency

# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_buffer_put_get_and_size():
    """
    Ensure that put() stores items correctly, size() reflects the number of
    stored items, and get() returns them in the expected order.
    """
    buf = DummyBuffer()

    g1, j1, job1 = GlobalContext(), JobContext(), Job()
    g2, j2, job2 = GlobalContext(), JobContext(), Job()

    await buf.put(g1, j1, job1)
    await buf.put(g2, j2, job2)

    assert buf.size() == 2

    out1 = await buf.get()
    assert out1 == (g1, j1, job1)
    assert buf.size() == 1

    out2 = await buf.get()
    assert out2 == (g2, j2, job2)
    assert buf.size() == 0


def test_buffer_default_max_concurrency():
    """
    Default max_concurrency() in the base class should return 1.
    """
    class DefaultBuffer(Buffer):
        async def put(self, gctx: GlobalContext, jctx: JobContext, job: Job):
            pass

        async def get(self):
            return None

        def size(self) -> int:
            return 0

    buf = DefaultBuffer()
    assert buf.max_concurrency == 1


def test_buffer_overridden_max_concurrency():
    """
    Custom Buffer implementations may override max_concurrency().
    """
    buf = DummyBuffer(max_concurrency=4)
    assert buf.max_concurrency() == 4
