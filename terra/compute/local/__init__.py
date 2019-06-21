import subprocess

from terra.compute.base import BaseService, BaseCompute
from terra.compute.utils import load_service
from terra.logger import getLogger
from terra import settings
logger = getLogger(__name__)


class Compute(BaseCompute):
  '''
  local computing model
  '''

  # run the service locally with the subprocess module
  def run(self, service_class):
    service_info = load_service(service_class)

    # pre run
    logger.debug2("pre-running %s ", str(service_info))
    service_info.pre_run()

    # Replace 'python' command with virtual environment python executable
    if settings.compute.get('venv_python', None):
      service_info.command = [settings.compute.venv_python
                              if el == 'python' else el
                              for el in service_info.command]

    # run command -- command must be a list of strings
    subprocess.call(service_info.command)

    # post run
    logger.debug2("post-running %s ", str(service_info))
    service_info.post_run()


class Service(BaseService):
  '''
  Base service class, prints pre_run and post_run steps
  '''

  def pre_run(self):
    logger.debug("local pre run: " + str(self))

  def post_run(self):
    logger.debug("local post run: " + str(self))