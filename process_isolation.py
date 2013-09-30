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

# TODO
# - Implement docstring copying
# - Omit __len__, __call__, __dir__, etc whenever the prime doesn't offer them
# - Catch exceptions on server side
# - Make unittest restart server if it ever crashes
# - Add an API to restart the server if it goes down
#   - Make it possible to automatically restart the server?
# - Implement reloading
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

# These attributes should never be overriden
SPECIAL_ATTRIBUTES = [ 
    '__class__',
    '__base__',
    '__bases__',
    '__getattribute__',
    '__getattr__',
    '__process_isolation_id__',
    ]



class ChildProcessTerminationHandler(object):
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
    def __init__(self, id):
        super(ObjectProxy,self).__init__(id)

    # To be run at client end:
    def __getattr__(self, attrname):
        if attrname in ['_client'] or attrname.startswith('__'):
            return super(ObjectProxy,self).__getattribute__(attrname)
        else:
            return self.client.execute(GetAttrDelegate(self._id, attrname))

    def __setattr__(self, attrname, val):
        if attrname in ['_id', '_client'] or attrname.startswith('__'):
            return super(ObjectProxy,self).__setattr__(attrname, val)
        else:
            return self.client.execute(SetAttrDelegate(self._id, attrname, val))

    def __delattr__(self, attrname):
        if attrname in ['_id', '_client']:
            return super(ObjectProxy,self).__detattr__(attrname)
        else:
            return self.client.execute(DelAttrDelegate(self._id, attrname))

    # TODO: use metaclass magic to omit this for objects that are not callable
    def __call__(self, *args, **kwargs):
        return self.client.execute(CallDelegate(self._id, *args, **kwargs))

    # TODO: use metaclass magic to omit this for objects that are not callable
    def __len__(self):
        return self.client.execute(FreeFuncDelegate(len, self._id))

class ExceptionProxy(Exception,Proxy):
    def __init__(self, id):
        Proxy.__init__(self, id)


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

class FreeFuncDelegate(PrimeDelegate):
    def __init__(self, func, obj_id, *args, **kwargs):
        super(FreeFuncDelegate, self).__init__(obj_id)
        self._func = func
        self._args = args
        self._kwargs = kwargs
    def run_on_server(self):
        return self._func(self.prime, *self._args, **self._kwargs)




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

# Reasons to re-implement pickle:
# - support auto-wrapping of nested objects
# - support lazily constructing proxies on client side and caching them
# - support transporting code and types directly
# - could dynamically create classes on server side and transport them to client side
# - pickle can't transport tracebacks

class Registry(dict):
    def wrap(self, obj):
        if isinstance(obj, (types.FunctionType, types.MethodType)):  # do _not_ use callable(...) here
            self[id(obj)] = obj
            return FunctionProxy(id(obj))

        elif isinstance(obj, (types.ModuleType, types.FileType)):
            self[id(obj)] = obj
            return ObjectProxy(id(obj))

        elif isinstance(obj, type):
            self[id(obj)] = obj
            return TypeProxy(obj)

        elif isinstance(obj, BaseException):
            if type(obj).__module__ in ('exceptions', '__builtin__'):
                # TODO: check that we can safely transport all standard exceptions
                return obj
            else:
                self[id(obj)] = obj
                return ExceptionProxy(id(obj))

        elif type(obj).__module__ != '__builtin__':
            self[id(obj)] = obj
            return ObjectProxy(id(obj))

        else:
            return obj


class TypeProxy(type):
    def attach_to_client(cls, client):
        cls._client = client

    def new_instance(cls, *args, **kwargs):
        return self._client.CallDelegate(self._class_id, *args, **kwargs)
    
    def __new__(cls, prime_class):
        # TODO: find or create proxies for the base classes of prime_class
        bases = (ObjectProxy,)
        members = {'__new__': TypeProxy.new_instance,
                   '_class_id': id(prime_class),
                   '_client': None
                   }
        return super(TypeProxy,cls).__new__(prime_class.__name__, bases, members)



# NOT CURRENTLY USED...
def make_proxy_type(cls, registry):
    if cls in (type, object):
        return cls
    elif id(cls) in registry:
        return registry[ id(cls) ]
    else:
        proxy_name = cls.__name__ + 'Proxy'
        proxy_bases = tuple([ make_proxy_type(base, registry) for base in cls.__bases__ ])
        proxy_cls = type(proxy_name, proxy_bases, {})

        # Our proxy must have certain overrides
        if '__new__' not in proxy_class.__dict__:
            proxy_cls.__new__ = ProxyDescriptor('__new__')
        if '__dir__' not in proxy_class.__dict__:
            proxy_cls.__dir__ = proxied_instance_method(dir)
        if '__len__' in cls.__dict__ and '__len__' not in proxy_class.__dict__:
            proxy_cls.__len__ = proxied_instance_method(len)

        proxy_cls.__process_isolation_id__ = id(cls)
        proxy_class.__getattribute__ = proxy_getattribute
        proxy_class.__delattr__ = proxy_delattribute
        proxy_class.__setattr__ = proxy_setattribute

        registry[ id(cls) ] = proxy_cls
        return proxy_cls


class Server(object):
    '''Represents the server that listens for delegates and runs them.'''
    def __init__(self, delegate_queue, result_queue):
        self._delegate_queue = delegate_queue
        self._result_queue = result_queue
        self._registry = Registry()

    def loop(self):
        terminate_flag = False
        while not terminate_flag:
            # Get the next delegate
            delegate = self._delegate_queue.get()

            # Attach the delegate to the server environment
            delegate.attach_to_server(self)

            try:
                # Run the delegate in the local environment and wrap the result
                result = self._registry.wrap(delegate.run_on_server())
            except TerminateProcess:
                # This exception indicates that the client requested that we terminate
                result = True
                terminate_flag = True
            except:
                # Any other exception gets transported back to the client
                ex_type, ex_value, ex_traceback = sys.exc_info()
                result = ExceptionalResult(self._registry.wrap(ex_value), None)
                # TODO: find a way to transport a traceback (pickle can't serialize it)

            # Send the result to the client
            self._result_queue.put(result)



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
        ChildProcessTerminationHandler.register_listener(server_process, self._on_sigchld)

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
        self._delegate_queue.put(delegate)

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

        # Unpack any exception raised on the server side
        if isinstance(result, ExceptionalResult):
            raise result.exception

        # Replace with a cached proxy if we have one and attach it to the client environment
        if isinstance(result, Proxy):
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
    if default_context._instance is None:
        default_context._instance = IsolationContext()
    return default_context._instance
default_context._instance = None

def import_isolated(module_name):
    return default_context().import_isolated(module_name)













