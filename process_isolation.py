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

# DONE
# - Implement docstring copying

# TODO
# - Switch to the lower-level multiprocessing.Pipe in order to get access to the pickler object
# - Omit __len__, __call__, __dir__, etc whenever the prime doesn't offer them
# - Add an API to restart the server if it goes down
#   - Make it possible to automatically restart the server?
# - Implement reload(mymodule)
# - Add the module to local sys.moduels so that it doesn't get loaded by some other module
# - Migrate objects between client and server?
# - Multiple separately isolated processes
# - Deal with extra-special funtions like __getattribute__, __class__, etc
# - Replace __getattr__ with descriptors? And add __getattr__ only if the prime implements it?
# - Avoid reloading modules on server side
# - Make registry and proxy_by_id use weak references
# - Wrap a module with a proxy that explicitly contains proxies for each member (for clarity)
# - Deal with files
# - Deal with generators
# - Can all exceptions classes in 'exceptions' be safely copied?
# - Deal with call to sys.exit() in child process
# - Deal with modifications to global state
# - Detect when cPickle fails to pickle an object and automatically proxy it instead
# - Deal with objects that are sent to server and then modified there -- e.g. appending to a list
# - Transport stack trace from server to client and append it to the system one at the client end
# - Figure out how to get rid of RemoteRef -- too confusing!
# - Implement syntax like:
#     with process_isolation.Timeout(200):
#       foo.bar()

# - When a proxy's __del__ gets called, change the server-side registry reference to a weakref so that
#   objects eventually get cleaned up

# Reasons to re-implement pickle:
# - support auto-wrapping of nested objects
# - support lazily constructing proxies on client side and caching them
# - support transporting code and types directly
# - could dynamically create classes on server side and transport them to client side
# - pickle can't transport tracebacks
# - pickle does not support __getstate__, __setstate__, etc on __getnewargs__ on types (i.e. metaclass instances)
# - When pickling exceptions, cPickle doesn't follow the pickle protocol at all
#   - in particular, the exception constructor must accept zero args
# - When we serialize proxies internally, we want to copy just the
#   remote_id and other metadata, but when the end-user pickles a
#   proxy object, we want someproxy.__getstate__ to delegate to the
#   server-side implementation. We can't cleanly meet both
#   requirements if we use pickle for transporting proxies between
#   client and server.

sys.setrecursionlimit(100)

# These attributes should never be overriden
SPECIAL_ATTRIBUTES = [ 
    '__class__',
    '__base__',
    '__bases__',
    '__getattribute__',
    '__getattr__',
    ]



class TerminateProcess(BaseException):
    pass

class ProcessTerminationError(Exception):
    def __init__(self, signal_or_returncode):
        self._signal_or_returncode = signal_or_returncode

class ClientStateError(Exception):
    '''Indicates that a command was attempted on a client that was in
    a state other than READY.'''
    def __init__(self, msg):
        super(self,ClientStateError).__init__(msg)

# Produce a representation using the default repr() regardless of
# whether the object provides an implementation of its own
def raw_repr(obj):
    if isinstance(obj, Proxy):
        return '<%s with prime_id=%d>' % (obj.__class__.__name__, obj.prime_id)
    else:
        return repr(obj)

def _raise_terminate():
    raise TerminateProcess()

def _do_import(module_name, path):
    # TODO: handle the case that the module is already loaded
    fd, filename, info = imp.find_module(module_name, path)
    try:
        return imp.load_module(module_name, fd, filename, info)
    finally:
        if fd is not None:
            fd.close()

class ChildProcessSignalHandler(object):
    '''Helper to catch SIGCHLD signals and dispatch them.'''
    _listeners = {}
    _processes = {}
    _installed = False

    @classmethod
    def register_listener(cls, process, listener):
        cls._install()
        cls._processes[process.pid] = process
        cls._listeners.setdefault(process.pid,[]).append(listener)

    @classmethod
    def _handle_sigchld(cls, signum, stackframe):
        pids_to_remove = []
        for pid,listeners in cls._listeners.iteritems():
            if not cls._processes[pid].is_alive():
                pids_to_remove.append(pid)
                for listener in listeners:
                    listener()
        for pid in pids_to_remove:
            del cls._processes[pid]
            del cls._listeners[pid]

    @classmethod
    def _install(cls):
        if not cls._installed:
            cls._installed = True
            signal.signal(signal.SIGCHLD, cls._handle_sigchld)




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

class ObjectProxy(Proxy):
    '''A proxy for a server-side object.'''
    # To be run at server end:
    def __init__(self, prime):
        super(ObjectProxy,self).__init__(id(prime))
        if hasattr(prime, '__doc__'):
            self.__doc__ = prime.__doc__

    # TODO: use a metaclass to omit these when the server does not support them:

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

    # Implement function-like special methods
    def __call__(self, *args, **kwargs):
        return self.client.call(self, *args, **kwargs)

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

    # Implement misc special methods
    def __hash__(self, other):
        return self.client.call(hash, self, other)
    def __dir__(self):
        return self.client.call(dir, self)

    # Note that we do not include get/set/delslice, etc because they
    # are deprecated and not necessary. If they exist on the prime
    # then the get/set/delitem overloads above will call them.
    
class ExceptionProxy(Exception,Proxy):
    def __init__(self, prime):
        print 'Creating an ExceptionProxy with id='+str(id)
        Proxy.__init__(self, id(prime))
        if hasattr(prime, '__doc__'):
            self.__doc__ = prime.__doc__
    def __reduce__(self):
        return ExceptionProxy, (self.prime_id,)

class FunctionProxy(Proxy):
    '''A proxy for a server-side function.'''
    # To be run at server end:
    def __init__(self, prime):
        super(FunctionProxy,self).__init__(id(prime))
        if hasattr(prime, '__doc__'):
            self.__doc__ = prime.__doc__

    # To be run at client end:
    def __call__(self, *args, **kwargs):
        return self.client.call(self, *args, **kwargs)




class ExceptionalResult(object):
    '''Used to transport exceptions from the server to the client.'''
    def __init__(self, exception, traceback):
        self.exception = exception
        self.traceback = traceback
    def __str__(self):
        return '<ExceptionalResult [%s]>' % str(self.exception)




class TypeProxyBlueprint(object):
    '''Represents the information needed to construct an instance of
    TypeProxy, in a form that, for the benefit of cPickle, is not
    itself a class (since classes are pickled by simply storing their
    name).'''
    def __init__(self, prime_class):
        self._name = prime_class.__name__
        self._module = prime_class.__module__
        self._class_id = id(prime_class)
        if hasattr(prime_class, '__doc__'):
            self._doc = prime_class.__doc__

class TypeProxy(type,Proxy):
    def attach_to_client(proxyclass, client):
        proxyclass._client = client

    def _new_instance(proxyclass, *args, **kwargs):
        return proxyclass._client.call(proxyclass, *args, **kwargs)

    def __instancecheck__(proxyclass, obj):
        return proxyclass._client.call(isinstance, obj, proxyclass)

    def __init__(proxyclass, blueprint):
        print 'TypeProxy.__init__ was called'
        Proxy.__init__(proxyclass, blueprint._class_id)
        proxyclass._client = None
        proxyclass.__new__ = TypeProxy._new_instance
        if hasattr(blueprint, '_doc'):
            proxyclass.__doc__ = blueprint._doc

    def __new__(metaclass, blueprint):
        # TODO: find or create proxies for the base classes of prime_class
        proxyname = blueprint._name
        proxybases = (object,)
        proxymembers = dict()
        return type.__new__(metaclass, proxyname, proxybases, proxymembers)




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
        print 'server[%d] starting' % os.getpid()
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

        print 'Server loop ended'

    def wrap(self, prime):
        if id(prime) in self._proxy_by_id:
            return self._proxy_by_id[id(prime)]
        else:
            proxy = self.wrap_impl(prime)
            print 'nserver storing a proxy for prime_id=%d' % id(prime)
            self._prime_by_id[id(prime)] = prime
            self._proxy_by_id[id(prime)] = proxy
            return proxy

    def wrap_impl(self, prime):
        print 'wrapping %s (id=%d)' % (str(prime), id(prime))

        if isinstance(prime, (types.FunctionType, types.MethodType)):  # do _not_ use callable(...) here
            print '  wrapping as callable'
            return FunctionProxy(prime)

        elif isinstance(prime, (types.ModuleType)):
            print '  wrapping as module'
            return ObjectProxy(prime)

        elif isinstance(prime, (types.FileType)):
            print '  wrapping as file'
            return ObjectProxy(prime)

        elif isinstance(prime, type):
            print '  wrapping as type'
            # Rather than returning a type directly, which would be
            # rejected by cPickle, we return a Blueprint, which is an
            # ordinary object containing all the information necessary
            # to construct a TypeProxy at the client site
            return TypeProxyBlueprint(prime)

        elif isinstance(prime, BaseException):
            print '  wrapping as exception'
            if type(prime).__module__ in ('exceptions', '__builtin__'):
                # TODO: check that we can safely transport all standard exceptions
                return prime
            else:
                return ExceptionProxy(prime)

        elif type(prime).__module__ != '__builtin__':
            print '  wrapping as object'
            return ObjectProxy(prime)

        elif type(prime) in (int,long,float,bool) or isinstance(prime, basestring):
            print '  not wrapping scalar'
            return prime

        # TEMP HACK
        elif operator.isSequenceType(prime):
            print '  not wrapping sequence'
            return prime

        else:
            print '  wrapping as object'
            return ObjectProxy(prime)


class Client(object):
    '''Represents a client that sends delegates and listens for results.'''
    def __init__(self, server_process, delegate_channel, result_channel):
        assert server_process.is_alive()
        self._delegate_channel = delegate_channel
        self._result_channel = result_channel
        self._server_process = server_process
        self._state = 'READY'
        self._proxy_by_id = dict()
        atexit.register(self._on_exit)
        ChildProcessSignalHandler.register_listener(server_process, self._on_sigchld)

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, v):
        print 'client changing to state=%s' % v
        self._state = v

    @property
    def server_process(self):
        return self._server_process

    def __getstate__(self):
        raise Exception('You attempted to pickle the client object')

    def _assert_alive(self):
        if not self._server_process.is_alive():
            self.state = 'TERMINATED_WITH_ERROR'
            raise ProcessTerminationError(self._server_process._popen.returncode)

    def _on_sigchld(self):
        print 'client got SIGCHLD (state=%s)' % self.state
        assert not self._server_process.is_alive()
        # Note that this will be called from *within* a signal handler
        # so it should be thought of as asynchronous with respect to
        # other code

        # If this client is currently waiting for a result then unwind
        # the stack up to the appropriate point. Otherwise, the
        # process is_alive() flag will be checked next time execute()
        # is called
        if self.state == 'WAITING_FOR_RESULT':
            raise ProcessTerminationError(self._server_process._popen.returncode)

        elif self.state == 'READY':
            # This means the child terminated at a time when it wasn't
            # running a command sent by this client. We will raise an
            # exception next time execute() is called.
            self.state = 'TERMINATED_ASYNC'

        elif self.state == 'TERMINATING':
            # If we just asked the server to terminate then don't throw an
            # exception.
            pass

        else:
            # In this case we had already terminated and we recieved another signal
            # This is very strange!
            print 'Unknown situation: Recieved SIGCHLD when client state=%s. Ignoring.' % self.state
            pass

        # Always "join" the process so that the OS can clean it up and free its memory
        # TODO: handle timeouts here
        # TODO: handle exceptions here (e.g. what if the process has already been joined for some reason?)
        self._server_process.join()

    def _on_exit(self):
        if self._server_process.is_alive():
            try:
                self.terminate()
            except ProcessTerminationError as ex:
                # Terminate can throw a ProcessTerminationError if the
                # process terminated at some point between the last
                # execute() and the call to terminate()
                # For now we just ignore this.
                pass            

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
            self.state = 'TERMINATED_WITH_ERROR'
            raise ProcessTerminationError(self._server_process._popen.returncode)

        elif self.state != 'READY':
            raise ClientStateError('execute() called while state='+self.state)

        # Dispatch the delegate
        # TODO: can the queue itself throw exceptions?
        print 'client sending delegate: %s' % str(delegate)
        self._delegate_channel.put(delegate)

        # Get the result
        # TODO: can the queue itself throw exceptions?
        try:
            self._assert_alive()
            self.state = 'WAITING_FOR_RESULT'
            self._assert_alive()
            result = self._result_channel.get()
            self._assert_alive()
            self.state = 'READY'
        except ProcessTerminationError as ex:
            # Change state and re-raise
            self.state = 'TERMINATED_WITH_ERROR'
            raise ex

        print 'client got result: '+raw_repr(result)

        # Unpack any exception raised on the server side
        if isinstance(result, ExceptionalResult):
            print 'client recieved exceptional result: '+str(result)
            raise result.exception

        # Unpack any types
        if isinstance(result, TypeProxyBlueprint):
            result = TypeProxy(result)
            # make sure to pass this through the check below too...

        # Replace with a cached proxy if we have one and attach it to the client environment
        if isinstance(result, Proxy):
            print 'client attaching: '+raw_repr(result)
            result = self.attach_proxy(result)

        return result

    def terminate(self):
        print 'client.terminate() called (state=%s)' % self.state
        if self.state == 'WAITING_FOR_RESULT':
            raise ClientStateError('terimate() called while state='+self.state)
        if self.state == 'TERMINATING':
            raise ClientStateError('terimate() called while state='+self.state)
        elif self.state in ('TERMINATED_CLEANLY', 'TERMINATED_WITH_ERROR', 'TERMINATED_ASYNC'):
            assert not self._server_process.is_alive()
            return
        elif self.state == 'READY':
            # Check that the process itself is still alive
            print 'server process alive:',self._server_process.is_alive()
            self._assert_alive()

            # Make sure the SIGCHLD signal handler doesn't throw any exceptions
            self.state = 'TERMINATING'

            # Do not call execute() because that function will check
            # whether the process is alive and throw an exception if not
            # TODO: can the queue itself throw exceptions?
            # TODO: it's walsy possible that the 
            self._delegate_channel.put(FuncDelegate(_raise_terminate))

            # Wait for acknowledgement
            # TODO: can the result queue throw an exception?
            result = self._result_channel.get()


class IsolationContext(object):
    def __init__(self):
        self._client = None

    @property
    def remote_pid(self):
        '''Get the PID of the process in which this context will run code.'''
        return self.client.server_process.pid

    @property
    def client(self):
        '''Get the client object that communicates with the isolation
        host, or None if start_subprocess has not yet been called.'''
        return self._client

    def start_subprocess(self):
        '''Create a process in which the isolated code will be run.'''
        assert self._client is None

        # Create the queues
        request_queue = multiprocessing.Queue()
        response_queue = multiprocessing.Queue()

        # Launch the server process
        server = Server(request_queue, response_queue)  # Do not keep a reference to this object!
        server_process = multiprocessing.Process(target=server.loop)
        server_process.start()

        # Create a client to talk to the server
        self._client = Client(server_process, request_queue, response_queue)

    def ensure_started(self):
        '''If the subprocess for this isolation context has not been created then create it.'''
        if self._client is None:
            self.start_subprocess()

    def import_isolated(self, module_name):
        '''Import a module into this isolation context and return a proxy for it.'''
        self.ensure_started()
        return self.client.call(_do_import, module_name, sys.path)




def default_context():
    if not hasattr(default_context, '_instance'):
        default_context._instance = IsolationContext()
    return default_context._instance

def import_isolated(module_name):
    return default_context().import_isolated(module_name)













