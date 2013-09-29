import os

x = 3

def foo():
    return 2

def bar(a, b):
    return a+b

def baz(n):
    print 'n =',n

class Woo(object):
    def hoo(self):
        return 30000

def incr():
    global x
    x += 1

def get():
    return x

class Getter(object):
    def get(self):
        return x
    def __len__(self):
        return x

def hard_abort():
    print 'executing hard abort...'
    os.abort()  # will do a hard exit of the process
