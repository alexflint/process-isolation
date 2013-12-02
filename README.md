# Process Isolation in Python

Process isolation is a simple and elegant tool that lets you run
python modules in sub-processes.

```python
from process_isolation import import_isolated
sys = import_isolated('sys')
sys.stdout.write('Hello world\n')
```

A few things happened here:

1. We imported the `process_isolation` module:
    ```python
    from process_isolation import import_isolated
    ````

2. A child process was forked off from the main python process and the
   `sys` module was imported in that process:
    ```python
    sys = import_isolated('sys')
    ```

3. The main python process requested that the child process run
    `sys.stdout.write('Hello world\n')`:
    ```python
    sys.stdout.write('Hello world\n')
    ```

4. The child process wrote `Hello world` to standard output.


One reason to run code in an isolated process is to debug code that
might crash at the C level (i.e. with a segmentation fault or similar
rather than a python exception). Here is some dangerous code:

```python
# buggy.py:

import types
def dragons_here():
    types.FunctionType(types.CodeType(0, 0, 1, 0, 'd\x00\x00S', (), (), (), '', '', 1, ''),{})()
```

Running this code causes a hard abort (not a regular python exception),
which makes it difficult to debug:

```
>>> import buggy
>>> buggy.dragons_here()
Segmentation fault: 11
```

However, inside an isolated process we can safely run this without our
whole python interpreter crashing:

```python
from process_isolation import import_isolated, ProcessTerminationError
buggy = import_isolated('buggy')
try:
    buggy.dragons_here()
except ProcessTerminationError as ex:
    print 'There be dragons!'
```

### Using process isolation

`process_isolation` tries to be invisible whenever possible. In many
cases it is possible to simply replace

    import X

with 

    X = import_isolated('X')

and leave all other code unchanged. 

Internally, `process_isolation` shuttles data back and forward between
the main python interpreter and the forked child process. When you
call a function from an isolated module, that function runs in the
isolated child process.

```python
os = import_isolated('os')
os.rmdir('/tmp/foo')  # this function will run in the isolated child process
```

The same is true when you instantiate a class -- after all, a
constructor is really just a special kind of function.

```python
collections = import_isolated('collections')
my_dict = collections.OrderedDict()
```

This code creates an `OrderedDict` object residing in the isolated
process. To make sure the isolated process really is isolated, the
`OrderedDict` will stay in the child process forever. `my_dict` is
actually a proxy object that will shuttle member calls back and forth
to the child process. For all intents and purposes, you can treat
`my_dict` just like a real `OrderedDict`.

```python
my_dict['abc'] = 123
print my_dict['abc']
print my_dict.viewvalues()
for key,value in my_dict.iteritems():
    print key,value

try:
    x = my_dict['xyz']
except KeyError:
    print 'The dictionary has no 
```

Under the hood, each of these calls involves some shuttling of data
back and forth between the child and server process. If anything were
to crash along the way, you would get a `ProcessTerminatedError`
instead of a hard crash, but other than that, everything should work
exactly as if there were no process isolation involved.

### Copying objects between processes

Sometimes this proxying behaviour can be inconvenient or
inefficient. To get a copy of the real object behind the proxy, use
`byvalue`:

```python
from process_isolation import import_isolated, byvalue
collections = import_isolated('collections')
proxy_to_a_dict = collections.OrderedDict({'fred':11, 'tom':12})
the_real_deal = byvalue(collections.OrderedDict({'fred':11, 'tom':12}))

print type(proxy_to_a_dict)
print type(the_real_deal)
```

Which prints:

```
>>> process_isolation.ObjectProxy
>>> collections.OrderedDict
```

Some caveats to using `byvalue` are:

1. All calls to members of `the_real_deal` will now execute in the
main python interpreter, so if one of those members causes a segfault
then the main python interpreter will crash, just as if you ran the
whole thing without involving `process_isolation` at all.

2. `byvalue` copies an object from the child process to the main
python interpreter, with the usual semantics of deep copies. Any
references to the original object will continue to refer to the
original object. If the original object is changed, those changes will
not show up in the copy residing in main python interpreter, and vice
versa.

### Why process isolation?

**Dealing with misbehaving C modules**

We originally built `process_isolation` to help benchmark a computer
vision library written in C. We had built a python interface to the
underlying C library using boost python and we wanted to use python to
manage the datasets, accumulate success rates, generate reports, and
so on. During development, it was not uncommon for our C library to
crash from time to time, but instead of getting an empty report
whenever any one test cases caused a crash, we wanted to record
exactly which inputs caused the crash, and then continue to run the
remaining tests. We built `process_isolation` and used it to run all
the computer vision code in an isolated process, which allowed us to
give detailed error reports when something went wrong at the C level,
and to continue running the remaining tests afterwards.

We were also running our computer vision code interactively from
ipython. However, importing the computer vision module directly meant
that a crash at the C level would destroyed the entire ipython session
and all the working variables along with it. Anyone who has done
interactive experiments with numerical software will appreciate the
frustration of losing an hour of carefully constructed matrices just
before the command that would have completed whatever experiment was
being run. Using `process_isolation` from ipython avoided this
possibility in a very robust way. At worst case, a command would raise
a `ProcessTerminationError`, but all the variables and other session
state would remain intact.

**Running untrusted code**

Although there are many ways of running untrusted code in python, the
most secure way is to use a restricted environment enforced by the
operating system. `process_isolation` is ideal for running some code
in a subprocess. Here is how to create a `chroot` jail. 

First we create an "untrusted" module to experiment with.

```python
# untrusted.py: untrusted code lives here
import os
def ls_root():
    return os.listdir('/')
```

Next we set up the chroot jail. Note that this code must be run with
superuser priveleges because the `chroot` system call requires
superuser priveleges.

```python
# run_untrusted_code.py
import os
import process_isolation

# Start a subprocess but do not import the untrusted module until we've installed the chroot jail
context = process_isolation.default_context()
context.ensure_started()

# Create a directoy in which to jail the untrusted module
os.mkdir('/tmp/chroot_jail')

# Create a file inside the chroot so that we can recognize the jail when we see it
with open('/tmp/chroot_jail/you_are_in_jail_muahaha','w'):
    pass

try:
    # Install the chroot
    context.client.call(os.chroot, '/tmp/chroot_jail')
except OSError:
    print 'This script must be run with superuser priveleges'

# Now we can safely import and run the untrusted module
untrusted = context.load_module('untrusted', path=['.'])
print untrusted.ls_root()

# Clean up
os.remove('/tmp/chroot_jail/you_are_in_jail_muahaha')
os.rmdir('/tmp/chroot_jail')
```

```python
$ sudo python run_untrusted_code.py
['you_are_in_jail_muahaha']
```

<!--

**Reloading binary modules**

Check back soon

**Running unittests in separate processes**

Check back soon

### Under the hood

Check back soon

-->
