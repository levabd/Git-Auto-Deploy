#!/usr/bin/env python

import logging

# Initialize loging
logFormatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")
logger = logging.getLogger()
logFilePath = ""

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
logger.addHandler(consoleHandler)

class Lock():
    """Simple implementation of a mutex lock using the file systems. Works on *nix systems."""

    path = None
    _has_lock = False

    def __init__(self, path):
        self.path = path

    def obtain(self):
        import os

        try:
            os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            self._has_lock = True
            logger.info("Successfully obtained lock: %s" % self.path)
        except OSError:
            return False
        else:
            return True

    def release(self):
        import os

        if not self._has_lock:
            raise Exception("Unable to release lock that is owned by another process")
        try:
            os.remove(self.path)
            logger.info("Successfully released lock: %s" % self.path)
        finally:
            self._has_lock = False

    def has_lock(self):
        return self._has_lock

    def clear(self):
        import os

        try:
            os.remove(self.path)
        except OSError:
            pass
        finally:
            logger.info("Successfully cleared lock: %s" % self.path)
            self._has_lock = False


class GitWrapper():
    """Wraps the git client. Currently uses git through shell command invocations."""

    def __init__(self):
        pass

    @staticmethod
    def pull(repo_config):
        """Pulls the latest version of the repo from the git server"""
        from subprocess import call

        branch = ('branch' in repo_config) and repo_config['branch'] or 'master'
        remote = ('remote' in repo_config) and repo_config['remote'] or 'origin'

        logger.info("Post push request received")

        if 'path' in repo_config:
            logger.info('Updating ' + repo_config['path'])

            cmd = 'cd "' + repo_config['path'] + '"' \
                  '&& unset GIT_DIR ' + \
                  '&& git fetch ' + remote + \
                  '&& git reset --hard ' + remote + '/' + branch + ' ' + \
                  '&& git submodule init ' + \
                  '&& git submodule update'

            # '&& git update-index --refresh ' +\
            if logFilePath:
                with open(logFilePath, 'a') as logfile:
                    res = call([cmd], stdout=logfile, stderr=logfile, shell=True)
            else:
                res = call([cmd], shell=True)
            logger.info('Pull result: ' + str(res))

            return int(res)

        else:
            return 0 #everething all right

    @staticmethod
    def clone(url, branch, path):
        from subprocess import call

        branchToClone = branch or 'master'

        if logFilePath:
            with open(logFilePath, 'a') as logfile:
                call(['git clone --recursive %s -b %s %s' % (url, branchToClone, path)], stdout=logfile, stderr=logfile, shell=True)
        else:
            call(['git clone --recursive %s -b %s %s' % (url, branchToClone, path)], shell=True)


    @staticmethod
    def deploy(repo_config):
        """Executes any supplied post-pull deploy command"""
        from subprocess import call

        if 'path' in repo_config:
            path = repo_config['path']

        cmds = []
        if 'deploy' in repo_config:
            cmds.append(repo_config['deploy'])

        gd = GitAutoDeploy().get_config()['global_deploy']
        if len(gd[0]) is not 0:
            cmds.insert(0, gd[0])
        if len(gd[1]) is not 0:
            cmds.append(gd[1])

        logger.info('Executing deploy command(s)')

        if logFilePath:
            with open(logFilePath, 'a') as logfile:
                for cmd in cmds:
                    if 'path' in repo_config:
                        call(['cd "' + path + '" && ' + cmd], stdout=logfile, stderr=logfile, shell=True)
                    else:
                        call([cmd], stdout=logfile, stderr=logfile, shell=True)
        else:
            for cmd in cmds:
                    if 'path' in repo_config:
                        call(['cd "' + path + '" && ' + cmd], shell=True)
                    else:
                        call([cmd], shell=True)


from BaseHTTPServer import BaseHTTPRequestHandler


class WebhookRequestHandler(BaseHTTPRequestHandler):
    """Extends the BaseHTTPRequestHandler class and handles the incoming HTTP requests."""

    def do_POST(self):
        """Invoked on incoming POST requests"""
        from threading import Timer

        # Extract repository URL(s) from incoming request body
        repo_urls, ref, action = self.get_repo_params_from_request()

        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()

        # Wait one second before we do git pull (why?)
        Timer(1.0, GitAutoDeploy.process_repo_urls, (repo_urls, ref, action)).start()


    def get_repo_params_from_request(self):
        """Parses the incoming request and extracts all possible URLs to the repository in question. Since repos can
        have both ssh://, git:// and https:// URIs, and we don't know which of them is specified in the config, we need
        to collect and compare them all."""
        import json

        content_type = self.headers.getheader('content-type')
        length = int(self.headers.getheader('content-length'))
        body = self.rfile.read(length)

        data = json.loads(body)

        repo_urls = []
        ref = ""
        action = ""

        gitlab_event = self.headers.getheader('X-Gitlab-Event')
        github_event = self.headers.getheader('X-GitHub-Event')
        user_agent = self.headers.getheader('User-Agent')

        # Assume GitLab if the X-Gitlab-Event HTTP header is set
        if gitlab_event:

            logger.info("Received '%s' event from GitLab" % gitlab_event)

            if not 'repository' in data:
                logger.error("ERROR - Unable to recognize data format")
                return repo_urls, ref or "master", action

            # One repository may posses multiple URLs for different protocols
            for k in ['url', 'git_http_url', 'git_ssh_url']:
                if k in data['repository']:
                    repo_urls.append(data['repository'][k])

        # Assume GitHub if the X-GitHub-Event HTTP header is set
        elif github_event:

            logger.info("Received '%s' event from GitHub" % github_event)

            if not 'repository' in data:
                logger.error("ERROR - Unable to recognize data format")
                return repo_urls, ref or "master", action

            # One repository may posses multiple URLs for different protocols
            for k in ['url', 'git_url', 'clone_url', 'ssh_url']:
                if k in data['repository']:
                    repo_urls.append(data['repository'][k])

            if 'pull_request' in data:
                if 'base' in data['pull_request']:
                    if 'ref' in data['pull_request']['base']:
                        ref = data['pull_request']['base']['ref']
                        logger.info("Pull request to branch '%s' was fired" % ref)
            elif 'ref' in data:
                ref = data['ref']
                logger.info("Push to branch '%s' was fired" % ref)

            if 'action' in data:
                action = data['action']
                logger.info("Action '%s' was fired" % action)

        # Assume BitBucket if the User-Agent HTTP header is set to 'Bitbucket-Webhooks/2.0' (or something similar)
        elif user_agent and user_agent.lower().find('bitbucket') != -1:

            logger.info("Received event from BitBucket")

            if not 'repository' in data:
                logger.error("ERROR - Unable to recognize data format")
                return repo_urls, ref or "master", action

            # One repository may posses multiple URLs for different protocols
            for k in ['url', 'git_url', 'clone_url', 'ssh_url']:
                if k in data['repository']:
                    repo_urls.append(data['repository'][k])

            if 'full_name' in data['repository']:
                repo_urls.append('git@bitbucket.org:%s.git' % data['repository']['full_name'])

                # Add a simplified version of the bitbucket HTTPS URL - without the username@bitbucket.com part. This is
                # needed since the configured repositories might be configured using a different username.
                repo_urls.append('https://bitbucket.org/%s.git' % (data['repository']['full_name']))

        # Special Case for Gitlab CI
        elif content_type == "application/json" and "build_status" in data:

            logger.info('Received event from Gitlab CI')

            if not 'push_data' in data:
                logger.error("ERROR - Unable to recognize data format")
                return repo_urls, ref or "master", action

            # Only add repositories if the build is successful. Ignore it in other case.
            if data['build_status'] == "success":
                for k in ['url', 'git_http_url', 'git_ssh_url']:
                    if k in data['push_data']['repository']:
                        repo_urls.append(data['push_data']['repository'][k])
            else:
                logger.warning("Gitlab CI build '%d' has status '%s'. Not pull will be done" % (
                data['build_id'], data['build_status']))

        # Try to find the repository urls and add them as long as the content type is set to JSON at least.
        # This handles old GitLab requests and Gogs requests for example.
        elif content_type == "application/json":

            logger.info("Received event from unknown origin. Assume generic data format.")

            if not 'repository' in data:
                logger.error("ERROR - Unable to recognize data format")
                return repo_urls, ref or "master", action

            # One repository may posses multiple URLs for different protocols
            for k in ['url', 'git_http_url', 'git_ssh_url', 'http_url', 'ssh_url']:
                if k in data['repository']:
                    repo_urls.append(data['repository'][k])
        else:
            logger.error("ERROR - Unable to recognize request origin. Don't know how to handle the request.")

        return repo_urls, ref or "master", action


class GitAutoDeploy(object):
    config_path = None
    debug = True
    daemon = False

    _instance = None
    _server = None
    _config = None
    _base_config = None

    def __new__(cls, *args, **kwargs):
        """Overload constructor to enable Singleton access"""
        if not cls._instance:
            cls._instance = super(GitAutoDeploy, cls).__new__(
                cls, *args, **kwargs)
        return cls._instance

    @staticmethod
    def debug_diagnosis(port):
        if GitAutoDeploy.debug is False:
            return

        pid = GitAutoDeploy.get_pid_on_port(port)
        if pid is False:
            logger.warning('I don\'t know the number of pid that is using my configured port')
            return

        logger.info('Process with pid number %s is using port %s' % (pid, port))
        with open("/proc/%s/cmdline" % pid) as f:
            cmdline = f.readlines()
            logger.info('cmdline ->', cmdline[0].replace('\x00', ' '))

    @staticmethod
    def get_pid_on_port(port):
        import os

        with open("/proc/net/tcp", 'r') as f:
            file_content = f.readlines()[1:]

        pids = [int(x) for x in os.listdir('/proc') if x.isdigit()]
        conf_port = str(port)
        mpid = False

        for line in file_content:
            if mpid is not False:
                break

            _, laddr, _, _, _, _, _, _, _, inode = line.split()[:10]
            decport = str(int(laddr.split(':')[1], 16))

            if decport != conf_port:
                continue

            for pid in pids:
                try:
                    path = "/proc/%s/fd" % pid
                    if os.access(path, os.R_OK) is False:
                        continue

                    for fd in os.listdir(path):
                        cinode = os.readlink("/proc/%s/fd/%s" % (pid, fd))
                        minode = cinode.split(":")

                        if len(minode) == 2 and minode[1][1:-1] == inode:
                            mpid = pid

                except Exception as e:
                    pass

        return mpid

    @staticmethod
    def process_repo_urls(urls, ref, action):
        import os
        import time

        # Get a list of configured repositories that matches the incoming web hook reqeust
        repo_configs = GitAutoDeploy().get_matching_repo_configs(urls)

        if len(repo_configs) == 0:
            logger.warning('Unable to find any of the repository URLs in the config: %s' % ', '.join(urls))
            return

        # Process each matching repository
        for repo_config in repo_configs:

            if repo_config['pullrequestfilter'] and (repo_config['ref'] != ref or repo_config['action'] != action):
                continue

            if 'path' in repo_config:
                running_lock = Lock(os.path.join(repo_config['path'], 'status_running'))
                waiting_lock = Lock(os.path.join(repo_config['path'], 'status_waiting'))
            try:

                if 'path' in repo_config:
                    # Attempt to obtain the status_running lock
                    while not running_lock.obtain():

                        # If we're unable, try once to obtain the status_waiting lock
                        if not waiting_lock.has_lock() and not waiting_lock.obtain():
                            logger.error("Unable to obtain the status_running lock nor the status_waiting lock. Another process is " \
                                  + "already waiting, so we'll ignore the request.")

                            # If we're unable to obtain the waiting lock, ignore the request
                            break

                        # Keep on attempting to obtain the status_running lock until we succeed
                        time.sleep(5)

                n = 4
                while 0 < n and 0 != GitWrapper.pull(repo_config):
                    n -= 1

                if 0 < n:
                    GitWrapper.deploy(repo_config)

            except Exception as e:
                if 'path' in repo_config:
                    logger.error('Error during \'pull\' or \'deploy\' operation on path: %s' % repo_config['path'])
                logger.error(e)

            finally:

                if 'path' in repo_config:
                    # Release the lock if it's ours
                    if running_lock.has_lock():
                        running_lock.release()

                    # Release the lock if it's ours
                    if waiting_lock.has_lock():
                        waiting_lock.release()

    def get_default_config_path(self):
        import os
        import re

        if self.config_path:
            return self.config_path

        # Look for a custom config file if no path is provided as argument
        target_directories = [
            os.path.dirname(os.path.realpath(__file__)),  # Script path
        ]

        # Add current CWD if not identical to script path
        if not os.getcwd() in target_directories:
            target_directories.append(os.getcwd())

        target_directories.reverse()

        # Look for a *conf.json or *config.json
        for dir in target_directories:
            for item in os.listdir(dir):
                if re.match(r"conf(ig)?\.json$", item):
                    path = os.path.realpath(os.path.join(dir, item))
                    logger.info("Using '%s' as config" % path)
                    return path

        return './GitAutoDeploy.conf.json'

    def get_base_config(self):
        import json

        if self._base_config:
            return self._base_config

        if not self.config_path:
            self.config_path = self.get_default_config_path()

        try:
            config_string = open(self.config_path).read()

        except Exception as e:
            logger.warning("Could not load %s file\n" % self.config_path)
            raise e

        try:
            self._base_config = json.loads(config_string)

        except Exception as e:
            logger.error("%s file is not valid JSON\n" % self.config_path)
            raise e

        return self._base_config

    def get_config(self):
        import os
        import re

        if self._config:
            return self._config

        self._config = self.get_base_config()

        # Translate any ~ in the path into /home/<user>
        if 'pidfilepath' in self._config:
            self._config['pidfilepath'] = os.path.expanduser(self._config['pidfilepath'])

        for repo_config in self._config['repositories']:

            # If a Bitbucket repository is configured using the https:// URL, a username is usually
            # specified in the beginning of the URL. To be able to compare configured Bitbucket
            # repositories with incoming web hook events, this username needs to be stripped away in a
            # copy of the URL.
            if 'url' in repo_config and not 'bitbucket_username' in repo_config:
                regexp = re.search(r"^(https?://)([^@]+)@(bitbucket\.org/)(.+)$", repo_config['url'])
                if regexp:
                    repo_config['url_without_usernme'] = regexp.group(1) + regexp.group(3) + regexp.group(4)

            # Translate any ~ in the path into /home/<user>
            if 'path' in repo_config:
                repo_config['path'] = os.path.expanduser(repo_config['path'])

            if 'path' in repo_config:
                if not os.path.isdir(repo_config['path']):

                    logger.error("Directory %s not found" % repo_config['path'])
                    GitWrapper.clone(url=repo_config['url'], branch=repo_config['branch'] if 'branch' in repo_config else None, path=repo_config['path'])

                    if not os.path.isdir(repo_config['path']):
                        logger.warning("Unable to clone %s branch of repository %s. So repo will not pull. Only deploy command will run(if it exist)" % (repo_config['branch'] if 'branch' in repo_config else "default", repo_config['url']))
                        #sys.exit(2)

                    else:
                        logger.info("Repository %s successfully cloned" % repo_config['url'])
                if not os.path.isdir(repo_config['path'] + '/.git'):
                    logger.warning("Directory %s is not a Git repository. So repo will not pull. Only deploy command will run(if it exist)" % repo_config['path'])
                    #sys.exit(2)
            else:
                logger.warning("'Path' was not found in config. So repo will not pull. Only deploy command will run(if it exist)")

        return self._config

    def get_matching_repo_configs(self, urls):
        """Iterates over the various repo URLs provided as argument (git://, ssh:// and https:// for the repo) and
        compare them to any repo URL specified in the config"""

        config = self.get_config()
        configs = []

        for url in urls:
            for repo_config in config['repositories']:
                if repo_config in configs:
                    continue
                if repo_config['url'] == url:
                    configs.append(repo_config)
                elif 'url_without_usernme' in repo_config and repo_config['url_without_usernme'] == url:
                    configs.append(repo_config)
        return configs

    def ssh_key_scan(self):
        import re
        from subprocess import call

        for repository in self.get_config()['repositories']:

            url = repository['url']
            logger.info("Scanning repository: %s" % url)
            m = re.match('.*@(.*?):', url)

            if m is not None:
                port = repository['port']
                port = '' if port is None else ('-p' + port)
                if logFilePath:
                    with open(logFilePath, 'a') as logfile:
                        call(['ssh-keyscan -t ecdsa,rsa ' + port + ' ' + m.group(1) + ' >> $HOME/.ssh/known_hosts'], stdout=logfile, stderr=logfile, shell=True)
                else:
                    call(['ssh-keyscan -t ecdsa,rsa ' + port + ' ' + m.group(1) + ' >> $HOME/.ssh/known_hosts'], shell=True)

            else:
                logger.error('Could not find regexp match in path: %s' % url)

    def kill_conflicting_processes(self):
        import os

        pid = GitAutoDeploy.get_pid_on_port(self.get_config()['port'])

        if pid is False:
            logger.error('[KILLER MODE] I don\'t know the number of pid that is using my configured port\n ' \
                  '[KILLER MODE] Maybe no one? Please, use --force option carefully')
            return False

        os.kill(pid, signal.SIGKILL)
        return True

    def create_pid_file(self):
        import os

        with open(self.get_config()['pidfilepath'], 'w') as f:
            f.write(str(os.getpid()))

    def read_pid_file(self):
        with open(self.get_config()['pidfilepath'], 'r') as f:
            return f.readlines()

    def remove_pid_file(self):
        import os

        os.remove(self.get_config()['pidfilepath'])

    def exit(self):
        import sys

        logger.info('\nGoodbye')
        self.remove_pid_file()
        sys.exit(0)

    @staticmethod
    def create_daemon():
        import os

        try:
            # Spawn first child
            pid = os.fork()
        except OSError, e:
            raise Exception("%s [%d]" % (e.strerror, e.errno))

        # First child
        if pid == 0:
            os.setsid()

            try:
                # Spawn second child
                pid = os.fork()
            except OSError, e:
                raise Exception, "%s [%d]" % (e.strerror, e.errno)

            if pid == 0:
                os.chdir('/')
                os.umask(0)
            else:
                # Kill first child
                os._exit(0)
        else:
            # Kill parent of first child
            os._exit(0)

        import resource

        maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
        if maxfd == resource.RLIM_INFINITY:
            maxfd = 1024

        # Close all file descriptors
        for fd in range(0, maxfd):
            try:
                os.close(fd)
            except OSError:
                # Ignore errors if fd isn't opened
                pass

        # Redirect standard input, output and error to devnull since we won't have a terminal
        os.open(os.devnull, os.O_RDWR)
        os.dup2(0, 1)
        os.dup2(0, 2)

        return 0

    def run(self):
        from sys import argv
        import sys
        from BaseHTTPServer import HTTPServer
        import socket
        import os

        global logFilePath

        # Initialize base config
        self.get_base_config()

        # All logs are recording
        logger.setLevel(logging.NOTSET)

        # Translate any ~ in the path into /home/<user>
        if "logfilepath" in self.get_base_config():
            logFilePath = os.path.expanduser(self.get_base_config()["logfilepath"])
            fileHandler = logging.FileHandler(logFilePath)
            fileHandler.setFormatter(logFormatter)
            logger.addHandler(fileHandler)

        if '-d' in argv or '--daemon-mode' in argv:
            self.daemon = True

        if '--ssh-keygen' in argv:
            logger.info('Scanning repository hosts for ssh keys...')
            self.ssh_key_scan()

        if '--force' in argv:
            logger.info('Attempting to kill any other process currently occupying port %s' % self.get_config()['port'])
            self.kill_conflicting_processes()

        if '--config' in argv:
            pos = argv.index('--config')
            if len(argv) > pos + 1:
                self.config_path = os.path.realpath(argv[argv.index('--config') + 1])
                logger.info('Using custom configuration file \'%s\'' % self.config_path)

        # Initialize base config
        self.get_config()

        if self.daemon:
            logger.info('Starting Git Auto Deploy in daemon mode')
            GitAutoDeploy.create_daemon()
        else:
            logger.info('Git Auto Deploy started')

        self.create_pid_file()

        # Suppress output
        if '-q' in argv or '--quiet' in argv:
            sys.stdout = open(os.devnull, 'w')

        # Clear any existing lock files, with no regard to possible ongoing processes
        for repo_config in self.get_config()['repositories']:
            if 'path' in repo_config:
                Lock(os.path.join(repo_config['path'], 'status_running')).clear()
                Lock(os.path.join(repo_config['path'], 'status_waiting')).clear()

        try:
            self._server = HTTPServer((self.get_config()['host'], self.get_config()['port']), WebhookRequestHandler)
            sa = self._server.socket.getsockname()
            logger.info("Listening on %s port %s", sa[0], sa[1])
            self._server.serve_forever()

        except socket.error, e:

            if not GitAutoDeploy.daemon:
                logger.critical("Error on socket: %s" % e)
                GitAutoDeploy.debug_diagnosis(self.get_config()['port'])

            sys.exit(1)

    def stop(self):
        if self._server is not None:
            self._server.socket.close()

    def signal_handler(self, signum, frame):
        self.stop()

        if signum == 1:
            self.run()
            return

        elif signum == 2:
            logger.info('\nRequested close by keyboard interrupt signal')

        elif signum == 6:
            logger.info('Requested close by SIGABRT (process abort signal). Code 6.')

        self.exit()


if __name__ == '__main__':
    import signal

    app = GitAutoDeploy()

    signal.signal(signal.SIGHUP, app.signal_handler)
    signal.signal(signal.SIGINT, app.signal_handler)
    signal.signal(signal.SIGABRT, app.signal_handler)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    app.run()