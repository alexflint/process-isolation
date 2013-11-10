from process_isolation import *
import unittest
import time
import inspect

class ProxyTest(unittest.TestCase):
    def setUp(self):
        print '\n\nRunning test case: %s\n' % self.id()
        self.ctx = IsolationContext()
        self.mod = self.ctx.load_module('somemodule')

    def _tearDown(self):
        del self.mod
        self.ctx.client.cleanup()

    def test_var(self):
        self.assertEqual(self.mod.x, 55)

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

    def test_standard_exception(self):
        with self.assertRaisesRegexp(Exception, 'foobar'):
            self.mod.raise_standard_exception()

    def test_two_copies_of_class(self):
        c1 = self.mod.SomeClass
        c2 = self.mod.SomeClass
        assert c1 is c2

    def test_instance(self):
        inst = self.mod.SomeClass(11)
        assert inst.x == 11

    def test_class_identity(self):
        obj = self.mod.make_instance()
        assert isinstance(obj, self.mod.SomeClass)
        assert isinstance(obj, self.mod.SomeBase)

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

    def test_iterator(self):
        a = self.mod.ObjectWithItems(5)
        self.assertItemsEqual(a, list(iter(a)))
        
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

    def test_call(self):
        adder = self.mod.AddOneHundred()
        self.assertEqual(adder(3), 103)


class ImportTest(unittest.TestCase):
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
        

class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.ctx = IsolationContext()
        self.mod = self.ctx.load_module('somemodule')

    def test_remote_crash(self):
        with self.assertRaises(ProcessTerminationError):
            self.mod.hard_abort()

    def test_method_call_after_remote_crash(self):
        with self.assertRaises(ProcessTerminationError):
            self.mod.hard_abort()
        with self.assertRaises(ClientStateError):
            self.mod.foo()

    def test_restart(self):
        self.assertEqual(self.mod.count_calls(), 1)

        #print '\n\nunittest calling context.restart()...'
        #self.ctx.restart()
        #print '\n\nunittest calling count_calls() again...'
        #self.assertEqual(self.mod.count_calls(), 1)

        ctx2 = IsolationContext()
        mod2 = ctx2.load_module('somemodule')
        self.assertEqual(mod2.count_calls(), 1)

    def test_foo(self):
        self.mod.foo()

    
class ByValueTest(unittest.TestCase):
    def setUp(self):
        self.ctx = IsolationContext()
        self.mod = self.ctx.load_module('somemodule')
    
    def test_by_value(self):
        x = self.mod.Woo()
        self.assertTrue(isproxy(x))
        y = byvalue(x)
        self.assertFalse(isproxy(y))

    def test_by_value_on_non_proxy(self):
        with self.assertRaises(AssertionError):
            byvalue(123)


if __name__ == '__main__':
    unittest.main()
