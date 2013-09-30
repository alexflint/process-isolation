from process_isolation import *
import unittest

class HardExit(Delegate):
    def run_on_server(self):
        sys.exit(0)

class TestProcessIsolation(unittest.TestCase):
    def setUp(self):
        print 'setup'
        self.ctx = IsolationContext()
        self.mod = self.ctx.import_isolated('somemodule')

    def lastpid(self):
        with open('/tmp/lastpid.txt','r') as fd:
            return int(fd.read())

    def assert_remote(self):
        self.assertEqual(self.lastpid(), self.ctx.client.pid)

    def assert_local(self):
        self.assertEqual(self.lastpid(), os.getpid())

    def test_var(self):
        self.assertEqual(self.mod.x, 55)

    def test_func(self):
        self.assertEqual(self.mod.foo(), 2)
        self.assert_remote()

        self.assertEqual(self.mod.bar(2,3), 302)
        self.assert_remote()

    def test_member_function(self):
        woo = self.mod.Woo()
        self.assert_remote()

        self.assertEqual(woo.hoo(), 300)
        self.assert_remote()

    def test_persistent_state(self):
        self.assertEqual(self.mod.get(), 55)
        self.assert_remote()
        self.mod.incr()
        self.assert_remote()
        self.assertEqual(self.mod.get(), 56)
        self.assert_remote()

    def test_globals(self):
        g = self.mod.Getter()
        self.assertEqual(g.get(), 20)
        self.assert_remote()

    def test_len(self):
        g = self.mod.Getter()
        self.assertEqual(len(g), 20)
        self.assert_remote()

    def test_remote_class(self):
        obj = self.mod.SomeClass(150)
        self.assertEqual(obj._x, 150)
        self.assertEqual(obj.get_x(), 150)
        self.assertEqual(obj.x, 150)
        self.assertEqual(obj.get_self().x, 150)
        self.assert_remote()

    def test_identity(self):
        obj = self.mod.SomeClass(150)
        self.assertTrue(obj is obj.get_self())

    def test_remote_crash(self):
        self.assertRaises(ProcessTerminationError, self.mod.hard_abort)

    def test_standard_exception(self):
        self.assertRaisesRegexp(Exception, 'foobar', self.mod.raise_standard_exception)

    def test_custom_exception(self):
        print self.mod.CustomException
        self.assertRaises(self.mod.CustomException,
                          self.mod.raise_custom_exception)

if __name__ == '__main__':
    unittest.main()
