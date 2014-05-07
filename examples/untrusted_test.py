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

def main():
    import os
    import sys
    import untrusted
    import process_isolation

    try:
        context = process_isolation.default_context()
        context.ensure_started()
        context.client.call(os.chroot, '/tmp/foob')
        untrusted = context.load_module('untrusted', path=['.'])
        print untrusted.ls_root()
    except OSError:
        print 'This script must be run with superuser priveleges'
