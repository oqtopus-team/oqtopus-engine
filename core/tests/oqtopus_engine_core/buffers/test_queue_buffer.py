import asyncio
import pytest

from oqtopus_engine_core.buffers.queue_buffer import QueueBuffer
from oqtopus_engine_core.framework.context import GlobalContext, JobContext
from oqtopus_engine_core.framework.model import JobInfo, Job


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_buffer_test_job(job_id: str) -> Job:
    """Minimal job instance used only for QueueBuffer unit tests."""
    return Job(
        job_id=job_id,
        job_type="test",
        device_id="test-device",
        shots=1,
        job_info=JobInfo(program=[]),
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="CREATED",
    )


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_buffer_put_and_get_returns_items_in_order():
    """QueueBuffer should behave as FIFO."""
    buffer = QueueBuffer()

    g1, j1, job1 = GlobalContext(config={}), JobContext(initial={}), make_buffer_test_job("1")
    g2, j2, job2 = GlobalContext(config={}), JobContext(initial={}), make_buffer_test_job("2")

    await buffer.put(g1, j1, job1)
    await buffer.put(g2, j2, job2)

    # First in → first out
    out1 = await buffer.get()
    out2 = await buffer.get()

    assert out1 == (g1, j1, job1)
    assert out2 == (g2, j2, job2)


@pytest.mark.asyncio
async def test_queue_buffer_size_updates_correctly():
    """size() must reflect the number of elements currently in the queue."""
    buffer = QueueBuffer()

    assert buffer.size() == 0

    g, j, job = GlobalContext(config={}), JobContext(initial={}), make_buffer_test_job("x")
    await buffer.put(g, j, job)
    assert buffer.size() == 1

    await buffer.get()
    assert buffer.size() == 0


# ---------------------------------------------------------------------------
# max_concurrency property
# ---------------------------------------------------------------------------

def test_queue_buffer_default_max_concurrency_is_one():
    """Default max_concurrency should be 1."""
    buffer = QueueBuffer()
    assert buffer.max_concurrency == 1


def test_queue_buffer_custom_max_concurrency_is_retained():
    """Custom max_concurrency should be stored and returned correctly."""
    buffer = QueueBuffer(max_concurrency=4)
    assert buffer.max_concurrency == 4


# ---------------------------------------------------------------------------
# Capacity control (optional behavior)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_buffer_respects_maxsize():
    """If maxsize > 0, put() should block when queue is full."""
    buffer = QueueBuffer(maxsize=1)

    g1, j1, job1 = GlobalContext(config={}), JobContext(initial={}), make_buffer_test_job("1")
    g2, j2, job2 = GlobalContext(config={}), JobContext(initial={}), make_buffer_test_job("2")

    await buffer.put(g1, j1, job1)

    # This task will block because queue is full (maxsize=1)
    put_task = asyncio.create_task(buffer.put(g2, j2, job2))

    await asyncio.sleep(0.1)
    assert not put_task.done()  # not completed yet

    # Once we get one item, the second put() can proceed
    await buffer.get()
    await asyncio.sleep(0.1)

    assert put_task.done()
