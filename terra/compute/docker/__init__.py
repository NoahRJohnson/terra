import os
from os import environ as env
from subprocess import Popen, PIPE
from shlex import quote
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import json

from envcontext import EnvironmentContext
import yaml

from vsi.tools.diff import dict_diff
from vsi.tools.python import nested_patch

from terra import settings
from terra.core.settings import TerraJSONEncoder, filename_suffixes
from terra.compute.base import BaseService, BaseCompute, StageRunFailed
from terra.compute.utils import load_service
from terra.logger import getLogger, DEBUG1
logger = getLogger(__name__)


docker_volume_re = r'^(([a-zA-Z]:[/\\])?[^:]*):(([a-zA-Z]:[/\\])?[^:]*)' \
                   r'((:ro|:rw|:z|:Z|:r?shared|:r?slave|:r?private|' \
                   r':delegated|:cached|:consistent|:nocopy)*)$'
'''str: A regular expression to parse old style docker volume strings

RE Groups

* 0: Source
* 1: Junk - source drive (windows)
* 2: Target
* 4: Junk - target drive (windows)
* 5: Flags
* 6: Junk - last flag
'''


class Compute(BaseCompute):
  '''
  Docker compute model, specifically ``docker-compose``
  '''

  docker_volume_re = r'^(([a-zA-Z]:[/\\])?[^:]*):(([a-zA-Z]:[/\\])?[^:]*)' \
                     r'((:ro|:rw|:z|:Z|:r?shared|:r?slave|:r?private|' \
                     r':delegated|:cached|:consistent|:nocopy)*)$'

  def just(self, *args, **kwargs):
    '''
    Run a ``just`` command. Primarily used to run
    ``--wrap Just-docker-compose``

    Arguments
    ---------
    *args :
        List of arguments to be pass to ``just``
    **kwargs :
        Arguments sent to Popen command
    '''
    logger.debug('Running: ' + ' '.join(
        # [quote(f'{k}={v}') for k, v in env.items()] +
        [quote(x) for x in ('just',) + args]))
    env = kwargs.pop('env', {})
    env['JUSTFILE'] = os.path.join(os.environ['TERRA_TERRA_DIR'], 'Justfile')

    if logger.getEffectiveLevel() <= DEBUG1:
      dd = dict_diff(os.environ, env)[3]
      if dd:
        logger.debug1('Environment Modification:\n' + '\n'.join(dd))

    with EnvironmentContext(**env):
      pid = Popen(('just',) + args, **kwargs)
      pid.wait()
      return pid

  def run(self, service_class):
    '''
    Use the service class information to run the service runner in a docker
    using

    .. code-block:: bash

        just --wrap Just-docker-compose \\
            -f {service_info.compose_file} \\
            run {service_info.compose_service_name} \\
            {service_info.command}
    '''
    service_info = load_service(service_class)
    service_info.pre_run(self)

    pid = self.just("--wrap", "Just-docker-compose",
                    '-f', service_info.compose_file,
                    'run', service_info.compose_service_name,
                    *(service_info.command),
                    env=service_info.env)

    if pid.returncode != 0:
      raise(StageRunFailed())

    service_info.post_run(self)

  def config(self, service_class, extra_compose_files=[]):
    '''
    Returns the ``docker-compose config`` output
    '''

    service_info = load_service(service_class)

    args = ["--wrap", "Just-docker-compose",
            '-f', service_info.compose_file] + \
        sum([['-f', extra] for extra in extra_compose_files], []) + \
        ['config']

    pid = self.just(*args, stdout=PIPE,
                    env=service_info.env)
    return pid.communicate()[0]

  def configuration_map(self, service_class, extra_compose_files=[]):
    '''
    Returns the mapping of volumes from the host to the container.

    Returns
    -------
    list
        Return a list of tuple pairs [(host, remote), ... ] of the volumes
        mounted from the host to container
    """

    '''
    # TODO: Make an OrderedDict
    volume_map = []

    service_info = load_service(service_class)
    config = yaml.load(self.config(service_info, extra_compose_files))

    for volume in config['services'][service_info.compose_service_name].get('volumes', []):
      if isinstance(volume, dict):
        if volume['type'] == 'bind':
          volume_map.append((volume['source'], volume['target']))
      else:
        if volume.startswith('/'):
          ans = re.match(docker_volume_re, volume).groups()
          volume_map.append((ans[0], ans[2]))

    volume_map = volume_map + service_info.volumes

    slashes = '/'
    if os.name == 'nt':
      slashes += '\\'

    # Strip trailing /'s to make things look better
    return [(volume_host.rstrip(slashes), volume_remote.rstrip(slashes))
            for volume_host, volume_remote in volume_map]


class Service(BaseService):
  '''
  Base docker service class
  '''

  def __init__(self):
    super().__init__()
    self.volumes_flags = []

  def pre_run(self, compute):  # Compute?
    super().pre_run(compute)

    self.temp_dir = TemporaryDirectory()
    temp_dir = Path(self.temp_dir.name)

    # with open(self.compose_file, 'r') as fid:
    #   docker_file = yaml.load(fid.read())

    # # Need to get the docker-compose version :-\
    # with open(self.compose_file, 'r') as fid:
    #   docker_file = yaml.load(fid.read())

    # temp_compose = f'version: "{docker_file["version"]}"\n'
    # temp_compose += 'services:\n'
    # temp_compose +=f'  {self.compose_service_name}:\n'
    # temp_compose += '    volumes:\n'

    # for (volume_host, volume_container), volume_flags in \
    #     zip(self.volumes, self.volumes_flags):
    #   temp_compose += f'      - {volume_host}:{volume_container}\n' #), volume_flags}\n'

    # temp_compose_file = temp_dir / "docker-compose.yml"

    # with open(temp_compose_file, 'w') as fid:
    #   fid.write(temp_compose)

    # Check to see if and are already defined, this will play nicely with
    # external influences
    env_volume_index = 1
    while f'TERRA_VOLUME_{env_volume_index}' in self.env:
      env_volume_index += 1

    # Setup volumes for docker
    self.env[f'TERRA_VOLUME_{env_volume_index}'] = \
        f'{str(temp_dir)}:/tmp_settings:rw'
    env_volume_index += 1

    for index, ((volume_host, volume_container), volume_flags) in \
        enumerate(zip(self.volumes, self.volumes_flags)):  # noqa bug
      volume_str = f'{volume_host}:{volume_container}'
      if volume_flags:
        volume_str += f':{volume_flags}'
      self.env[f'TERRA_VOLUME_{env_volume_index}'] = volume_str
      env_volume_index += 1

    # volume_map = compute.configuration_map(self, [str(temp_compose_file)])
    volume_map = compute.configuration_map(self)

    logger.debug3("Volume map: %s", volume_map)

    # Setup config file for docker
    docker_config = TerraJSONEncoder.serializableSettings(settings)

    self.env['TERRA_SETTINGS_FILE'] = '/tmp_settings/config.json'

    if os.name == "nt":
      logger.warning("Windows volume mapping is experimental.")

      def patch_volume(value, volume_map):
        for vol_from, vol_to in volume_map:
          if (isinstance(value, str)
              and value.lower().startswith(vol_from.lower())):
            return value.lower().replace(vol_from.lower(), vol_to.lower(), 1)
        return value
    else:
      def patch_volume(value, volume_map):
        for vol_from, vol_to in volume_map:
          if isinstance(value, str) and value.startswith(vol_from):
            return value.replace(vol_from, vol_to, 1)
        return value

    docker_config = nested_patch(
        docker_config,
        lambda key, value: (isinstance(key, str) and
                            any(key.endswith(pattern)
                            for pattern in filename_suffixes)),  # noqa bug
        lambda key, value: patch_volume(value, reversed(volume_map))
    )

    # This test doesn't work. It's already faked out long before this
    # if 'processing_dir' not in docker_config:
    #   logger.warning('No processing dir set. Using "/tmp"')
    docker_config['processing_dir'] = '/tmp'  # TODO: Remove/unhardcode this

    with open(temp_dir / 'config.json', 'w') as fid:
      json.dump(docker_config, fid)

  def post_run(self, compute):
    super().post_run(compute)

    self.temp_dir = None  # Delete temp_dir

  def add_volume(self, local, remote, flags=None):
    self.volumes.append([local, remote])
    self.volumes_flags.append(flags)
