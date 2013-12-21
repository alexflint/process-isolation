import sys
import os

x = 55
y = 20
n = 0

def foo():
    return 2

def count_calls():
    global n
    n += 1
    return n

def bar(a, b):
    return a+b*100

def baz(n):
    print 'n =',n

class Woo(object):
    def __init__(self):
        pass
    def hoo(self):
        return 300

def incr():
    global x
    x += 1

#@reportpid
def get():
    return x

class Getter(object):
    def get(self):
        return y
    def __len__(self):
        return y

def hard_abort():
    print 'about to abort()...'
    os.abort()  # will do a hard exit of the process

def make_range(n):
    return range(n)

class Unpicklable(object):
    def __getstate__(self):
        raise Exception('You attempted to pickle a remote object')

class SomeBase(Unpicklable):
    def get_message(self):
        return "hello world"

class SomeClass(SomeBase):
    def __init__(self, x):
        self._x = x
    def printx(self):
        print self._x
    def get_self(self):
        return self
    def get_x(self):
        return self._x
    @property
    def x(self):
        print 'Getting property x at pid=%d' % os.getpid()
        return self._x

def make_instance():
    return SomeClass(22)

def raise_standard_exception():
    raise Exception('foobar')

class CustomException(Exception):
    def __str__(self):
        return '<CustomException object>'

def raise_custom_exception():
    print 'Raising CustomException now.'
    raise CustomException()

class EmptyObject(Unpicklable):
    pass

class ObjectWithItems(Unpicklable):
    def __init__(self, n):
        self._a = [0]*n
    def __len__(self):
        return len(self._a)
    def __getitem__(self, i):
        return self._a[i]
    def __setitem__(self, i, v):
        self._a[i] = v
    def __delitem__(self, i):
        print 'Object with items got delitem: '+str(i)
        del self._a[i]
    def __iter__(self):
        return iter(self._a)

class ObjectWithStr(Unpicklable):
    def __str__(self):
        return 'this is str'
    def __repr__(self):
        return 'this is repr'

class ObjectWithComparison(Unpicklable):
    def __lt__(self, other):
        return bool(other)
    def __le__(self, other):
        return bool(other)
    def __gt__(self, other):
        return bool(other)
    def __ge__(self, other):
        return bool(other)
    def __eq__(self, other):
        return bool(other)
    def __ne__(self, other):
        return bool(other)

class ObjectWithCall(Unpicklable):
    def __call__(self, *args):
        return args[::-1]

class ObjectWithDir(Unpicklable):
    def __dir__(self):
        return ['foo','bar']

def documented_func():
    '''foobar'''
    pass

class DocumentedClass(object):
    '''baz'''
    def documented_member(self):
        '''some documentation here'''
        pass

class AddOneHundred(object):
    def __call__(self, x):
        return x + 100

def generate_strings():
    yield 'foo'
    yield 'bar'

def open_file(path):
    return open(path)

lambda_101 = lambda: 101

def get_lambda_101():
    return lambda: 101
