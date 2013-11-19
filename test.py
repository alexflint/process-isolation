import sys
import os
import unittest
import logging
import tempfile
import subprocess

from process_isolation import *

class SomeModuleTest(unittest.TestCase):
    def setUp(self):
        self.ctx = IsolationContext()
        self.mod = self.ctx.load_module('somemodule')

    def tearDown(self):
        self.ctx.client.cleanup()
        del self.ctx
        del self.mod

class AccessorTest(SomeModuleTest):
    def test_var(self):
        self.assertEqual(self.mod.y, 20)

    def test_func(self):
        self.assertEqual(self.mod.foo(), 2)
        self.assertEqual(self.mod.bar(2,3), 302)

    def test_member_function(self):
        woo = self.mod.Woo()
        self.assertEqual(woo.hoo(), 300)

    def test_persistent_state(self):
        self.assertEqual(self.mod.get(), 55)
        self.mod.incr()
        self.assertEqual(self.mod.get(), 56)

    def test_globals(self):
        g = self.mod.Getter()
        self.assertEqual(g.get(), 20)

    def test_remote_class(self):
        obj = self.mod.SomeClass(150)
        self.assertEqual(obj._x, 150)
        self.assertEqual(obj.get_x(), 150)
        self.assertEqual(obj.x, 150)
        self.assertEqual(obj.get_self().x, 150)

    def test_identity(self):
        print '\n-> Getting class...'
        cls = self.mod.SomeClass
        print '\n-> Instantiating object...'
        obj = cls(150)
        print '\n-> Calling get_self()...'
        obj2 = obj.get_self()
        print '\n-> Comparing identity (%d vs %d)...' % (id(obj), id(obj2))
        self.assertTrue(obj is obj2)


class ClassTest(SomeModuleTest):
    def test_two_copies_of_class(self):
        c1 = self.mod.SomeClass
        c2 = self.mod.SomeClass
        assert c1 is c2

    def test_instancecheck(self):
        obj = self.mod.make_instance()
        assert isinstance(obj, self.mod.SomeClass)
        assert isinstance(obj, self.mod.SomeBase)

    def test_instance(self):
        inst = self.mod.SomeClass(11)
        self.assertEqual(inst.x, 11)



class ExceptionTest(SomeModuleTest):
    def test_standard_exception(self):
        with self.assertRaisesRegexp(Exception, 'foobar'):
            self.mod.raise_standard_exception()

    def test_sequence_special_funcs(self):
        a = self.mod.ObjectWithItems(10)
        self.assertEqual(len(a), 10)
        a[6] = 10
        self.assertEqual(a[6], 10)
        del a[9]
        self.assertEqual(len(a), 9)
        a[2:6] = [-1,-2,-3,-4]
        self.assertEqual(a[2:6], [-1,-2,-3,-4])
        del a[-3:]
        self.assertEqual(len(a), 6)        

    def _test_custom_exception(self):
        exception_type = self.mod.CustomException
        try:
            self.mod.raise_custom_exception()
        except Exception as ex:
            print 'Unittest caught an exception:'+str(ex)
            #print 'isinstance?'
            #print isinstance(ex, exception_type)
            #print 'instancecheck?'
            #print exception_type.__instancecheck__(ex)
            sys.exc_clear()

class SequenceTest(SomeModuleTest):
    def test_iterator(self):
        a = self.mod.ObjectWithItems(5)
        self.assertItemsEqual(a, list(iter(a)))

    def test_generator(self):
        generator = self.mod.generate_strings()
        self.assertEqual(generator.next(), 'foo')
        self.assertEqual(generator.next(), 'bar')
        with self.assertRaises(StopIteration):
            generator.next()

    def test_generator_to_list(self):
        L = list(self.mod.generate_strings())
        self.assertEqual(L, ['foo','bar'])

    def test_iterate_over_generator(self):
        for i,x in enumerate(self.mod.generate_strings()):
            if i == 0:
                self.assertEqual(x, 'foo')
            elif i == 1:
                self.assertEqual(x, 'bar')
            else:
                self.fail('Got more than two items from generate_strings()')
        
class SpecialFuncTest(SomeModuleTest):
    def test_str_special_funcs(self):
        obj = self.mod.ObjectWithStr()
        self.assertEqual(str(obj), 'this is str')
        self.assertEqual(repr(obj), 'this is repr')

    def test_comparison_special_funcs(self):
        assert self.mod.ObjectWithComparison() < True
        assert not self.mod.ObjectWithComparison() < False

        assert self.mod.ObjectWithComparison() <= True
        assert not self.mod.ObjectWithComparison() <= False

        assert self.mod.ObjectWithComparison() > True
        assert not self.mod.ObjectWithComparison() > False

        assert self.mod.ObjectWithComparison() >= True
        assert not self.mod.ObjectWithComparison() >= False

        assert self.mod.ObjectWithComparison() == True
        assert not self.mod.ObjectWithComparison() == False

        assert self.mod.ObjectWithComparison() != True
        assert not self.mod.ObjectWithComparison() != False

    def test_dir_special_func(self):
        obj = self.mod.ObjectWithDir()
        self.assertItemsEqual(dir(obj), ['foo','bar'])

    def test_docs(self):
        # Test getting documentation from a function
        self.assertEqual(self.mod.documented_func.__doc__, 'foobar')

        # Test getting documentation from the class
        self.assertEqual(self.mod.DocumentedClass.__doc__, 'baz')
        self.assertEqual(self.mod.DocumentedClass.documented_member.__doc__, 'some documentation here')

        # Test getting documentation from an instance
        obj = self.mod.DocumentedClass()
        self.assertEqual(obj.__doc__, 'baz')
        self.assertEqual(obj.documented_member.__doc__, 'some documentation here')

    def test_call(self):
        adder = self.mod.AddOneHundred()
        self.assertEqual(adder(3), 103)



class SpecialObjectTest(SomeModuleTest):
    def test_file(self):
        # Create a temporary file
        with tempfile.NamedTemporaryFile() as temp_fd:
            # Write something to the file
            temp_fd.write('foobar')
            temp_fd.flush()

            # Do not release the file here because it will be deleted
            # Get a proxy to a file descriptor for the same file
            proxy_fd = self.mod.open_file(temp_fd.name)
            self.assertEqual(proxy_fd.read(), 'foobar')

    def test_lambda(self):
        # Get a proxy to a lambda function created on the server and then execute it
        self.assertEqual(self.mod.lambda_101(), 101)
        self.assertEqual(self.mod.get_lambda_101()(), 101)

class ByValueTest(SomeModuleTest):
    def test_byvalue(self):
        x = self.mod.Woo()
        self.assertTrue(isproxy(x))
        y = byvalue(x)
        self.assertFalse(isproxy(y))

    def test_byvalue_on_non_proxy(self):
        with self.assertRaises(AssertionError):
            byvalue(123)

class SerializationTest(SomeModuleTest):
    def test_unpicklable(self):
        # This class is explicitly unpicklable
        with self.assertRaises(AssertionError):
            byvalue(self.mod.Unpicklable())


class ImportTest(unittest.TestCase):
    '''Tests the high level import functions.'''
    def test_load_twice(self):
        self.mod = load_module('somemodule')
        self.mod2 = load_module('somemodule')
        assert self.mod is self.mod2

    def test_import_twice(self):
        self.mod = import_isolated('somemodule')
        self.mod2 = import_isolated('somemodule')
        assert self.mod is self.mod2

    def test_import_then_system_import(self):
        self.mod = import_isolated('somemodule')
        import somemodule
        assert somemodule is self.mod
        

class LifecycleTest(SomeModuleTest):
    '''Tests functionality relating to forking, signalling, and
    managing process state.'''
    def test_remote_crash(self):
        with self.assertRaises(ProcessTerminationError):
            self.mod.hard_abort()

    def test_method_call_after_remote_crash(self):
        with self.assertRaises(ProcessTerminationError):
            self.mod.hard_abort()
        with self.assertRaises(ClientStateError):
            self.mod.foo()

    def test_second_context(self):
        self.assertEqual(self.mod.count_calls(), 1)
        ctx2 = IsolationContext()
        mod2 = ctx2.load_module('somemodule')
        self.assertEqual(mod2.count_calls(), 1)


class SysTest(unittest.TestCase):
    '''Tests involving running the 'sys' module in an isolated context.'''
    def setUp(self):
        self.isolated_sys = load_module('sys')

    def test_maxint(self):
        self.assertEqual(self.isolated_sys.maxint, sys.maxint)

    def test_platform(self):
        self.assertEqual(self.isolated_sys.platform, sys.platform)

    def _test_flags(self):
        self.assertEqual(self.isolated_sys.flags, sys.flags)

class OsTest(unittest.TestCase):
    '''Tests involving running the 'sys' module in an isolated context.'''
    def setUp(self):
        self.isolated_os = load_module('os')

    def test_remove(self):
        # Create a temporary dir
        temp_fd = tempfile.NamedTemporaryFile(delete=False)
        temp_fd.close()

        self.isolated_os.remove(temp_fd.name)
        self.assertFalse(os.path.exists(temp_fd.name))

   
if __name__ == '__main__':
    # Log to stdout
    logger.addHandler(logging.StreamHandler(sys.stdout))
    logger.setLevel(logging.DEBUG)

    if len(sys.argv) == 1:
        # With no arguments, run each test case in a separate python
        # interpreter to avoid problems with too many files open.
        thismodule = sys.modules[__name__]
        keys = list(thismodule.__dict__.iterkeys())
        for key in keys:
            item = getattr(thismodule, key)
            if isinstance(item, type) and issubclass(item, unittest.TestCase):
                cmd = ['python', 'test.py', key]
                sys.stderr.write('\n*** %s ***\n' % key)
                subprocess.call(cmd)
    else:
        # With no arguments, run each test case in a separate python
        # interpreter to avoid problems with too many files open.
        unittest.main()
