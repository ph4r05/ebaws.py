import os
import util
from sarge import run, Capture, Feeder
from ebclient.eb_utils import EBUtils
from datetime import datetime
import time
import sys
import types
import subprocess
import shutil
import re
import psutil
import math
import consts


__author__ = 'dusanklinec'


class SysConfig(object):
    """Basic system configuration object"""

    def __init__(self, print_output=False, *args, **kwargs):
        self.print_output = print_output
        pass

    def get_virt_mem(self):
        return psutil.virtual_memory().total

    def get_swap_mem(self):
        return psutil.swap_memory().total

    def get_total_usable_mem(self):
        """
        Virtual Memory + Swap
        :return:
        """
        return self.get_virt_mem() + self.get_swap_mem()

    def is_enough_ram(self):
        """
        Return true if there is at least 2GB RAM available (RAM+SWAP)
        :return:
        """
        return self.get_total_usable_mem() >= 1024*1024*1024*1.6

    def get_swap_size_needed(self):
        """
        Returns number of bytes a swapfile should have so we can finish the installation.
        Minimally we add 1GB of swap.
        :return:
        """
        base = 1024*1024*1024

        # If virt mem is < 1GB, add the difference
        virt_mem = self.get_virt_mem()
        if virt_mem < 1024*1024*1024*0.9:
            base += 1024*1024*1024 - virt_mem

        return base

    def create_swap(self, swap_file='/var/swap.bin', desired_size=None):
        """
        If size is none, optimal size is computed.

        dd if=/dev/zero of=/var/swap.1 bs=1M count=1024
        chmod 0600 /var/swap.1
        mkswap /var/swap.1
        swapon /var/swap.1
        echo '/var/swap.1 swap swap defaults 0 0' >> /etc/fstab
        :return:
        """
        if desired_size is None:
            desired_size = self.get_swap_size_needed()

        size_in_mb = int(math.ceil(desired_size/1024/1024))

        # Get unique swap file name
        fhnd, fname = util.unique_file(swap_file, mode=0o600)
        path, tail = os.path.split(fname)
        fhnd.close()

        # Check if there is enough free space + 128MB extra
        fs_stats = psutil.disk_usage(path)
        size_required = desired_size+1024*1024*128

        if fs_stats.free < size_required:
            sys.stderr.write('Not enough free space in %s required: %d, free: %s\n' % (path, size_required, fs_stats.free))
            return -1

        # Create swap file
        cmd = '/bin/rm "%s" && ' % fname
        cmd += ' dd if=/dev/zero of="%s" bs=1M count=%d >/dev/null && ' % (fname, size_in_mb)
        cmd += ' chmod 600 %s >/dev/null && ' % fname
        cmd += ' mkswap %s >/dev/null && ' % fname
        cmd += ' swapon %s >/dev/null && ' % fname
        cmd += ' echo "%s swap swap defaults 0 0" >> /etc/fstab ' % fname
        cmd_exec = 'sudo -E -H /bin/bash -c \'%s\'' % cmd

        # Swap create
        p = subprocess.Popen(cmd_exec, shell=True)
        p.communicate()
        return p.returncode, fname, desired_size

    def print_error(self, msg):
        if self.print_output:
            sys.stderr.write(msg)

    def install_cron_renew(self):
        """
        Installs cronjob for certificate renewal
        :return:
        """
        cron_path = '/etc/cron.d/ebaws-renew'
        if os.path.exists(cron_path):
            os.remove(cron_path)

        with util.safe_open(cron_path, mode='w', chmod=0o644) as handle:
            handle.write('# Daily certificate renewal for the PKI key management system (EJBCA LetsEncrypt)\n')
            handle.write('*/5 * * * * root /usr/local/bin/ebaws-cli -n --pid-lock 3 renew >/dev/null 2>/dev/null \n')
        return 0

    def install_onboot_check(self):
        """
        Installs a service invocation after boot to reclaim domain again
        :return:
        """
        os_name, os_version = util.get_os_info()
        os_name = os_name.lower()

        if os_name in ['rhel', 'centos'] and os_version.startswith('7'):
            self.print_error('CentOS/RHEL version 7 is not supported yet')
            return 1

        # Write simple init script
        initd_path = '/etc/init.d/ebaws-onboot'
        if os.path.exists(initd_path):
            os.remove(initd_path)

        with util.safe_open(initd_path, mode='w', chmod=0o755) as handle:
            handle.write(self.get_onboot_init_script())
            handle.write('\n')

        # Set service to start after boot
        p = subprocess.Popen('chkconfig --level=345 ebaws-onboot on', shell=True)
        p.communicate()
        if p.returncode != 0:
            self.print_error('Error: Could not install on boot system service\n')
            return 2

        return 0

    def get_onboot_init_script(self):
        return consts.ONBOOT_INIT_SCRIPT

