import os
import re
import json
from unittest import mock
import warnings

from terra import settings
from terra.compute import base
from terra.compute import docker
from terra.compute import compute
import terra.compute.utils

from .utils import TestCase


class TestComputeDockerCase(TestCase):
  def setUp(self):
    # Use settings
    self.patches.append(mock.patch.object(settings, '_wrapped', None))
    # This will resets the _connection to an uninitialized state
    self.patches.append(
        mock.patch.object(terra.compute.utils.ComputeHandler,
                          '_connection',
                          mock.PropertyMock(return_value=docker.Compute())))

    # patches.append(mock.patch.dict(base.services, clear=True))
    super().setUp()

    # Configure for docker
    settings.configure({
        'compute': {'arch': 'docker'},
        'processing_dir': self.temp_dir.name,
        'test_dir': '/opt/projects/terra/terra_dsm/external/terra/foo'})


class TestDockerRe(TestComputeDockerCase):
  def test_re(self):
    # Copied from test-docker_functions.bsh "Docker volume string parsing"
    host_paths = (".",
                  "/",
                  "C:\\",
                  "/foo/bar",
                  "/foo/b  ar",
                  "D:/foo/bar",
                  "D:\\foo\\bar",
                  "vl")
    docker_paths = ("/test/this",
                    "/te st/th  is",
                    "C:\\",
                    "z")
    test_volume_flags = ("",
                         ":ro",
                         ":ro:z",
                         ":z:ro",
                         ":Z:rshared:rw:nocopy")

    parser = re.compile(docker.docker_volume_re)

    for host_path in host_paths:
      for docker_path in docker_paths:
        for test_volume_flag in test_volume_flags:
          results = parser.match(host_path + ":" + docker_path
                                 + test_volume_flag).groups()

          self.assertEqual(results[0], host_path)
          self.assertEqual(results[2], docker_path)
          self.assertEqual(results[4], test_volume_flag)


###############################################################################


# Dummy mock to double check args
def mock_popen(*args, **kwargs):
  return (args, kwargs)


class TestDockerJust(TestComputeDockerCase):
  def setUp(self):
    self.patches.append(mock.patch.object(docker, 'Popen', mock_popen))
    super().setUp()
    # Make a copy
    self.original_env = os.environ.copy()

  def tearDown(self):
    super().tearDown()
    # Make sure nothing inadvertently changed environ
    self.assertEqual(self.original_env, os.environ)

  def test_just_simple(self):
    default_justfile = os.path.join(os.environ['TERRA_TERRA_DIR'], 'Justfile')
    # Create a compute
    compute = docker.Compute()
    # Call just, and get the args calculated, retrieved via mock
    args, kwargs = compute.just("foo  bar")
    self.assertEqual(args, (('bash', 'just', 'foo  bar'),))
    self.assertEqual(set(kwargs.keys()), {'executable', 'env'})
    self.assertEqual(kwargs['env']['JUSTFILE'], default_justfile)

  def test_just_custom_env(self):
    default_justfile = os.path.join(os.environ['TERRA_TERRA_DIR'], 'Justfile')
    # Use the env kwarg
    args, kwargs = compute.just("foo", "bar", env={"FOO": "BAR"})
    self.assertEqual(args, (('bash', 'just', 'foo', 'bar'),))
    self.assertEqual(set(kwargs.keys()), {'executable', 'env'})
    self.assertTrue(kwargs.pop('executable').endswith('bash'))
    self.assertEqual(kwargs, {'env': {'FOO': 'BAR',
                                      'JUSTFILE': default_justfile}})

  def test_just_custom_justfile(self):
    # Use the justfile kwarg
    args, kwargs = compute.just("foobar", justfile="/foo/bar")
    self.assertEqual(args, (('bash', 'just', 'foobar'),))
    self.assertEqual(set(kwargs.keys()), {'executable', 'env'})
    self.assertEqual(kwargs['env']['JUSTFILE'], "/foo/bar")

  def test_just_kwargs(self):
    default_justfile = os.path.join(os.environ['TERRA_TERRA_DIR'], 'Justfile')
    # Use the shell kwarg for Popen
    args, kwargs = compute.just("foobar", shell=False)
    self.assertEqual(args, (('bash', 'just', 'foobar'),))
    self.assertEqual(set(kwargs.keys()), {'executable', 'env', 'shell'})
    self.assertEqual(kwargs['shell'], False)
    self.assertEqual(kwargs['env']['JUSTFILE'], default_justfile)

  def test_logging_code(self):
    # Test the debug1 diffdict log output
    with self.assertLogs(docker.__name__, level="DEBUG1") as cm:
      env = os.environ.copy()
      env.pop('PATH')
      env['FOO'] = 'BAR'
      # Sometimes JUSTFILE is set, so make this part of the test!
      with mock.patch.dict(os.environ, JUSTFILE='/foo/bar'):
        compute.just("foo", "bar", env=env)

    env_lines = [x for x in cm.output if "Environment Modification:" in x][0]
    env_lines = env_lines.split('\n')
    self.assertEqual(len(env_lines), 5, env_lines)

    # Verify logs say PATH was removed
    self.assertTrue(any(o.startswith('- PATH:') for o in env_lines))
    # FOO was added
    self.assertTrue(any(o.startswith('+ FOO:') for o in env_lines))
    # JUSTFILE was changed
    self.assertTrue(any(o.startswith('+ JUSTFILE:') for o in env_lines))
    self.assertTrue(any(o.startswith('- JUSTFILE:') for o in env_lines))


###############################################################################


class MockJustService:
  compose_file = "file1"
  compose_service_name = "launch"
  command = ["ls"]
  env = {"BAR": "FOO"}


class TestDockerRun(TestComputeDockerCase):
  # Create a special mock functions that takes the Tests self as _self, and the
  # rest of the args as args/kwargs. This lets me do testing inside the mocked
  # function. self.expected_args and self.expected_kwargs should be set by the
  # test function before calling. self.return_value should also be set before
  # calling, so that the expected return value is returned
  def mock_just(_self, *args, **kwargs):
    _self.just_args = args
    _self.just_kwargs = kwargs
    return type('blah', (object,), {'wait': lambda self: _self.return_value})()

  def setUp(self):
    # Mock the just call for recording
    self.patches.append(mock.patch.object(docker.Compute, 'just',
                                          self.mock_just))
    super().setUp()

  def test_run(self):
    compute = docker.Compute()

    self.return_value = 0
    # This part of the test looks fragile
    compute.run(MockJustService())
    # Run a docker service
    self.assertEqual(('--wrap', 'Just-docker-compose',
                      '-f', 'file1', 'run', 'launch', 'ls'),
                     self.just_args)
    self.assertEqual({'env': {'BAR': 'FOO'}}, self.just_kwargs)

    # Test a non-zero return value
    self.return_value = 1
    with self.assertRaises(base.ServiceRunFailed):
      compute.run(MockJustService())


###############################################################################


class TestDockerConfig(TestComputeDockerCase):
  def setUp(self):
    # Mock the just call for recording
    self.patches.append(mock.patch.object(docker.Compute, 'just',
                                          self.mock_just_config))
    super().setUp()

  # Create a special mock functions that takes the Tests self as _self, and the
  # rest of the args as args/kwargs. This lets me do testing inside the mocked
  # function.
  def mock_just_config(_self, *args, **kwargs):
    _self.just_args = args
    _self.just_kwargs = kwargs
    # _self.assertEqual(args, _self.expected_args)
    # _self.assertEqual(kwargs, _self.expected_kwargs)
    return type('blah', (object,),
                {'communicate': lambda self: ('out', None)})()

  def test_config(self):
    compute = docker.Compute()

    self.assertEqual(compute.config(MockJustService()), 'out')
    self.assertEqual(('--wrap', 'Just-docker-compose', '-f', 'file1',
                      'config'), self.just_args)

    self.assertEqual({'stdout': docker.PIPE, 'env': {'BAR': 'FOO'}},
                     self.just_kwargs)

  def test_config_with_custom_files(self):
    compute = docker.Compute()
    self.assertEqual(compute.config_service(MockJustService(),
                                            ['file15.yml', 'file2.yaml']),
                     'out')
    self.assertEqual(('--wrap', 'Just-docker-compose', '-f', 'file1',
                      '-f', 'file15.yml', '-f', 'file2.yaml',
                      'config'),
                     self.just_args)
    self.assertEqual({'stdout': docker.PIPE, 'env': {'BAR': 'FOO'}},
                     self.just_kwargs)


###############################################################################


mock_yaml = r'''secrets:
  redis_commander_secret:
    file: /opt/projects/terra/terra_dsm/external/terra/redis_commander_password.secret
  redis_secret:
    file: /opt/projects/terra/terra_dsm/external/terra/redis_password.secret
services:
  ipykernel:
    build:
      args:
        TERRA_PIPENV_DEV: '0'
      context: /opt/projects/terra/terra_dsm/external/terra
      dockerfile: docker/terra.Dockerfile
    cap_add:
    - SYS_PTRACE
    environment:
      DISPLAY: :1
      DOCKER_GIDS: 1000 10 974
      DOCKER_GROUP_NAMES: group wheel docker
      DOCKER_UID: '1001'
      DOCKER_USERNAME: user
      JUSTFILE: /terra/docker/terra.Justfile
      JUST_DOCKER_ENTRYPOINT_INTERNAL_VOLUMES: /venv
      JUST_SETTINGS: /terra/terra.env
      PYTHONPATH: /src
      TERRA_APP_DIR: /src
      TERRA_APP_DIR_HOST: /opt/projects/terra/terra_dsm/external/terra
      TERRA_REDIS_COMMANDER_PORT: '4567'
      TERRA_REDIS_COMMANDER_PORT_HOST: '4567'
      TERRA_REDIS_DIR: /data
      TERRA_REDIS_DIR_HOST: terra-redis
      TERRA_REDIS_HOSTNAME: terra-redis
      TERRA_REDIS_HOSTNAME_HOST: localhost
      TERRA_REDIS_PORT: '6379'
      TERRA_REDIS_PORT_HOST: '6379'
      TERRA_REDIS_SECRET: redis_password
      TERRA_REDIS_SECRET_HOST: redis_secret
      TERRA_SETTINGS_DIR: /settings
      TERRA_TERRA_DIR: /terra
      TERRA_TERRA_DIR_HOST: /opt/projects/terra/terra_dsm/external/terra
      TZ: /usr/share/zoneinfo/America/New_York]
    image: terra:terra_me
    ports:
    - published: 10001
      target: 10001
    - published: 10002
      target: 10002
    - published: 10003
      target: 10003
    - published: 10004
      target: 10004
    - published: 10005
      target: 10005
    volumes:
    - /tmp:/bar:ro
    - read_only: true
      source: /opt/projects/terra/terra_dsm/external/terra
      target: /src
      type: bind
    - read_only: true
      source: /opt/projects/terra/terra_dsm/external/terra
      target: /terra
      type: bind
    - /tmp/.X11-unix:/tmp/.X11-unix:ro
    - source: terra-venv
      target: /venv
      type: volume
    - read_only: true
      source: /opt/projects/terra/terra_dsm/external/terra/external/vsi_common
      target: /vsi
      type: bind
  redis-commander:
    command: "sh -c '\n  echo -n '\"'\"'{\n    \"connections\":[\n      {\n      \
      \  \"password\": \"'\"'\"' > /redis-commander/config/local-production.json\n\
      \  cat /run/secrets/redis_password | sed '\"'\"'s|\\\\|\\\\\\\\|g;s|\"|\\\\\"\
      |g'\"'\"' >> /redis-commander/config/local-production.json\n  echo -n '\"'\"\
      '\",\n        \"host\": \"terra-redis\",\n        \"label\": \"terra\",\n  \
      \      \"dbIndex\": 0,\n        \"connectionName\": \"redis-commander\",\n \
      \       \"port\": \"6379\"\n      }\n    ],\n    \"server\": {\n      \"address\"\
      : \"0.0.0.0\",\n      \"port\": 4567,\n      \"httpAuth\": {\n        \"username\"\
      : \"admin\",\n        \"passwordHash\": \"'\"'\"'>> /redis-commander/config/local-production.json\n\
      \    cat \"/run/secrets/redis_commander_password.secret\" | sed '\"'\"'s|\\\\\
      |\\\\\\\\|g;s|\"|\\\\\"|g'\"'\"' >> /redis-commander/config/local-production.json\n\
      \    echo '\"'\"'\"\n      }\n    }\n  }'\"'\"' >> /redis-commander/config/local-production.json\n\
      \  /redis-commander/docker/entrypoint.sh'\n"
    environment:
      TERRA_APP_DIR: /src
      TERRA_APP_DIR_HOST: /opt/projects/terra/terra_dsm/external/terra
      TERRA_REDIS_COMMANDER_PORT: '4567'
      TERRA_REDIS_COMMANDER_PORT_HOST: '4567'
      TERRA_REDIS_DIR: /data
      TERRA_REDIS_DIR_HOST: terra-redis
      TERRA_REDIS_HOSTNAME: terra-redis
      TERRA_REDIS_HOSTNAME_HOST: localhost
      TERRA_REDIS_PORT: '6379'
      TERRA_REDIS_PORT_HOST: '6379'
      TERRA_REDIS_SECRET: redis_password
      TERRA_REDIS_SECRET_HOST: redis_secret
      TERRA_SETTINGS_DIR: /settings
      TERRA_TERRA_DIR: /terra
      TERRA_TERRA_DIR_HOST: /opt/projects/terra/terra_dsm/external/terra
    image: rediscommander/redis-commander
    ports:
    - published: 4567
      target: 4567
    secrets:
    - source: redis_commander_secret
      target: redis_commander_password.secret
    - source: redis_secret
      target: redis_password
    volumes:
    - /tmp:/bar:ro
    - /tmp/.X11-unix:/tmp/.X11-unix:ro
  terra:
    build:
      args:
        TERRA_PIPENV_DEV: '0'
      context: /opt/projects/terra/terra_dsm/external/terra
      dockerfile: docker/terra.Dockerfile
    cap_add:
    - SYS_PTRACE
    environment:
      DISPLAY: :1
      DOCKER_GIDS: 1000 10 974
      DOCKER_GROUP_NAMES: group wheel docker
      DOCKER_UID: '1001'
      DOCKER_USERNAME: user
      JUSTFILE: /terra/docker/terra.Justfile
      JUST_DOCKER_ENTRYPOINT_INTERNAL_VOLUMES: /venv
      JUST_SETTINGS: /terra/terra.env
      PYTHONPATH: /src
      TERRA_APP_DIR: /src
      TERRA_APP_DIR_HOST: /opt/projects/terra/terra_dsm/external/terra
      TERRA_REDIS_COMMANDER_PORT: '4567'
      TERRA_REDIS_COMMANDER_PORT_HOST: '4567'
      TERRA_REDIS_DIR: /data
      TERRA_REDIS_DIR_HOST: terra-redis
      TERRA_REDIS_HOSTNAME: terra-redis
      TERRA_REDIS_HOSTNAME_HOST: localhost
      TERRA_REDIS_PORT: '6379'
      TERRA_REDIS_PORT_HOST: '6379'
      TERRA_REDIS_SECRET: redis_password
      TERRA_REDIS_SECRET_HOST: redis_secret
      TERRA_SETTINGS_DIR: /settings
      TERRA_TERRA_DIR: /terra
      TERRA_TERRA_DIR_HOST: /opt/projects/terra/terra_dsm/external/terra
      TZ: /usr/share/zoneinfo/America/New_York]
    image: terra:terra_me
    volumes:
    - /tmp:/bar:ro
    - read_only: true
      source: /opt/projects/terra/terra_dsm/external/terra
      target: /src
      type: bind
    - read_only: true
      source: /opt/projects/terra/terra_dsm/external/terra
      target: /terra
      type: bind
    - /tmp/.X11-unix:/tmp/.X11-unix:ro
    - source: terra-venv
      target: /venv
      type: volume
    - read_only: true
      source: /opt/projects/terra/terra_dsm/external/terra/external/vsi_common
      target: /vsi
      type: bind
  test:
    build:
      args:
        TERRA_PIPENV_DEV: '0'
      context: /opt/projects/terra/terra_dsm/external/terra
      dockerfile: docker/terra.Dockerfile
    cap_add:
    - SYS_PTRACE
    environment:
      DISPLAY: :1
      DOCKER_GIDS: 1000 10 974
      DOCKER_GROUP_NAMES: group wheel docker
      DOCKER_UID: '1001'
      DOCKER_USERNAME: user
      JUSTFILE: /terra/docker/terra.Justfile
      JUST_DOCKER_ENTRYPOINT_INTERNAL_VOLUMES: /venv
      JUST_SETTINGS: /terra/terra.env
      PYTHONPATH: /src
      TERRA_APP_DIR: /src
      TERRA_APP_DIR_HOST: /opt/projects/terra/terra_dsm/external/terra
      TERRA_REDIS_COMMANDER_PORT: '4567'
      TERRA_REDIS_COMMANDER_PORT_HOST: '4567'
      TERRA_REDIS_DIR: /data
      TERRA_REDIS_DIR_HOST: terra-redis
      TERRA_REDIS_HOSTNAME: terra-redis
      TERRA_REDIS_HOSTNAME_HOST: localhost
      TERRA_REDIS_PORT: '6379'
      TERRA_REDIS_PORT_HOST: '6379'
      TERRA_REDIS_SECRET: redis_password
      TERRA_REDIS_SECRET_HOST: redis_secret
      TERRA_SETTINGS_DIR: /settings
      TERRA_TERRA_DIR: /terra
      TERRA_TERRA_DIR_HOST: /opt/projects/terra/terra_dsm/external/terra
      TZ: /usr/share/zoneinfo/America/New_York]
    image: terra:terra_me
    volumes:
    - /tmp:/bar:ro
    - source: /opt/projects/terra/terra_dsm/external/terra
      target: /terra
      type: bind
    - /tmp/.X11-unix:/tmp/.X11-unix:ro
    - source: terra-venv
      target: /venv
      type: volume
    - read_only: true
      source: /opt/projects/terra/terra_dsm/external/terra/external/vsi_common
      target: /vsi
      type: bind
version: '3.7'
volumes:
  terra-venv:
    labels:
      com.vsi.just.clean_action: delete
      com.vsi.just.clean_setup: run terra nopipenv true
'''  # noqa


def mock_config(*args, **kwargs):
  return mock_yaml


class TestDockerMap(TestComputeDockerCase):
  class Service:
    compose_service_name = "foo"
    volumes = []

  @mock.patch.object(docker.Compute, 'config_service', mock_config)
  def test_config_non_existing_service(self):
    compute = docker.Compute()
    service = TestDockerMap.Service()

    with warnings.catch_warnings():
      warnings.simplefilter('ignore')
      # USe the default name, foo, which doesn't even exist
      volume_map = compute.configuration_map(service)
    # Should be empty
    self.assertEqual(volume_map, [])

  @mock.patch.object(docker.Compute, 'config_service', mock_config)
  def test_config_terra_service(self):
    compute = docker.Compute()
    service = TestDockerMap.Service()

    service.compose_service_name = "terra"
    volume_map = compute.configuration_map(service)
    ans = [('/tmp', '/bar'),
           ('/opt/projects/terra/terra_dsm/external/terra', '/src'),
           ('/opt/projects/terra/terra_dsm/external/terra', '/terra'),
           ('/tmp/.X11-unix', '/tmp/.X11-unix'),
           ('/opt/projects/terra/terra_dsm/external/terra/external/vsi_common',
            '/vsi')]
    self.assertEqual(volume_map, ans)

  @mock.patch.object(docker.Compute, 'config_service', mock_config)
  def test_config_test_service(self):
    compute = docker.Compute()
    service = TestDockerMap.Service()

    service.compose_service_name = "test"
    volume_map = compute.configuration_map(service)
    ans = [('/tmp', '/bar'),
           ('/opt/projects/terra/terra_dsm/external/terra', '/terra'),
           ('/tmp/.X11-unix', '/tmp/.X11-unix'),
           ('/opt/projects/terra/terra_dsm/external/terra/external/vsi_common',
            '/vsi')]
    self.assertEqual(volume_map, ans)

###############################################################################


class SomeService(docker.Service):
  def __init__(self, compose_service_name="launch", compose_file="file1",
               command=["ls"], env={"BAR": "FOO"}):
    self.compose_service_name = compose_service_name
    self.compose_file = compose_file
    self.command = command
    self.env = env
    super().__init__()


def mock_map(self, *args, **kwargs):
  return [('/foo', '/bar'),
          ('/tmp/.X11-unix', '/tmp/.X11-unix')]


class TestDockerService(TestComputeDockerCase):
  # Test the flushing configuration to json for a container mechanism

  def common(self, compute, service):
    service.pre_run()
    setup_dir = service.temp_dir.name
    with open(os.path.join(setup_dir, 'config.json'), 'r') as fid:
      config = json.load(fid)

    # Test that foo_dir has been translated
    self.assertEqual(config['foo_dir'], '/bar',
                     'Path translation test failed')
    # Test that bar_dir has not changed
    self.assertEqual(config['bar_dir'], '/not_foo',
                     'Nontranslated directory failure')
    # Verify the setting files is pointed to correctly
    self.assertEqual(service.env['TERRA_SETTINGS_FILE'],
                     "/tmp_settings/config.json",
                     'Failure to set TERRA_SETTINGS_FILE')
    # Clean up temp file
    service.post_run()

    # Test that the config dir was set to be mounted
    self.assertIn(f'{setup_dir}:/tmp_settings:rw',
                  (v for k, v in service.env.items()
                   if k.startswith('TERRA_VOLUME_')),
                  'Configuration failed to injected into docker')

  @mock.patch.object(docker.Compute, 'configuration_map_service', mock_map)
  def test_service_simple(self):
    # Must not be a decorator, because at decorator time (before setUp is run),
    # settings._wrapped is still None. Mock the version from setUpModule so I
    # change the values without affecting any other test
    with mock.patch.dict(settings._wrapped, {}):
      compute = docker.Compute()
      compute.configuration_map(SomeService())

      # Test setting for translation
      settings.foo_dir = "/foo"
      settings.bar_dir = "/not_foo"

      service = SomeService()
      # Simple case
      self.common(compute, service)

  @mock.patch.object(docker.Compute, 'configuration_map_service', mock_map)
  def test_service_other_dir_methods(self):
    compute = docker.Compute()
    compute.configuration_map(SomeService())

    # Test setting for translation
    settings.foo_dir = "/foo"
    settings.bar_dir = "/not_foo"

    # Run same tests with a TERRA_VOLUME externally set
    service = SomeService()
    service.add_volume('/test1', '/test2', 'z')
    service.env['TERRA_VOLUME_1'] = "/Foo:/Bar"
    self.common(compute, service)
    # Make sure this is still set correctly
    self.assertEqual(service.env['TERRA_VOLUME_1'], "/Foo:/Bar")
    self.assertIn('/test1:/test2:z',
                  (v for k, v in service.env.items()
                   if k.startswith('TERRA_VOLUME_')),
                  'Added volume failed to be bound')

  def test_add_volume(self):
    service = SomeService()
    self.assertEqual(service.volumes, [])

    service.add_volume('/foo', '/bar')
    self.assertEqual(service.volumes, [('/foo', '/bar')])
    self.assertEqual(service.volumes_flags, [None])

    service.add_volume('/data', '/testData', 'ro')
    self.assertEqual(service.volumes, [('/foo', '/bar'),
                                       ('/data', '/testData')])
    self.assertEqual(service.volumes_flags, [None, 'ro'])
