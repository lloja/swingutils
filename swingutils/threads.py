from threading import Event
import traceback

from java.lang import Runnable, Throwable
from java.util.concurrent import ThreadPoolExecutor, TimeUnit, \
    LinkedBlockingDeque, Future, ExecutionException, \
    CancellationException
from javax.swing import SwingUtilities

__all__ = ('TaskExecutor',)


class RunnableWrapper(Runnable):
    def __init__(self, func, args, kwargs):
        self._func = func
        self._args = args
        self._kwargs = kwargs

    def run(self):
        self._func(*self._args, **self._kwargs)


class AsyncResult(RunnableWrapper, Future):
    """
    Class that stores both the function reference, and the return value or
    raised exception from an asynchronously invoked callable. Users should not
    instantiate or run these directly.

    """
    def __init__(self, func, args, kwargs, name=None, beforeCallback=None,
                 afterCallback=None):
        RunnableWrapper.__init__(self, func, args, kwargs, name,
                                 beforeCallback, afterCallback)
        self._event = Event()

    def run(self):
        if not self._func:
            return

        try:
            self._retval = self._func(*self._args, **self._kwargs)
        except BaseException, e:
            self._exception = e
            traceback.print_exc()

        self._func = None        # Free any memory taken by possible closures
        self._event.set()

    def cancel(self, mayInterruptIfRunning):
        if self.isDone():
            return False
        self._func = None
        return True

    def get(self, timeout=None, unit=None):
        if timeout and unit:
            timeout = unit.toMillis(timeout) / 1000.0
        self._event.wait(timeout)
        if hasattr(self, '_exception'):
            if isinstance(self._exception, Throwable):
                raise ExecutionException(self._exception)
            raise ExecutionException(unicode(self._exception), None)
        if not hasattr(self, '_retval'):
            raise CancellationException
        return self._retval

    def isCancelled(self):
        return self._func is None

    def isDone(self):
        return hasattr(self, '_retval') or hasattr(self, '_exception')


class TaskExecutor(ThreadPoolExecutor):
    beforeCallback = None
    afterCallback = None

    def __init__(self, coreThreads=1, maxThreads=1, keepalive=5, queue=None,
                 beforeCallback=None, afterCallback=None):
        queue = queue or LinkedBlockingDeque()
        ThreadPoolExecutor.__init__(self, coreThreads, maxThreads, keepalive,
                                    TimeUnit.SECONDS, queue)
        self.beforeCallback = beforeCallback
        self.afterCallback = afterCallback

    def beforeExecute(self, thread, runnable):
        if self.beforeCallback:
            self.beforeCallback(thread, runnable)
        if runnable.beforeCallback:
            runnable.beforeCallback(thread, runnable)

    def afterExecute(self, runnable, throwable):
        if self.afterCallback:
            self.afterCallback(runnable, throwable)
        if runnable.afterCallback:
            runnable.afterCallback(runnable, throwable)

    def task(self, func):
        def wrapper(*args, **kwargs):
            return self.runTask(func, *args, **kwargs)
        return wrapper

    def namedTask(self, name, beforeCallback=None, afterCallback=None):
        def outer(func):
            def inner(*args, **kwargs):
                kwargs['beforeCallback'] = beforeCallback
                kwargs['afterCallback'] = afterCallback
                return self.runNamedTask(func, name, *args, **kwargs)
            return inner
        return outer

    def runTask(self, func, *args, **kwargs):
        result = AsyncResult(func, args, kwargs)
        self.execute(result)
        return result

    def runNamedTask(self, func, name, *args, **kwargs):
        beforeCallback = kwargs.pop('beforeCallback', None)
        afterCallback = kwargs.pop('afterCallback', None)
        result = AsyncResult(func, args, kwargs, name, beforeCallback,
                             afterCallback)
        self.execute(result)
        return result


def execInEDT(func, *args, **kwargs):
    """
    Run the given function in the Event Dispatch Thread.
    The calling thread will block until the function has been run.
    Any exceptions will be propagated to the calling thread.

    """
    if SwingUtilities.isEventDispatchThread():
        return func(*args, **kwargs)
    else:
        holder = AsyncResult(func, args, kwargs)
        SwingUtilities.invokeAndWait(holder)
        return holder.get()


def invokeInEDT(func):
    """
    Decorator that causes the wrapped function to be queued for execution
    in the event dispatch thread. The calling thread will block
    until the function has executed in the event dispatch thread.
    If the target function is called from the event dispatch thread,
    it will be executed directly The return value of the target function
    is preserved and returned always.

    """
    def wrapper(*args, **kwargs):
        return execInEDT(func, *args, **kwargs)
    return wrapper


def invokeLater(func):
    """
    Decorator that ensures that the given function is executed in the Event
    Dispatch Thread. If the current thread is the EDT, the function is executed
    normally. Otherwise, it is queued for execution in the EDT. The return
    value and any raised exception are always discarded.

    """
    def wrapper(*args, **kwargs):
        if SwingUtilities.isEventDispatchThread():
            func(*args, **kwargs)
        else:
            wrapper = RunnableWrapper(func, args, kwargs)
            SwingUtilities.invokeLater(wrapper)
    return wrapper


def asyncSwingTask(func):
    """
    Decorator that causes the wrapped function to be queued for execution
    in the Event Dispatch Thread. The call will return immediately.

    :return: a result holder that will contain the return value
    :rtype: :class:`~AsyncResult`

    """
    def wrapper(*args, **kwargs):
        holder = AsyncResult(func, args, kwargs)
        SwingUtilities.invokeLater(holder)
        return holder
    return wrapper
