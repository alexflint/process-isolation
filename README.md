### Process Isolation in Python

Process isolation is a simple and elegant tool that lets you run
python modules in sub-processes.

```
import process_isolation
sys = process_isolation.import_isolated('sys')
sys.stdout.write('Hello world\n')
````

A few things happened here:

1. We imported process_isolation:
    ```
    import process_isolation
    ````

2. The current process forked and the `sys` module was imported in the child process:

        sys = process_isolation.import_isolated('sys')

3. The parent process requested that the child process run `sys.stdout.write('Hello world\n')`:

        sys.stdout.write('Hello world\n')

4. The child process wrote "Hello world\n" to standard output.

## Why process isolation?

## More sophisticated examples

## Copying objects between processes
