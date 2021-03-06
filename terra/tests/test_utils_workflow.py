from unittest import mock
import re
import os
import json

from terra.utils.workflow import resumable, AlreadyRunException
from terra import settings
from terra.logger import DEBUG1
from .utils import TestCase


class Klass:
  pass


class TestResumable(TestCase):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.patches = []

  def setUp(self):
    self.patches.append(mock.patch.dict(os.environ,
                                        {'TERRA_SETTINGS_FILE': ""}))
    self.patches.append(mock.patch.object(settings, '_wrapped', None))
    super().setUp()
    settings.configure({'processing_dir': self.temp_dir.name})

  def test_simple(self):
    @resumable
    def test1(self):
      self.x = 13
    status = Klass()
    test1(status)
    self.assertEqual(status.x, 13)

  def test_no_multi_run(self):
    @resumable
    def test1(self):
      pass

    test1(Klass())
    with self.assertRaises(AlreadyRunException):
      test1(Klass())
    with self.assertRaises(AlreadyRunException):
      test1(Klass())

  def test_status_file(self):
    @resumable
    def test1(self):
      pass

    test1(Klass())

    with open(settings.status_file, 'r') as fid:
      status = json.load(fid)
    self.assertEqual(status['stage_status'], 'done')
    self.assertEqual(status['stage'], f'{__file__}//{test1.__qualname__}')

  def test_status_file_stop_in_middle(self):
    @resumable
    def test1(self):
      pass

    @resumable
    def test2(self):
      raise RuntimeError('foobar')

    test1(Klass())
    with open(settings.status_file, 'r') as fid:
      status = json.load(fid)
    self.assertEqual(status['stage_status'], 'done')
    self.assertEqual(status['stage'], f'{__file__}//{test1.__qualname__}')

    with self.assertRaisesRegex(RuntimeError, '^foobar$'):
      test2(Klass())

    with open(settings.status_file, 'r') as fid:
      status = json.load(fid)
    self.assertEqual(status['stage_status'], 'starting')
    self.assertEqual(status['stage'], f'{__file__}//{test2.__qualname__}')

  def test_resuming(self):
    @resumable
    def test1(self):
      self.x = 12
      return 11

    @resumable
    def test2(self):
      self.y = 13
      return 17

    klass = Klass()

    with settings:
      settings.resume = True
      with open(settings.status_file, 'w') as fid:
        json.dump({'stage_status': 'done',
                   'stage': f'{__file__}//{test1.__qualname__}'}, fid)

      with self.assertLogs(resumable.__module__, DEBUG1) as cm:
        self.assertIsNone(test1(klass))
        self.assertEqual(test2(klass), 17)

      self.assertEqual(klass.y, 13)
      self.assertFalse(hasattr(klass, 'x'))

      self.assertTrue(any(re.search(
          f"Skipping .*{test1.__qualname__}", o) for o in cm.output))
      self.assertTrue(any(re.search(
          f"Starting stage: .*{test2.__qualname__}", o) for o in cm.output))
      self.assertTrue(any(re.search(
          f"Finished stage: .*{test2.__qualname__}", o) for o in cm.output))

  def test_resuming_after_incomplete(self):
    @resumable
    def test1(self):
      self.x = 12
      return 11

    @resumable
    def test2(self):
      self.y = 13
      return 17

    klass = Klass()

    with settings:
      settings.resume = True
      with open(settings.status_file, 'w') as fid:
        json.dump({'stage_status': 'starting',
                   'stage': f'{__file__}//{test2.__qualname__}'}, fid)

      with self.assertLogs(resumable.__module__, DEBUG1) as cm:
        self.assertIsNone(test1(klass))
        self.assertEqual(test2(klass), 17)

      self.assertEqual(klass.y, 13)
      self.assertFalse(hasattr(klass, 'x'))

      self.assertTrue(any(re.search(
          f"Skipping .*{test1.__qualname__}", o) for o in cm.output))
      self.assertTrue(any(re.search(
          f"Starting stage: .*{test2.__qualname__}", o) for o in cm.output))
      self.assertTrue(any(re.search(
          f"Finished stage: .*{test2.__qualname__}", o) for o in cm.output))

  def test_resume_no_status_file(self):
    settings.resume = True

    @resumable
    def test1(self):
      self.x = 12
      return 11

    klass = Klass()

    self.assertNotExist(settings.status_file)
    self.assertEqual(test1(klass), 11)
    self.assertExist(settings.status_file)
    self.assertEqual(klass.x, 12)
