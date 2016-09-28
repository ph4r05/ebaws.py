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


__author__ = 'dusanklinec'


class Ejbca(object):
    """
    EJBCA configuration & builder
    https://www.ejbca.org/docs/installation.html#Install
    """

    # Default home dirs
    EJBCA_HOME = '/opt/ejbca_ce_6_3_1_1'
    JBOSS_HOME = '/opt/jboss-eap-6.4.0'
    JBOSS_USER = 'jboss'
    USER_HOME = '/home/ec2-user'
    SSH_USER = 'ec2-user'

    INSTALL_PROPERTIES_FILE = 'conf/install.properties'
    WEB_PROPERTIES_FILE = 'conf/web.properties'
    P12_FILE = 'p12/superadmin.p12'

    PASSWORDS_FILE = '/root/ejbca.passwords'
    PASSWORDS_BACKUP_DIR = '/root/ejbca.passwords.old'
    DB_BACKUPS = '/root/ejbcadb.old'

    JBOSS_CLI = 'bin/jboss-cli.sh'

    # Default installation settings
    INSTALL_PROPERTIES = {
        'ca.name': 'ManagementCA',
        'ca.dn': 'CN=ManagementCA,O=EJBCA EnigmaBridge,C=GB',
        'ca.tokentype': 'soft',
        'ca.keytype': 'RSA',
        'ca.keyspec': '2048',
        'ca.signaturealgorithm': 'SHA256WithRSA',
        'ca.validity': '3650',
        'ca.policy': 'null'
    }

    WEB_PROPERTIES = {
        'cryptotoken.p11.lib.255.name': 'SoftHSMv1',
        'cryptotoken.p11.lib.255.file': '/usr/lib64/softhsm/libsofthsm.so',

        'httpsserver.hostname': 'localhost',
        'httpsserver.dn': 'CN=localhost,O=EJBCA EnigmaBridge,C=GB',

        'superadmin.cn': 'SuperAdmin',
        'superadmin.dn': 'CN=SuperAdmin',
        'superadmin.batch': 'true',

        # Credentials, generated at random, stored into password file
        #'httpsserver.password': 'serverpwd',
        #'java.trustpassword': 'changeit',
        #'superadmin.password': 'ejbca',
    }

    def __init__(self, install_props=None, web_props=None, print_output=False, *args, **kwargs):
        self.install_props = install_props if install_props is not None else {}
        self.web_props = web_props if web_props is not None else {}

        self.http_pass = util.random_password(16)
        self.java_pass = 'changeit' # EJBCA & JBoss bug here
        self.superadmin_pass = util.random_password(16)

        self.print_output = print_output

        self.ejbca_install_result = 1
        pass

    def get_ejbca_home(self):
        """
        Returns EJBCA home, first try to look at env var, then return default val
        :return:
        """
        if 'EJBCA_HOME' in os.environ and len(os.environ['EJBCA_HOME']) > 0:
            return os.path.abspath(os.environ['EJBCA_HOME'])
        else:
            return os.path.abspath(self.EJBCA_HOME)

    def get_jboss_home(self):
        """
        Returns JBoss home directory, first try to look at env var, then return default val
        :return:
        """
        if 'JBOSS_HOME' in os.environ and len(os.environ['JBOSS_HOME']) > 0:
            return os.path.abspath(os.environ['JBOSS_HOME'])
        else:
            return os.path.abspath(self.JBOSS_HOME)

    def get_install_prop_file(self):
        return os.path.abspath(os.path.join(self.get_ejbca_home(), self.INSTALL_PROPERTIES_FILE))

    def get_web_prop_file(self):
        return os.path.abspath(os.path.join(self.get_ejbca_home(), self.WEB_PROPERTIES_FILE))

    def properties_to_string(self, prop):
        """
        Converts dict based properties to a string
        :return:
        """
        result = []
        for k in prop:
            result.append("%s=%s" % (k, prop[k]))
        result = sorted(result)
        return '\n'.join(result)

    def update_properties(self):
        """
        Updates properties files of the ejbca
        :return:
        """
        file_web = self.get_web_prop_file()
        file_ins = self.get_install_prop_file()

        prop_web = EBUtils.merge(self.WEB_PROPERTIES, self.web_props)
        prop_ins = EBUtils.merge(self.INSTALL_PROPERTIES, self.install_props)

        prop_hdr = '#\n'
        prop_hdr += '# Config file generated: %s\n' % (datetime.now().strftime("%Y-%m-%d %H:%M"))
        prop_hdr += '#\n'

        file_web_hnd = None
        file_ins_hnd = None
        try:
            file_web_hnd, file_web_backup = util.safe_create_with_backup(file_web, 'w', 0o644)
            file_ins_hnd, file_ins_backup = util.safe_create_with_backup(file_ins, 'w', 0o644)

            file_web_hnd.write(prop_hdr + self.properties_to_string(prop_web)+"\n\n")
            file_ins_hnd.write(prop_hdr + self.properties_to_string(prop_ins)+"\n\n")
        finally:
            if file_web_hnd is not None:
                file_web_hnd.close()
            if file_ins_hnd is not None:
                file_ins_hnd.close()

    def cli_cmd(self, cmd, log_obj=None, write_dots=False, on_out=None, on_err=None, ant_answer=True, cwd=None):
        """
        Runs command line task
        Used for ant and jboss-cli.sh
        :return:
        """
        feeder = Feeder()
        default_cwd = self.get_ejbca_home()
        p = run(cmd,
                input=feeder, async=True,
                stdout=Capture(buffer_size=1),
                stderr=Capture(buffer_size=1),
                cwd=cwd if cwd is not None else default_cwd)

        out_acc = []
        err_acc = []
        ret_code = 1
        log = None
        close_log = False

        # Logging - either filename or logger itself
        if log_obj is not None:
            if isinstance(log_obj, types.StringTypes):
                util.delete_file_backup(log_obj, chmod=0o600)
                log = util.safe_open(log_obj, mode='w', chmod=0o600)
                close_log = True
            else:
                log = log_obj

        try:
            while len(p.commands) == 0:
                time.sleep(0.15)

            while p.commands[0].returncode is None:
                out = p.stdout.readline()
                err = p.stderr.readline()

                # If output - react on input challenges
                if out is not None and len(out) > 0:
                    out_acc.append(out)

                    if log is not None:
                        log.write(out)
                        log.flush()

                    if write_dots:
                        sys.stderr.write('.')

                    if on_out is not None:
                        on_out(out, feeder)
                    elif ant_answer:
                        if out.strip().startswith('Please enter'):            # default - use default value, no starving
                            feeder.feed('\n')
                        elif out.strip().startswith('[input] Please enter'):  # default - use default value, no starving
                            feeder.feed('\n')

                # Collect error
                if err is not None and len(err)>0:
                    err_acc.append(err)

                    if log is not None:
                        log.write(err)
                        log.flush()

                    if write_dots:
                        sys.stderr.write('.')

                    if on_err is not None:
                        on_err(err, feeder)

                p.commands[0].poll()
                time.sleep(0.01)

            ret_code = p.commands[0].returncode

            # Collect output to accumulator
            rest_out = p.stdout.readlines()
            if rest_out is not None and len(rest_out) > 0:
                for out in rest_out:
                    out_acc.append(out)
                    if log is not None:
                        log.write(out)
                        log.flush()
                    if on_out is not None:
                        on_out(out, feeder)

            # Collect error to accumulator
            rest_err = p.stderr.readlines()
            if rest_err is not None and len(rest_err) > 0:
                for err in rest_err:
                    err_acc.append(err)
                    if log is not None:
                        log.write(err)
                        log.flush()
                    if on_err is not None:
                        on_err(err, feeder)

            return ret_code, out_acc, err_acc

        finally:
            feeder.close()
            if close_log:
                log.close()
        pass

    def ant_cmd(self, cmd, log_obj=None, write_dots=False, on_out=None, on_err=None):
        ret, out, err = self.cli_cmd('sudo -E -H -u %s ant %s' % (self.JBOSS_USER, cmd),
                                     log_obj=log_obj, write_dots=write_dots,
                                     on_out=on_out, on_err=on_err, ant_answer=True)
        if ret != 0:
            sys.stderr.write('\nError, process returned with invalid result code: %s\n' % ret)
            if isinstance(log_obj, types.StringTypes):
                sys.stderr.write('For more details please refer to %s \n' % log_obj)
        if write_dots:
            sys.stderr.write('\n')
        return ret, out, err

    def ant_deploy(self):
        return self.ant_cmd('deploy', log_obj='/tmp/ant-deploy.log', write_dots=self.print_output)

    def ant_deployear(self):
        return self.ant_cmd('deployear', log_obj='/tmp/ant-deployear.log', write_dots=self.print_output)

    def ant_install_answer(self, out, feeder):
        out = out.strip()
        if 'truststore with the CA certificate for https' in out:
            feeder.feed(self.java_pass + '\n')
        elif 'keystore with the TLS key for https' in out:
            feeder.feed(self.http_pass + '\n')
        elif 'the superadmin password' in out:
            feeder.feed(self.superadmin_pass + '\n')
        elif 'password CA token password' in out:
            feeder.feed('\n')
        elif out.startswith('Please enter'):          # default - use default value, no starving
            feeder.feed('\n')
        elif out.startswith('[input] Please enter'):  # default - use default value, no starving
            feeder.feed('\n')

    def ant_install(self):
        """
        Installation
        :return:
        """
        return self.ant_cmd('install', log_obj='/tmp/ant-install.log', write_dots=self.print_output, on_out=self.ant_install_answer)

    def ant_client_tools(self):
        return self.ant_cmd('clientToolBox', log_obj='/tmp/ant-clientToolBox.log', write_dots=self.print_output)

    def jboss_cmd(self, cmd):
        cli = os.path.abspath(os.path.join(self.get_jboss_home(), self.JBOSS_CLI))
        cli_cmd = 'sudo -E -H -u %s %s -c \'%s\'' % (self.JBOSS_USER, cli, cmd)

        with open('/tmp/jboss-cli.log', 'a+') as logger:
            ret, out, err = self.cli_cmd(cli_cmd, log_obj=logger,
                                         write_dots=self.print_output, ant_answer=False,
                                         cwd=self.get_jboss_home())
            return ret, out, err

    def jboss_reload(self):
        ret = self.jboss_cmd(':reload')
        time.sleep(3)
        self.jboss_wait_after_start()
        return ret

    def jboss_undeploy(self):
        return self.jboss_cmd('undeploy ejbca.ear')

    def jboss_remove_datasource(self):
        return self.jboss_cmd('data-source remove --name=ejbcads')

    def jboss_rollback_ejbca(self):
        cmds = ['/core-service=management/security-realm=SSLRealm/authentication=truststore:remove',
                '/core-service=management/security-realm=SSLRealm/server-identity=ssl:remove',
                '/core-service=management/security-realm=SSLRealm:remove',

                '/socket-binding-group=standard-sockets/socket-binding=httpspub:remove',
                '/subsystem=undertow/server=default-server/https-listener=httpspub:remove',
                '/subsystem=web/connector=httpspub:remove',

                '/socket-binding-group=standard-sockets/socket-binding=httpspriv:remove',
                '/subsystem=undertow/server=default-server/https-listener=httpspriv:remove',
                '/subsystem=web/connector=httpspriv:remove',

                '/socket-binding-group=standard-sockets/socket-binding=http:remove',
                '/subsystem=undertow/server=default-server/http-listener=http:remove',
                '/subsystem=web/connector=http:remove',

                '/subsystem=undertow/server=default-server/http-listener=default:remove',

                '/system-property=org.apache.catalina.connector.URI_ENCODING:remove',
                '/system-property=org.apache.catalina.connector.USE_BODY_ENCODING_FOR_QUERY_STRING:remove',

                '/interface=http:remove',
                '/interface=httpspub:remove',
                '/interface=httpspriv:remove']
        for cmd in cmds:
            self.jboss_cmd(cmd)
        self.jboss_reload()

    def jboss_backup_database(self):
        """
        Removes original database, moving it to a backup location.
        :return:
        """
        jboss_dir = self.get_jboss_home()
        db1 = os.path.join(jboss_dir, 'ejbcadb.h2.db')
        db2 = os.path.join(jboss_dir, 'ejbcadb.trace.db')
        db3 = os.path.join(jboss_dir, 'ejbcadb.lock.db')

        util.make_or_verify_dir(self.DB_BACKUPS)

        backup1 = util.delete_file_backup(db1, backup_dir=self.DB_BACKUPS)
        backup2 = util.delete_file_backup(db2, backup_dir=self.DB_BACKUPS)
        backup3 = util.delete_file_backup(db3, backup_dir=self.DB_BACKUPS)
        return backup1, backup2, backup3

    def jboss_fix_privileges(self):
        p = subprocess.Popen('sudo chown -R %s:%s %s' % (self.JBOSS_USER, self.JBOSS_USER, self.get_jboss_home()), shell=True)
        p.wait()
        p = subprocess.Popen('sudo chown -R %s:%s %s' % (self.JBOSS_USER, self.JBOSS_USER, self.get_ejbca_home()), shell=True)
        p.wait()

    def jboss_wait_after_start(self):
        """
        Waits until JBoss responds with success after start
        :return:
        """
        jboss_works = False
        max_attempts = 20

        for i in range(0, max_attempts):
            if i > 0:
                if self.print_output:
                    sys.stderr.write('.')
                time.sleep(3)

            try:
                ret, out, err = self.jboss_cmd(':read-attribute(name=server-state)')
                if out is None or len(out) == 0:
                    continue

                out_total = '\n'.join(out)

                if re.search(r'["\']?outcome["\']?\s*=>\s*["\']?success["\']?', out_total) and \
                        re.search(r'["\']?result["\']?\s*=>\s*["\']?running["\']?', out_total):
                    jboss_works = True
                    break

            except Exception as ex:
                continue

        return jboss_works

    def jboss_wait_after_deploy(self):
        """
        Waits for JBoss to finish initial deployment.
        :return:
        """
        jboss_works = False
        max_attempts = 30

        for i in range(0, max_attempts):
            if i > 0:
                if self.print_output:
                    sys.stderr.write('.')
                time.sleep(3)

            try:
                ret, out, err = self.jboss_cmd('deploy -l')
                if out is None or len(out) == 0:
                    continue

                out_total = '\n'.join(out)

                if re.search(r'ejbca.ear.+?\sOK', out_total):
                    jboss_works = True
                    break

            except Exception as ex:
                continue

        return jboss_works

    def jboss_restart(self):
        """
        Restarts JBoss daemon
        Here is important to start it with setsid so daemon is started in a new shell session.
        Otherwise Jboss would have been killed in case python terminates.
        :return:
        """
        os.spawnlp(os.P_NOWAIT, "sudo", "bash", "bash", "-c",
                   "setsid /etc/init.d/jboss-eap-6.4.0 restart 2>/dev/null >/dev/null </dev/null &")
        time.sleep(10)
        self.jboss_wait_after_start()

    def backup_passwords(self):
        """
        Backups the generated passwords to /root/ejbca.passwords
        :return:
        """
        util.make_or_verify_dir(self.PASSWORDS_BACKUP_DIR, mode=0o600)
        util.delete_file_backup(self.PASSWORDS_FILE, chmod=0o600, backup_dir=self.PASSWORDS_BACKUP_DIR)
        with util.safe_open(self.PASSWORDS_FILE, chmod=0o600) as f:
            f.write('httpsserver.password=%s\n' % self.http_pass)
            f.write('java.trustpassword=%s\n' % self.java_pass)
            f.write('superadmin.password=%s\n' % self.superadmin_pass)
            f.flush()

    def get_p12_file(self):
        return os.path.abspath(os.path.join(self.get_ejbca_home(), self.P12_FILE))

    def copy_p12_file(self):
        """
        Copies p12 file to the home directory & chowns so user can download it via scp
        :return:
        """
        p12 = self.get_p12_file()
        new_p12 = os.path.abspath(os.path.join(self.USER_HOME, 'ejbca-admin.p12'))
        os.remove(new_p12)

        # copy in a safe mode - create file non readable by others, copy
        with open(p12, 'r') as src_p12:
            with util.safe_open(new_p12, mode='w', chmod=0o600) as dst_p12:
                shutil.copyfileobj(src_p12, dst_p12)

        p = subprocess.Popen('sudo chown %s:%s %s' % (self.SSH_USER, self.SSH_USER, new_p12), shell=True)
        p.wait()

        return new_p12

    def pkcs11_get_cwd(self):
        return os.path.join(self.get_ejbca_home(), 'bin')

    def pkcs11_get_command(self, cmd):
        return 'sudo -E -H -u %s %s/pkcs11HSM.sh %s' % (self.JBOSS_USER, self.pkcs11_get_cwd(), cmd)

    def pkcs11_cmd(self, cmd, retry_attempts=3, write_dots=False, on_out=None, on_err=None):
        """
        Executes cd $EJBCA_HOME/bin
        ./pkcs11HSM.sh $*

        :param cmd:
        :param retry_attempts:
        :return:
        """
        cwd = self.pkcs11_get_cwd()
        ret, out, err = -1, None, None
        cmd_exec = self.pkcs11_get_command(cmd)

        for i in range(0, retry_attempts):
            ret, out, err = self.cli_cmd(
                cmd_exec,
                log_obj=None, write_dots=write_dots,
                on_out=on_out, on_err=on_err,
                ant_answer=False, cwd=cwd)

            if write_dots:
                sys.stderr.write('\n')

            if ret == 0:
                return ret, out, err

        return ret, out, err

    def pkcs11_answer(self, out, feeder):
        out = out.strip()
        if 'Password:' in out:
            feeder.feed('0000\n')

    def pkcs11_get_generate_key_cmd(self, softhsm=None, bit_size=2048, alias=None, slot_id=0):
        so_path = softhsm.get_so_path() if softhsm is not None else '/usr/lib64/softhsm/libsofthsm.so'
        return 'generate %s %s %s %s' % (so_path, bit_size, alias, slot_id)

    def pkcs11_get_test_key_cmd(self, softhsm=None, slot_id=0):
        so_path = softhsm.get_so_path() if softhsm is not None else '/usr/lib64/softhsm/libsofthsm.so'
        return 'test %s %s' % (so_path, slot_id)

    def pkcs11_generate_key(self, softhsm=None, bit_size=2048, alias=None, slot_id=0, retry_attempts=3):
        """
        Generates keys in the PKCS#11 token.
        Can be used with the EJBCA.

        cd $EJBCA_HOME/bin
        ./pkcs11HSM.sh generate /usr/lib64/softhsm/libsofthsm.so 4096 signKey 0
        :return:
        """
        cmd = self.pkcs11_get_generate_key_cmd(softhsm=softhsm, bit_size=bit_size, alias=alias, slot_id=slot_id)
        return self.pkcs11_cmd(cmd=cmd, retry_attempts=retry_attempts, write_dots=self.print_output,
                               on_out=self.pkcs11_answer, on_err=self.pkcs11_answer)

    def pkcs11_generate_default_key_set(self, softhsm=None, slot_id=0, retry_attempts=3,
                                        sign_key_alias='signKey',
                                        default_key_alias='defaultKey',
                                        test_key_alias='testKey'):
        """
        Generates a default key set to be used with EJBCA
        :param sign_key_alias:
        :param default_key_alias:
        :param test_key_alias:
        :return:
        """
        aliases = [sign_key_alias, default_key_alias, test_key_alias]
        key_sizes = [2048, 2048, 1024]

        for idx,alias in enumerate(aliases):
            key_size = key_sizes[idx]
            ret, out, cmd = self.pkcs11_generate_key(softhsm=softhsm, bit_size=key_size, alias=alias,
                                                     slot_id=slot_id, retry_attempts=retry_attempts)

            if ret != 0:
                return 1

            if self.print_output:
                sys.stderr.write('.')
        return 0

    def configure(self):
        """
        Configures EJBCA for installation deployment
        :return:
        """

        # 1. update properties file
        if self.print_output:
            print " - Updating settings"
        self.update_properties()
        self.backup_passwords()

        # 2. Undeploy original EJBCA
        if self.print_output:
            print " - Cleaning JBoss environment (DB backup)"
        self.jboss_undeploy()
        self.jboss_remove_datasource()
        self.jboss_rollback_ejbca()
        self.jboss_reload()

        # restart jboss
        if self.print_output:
            print "\n - Restarting JBoss, please wait..."
        self.jboss_restart()
        self.jboss_backup_database()
        self.jboss_fix_privileges()
        self.jboss_reload()

        # 3. deploy, 5 attempts
        for i in range(0, 5):
            if self.print_output:
                print "\n - Deploying EJBCA" if i == 0 else "\n - Deploying EJBCA, attempt %d" % (i+1)
            res, out, err = self.ant_deploy()
            self.ejbca_install_result = res
            if res == 0:
                break

        if self.ejbca_install_result != 0:
            return 2

        # 4. install, 3 attempts
        for i in range(0, 3):
            if self.print_output:
                print " - Installing EJBCA" if i == 0 else " - Installing EJBCA, attempt %d" % (i+1)
            self.jboss_fix_privileges()
            self.jboss_wait_after_deploy()

            res, out, err = self.ant_install()
            self.ejbca_install_result = res
            if res == 0:
                break

        self.ant_client_tools()
        self.jboss_fix_privileges()
        self.jboss_reload()
        return self.ejbca_install_result





