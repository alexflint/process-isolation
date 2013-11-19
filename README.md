# Process Isolation in Python

Process isolation is a simple and elegant tool that lets you run
python modules in sub-processes.

```
from process_isolation import import_isolated
sys = import_isolated('sys')
sys.stdout.write('Hello world\n')
````

A few things happened here:

1. We imported the `process_isolation` module:
    ```
    from process_isolation import import_isolated
    ````

2. The process forked and the `sys` module was imported in the child process:
    ```
    sys = import_isolated('sys')
    ```

3. The parent process requested that the child process run `sys.stdout.write('Hello world\n')`:
    ```
    sys.stdout.write('Hello world\n')
    ```

4. The child process wrote `Hello world` to standard output.


One reason tun run code in an isolated process is to debug code that
crashes at the operating system level with a segmentation fault or
other signal. Here is some dangerous code:

```
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

We can safely run this code inside an isolated process, and do
something when it crashes:

```
from process_isolation import import_isolated, ProcessTerminationError
buggy = import_isolated('buggy')
try:
    buggy.dragons_here()
except ProcessTerminationError as ex:
    print 'There be dragons!'
````

### Using process isolation

`process_isolation` tries to be as transparent as possible. In many
cases it is possible to simply replace 

    import X

with 

    X = import_isolated('X')

and leave all other code unchanged. `process_isolation` shuttles data
back and forward between the main python interpreter and the forked
child process, using proxies on the client side in place of objects
that actually reside inside an isolated sub-process.

### Caveats

### Why process isolation?

### More sophisticated examples

### Copying objects between processes

### Under the hood

