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

# TODO
# - Switch to the lower-level multiprocessing.Pipe in order to get access to the pickler object


# - Implement docstring copying
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
# - Make registry and proxy_cache use weak references
# - Wrap a module with a proxy that explicitly contains proxies for each member (for clarity)
# - Deal with files
# - Deal with generators
# - Can all exceptions classes in 'exceptions' be safely copied?
# - Deal with call to sys.exit() in child process
# - Deal with modifications to global state
# - Detect when cPickle fails to pickle an object and automatically proxy it instead
# - Deal with objects that are sent to server and then modified there -- e.g. appending to a list
# - Transport stack trace from server to client and append it to the system one at the client end
# - Get rid of RemoteId -- too confusing!


# Reasons to re-implement pickle:
# - support auto-wrapping of nested objects
# - support lazily constructing proxies on client side and caching them
# - support transporting code and types directly
# - could dynamically create classes on server side and transport them to client side
# - pickle can't transport tracebacks
# - pickle does not support __getstate__, __setstate__, etc on __getnewargs__ on types (i.e. metaclass instances)
# - When pickling exceptions, cPickle doesn't follow the pickle protocol at all
#   - in particular, the exception constructor must accept zero args


# These attributes should never be overriden
SPECIAL_ATTRIBUTES = [ 
    '__class__',
    '__base__',
    '__bases__',
    '__getattribute__',
    '__getattr__',
    ]


# Produce a representation using the default repr() regardless of
# whether the object provides an implementation of its own
def raw_repr(obj):
    if obj.__class__.__module__ == '__builting__':
        return repr(obj)
    else:
        return object.__repr__(obj)
    

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
    def __init__(self, id):
        self._id = id
        self._client = None
    def attach_to_client(self, client):
        self._client = client
    @property
    def prime_id(self):
        return self._id
    @property
    def prime_ref(self):
        return RemoteRef(self._id)
    @property
    def client(self):
        return self._client

class Delegate(object):
    '''Represents a delegate constructed at client and transported to server.'''
    def attach_to_server(self, server):
        self._server = server
    @property
    def registry(self):
        return self._server._registry

class PrimeDelegate(Delegate):
    '''A delegate with a convenient interface to look up an object on the server.'''
    def __init__(self, id):
        self._id = id
    @property
    def prime(self):
        return self.registry[self._id]





class TerminateProcess(Exception):
    pass

class TerminateDelegate(Delegate):
    def run_on_server(self):
        raise TerminateProcess()




class ObjectProxy(Proxy):
    '''A proxy for a server-side object.'''
    # To be run at server end:
    def __init__(self, prime_id):
        super(ObjectProxy,self).__init__(prime_id)

    # To be run at client end:
    def __getattr__(self, attrname):
        if attrname in ['_client'] or attrname.startswith('__'):
            return super(ObjectProxy,self).__getattribute__(attrname)
        else:
            return self.client.execute(GetAttrDelegate(self.prime_id, attrname))

    def __setattr__(self, attrname, val):
        if attrname in ['_id', '_client'] or attrname.startswith('__'):
            return super(ObjectProxy,self).__setattr__(attrname, val)
        else:
            return self.client.execute(SetAttrDelegate(self.prime_id, attrname, val))

    def __delattr__(self, attrname):
        if attrname in ['_id', '_client']:
            return super(ObjectProxy,self).__detattr__(attrname)
        else:
            return self.client.execute(DelAttrDelegate(self.prime_id, attrname))

    # TODO: use metaclass magic to omit this for objects that are not callable
    def __call__(self, *args, **kwargs):
        return self.client.execute(CallDelegate(self.prime_id, *args, **kwargs))

    # TODO: use metaclass magic to omit this for objects that are not callable
    def __len__(self):
        return self.client.execute(FreeFuncDelegate(len, self.prime_ref))

    def __str__(self):
        return self.client.execute(FreeFuncDelegate(str, self.prime_ref))

    def __repr__(self):
        return self.client.execute(FreeFuncDelegate(repr, self.prime_ref))
    
class ExceptionProxy(Exception,Proxy):
    def __init__(self, id):
        print 'Creating an ExceptionProxy with id='+str(id)
        Proxy.__init__(self, id)

    def __reduce__(self):
        return ExceptionProxy, (self._id,)


class GetAttrDelegate(PrimeDelegate):
    def __init__(self, id, attrname):
        super(GetAttrDelegate,self).__init__(id)
        self._attrname = attrname
    def run_on_server(self):
        return getattr(self.prime, self._attrname)

class SetAttrDelegate(PrimeDelegate):
    def __init__(self, id, attrname, val):
        super(SetAttrDelegate,self).__init__(id)
        self._attrname = attrname
        self._val = val
    def run_on_server(self):
        return setattr(self.prime, attrname, val)

class DelAttrDelegate(PrimeDelegate):
    def __init__(self, id, attrname):
        super(DelAttrDelegate,self).__init__(id)
        self._attrname = attrname
    def run_on_server(self):
        return delattr(self.prime, attrname)

class FreeFuncDelegate(Delegate):
    def __init__(self, func, *args, **kwargs):
        super(FreeFuncDelegate, self).__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs
        print 'delegate on client: '+raw_repr(args)
    def run_on_server(self):
        resolved_args = [self.registry[v.id] if isinstance(v,RemoteRef) else v for v in self._args]
        resolved_kwargs = {k:self.registry[v.id] if isinstance(v,RemoteRef) else v for k,v in self._kwargs}
        return self._func(*resolved_args, **resolved_kwargs)




class FunctionProxy(Proxy):
    '''A proxy for a server-side function.'''
    # To be run at server end:
    def __init__(self, id):
        super(FunctionProxy,self).__init__(id)
    # To be run at client end:
    def __call__(self, *args, **kwargs):
        return self.client.execute(CallDelegate(self._id, *args, **kwargs))

class CallDelegate(PrimeDelegate):
    def __init__(self, function_id, *args, **kwargs):
        super(CallDelegate, self).__init__(function_id)
        self._args = args
        self._kwargs = kwargs
    def run_on_server(self):
        return self.prime(*self._args, **self._kwargs)




class ImportDelegate(Delegate):
    def __init__(self, module_name, path):
        self._module_name = module_name
        self._path = path
    def run_on_server(self):
        # TODO: handle the case that the module is already loaded
        fd, filename, info = imp.find_module(self._module_name, self._path)
        try:
            return imp.load_module(self._module_name, fd, filename, info)
        finally:
            if fd is not None:
                fd.close()



class ExceptionalResult(object):
    '''Used to transport exceptions from the server to the client.'''
    def __init__(self, exception, traceback):
        self.exception = exception
        self.traceback = traceback


class ProcessTerminationError(Exception):
    def __init__(self, signal_or_returncode):
        self._signal_or_returncode = signal_or_returncode


class TypeProxyBlueprint(object):
    '''Represents the information needed to construct an instance of
    TypeProxy, in a form that, for the benefit of cPickle, is not
    itself a class (since classes are pickled by simply storing their
    name).'''
    def __init__(self, prime_class):
        self._name = prime_class.__name__
        self._module = prime_class.__module__
        self._class_id = id(prime_class)

class TypeProxy(type,Proxy):
    def attach_to_client(proxyclass, client):
        proxyclass._client = client

    def _new_instance(proxyclass, *args, **kwargs):
        return proxyclass._client.execute(CallDelegate(proxyclass._id, *args, **kwargs))

    def __instancecheck__(proxyclass, obj):
        return proxyclass._client.execute(FreeFuncDelegate(isinstance, obj.prime_ref, proxyclass.prime_ref))

    def __init__(proxyclass, blueprint):
        print 'TypeProxy.__init__ was called'
        Proxy.__init__(proxyclass, blueprint._class_id)

    def __new__(metaclass, blueprint):
        # TODO: find or create proxies for the base classes of prime_class
        proxyname = blueprint._name
        proxybases = (object,)
        proxymembers = {
            '__new__': TypeProxy._new_instance,
            '_client': None,
            }
        return type.__new__(metaclass, proxyname, proxybases, proxymembers)




class Server(object):
    '''Represents the server that listens for delegates and runs them.'''
    def __init__(self, delegate_queue, result_queue):
        self._delegate_queue = delegate_queue
        self._result_queue = result_queue
        self._registry = dict()

    def __getstate__(self):
        raise Exception('You attempted to pickle the server object')

    def loop(self):
        terminate_flag = False
        while not terminate_flag:
            # Get the next delegate
            delegate = self._delegate_queue.get()

            # Attach the delegate to the server environment
            delegate.attach_to_server(self)

            try:
                # Run the delegate in the local environment and wrap the result
                result = self.wrap(delegate.run_on_server())
            except TerminateProcess:
                # This exception indicates that the client requested that we terminate
                result = True
                terminate_flag = True
            except:
                # Any other exception gets transported back to the client
                ex_type, ex_value, ex_traceback = sys.exc_info()
                print 'Caught on server:'
                print ex_value
                traceback.print_exc()

                result = ExceptionalResult(self.wrap(ex_value), None)
                # TODO: find a way to transport a traceback (pickle can't serialize it)

            # Send the result to the client
            print 'server putting object of type %s onto result queue' % str(result.__class__)
            self._result_queue.put(result)


    def wrap(self, prime):
        print 'Wrapping: '+repr(prime)

        if id(prime) in self._registry:
            return self._registry[id(prime)]

        elif isinstance(prime, (types.FunctionType, types.MethodType)):  # do _not_ use callable(...) here
            print '  wrapping as callable'
            self._registry[id(prime)] = prime
            return FunctionProxy(id(prime))

        elif isinstance(prime, (types.ModuleType, types.FileType)):
            print '  wrapping as module or file'
            self._registry[id(prime)] = prime
            return ObjectProxy(id(prime))

        elif isinstance(prime, type):
            print '  wrapping as type'
            self._registry[id(prime)] = prime
            # Rather than returning a type directly, which would be
            # rejected by cPickle, we return a Blueprint, which is an
            # ordinary object containing all the information necessary
            # to construct a TypeProxy at the client site
            return TypeProxyBlueprint(prime)

        elif isinstance(prime, BaseException):
            if type(prime).__module__ in ('exceptions', '__builtin__'):
                # TODO: check that we can safely transport all standard exceptions
                return prime
            else:
                self._registry[id(prime)] = prime
                proxy = ExceptionProxy(id(prime))
                print 'Wrapped a %s with an ExceptionProxy'%str(type(prime))
                return proxy

        elif type(prime).__module__ != '__builtin__':
            self._registry[id(prime)] = prime
            return ObjectProxy(id(prime))

        else:
            return prime

class Client(object):
    '''Represents a client that sends delegates and listens for results.'''
    def __init__(self, server_process, delegate_queue, result_queue):
        assert server_process.is_alive()
        self._delegate_queue = delegate_queue
        self._result_queue = result_queue
        self._server_process = server_process
        self._waiting_for_result = False
        self._finishing = False
        self._proxy_cache = {}
        atexit.register(self._atexit)
        ChildProcessSignalHandler.register_listener(server_process, self._on_sigchld)

    def __getstate__(self):
        raise Exception('You attempted to pickle the client object')

    def _on_sigchld(self):
        assert not self._server_process.is_alive()
        # Note that this will be called from *within* a signal handler
        # so it should be thought of as asynchronous with respect to
        # other code

        # If we just asked the server to terminate then don't throw an
        # exception.
        if self._finishing:
            return

        # If this client is currently waiting for a result then unwind
        # the stack up to the appropriate point. Otherwise, the
        # process is_alive() flag will be checked next time execute()
        # is called
        if self._waiting_for_result:
            raise ProcessTerminationError(self._server_process._popen.returncode)

    def _check_alive(self):
        if not self._server_process.is_alive():
            raise ProcessTerminationError(self._server_process._popen.returncode)

    def _atexit(self):
        if self._server_process.is_alive():
            try:
                self.finish()
                # TODO: handle timeouts and exceptions from join() below
                self._server_process.join()
            except ProcessTerminationError as ex:
                # Ignore it for now
                pass            

    @property
    def subprocess(self):
        return self._server_process

    @property
    def pid(self):
        return self._server_process.pid

    def resolve(self, proxy):
        if proxy.prime_id in self._proxy_cache:
            # Replace the proxy with the cached version so that
            # proxyect identity tests match the server
            return self._proxy_cache[proxy.prime_id]
        else:
            proxy.attach_to_client(self)
            self._proxy_cache[proxy.prime_id] = proxy
            return proxy

    def execute(self, delegate):
        # Dispatch the delegate
        # TODO: can the queue itself throw exceptions?
        print 'client putting object of type %s onto delegate queue' % str(delegate.__class__)
        self._delegate_queue.put(delegate)
        print 'executing...'

        # Get the result
        # TODO: can the queue itself throw exceptions?
        try:
            self._check_alive()
            self._waiting_for_result = True
            self._check_alive()
            result = self._result_queue.get()
            self._check_alive()
        finally:
            self._waiting_for_result = False

        print 'got result: '+raw_repr(result)

        # Unpack any exception raised on the server side
        if isinstance(result, ExceptionalResult):
            print 'got an exception result'
            raise result.exception

        # Unpack any types
        if isinstance(result, TypeProxyBlueprint):
            result = TypeProxy(result)
            # make sure to pass this through the check below too...

        # Replace with a cached proxy if we have one and attach it to the client environment
        if isinstance(result, Proxy):
            print 'Resolving at client: '+raw_repr(result)
            return self.resolve(result)
        else:
            return result

    def finish(self):
        # Make sure the SIGCHLD signal handler doesn't throw any exceptions
        self._finishing = True

        # Do not call execute() because that function will check
        # whether the process is alive and throw an exception if not
        # TODO: can the queue itself throw exceptions?
        self._delegate_queue.put(TerminateDelegate())

        # Wait for acknowledgement
        # TODO: can the result queue throw an exception
        result = self._result_queue.get()


class IsolationContext(object):
    def __init__(self):
        self._client = None

    @property
    def client(self): return self._client

    def start_subprocess(self):
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
        if self._client is None:
            self.start_subprocess()

    def import_isolated(self, module_name):
        self.ensure_started()
        return self.client.execute(ImportDelegate(module_name, sys.path))




def default_context():
    if not hasattr(default_context, '_instance'):
        default_context._instance = IsolationContext()
    return default_context._instance

def import_isolated(module_name):
    return default_context().import_isolated(module_name)













