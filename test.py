from process_isolation import *
import unittest

class TestProcessIsolation(unittest.TestCase):
    def setUp(self):
        print '\n\nRunning test case: %s\n' % self.id()
        self.ctx = IsolationContext()
        self.mod = self.ctx.import_isolated('somemodule')

    def tearDown(self):
        del self.mod
        self.ctx.client.terminate()

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

    def test_remote_crash(self):
        self.assertRaises(ProcessTerminationError, self.mod.hard_abort)

    def test_standard_exception(self):
        self.assertRaisesRegexp(Exception, 'foobar', self.mod.raise_standard_exception)

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

    def _test_sequence_iter(self):
        a = self.mod.ObjectWithItems(5)
        self.assertEqual(a, list(iter(a)))
        
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



    def test_custom_exception(self):
        exception_type = self.mod.CustomException
        try:
            self.mod.raise_custom_exception()
        except:
            ex = sys.exc_value
            print 'Unittest caught an exception:'+str(ex)
            print 'isinstance?'
            print isinstance(ex, exception_type)
            print 'instancecheck?'
            print exception_type.__instancecheck__(ex)


if __name__ == '__main__':
    unittest.main()
