import os

x = 55
y = 20

def reportpid(f):
    def wrapper(*args, **kwargs):
        with open('/tmp/lastpid.txt','w') as fd:
            fd.write(str(os.getpid()))
        return f(*args, **kwargs)
    return wrapper

@reportpid
def foo():
    return 2

@reportpid
def bar(a, b):
    return a+b*100

@reportpid
def baz(n):
    print 'n =',n

class Woo(object):
    @reportpid
    def __init__(self):
        pass
    @reportpid
    def hoo(self):
        return 300

@reportpid
def incr():
    global x
    x += 1

@reportpid
def get():
    return x

class Getter(object):
    @reportpid
    def get(self):
        return y
    @reportpid
    def __len__(self):
        return y

@reportpid
def hard_abort():
    print 'about to abort()...'
    os.abort()  # will do a hard exit of the process

def make_range(n):
    return range(n)

class SomeBase(object):
    def get_message(self):
        return "hello world"

class SomeClass(SomeBase):
    @reportpid
    def __init__(self, x):
        self._x = x

    @reportpid
    def printx(self):
        print self._x

    @reportpid
    def get_self(self):
        return self

    @reportpid
    def get_x(self):
        return self._x

    @property
    @reportpid
    def x(self):
        print 'Getting property x at pid=%d' % os.getpid()
        return self._x

def make_instance():
    return SomeClass(22)

@reportpid
def raise_standard_exception():
    raise Exception('foobar')

class CustomException(Exception):
    pass

def raise_custom_exception():
    print 'Raising CustomException now.'
    raise CustomException()

class ObjectWithLength(object):
    def __len__(self):
        return 3

class ObjectWithStr(object):
    def __str__(self):
        return 'this is str'

class ObjectWithRepr(object):
    def __repr__(self):
        return 'this is repr'
