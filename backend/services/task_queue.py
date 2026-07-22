import asyncio
import logging
import traceback
from typing import Callable, Any

logger = logging.getLogger("freja.task_queue")

# In-process FIFO queue for sequential background execution
_queue = asyncio.Queue()
_worker_task = None

async def task_worker_loop():
    """Sequential worker processing background tasks one by one safely."""
    logger.info("Background task queue worker started.")
    while True:
        try:
            func, args, kwargs, future = await _queue.get()
            try:
                logger.info(f"Running task {func.__name__} in background queue.")
                # Run coroutine directly, or run blocking sync function in thread pool executor
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = await asyncio.to_thread(func, *args, **kwargs)
                
                if not future.cancelled():
                    future.set_result(result)
                logger.info(f"Task {func.__name__} completed successfully.")
            except asyncio.CancelledError:
                # The worker itself is being shut down (stop_task_queue) - let this propagate
                # to the outer handler instead of being reported as a task failure.
                raise
            except BaseException as e:
                # Deliberately wider than Exception: a task raising anything else (e.g. from a
                # library's internals) must not silently kill this loop forever - every future
                # sync would then get stuck reporting "syncing" since nothing drains the queue.
                logger.error(f"Task {func.__name__} failed with error: {e}\n{traceback.format_exc()}")
                if not future.cancelled():
                    future.set_exception(e)
            finally:
                _queue.task_done()
        except asyncio.CancelledError:
            logger.info("Background task queue worker stopping...")
            break
        except Exception as e:
            logger.error(f"Error in task queue worker loop: {e}")
            await asyncio.sleep(1)

def enqueue_task(func: Callable[..., Any], *args: Any, **kwargs: Any) -> asyncio.Future:
    """Enqueues a task (either sync or async) to be executed sequentially in the background."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _queue.put_nowait((func, args, kwargs, future))
    return future

def start_task_queue():
    """Starts the background worker task loop."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(task_worker_loop())

async def stop_task_queue():
    """Cleanly cancels and awaits the background worker task."""
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
