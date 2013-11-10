import os
import sys
import time
import types
import multiprocessing
import xmlrpclib
import exceptions
import atexit
import functools
import imp
import types
import collections
import signal
import Queue
import traceback
import inspect
import operator
import threading

# TODO moved to file "TODO"

# TEMP DEBUG:
sys.setrecursionlimit(100)

class TerminateProcess(BaseException):
    '''This exception is raised within the host to request a graceful
    termination.'''
    pass

class ProcessTerminationError(Exception):
    '''Indicates that the host process crashed while processing a command.'''
    def __init__(self, signal_or_returncode):
        Exception.__init__(self,
                           'Isolation host terminated with signal or returncode '
                           +str(signal_or_returncode))
        self._signal_or_returncode = signal_or_returncode

class ClientStateError(Exception):
    '''Indicates that a command was attempted when a client that was in
    a state other than READY.'''
    def __init__(self, msg):
        Exception.__init__(self, msg)

class ChannelError(Exception):
    '''The client was repeatedly interrupted when trying to read data
    from the isolation host.'''
    def __init__(self, msg):
        Exception.__init__(self, msg)


def isproxy(x):
    '''Determine whether x is a proxy. Since proxies are designed to
    by as transparent as possible, they override __subclasscheck__, so
    we cannot use isinstance here.'''
    return Proxy in inspect.getmro(type(x))

def raw_repr(obj):
    '''Produce a representation using the default repr() regardless of
    whether the object provides an implementation of its own.'''
    if isproxy(obj):
        return '<%s with prime_id=%d>' % (obj.__class__.__name__, obj.prime_id)
    else:
        return repr(obj)

def _raise_terminate():
    '''A helper function invoked on the server to tell it to terminate.'''
    raise TerminateProcess()

def _load_module(module_name, path):
    '''A helper function invoked on the server to tell it to import a module.'''
    # TODO: handle the case that the module is already loaded
    fd, filename, info = imp.find_module(module_name, path)
    try:
        return imp.load_module(module_name, fd, filename, info)
    finally:
        if fd is not None:
            fd.close()

def read_channel(channel, num_retries):
    '''Read an object from a channel, possibly retrying if the attempt
    is interrupted by a signal from the operating system.'''
    for i in range(num_retries):
        try:
            return channel.get()
        except IOError as ex:
            if ex.errno == 4:
                # This errno corresponds to "System call interrupted",
                # which means a signal was recieved before any data
                # was sent. For now I think it's safe to ignore this
                # and continue.
                print 'attempt to read from channel was interrupted by something'
                print ex
            else:
                # Something else went wrong - raise the exception as usual
                raise ex
    raise ChannelError('failed to read from channel after %d retries' % num_retries)

class ChildProcessSignalHandler(object):
    '''Helper to catch SIGCHLD signals and dispatch them.'''
    _listeners = {}
    _processes = {}
    _installed = False
    _signal_recieved = False
    _mutex = threading.Lock()  # we're not doing any multithreading
                               # here but we need this to prevent
                               # signal-related race conditions

    @classmethod
    def register_listener(cls, process, listener):
        cls._install()
        cls._processes[process.pid] = process

        # Make sure that the update to the listeners dictionary is
        # atomic in case we get a signal while attempting to update
        updated_listeners = dict(cls._listeners)
        updated_listeners.setdefault(process.pid,[]).append(listener)
        cls._listeners = updated_listeners

    @classmethod
    def _handle_sigchld(cls, signum, stackframe):
        print 'ChildProcessSignalHandler recieved SIGCHLD'
        # Note that when handling signals in python, this signal
        # handler is called out-of-line relative to other code, and
        # may itself be interrupted by any further signals. We should
        # never block on a mutex because that will deadlock
        # immediately, but we must non-blocking mutexes in certain
        # places to ensure that we are race-condition free

        # I'm not certain that it's even possible to cover all the
        # race conditions in python. The worst that can happen here is
        # that we fail to notify about a child process that did in
        # fact terminate

        if cls._mutex.acquire(False):
            print 'ChildProcessSignalHandler acquired lock'
            # We got the lock, we are reponsible for dispatching
            # listeners until we complete an iteration without
            # recieving any signals
            _signal_recieved = True

            while _signal_recieved:
                _signal_recieved = False
                pids_to_remove = []
                for pid,listeners in cls._listeners.iteritems():
                    if not cls._processes[pid].is_alive():
                        pids_to_remove.append(pid)
                        for listener in listeners:
                            print 'ChildProcessSignalHandler calling a listener'
                            listener()

                # Must happen here and not outside the "while" or we will call some listeners multiple times
                for pid in pids_to_remove:
                    del cls._processes[pid]
                    del cls._listeners[pid]

            # Release the mutex
            cls._mutex.release()

        else:
            print 'ChildProcessSignalHandler did not acquire lock'
            # There is another signal handler currently dispatching
            # signals so set the flag and leave the dispatching to
            # that handler.
            _signal_recieved = True


    @classmethod
    def _install(cls):
        if not cls._installed:
            cls._installed = True
            signal.signal(signal.SIGCHLD, cls._handle_sigchld)

ChildProcessSignalHandler._install()



class RemoteRef(object):
    '''Represents a reference to a remote object.'''
    def __init__(self, id):
        self._id = id
    @property
    def id(self):
        return self._id


class Proxy(object):
    '''Represents a proxy constructed at server and transported to client'''
    def __init__(self, prime_id):
        assert type(prime_id) is int
        self._prime_id = prime_id
        self._client = None
    def attach_to_client(self, client):
        self._client = client
    @property
    def prime_id(self):
        return self._prime_id
    @property
    def client(self):
        return self._client

class Delegate(object):
    '''Represents a delegate constructed at client and transported to server.'''
    def attach_to_server(self, server):
        self._server = server
    @property
    def server(self):
        return self._server

class FuncDelegate(Delegate):
    def _transport(self, x):
        if isinstance(x, Proxy):
            return RemoteRef(x.prime_id)
        else:
            return x
    def _resolve(self, x):
        if isinstance(x, RemoteRef):
            return self.server.get_prime(x.id)
        else:
            return x
    def __init__(self, func, *args, **kwargs):
        super(FuncDelegate, self).__init__()
        self._func = self._transport(func)
        self._args = map(self._transport, args)
        self._kwargs = { k:self._transport(v) for k,v in kwargs.iteritems() }
    def run_on_server(self):
        func = self._resolve(self._func)
        args = map(self._resolve, self._args)
        kwargs = { k:self._resolve(v) for k,v in self._kwargs.iteritems() }
        return func(*args, **kwargs)
    def __str__(self):
        funcname = getattr(self._func, '__name__', '<remote func>')
        nargs = len(self._args) + len(self._kwargs)
        return '<FuncDelegate: %s nargs=%d>' % (funcname, nargs)

class ByValueDelegate(Delegate):
    def __init__(self, proxy):
        assert isinstance(proxy, Proxy)
        self._prime_id = proxy.prime_id
    def run_on_server(self):
        return ByValue(self.server.get_prime(self._prime_id))

def byvalue(proxy):
    assert isinstance(proxy, Proxy)
    return proxy.client.execute(ByValueDelegate(proxy))

class ObjectProxy(Proxy):
    '''A proxy for a server-side object.'''
    # To be run at server end:
    def __init__(self, prime_id, docstring):
        super(ObjectProxy,self).__init__(prime_id)
        if docstring is not None:
            self.__doc__ = docstring

    # Implement object-like special methods
    def __getattr__(self, attrname):
        if attrname in ['_prime_id', '_client'] or attrname.startswith('__'):
            return super(ObjectProxy,self).__getattr__(attrname)
        else:
            return self.client.call(getattr, self, attrname)
    def __setattr__(self, attrname, val):
        if attrname in ['_prime_id', '_client'] or attrname.startswith('__'):
            return super(ObjectProxy,self).__setattr__(attrname, val)
        else:
            return self.client.call(setattr, self, attrname, val)
    def __delattr__(self, attrname):
        if attrname in ['_prime_id', '_client']:
            return super(ObjectProxy,self).__detattr__(attrname)
        else:
            return self.client.call(gelattr, self, attrname)

    # Implement string-like special methods
    def __str__(self):
        return self.client.call(str, self)
    def __repr__(self):
        return self.client.call(repr, self)
    def __unicode__(self, other):
        return self.client.call(unicode, self, other)

    # Implement comparison special methods
    def __lt__(self, other):
        return self.client.call(operator.lt, self, other)
    def __gt__(self, other):
        return self.client.call(operator.gt, self, other)
    def __le__(self, other):
        return self.client.call(operator.le, self, other)
    def __ge__(self, other):
        return self.client.call(operator.ge, self, other)
    def __eq__(self, other):
        return self.client.call(operator.eq, self, other)
    def __ne__(self, other):
        return self.client.call(operator.ne, self, other)
    def __ne__(self, other):
        return self.client.call(operator.ne, self, other)
    def __cmp__(self, other):
        # Although __cmp__ will never be called by python builtins when
        # __lt__ are defined, we include it incase any user code explicitly calls it
        return self.client.call(cmp, self, other)
    def __nonzero__(self, other):
        return self.client.call(operator.truth, self, other)

    # Implement sequence-like special methods
    def __len__(self):
        return self.client.call(len, self)
    def __getitem__(self, key):
        return self.client.call(operator.getitem, self, key)
    def __setitem__(self, key, val):
        return self.client.call(operator.setitem, self, key, val)
    def __delitem__(self, key):
        return self.client.call(operator.delitem, self, key)
    def __contains__(self, val):
        return self.client.call(operator.contains, self, val)
    def __iter__(self):
        return self.client.call(iter, self)
    # TODO: move this to a subclass that only gets used for iterators
    def next(self):
        return self.client.call(next, self)

    # Implement misc special methods
    def __hash__(self, other):
        return self.client.call(hash, self, other)
    def __dir__(self):
        return self.client.call(dir, self)

    # Note that we do not include get/set/delslice, etc because they
    # are deprecated and not necessary. If they exist on the prime
    # then the get/set/delitem overloads above will call them.


class CallableObjectProxy(ObjectProxy):
    '''Represents an object proxy that can also be called like a
    function. This is broken out into a sub-class so that TypeProxy
    can be derived from ObjectProxy'''
    # Implement function-like special methods
    def __call__(self, *args, **kwargs):
        return self.client.call(self, *args, **kwargs)
    
class ExceptionProxy(Exception,ObjectProxy):
    def __init__(self, prime_id, prime_docstring=None):
        ObjectProxy.__init__(self, prime_id, prime_docstring)
    def __reduce__(self):
        return ExceptionProxy, (self.prime_id,)




class ExceptionalResult(object):
    '''Used to transport exceptions from the server to the client.'''
    def __init__(self, exception, traceback):
        self.exception = exception
        self.traceback = traceback
    def __str__(self):
        return '<ExceptionalResult [%s]>' % str(self.exception)




class TypeInfo(object):
    '''Represents the information needed to construct an instance of
    TypeProxy, in a form that, for the benefit of cPickle, is not
    itself a class (since classes are pickled by simply storing their
    name).'''
    def __init__(self, prime_class):
        self._name = prime_class.__name__
        self._module = prime_class.__module__
        self._class_id = id(prime_class)
        self._docstring = getattr(prime_class, '__doc__', None)

class TypeProxy(type,ObjectProxy):
    def attach_to_client(proxyclass, client):
        proxyclass._client = client

    def _new_instance(proxyclass, *args, **kwargs):
        return proxyclass._client.call(proxyclass, *args, **kwargs)

    def __instancecheck__(proxyclass, obj):
        return proxyclass._client.call(isinstance, obj, proxyclass)

    def __init__(proxyclass, info):
        print 'TypeProxy.__init__ was called'
        ObjectProxy.__init__(proxyclass, info._class_id, info._docstring)
        proxyclass._client = None
        proxyclass.__new__ = TypeProxy._new_instance

    def __new__(metaclass, blueprint):
        # TODO: find or create proxies for the base classes of prime_class
        proxyname = blueprint._name
        proxybases = (object,)
        proxymembers = dict()
        return type.__new__(metaclass, proxyname, proxybases, proxymembers)


class ByValue(object):
    '''A container used by delegates to indicate to the object
    transport mechanism that the value contained within should be
    returned to the client by value.'''
    def __init__(self, value):
        self.value = value

class ByProxy(object):
    '''A container used by delegates to indicate to the object
    transport mechanism that the value contained within should be
    returned to the client by proxy.'''
    def __init__(self, value):
        self.value = value


class Server(object):
    '''Represents the server that listens for delegates and runs them.'''
    def __init__(self, delegate_channel, result_channel):
        self._delegate_channel = delegate_channel
        self._result_channel = result_channel
        self._prime_by_id = dict()
        self._proxy_by_id = dict()

    def __getstate__(self):
        raise Exception('You attempted to pickle the server object')

    def get_prime(self, prime_id):
        return self._prime_by_id[prime_id]

    def get_proxy(self, prime_id):
        return self._proxy_by_id[prime_id]

    def loop(self):
        print 'server[%d] loop() starting' % os.getpid()
        terminate_flag = False
        while not terminate_flag:
            # Get the next delegate
            delegate = self._delegate_channel.get()
            print 'server[%d] executing: %s' % (os.getpid(), str(delegate))

            # Attach the delegate to the server environment
            delegate.attach_to_server(self)

            try:
                # Run the delegate in the local environment 
                # The delegate will wrap the result itself
                result = self.wrap(delegate.run_on_server())
            except TerminateProcess:
                # This exception indicates that the client requested that we terminate
                print 'server[%d] caught TerminateProcess' % os.getpid()
                result = True
                terminate_flag = True
            except:
                # Any other exception gets transported back to the client
                ex_type, ex_value, ex_traceback = sys.exc_info()
                print 'Caught on server[%d]: %s' % (os.getpid(), str(ex_value))
                traceback.print_exc()

                result = ExceptionalResult(self.wrap(ex_value), None)
                # TODO: find a way to transport a traceback (pickle can't serialize it)

            # Send the result to the client
            print 'server putting %s onto result queue' % raw_repr(result)
            self._result_channel.put(result)

        print 'server[%d] loop ended' % os.getpid()

    def wrap(self, prime):
        if id(prime) in self._proxy_by_id:
            return self._proxy_by_id[id(prime)]
        else:
            proxy = self.wrap_impl(prime)
            if proxy is None:
                # indicates that this object should be returned by value: do not cache
                return prime
            else:
                print 'server created a proxy for prime_id=%d (will cache both prime and proxy)' % id(prime)
                self._prime_by_id[id(prime)] = prime
                self._proxy_by_id[id(prime)] = proxy
                return proxy

    def wrap_impl(self, prime):
        print 'wrapping %s (id=%d)' % (str(prime), id(prime))

        prime_id = id(prime)
        prime_docstring = getattr(prime, '__doc__', None)

        if isinstance(prime, ByValue):
            # this indicates that the standard object proxying semantics are being overridden
            return prime.value

        if isinstance(prime, (types.FunctionType, types.MethodType)):  # do _not_ use callable(...) here
            print '  wrapping as callable'
            return CallableObjectProxy(prime_id, prime_docstring)

        elif isinstance(prime, (types.ModuleType)):
            print '  wrapping as module'
            return ObjectProxy(prime_id, prime_docstring)

        elif isinstance(prime, (types.FileType)):
            print '  wrapping as file'
            return ObjectProxy(prime_id, prime_docstring)

        elif isinstance(prime, type):
            print '  wrapping as type'
            # Rather than returning a type directly, which would be
            # rejected by cPickle, we return a Blueprint, which is an
            # ordinary object containing all the information necessary
            # to construct a TypeProxy at the client site
            return TypeInfo(prime)

        elif isinstance(prime, BaseException):
            print '  wrapping as exception'
            if type(prime).__module__ in ('exceptions', '__builtin__'):
                # TODO: check that we can safely transport all standard exceptions
                return None  # indicates that we should return this object by value
            else:
                return ExceptionProxy(prime_id, prime_docstring)

        elif type(prime).__module__ != '__builtin__':
            print '  wrapping as object'
            if callable(prime):
                return CallableObjectProxy(prime_id, prime_docstring)
            else:
                return ObjectProxy(prime_id, prime_docstring)

        elif type(prime) in (int,long,float,bool) or isinstance(prime, basestring):
            print '  not wrapping scalar'
            return None  # indicates that we should return this object by value

        # TEMP HACK
        elif operator.isSequenceType(prime):
            print '  not wrapping sequence'
            return None  # indicates that we should return this object by value

        else:
            print '  wrapping as object'
            return ObjectProxy(prime_id, prime_docstring)

class ClientState(object):
    '''An enumeration representing the state of a client object.'''
    READY = 1
    WAITING_FOR_RESULT = 2
    TERMINATING = 3
    TERMINATED_CLEANLY = 10
    TERMINATED_WITH_ERROR = 11
    TERMINATED_ASYNC = 12

    TerminatedSet = (TERMINATED_CLEANLY, TERMINATED_WITH_ERROR, TERMINATED_ASYNC)

    Names = {
        READY: 'READY',
        WAITING_FOR_RESULT: 'WAITING_FOR_RESULT',
        TERMINATING: 'TERMINATING',
        TERMINATED_CLEANLY: 'TERMINATED_CLEANLY',
        TERMINATED_WITH_ERROR: 'TERMINATED_WITH_ERROR',
        TERMINATED_ASYNC: 'TERMINATED_ASYNC'
        }

class Client(object):
    '''Represents a client that sends delegates and listens for results.'''
    def __init__(self, server_process, delegate_channel, result_channel):
        assert server_process.is_alive()
        self._delegate_channel = delegate_channel
        self._result_channel = result_channel
        self._server_process = server_process
        self._state = ClientState.READY
        self._proxy_by_id = dict()
        self._sigchld_count = 0
        atexit.register(self._on_exit)
        ChildProcessSignalHandler.register_listener(server_process, self._on_sigchld)

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, v):
        print 'client changing to state='+ClientState.Names[v]
        self._state = v

    @property
    def server_process(self):
        return self._server_process

    def __getstate__(self):
        raise Exception('You attempted to pickle the client object')

    def _assert_alive(self):
        if not self._server_process.is_alive():
            self.state = ClientState.TERMINATED_WITH_ERROR
            raise ProcessTerminationError(self._server_process._popen.returncode)

    def _on_sigchld(self):
        print 'client got SIGCHLD (state=%s)' % self.state
        assert not self._server_process.is_alive()
        # Note that this will be called from *within* a signal handler
        # so it should be thought of as asynchronous with respect to
        # other code

        self._sigchld_count += 1

        # If this client is currently waiting for a result then unwind
        # the stack up to the appropriate point. Otherwise, the
        # process is_alive() flag will be checked next time execute()
        # is called
        if self.state == ClientState.WAITING_FOR_RESULT:
            raise ProcessTerminationError(self._server_process._popen.returncode)

        elif self.state == ClientState.READY:
            # This means the child terminated at a time when it wasn't
            # running a command sent by this client. We will raise an
            # exception next time execute() is called.
            self.state = ClientState.TERMINATED_ASYNC

        elif self.state == ClientState.TERMINATING:
            # We just asked the server to terminate and it did so.
            # This case comes up if SIGCHLD arrives before terminate()
            # gets a chance to change self.state. Either way, we're
            # fine.
            pass

        elif self.state == ClientState.TERMINATED_CLEANLY:
            # We just asked the server to terminate and it did so.
            # This case comes up if SIGCHLD arrives after terminate()
            # changes self.state. Either way, we're fine.
            pass

        elif self.state == ClientState.TERMINATED_WITH_ERROR:
            # This should not come up because the only way we can get
            # to this state is if we execute something that crashes
            # the server, which causes SIGCHLD in state
            # ClientState.WAITING_FOR_RESULT, which throws
            # ProcessTerminationError, which is caught in self.execute()

            # Update: It seems that it's possible to get multiple SIGCHLD signals!
            raise ClientStateError('Recieved SIGCHLD when client state=%s, n=%d. This should not happen!' % \
                                       (self.state, self._sigchld_count))

        elif self.state == ClientState.TERMINATED_ASYNC:
            # This should not come up because the only way we can get
            # to TERMINATED_ASYNC is if we recieved a previous
            # SIGCHLD, and we should only ever get one SIGCHLD per
            # child process.
            raise ClientStateError('Recieved SIGCHLD when client state=%s, n=%d. This should not happen!' % \
                                       (self.state, self._sigchld_count))

        else:
            # We are in an unknown state
            raise ClientStateError('Recieved SIGCHLD when client state=%s (which is an unknown state)' % self.state)

        # Always "join" the process so that the OS can clean it up and free its memory
        # TODO: handle timeouts here
        # TODO: handle exceptions here (e.g. what if the process has already been joined for some reason?)
        self._server_process.join()

    def _on_exit(self):
        self.cleanup()

    def attach_proxy(self, proxy):
        if proxy.prime_id in self._proxy_by_id:
            # Replace the proxy with the cached version so that
            # proxyect identity tests match the server
            proxy = self._proxy_by_id[proxy.prime_id]
            print 'client replacing proxy for prime_id=%d with a cached proxy for the same prime (proxy_id=%d)' % \
                (proxy.prime_id, id(proxy))
            return proxy
        else:
            proxy.attach_to_client(self)
            self._proxy_by_id[proxy.prime_id] = proxy
            print 'client added a proxy for prime_id=%d to its cache (proxy_id=%d)' % (proxy.prime_id, id(proxy))
            return proxy

    def call(self, func, *args, **kwargs):
        return self.execute(FuncDelegate(func, *args, **kwargs))

    def execute(self, delegate):
        # The server process may have terminated since we last
        # returned a result due to e.g. a background thread running
        # asynchronously on the server. In this case, throw an
        # ordinary ProcessTerminationError as though the server died
        # as a result of the current call.
        if self.state == 'TERIMATED_ASYNC':
            assert not self._server_process.is_alive()
            self.state = ClientState.TERMINATED_WITH_ERROR
            raise ProcessTerminationError(self._server_process._popen.returncode)

        elif self.state != ClientState.READY:
            raise ClientStateError('execute() called while state='+ClientState.Names[self.state])

        # Dispatch the delegate
        # TODO: can the queue itself throw exceptions?
        print 'client sending delegate: %s' % str(delegate)
        self._delegate_channel.put(delegate)

        # Get the result
        # TODO: can the queue itself throw exceptions?
        try:
            self._assert_alive()
            self.state = ClientState.WAITING_FOR_RESULT
            self._assert_alive()
            result = read_channel(self._result_channel, num_retries=1)
            self._assert_alive()
        except ProcessTerminationError as ex:
            # Change state and re-raise
            self.state = ClientState.TERMINATED_WITH_ERROR
            raise ex
        finally:
            # In case some other exception is thrown by result_channel.get()...
            self.state = ClientState.READY

        print 'client recieved result: '+raw_repr(result)

        # Unpack any exception raised on the server side
        if isinstance(result, ExceptionalResult):
            print 'client recieved exceptional result: '+str(result)
            raise result.exception

        # Unpack any types
        if isinstance(result, TypeInfo):
            result = TypeProxy(result)
            # make sure to pass this through the check below too...

        # Replace with a cached proxy if we have one and attach it to the client environment
        if isinstance(result, Proxy):
            print 'client attaching: '+raw_repr(result)
            result = self.attach_proxy(result)

        return result

    def terminate(self):
        '''Stop the server process and change our state to TERMINATING. Only valid if state=READY.'''
        print 'client.terminate() called (state=%s)' % self.state
        if self.state == ClientState.WAITING_FOR_RESULT:
            raise ClientStateError('terimate() called while state='+ClientState.Names[self.state])
        if self.state == ClientState.TERMINATING:
            raise ClientStateError('terimate() called while state='+ClientState.Names[self.state])
        elif self.state in ClientState.TerminatedSet:
            assert not self._server_process.is_alive()
            return
        elif self.state == ClientState.READY:
            # Check that the process itself is still alive
            self._assert_alive()

            # Make sure the SIGCHLD signal handler doesn't throw any exceptions
            self.state = ClientState.TERMINATING

            # Do not call execute() because that function will check
            # whether the process is alive and throw an exception if not
            # TODO: can the queue itself throw exceptions?
            self._delegate_channel.put(FuncDelegate(_raise_terminate))

            # Wait for acknowledgement
            # TODO: can the result queue throw an exception?
            try:
                result = read_channel(self._result_channel, num_retries=5)
            except ChannelError as ex:
                # Was interrupted five times in a row! Ignore for now
                print 'client failed to read sentinel from channel after 5 retries - will terminate anyway'

            self.state = ClientState.TERMINATED_CLEANLY

    def cleanup(self):
        '''Terminate this client if it is not already terminated.'''
        print 'cleanup() called while state is '+ClientState.Names[self.state]
        if self.state == ClientState.WAITING_FOR_RESULT:
            # There is an ongoing call to execute()
            # Not sure what to do here
            print '  not sure what to do so doing nothing'
            pass
        elif self.state == ClientState.TERMINATING:
            # terminate() has been called but we have not recieved SIGCHLD yet
            # Not sure what to do here
            print '  not sure what to do so doing nothing'
            pass
        elif self.state in ClientState.TerminatedSet:
            # We have already terminated
            # TODO: should we deal with TERMINATED_ASYNC in some special way?
            print '  nothing needs to be done'
            pass
        else:
            print '  attempting to terminate'
            try:
                self.terminate()
            except ProcessTerminationError as ex:
                # Terminate can throw a ProcessTerminationError if the
                # process terminated at some point between the last
                # execute() and the call to terminate()
                # For now we just ignore this.
                pass            


class IsolationContext(object):
    '''Represents a domain for executing code that is isolated from
    the rest of the process. Each isolation context corresponds to a
    single sub-process in which one or more python modules has been
    loaded.'''
    def __init__(self):
        self._client = None

    @property
    def remote_pid(self):
        '''Get the PID of the process in which this context will run code.'''
        return self.client.server_process.pid

    @property
    def client(self):
        '''Get the client object that communicates with the isolation
        host, or None if start has not yet been called.'''
        return self._client

    def start(self):
        '''Create a process in which the isolated code will be run.'''
        assert self._client is None

        print 'IsolationContext[%d] starting' % id(self)

        # Create the queues
        request_queue = multiprocessing.Queue()
        response_queue = multiprocessing.Queue()

        # Launch the server process
        server = Server(request_queue, response_queue)  # Do not keep a reference to this object!
        server_process = multiprocessing.Process(target=server.loop)
        server_process.start()

        # Create a client to talk to the server
        self._client = Client(server_process, request_queue, response_queue)

    def restart(self):
        '''Terminate the child process and start a new process. This
        does not restore any state, so any python modules that were
        previously loaded will need to be reloaded.'''
        print 'IsolationContext[%d] restarting' % id(self)
        if self._client is not None:
            # It is always safe to call cleanup no matter what state the client is in
            self._client.cleanup()
            self._client = None
        self.start()

    def ensure_started(self):
        '''If the subprocess for this isolation context has not been created then create it.'''
        print 'IsolationContext[%d] ensure_started: client is None? %s' % (id(self), str(self._client is None))
        if self._client is None:
            self.start()

    def load_module(self, module_name):
        '''Import a module into this isolation context and return a proxy for it.'''
        self.ensure_started()
        mod = self.client.call(_load_module, module_name, sys.path)
        mod.__isolation_context__ = self
        return mod

        




def default_context():
    if not hasattr(default_context, '_instance'):
        default_context._instance = IsolationContext()
    return default_context._instance

def load_module(module_name):
    '''Import a module into the default isolated context and return a
    reference to it. This does not import the module into sys.modules
    and it does not have any of the other effects of python's "import
    foo" syntax.'''
    return default_context().load_module(module_name)

def import_isolated(module_name, fromlist=[], level=-1):
    '''Import an module into an isolated context as if with
    "__import__('module_name')"'''
    mod = load_module(module_name)
    sys.modules['module_name'] = mod
    return __import__(module_name, fromlist=fromlist, level=level)
