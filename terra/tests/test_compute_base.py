import os
from unittest import mock

from terra import settings
from terra.compute import base
from .utils import TestCase


# Registration test
class Foo:
  class TestService(base.BaseService):
    pass


class TestService_base(Foo.TestService, base.BaseService):
  pass


class TestServiceBase(TestCase):
  def setUp(self):
    # I want to be able to use settings
    self.patches.append(mock.patch.object(settings, '_wrapped', None))
    super().setUp()
    settings.configure({})

  # Simulate external env var
  @mock.patch.dict(os.environ, {'FOO': "BAR"})
  def test_env(self):
    # Test that a service inherits the environment correctly
    service = base.BaseService()
    # App specific env var
    service.env['BAR'] = 'foo'
    # Make sure both show up
    self.assertEqual(service.env['FOO'], 'BAR')
    self.assertEqual(service.env['BAR'], 'foo')
    # Make sure BAR is isolated from process env
    self.assertNotIn("BAR", os.environ)

  def test_add_volumes(self):
    service = base.BaseService()
    # Add a volumes
    service.add_volume("/local", "/remote")
    # Make sure it's in the list
    self.assertIn(("/local", "/remote"), service.volumes)

  # Unconfigure settings
  @mock.patch.object(settings, '_wrapped', None)
  def test_volumes_and_configuration_map(self):
    # Add a volumes
    service = base.BaseService()
    service.add_volume("/local", "/remote")

    # Test configuration_map
    settings.configure({})
    # Make sure the volume is in the map
    self.assertEqual([("/local", "/remote")],
                     base.BaseCompute().configuration_map(service))

  @mock.patch.dict(base.services, clear=True)
  def test_registry(self):
    # Register a class class, just for fun
    base.BaseCompute.register(Foo.TestService)(TestService_base)

    self.assertIn(Foo.TestService.__module__ + '.Foo.TestService',
                  base.services)


class TestUnitTests(TestCase):
  def last_test_registered_services(self):
    self.assertFalse(
      base.services,
      msg="If you are seting this, one of the other unit tests has "
      "registered a terra service. This side effect should be "
      "prevented by mocking out the terra.compute.base.services dict. "
      "Otherwise unit tests can interfere with each other.")
