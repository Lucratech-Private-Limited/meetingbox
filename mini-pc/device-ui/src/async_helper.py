"""
Async Helper

Bridges Kivy's main thread with an asyncio event loop running
in a background thread. This allows async operations (backend API calls,
WebSocket connections) to work alongside Kivy's synchronous event loop.
"""

import asyncio
import threading

_async_loop = None
_async_thread = None


def _start_async_loop(loop):
    """Run the asyncio event loop in a background thread"""
    asyncio.set_event_loop(loop)
    loop.run_forever()


def init_async():
    """Initialize the background asyncio event loop"""
    global _async_loop, _async_thread
    _async_loop = asyncio.new_event_loop()
    _async_thread = threading.Thread(
        target=_start_async_loop,
        args=(_async_loop,),
        daemon=True
    )
    _async_thread.start()


def run_async(coro):
    """
    Schedule an async coroutine on the background event loop.
    Safe to call from Kivy's main thread.
    
    Args:
        coro: An awaitable coroutine
        
    Returns:
        concurrent.futures.Future that can be used to get the result
    """
    if _async_loop and _async_loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, _async_loop)
    return None


def get_async_loop():
    """Get the background asyncio event loop"""
    return _async_loop


# Start the async loop immediately on import
init_async()
