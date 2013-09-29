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

# TEMP HACK
sys.setrecursionlimit(40)

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
    def attach_to_client(self, client):
        self._client = client
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
    def __init__(self, _id):
        self._id = _id
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
        self._id = id
        self._client = None
    # To be run at client end:
    def __getattr__(self, attrname):
        if attrname in ['_client'] or attrname.startswith('__'):
            return super(type(self),self).__getattr(attrname)
        else:
            return self.client.submit(GetAttrDelegate(self._id, attrname))
    def __setattr__(self, attrname, val):
        if attrname in ['_id', '_client'] or attrname.startswith('__'):
            return super(type(self),self).__setattr__(attrname, val)
        else:
            return self.client.submit(SetAttrDelegate(self._id, attrname, val))
    def __delattr__(self, attrname):
        if attrname in ['_id', '_client']:
            return super(type(self),self).__detattr__(attrname)
        else:
            return self.client.submit(DelAttrDelegate(self._id, attrname))

class GetAttrDelegate(PrimeDelegate):
    def __init__(self, _id, attrname):
        super(type(self),self).__init__(_id)
        self._attrname = attrname
    def run_on_server(self):
        return getattr(self.prime, self._attrname)

class SetAttrDelegate(PrimeDelegate):
    def __init__(self, _id, attrname, val):
        super(type(self),self).__init__(_id)
        self._attrname = attrname
        self._val = val
    def run_on_server(self):
        return setattr(self.prime, attrname, val)

class DelAttrDelegate(PrimeDelegate):
    def __init__(self, _id, attrname):
        super(type(self),self).__init__(_id)
        self._attrname = attrname
    def run_on_server(self):
        return delattr(self.prime, attrname)





class FunctionProxy(Proxy):
    '''A proxy for a server-side function.'''
    # To be run at server end:
    def __init__(self, id):
        self._id = id
        self._client = None
    # To be run at client end:
    def __call__(self, *args, **kwargs):
        return self.client.submit(FunctionCallDelegate(self._id, *args, **kwargs))

class FunctionCallDelegate(PrimeDelegate):
    def __init__(self, function_id, *args, **kwargs):
        super(type(self), self).__init__(function_id)
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



# Reasons to re-implement pickle:
# - support auto-wrapping of nested objects
# - support lazily constructing proxies on client side and caching them
# - support transporting code and types directly

class Registry(dict):
    def wrap(self, obj):
        if isinstance(obj, types.FunctionType):  # do _not_ use callable(...) here
            self[id(obj)] = obj
            return FunctionProxy(id(obj))
        elif isinstance(obj, types.ModuleType):
            self[id(obj)] = obj
            return ObjectProxy(id(obj))
        elif type(obj).__module__ != '__builtin__':
            self[id(obj)] = obj
            return ObjectProxy(id(obj))
        else:
            return obj



class Server(object):
    '''Represents the server that listens for delegates and runs them.'''
    def __init__(self, delegate_queue, result_queue):
        self._delegate_queue = delegate_queue
        self._result_queue = result_queue
        self._registry = Registry()

    def loop(self):
        while True:
            # Get the next delegate
            delegate = self._delegate_queue.get()

            # Allow the delegate to run in the server environment
            delegate.attach_to_server(self)

            # TODO: catch exceptions
            try:
                result = delegate.run_on_server()
            except TerminateProcess as ex:
                # Indicates that the client requested that we terminate
                self._result_queue.put(True)
                return

            # If necessary, replace the result with a proxy
            wrapped_result = self._registry.wrap(result)

            # Return the result
            self._result_queue.put(wrapped_result)



class ProcessTerminationError(Exception):
    def __init__(self, signal_or_returncode):
        self._signal_or_returncode = signal_or_returncode

class Client(object):
    '''Represents a client that sends delegates and listens for results.'''
    def __init__(self, server_process, delegate_queue, result_queue):
        assert server_process.is_alive()
        self._delegate_queue = delegate_queue
        self._result_queue = result_queue
        self._server_process = server_process
        self._waiting_for_result = False
        self._finishing = False
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
        # process is_alive() flag will be checked next time submit()
        # is called
        if self._waiting_for_result:
            raise ProcessTerminationError(self._server_process._popen.returncode)

    def _check_alive(self):
        if not self._server_process.is_alive():
            raise ProcessTerminationError(self._server_process._popen.returncode)

    def _pop(self):
        while True:
            try:
                # Note that the result could be None!
                return self._result_queue.get(timeout=1)
            except Queue.Empty as ex:
                pass
            

    def submit(self, delegate):
        # Dispatch the delegate
        # TODO: can the queue itself throw exceptions?
        self._delegate_queue.put(delegate)

        # Get the result
        # TODO: handle exceptions passed within result
        # TODO: can the queue itself throw exceptions?
        try:
            self._check_alive()
            self._waiting_for_result = True
            self._check_alive()
            result = self._pop()
            #result = self._result_queue.get()
            self._check_alive()
        finally:
            self._waiting_for_result = False

        # Attach the result to the client environment
        # TODO: replace result with an earlier proxy for the same object if one exists
        # TODO: find any other proxy objects within result that should be attached
        if isinstance(result, Proxy):
            result.attach_to_client(self)

        # Return the result
        return result

    def finish(self):
        # Make sure the SIGCHLD signal handler doesn't throw any exceptions
        self._finishing = True

        # Do not call submit() because that function will check
        # whether the process is alive and throw an exception if not
        # TODO: can the queue itself throw exceptions?
        self._delegate_queue.put(TerminateDelegate())

        # Wait for acknowledgement
        # TODO: can the result queue throw an exception
        result = self._pop()
        #result = self._result_queue.get()

    def import_remotely(self, module_name):
        return self.submit(ImportDelegate(module_name, sys.path))

    def _atexit(self):
        if self._server_process.is_alive():
            try:
                self.finish()
                # TODO: handle timeouts and exceptions from join() below
                self._server_process.join()
            except ProcessTerminationError as ex:
                # Ignore it for now
                pass

def launch_server_process():
    # Create the queues
    request_queue = multiprocessing.Queue()
    response_queue = multiprocessing.Queue()

    # Launch the server process
    server = Server(request_queue, response_queue)
    server_process = multiprocessing.Process(target=server.loop)
    server_process.start()

    # Create a client to talk to the server
    return Client(server_process, request_queue, response_queue)



def client_singleton():
    if client_singleton._instance is None:
        client_singleton._instance = launch_server_process()
    return client_singleton._instance
client_singleton._instance = None


def import_isolated(module_name):
    client = client_singleton()
    module = client.import_remotely(module_name)
    module.__process_isolation_client__ = client
    return module


class HardExit(Delegate):
    def run_on_server(self):
        sys.exit(0)

import somemodule
somemodule_isolated = import_isolated('somemodule')

def test_proxy():
    D = collections.OrderedDict(local=somemodule, remote=somemodule_isolated)
    for name,mod in D.iteritems():
        print name,'version:'
        print 'mod.x:'
        print mod.x
        print 'mod.foo():'
        print mod.foo()
        print 'mod.bar(2,3):'
        print mod.bar(2,3)
        print 'w = mod.Woo():'
        w = mod.Woo()
        print 'w.hoo():'
        print w.hoo()
        print 'mod.get():'
        print mod.get()
        print 'mod.incr():'
        mod.incr()
        print 'mod.get():'
        print mod.get()
        print 'g = mod.Getter():'
        g = mod.Getter()
        print 'g.get():'
        print g.get()
        print '\n'

    print 'attempting hard abort...'
    try:
        somemodule_isolated.hard_abort()
        print 'returned'
    except ProcessTerminationError as ex:
        print 'caught segfault: ',str(ex)
            


        






def onsignal(signal, stackframe):
    print 'got signal',signal

def test_kill():
    signal.signal(signal.SIGCHLD, onsignal)
    somemodule_isolated.__process_isolation_client__.submit(HardExit())


def child_proc():
    print 'Child started...'
    time.sleep(1)
    print 'Child finishing...'

def raise_exception(signum, traceback):
    raise Exception('signal caught!')

def test_exception():
    signal.signal(signal.SIGCHLD, raise_exception)
    multiprocessing.Process(target=child_proc).start()
    try:
        time.sleep(3)
    except Exception as ex:
        print 'caught "%s"' % str(ex)

if __name__ == '__main__':
    test_proxy()
    #test_kill()
    #test_exception()
