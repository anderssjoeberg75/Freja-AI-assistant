import asyncio
import pytest
import pytest_asyncio
from backend.services.task_queue import enqueue_task, start_task_queue, stop_task_queue

@pytest_asyncio.fixture(autouse=True)
async def manage_test_queue():
    # Make sure task queue worker is running during the test
    start_task_queue()
    yield
    await stop_task_queue()

@pytest.mark.asyncio
async def test_async_task_execution():
    async def sample_async_task(val):
        await asyncio.sleep(0.01)
        return val * 2

    fut = enqueue_task(sample_async_task, 10)
    result = await fut
    assert result == 20

@pytest.mark.asyncio
async def test_sync_task_execution():
    def sample_sync_task(val):
        import time
        time.sleep(0.01)
        return val + 5

    fut = enqueue_task(sample_sync_task, 10)
    result = await fut
    assert result == 15

@pytest.mark.asyncio
async def test_sequential_execution_order():
    execution_order = []

    async def task_one():
        await asyncio.sleep(0.02)
        execution_order.append(1)
        return "one"

    def task_two():
        execution_order.append(2)
        return "two"

    async def task_three():
        execution_order.append(3)
        return "three"

    fut1 = enqueue_task(task_one)
    fut2 = enqueue_task(task_two)
    fut3 = enqueue_task(task_three)

    r1 = await fut1
    r2 = await fut2
    r3 = await fut3

    assert r1 == "one"
    assert r2 == "two"
    assert r3 == "three"
    assert execution_order == [1, 2, 3]

@pytest.mark.asyncio
async def test_task_error_handling():
    def failing_task():
        raise ValueError("Intentional failure")

    fut = enqueue_task(failing_task)
    with pytest.raises(ValueError, match="Intentional failure"):
        await fut

@pytest.mark.asyncio
async def test_worker_survives_a_baseexception_and_keeps_processing():
    """A task raising something other than Exception (e.g. a library's internals) must not
    silently kill the worker loop - every future enqueued task would otherwise never run,
    leaving any "syncing" flag set by the caller stuck forever."""
    class _WeirdFailure(BaseException):
        pass

    def failing_task():
        raise _WeirdFailure("not a plain Exception")

    fut1 = enqueue_task(failing_task)
    with pytest.raises(_WeirdFailure):
        await fut1

    # The worker loop must still be alive and processing after that.
    async def sample_async_task(val):
        return val * 2

    fut2 = enqueue_task(sample_async_task, 21)
    result = await fut2
    assert result == 42
