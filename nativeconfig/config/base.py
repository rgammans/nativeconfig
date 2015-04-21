from abc import ABCMeta, abstractmethod
from collections import OrderedDict
import inspect
import logging
import threading

from nativeconfig.options.base import BaseOption
from nativeconfig.options import StringOption


LOG = logging.getLogger('nativeconfig')


class _OrderedClass(ABCMeta):
    """
    Simple metaclass that maintains list of all properties (including all superclasses) in order of definition.
    """
    @classmethod
    def __prepare__(metacls, name, bases):
        return OrderedDict()

    def __new__(cls, name, bases, classdict):
        result = type.__new__(cls, name, bases, dict(classdict))
        result.ordered_options = []

        # Get ordered options from base class.
        mro = inspect.getmro(result)
        if len(mro) > 1:
            if hasattr(mro[1], 'ordered_options'):
                result.ordered_options.extend(mro[1].ordered_options)

        for k, v in classdict.items():
            if inspect.isdatadescriptor(v):
                try:
                    i = result.ordered_options.index(v._name)
                except ValueError:
                    pass
                else:
                    del result.ordered_options[i]

                result.ordered_options.append(v._name)

        return result


class BaseConfig(metaclass=_OrderedClass):
    """
    Base class for all configs.

    Users are supposed to subclass for each config backend and then instantiate singleton via the get_instance method.

    @cvar CONFIG_VERSION: Version of the config. Used during migrations and usually should be identical to app's __version__.
    @cvar CONFIG_VERSION_OPTION_NAME: Name of the option that represents config version in backend.
    @cvar CREATE_IF_NEEDED: Whether entity should be created during initialization of config. E.g. file or registry record.
    @cvar CONFIG_PATH: Implementation-dependent path to config file. See docstring of concrete implementation.
    """
    CONFIG_VERSION = '1.0'
    CONFIG_VERSION_OPTION_NAME = "ConfigVersion"
    CREATE_IF_NEEDED = True
    CONFIG_PATH = None

    _instances = {}
    _instances_events = {}
    _instances_lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        """
        Return singleton for current config subclass.

        @rtype: type(cls)
        """
        with cls._instances_lock:
            instance_event = cls._instances_events.get(cls, threading.Event())

            if not instance_event.is_set():
                try:
                    cls._instances[cls] = cls()
                finally:
                    instance_event.set()
                    cls._instances_events[cls] = instance_event

        return cls._instances.get(cls, None)

    @classmethod
    def validate(cls):
        """
        Validate config options.

        @raise AttributeError: Raised if properties are duplicated.
        """
        properties = set()

        for attribute_name, attribute_value in inspect.getmembers(cls, inspect.isdatadescriptor):
            if isinstance(attribute_value, BaseOption):
                if attribute_value._name in properties:
                    raise AttributeError("Duplication of property named {}!".format(attribute_value._name))
                else:
                    properties.add(attribute_value._name)

    def __init__(self):
        self.validate()
        super().__init__()
        self.migrate(self.config_version)

#{ Default options

    config_version = StringOption(CONFIG_VERSION_OPTION_NAME, default=CONFIG_VERSION)

#{ Access options by name

    def get_value_for_option_name(self, name):
        """
        Get option's Raw Value by its name in backend.

        @param name: Name of the option.
        @type name: str

        @return: JSON Value. None if such option does not exist.
        @rtype: str or dict or list or None
        """
        attribute = self.property_for_option_name(name)

        if attribute:
            return attribute.serialize_json(attribute.fget(self))
        else:
            LOG.warning("No option named '%s'.", name)

    def set_value_for_option_name(self, name, json_value):
        """
        Set option by its name in backend.

        @param name: Name of the option.
        @type name: str or dict or list or None

        @param json_value: JSON value.
        @type json_value: str
        """
        attribute = self.property_for_option_name(name)

        if attribute:
            attribute.fset(self, attribute.deserialize_json(json_value))
        else:
            LOG.warning("No option named '%s'.", name)

    def set_one_shot_value_for_option_name(self, name, json_value):
        """
        Set One Shot Value for a given name.

        @param name: Name of the option.
        @type name: str

        @param json_value: JSON value.
        @type json_value: str or dict or list or None
        """
        attribute = self.property_for_option_name(name)

        if attribute:
            attribute.fset(self, attribute.deserialize_json(json_value))
        else:
            LOG.warning("No option named '%s'.", name)

    def del_value_for_option_name(self, name):
        """
        Delete option by its name in backend.

        @param name: Name of the option.
        @type name: str
        """
        attribute = self.property_for_option_name(name)

        if attribute:
            attribute.fdel(self)
        else:
            LOG.warning("No option named '%s'.", name)

    def snapshot(self):
        """
        Get snapshot of current config.

        @return: Dict of option: value format.
        @rtype: dict
        """
        options = {}

        for attribute_name in dir(type(self)):
            attribute = getattr(type(self), attribute_name)

            if isinstance(attribute, BaseOption):
                name = attribute._name
                options[name] = self.get_value_for_option_name(name)

        return options

#{ Introspection

    def property_for_option_name(self, name):
        """
        Return property object for an option name. Useful for introspection.

        @param name: Name of an option.
        @type name: str

        @rtype: BaseOption or None
        """
        for attribute_name in dir(type(self)):
            attribute = getattr(type(self), attribute_name)

            if isinstance(attribute, BaseOption) and attribute._name == name:
                return attribute
        else:
            return None

#{ Recovery and migrations

    def resolve_value(self, exception, name, raw_value):
        """
        Resolve Raw Value that cannot be deserialized or re-raise exception.

        Default implementation logs an exception and returns default value.

        @param exception: Exception that was raised during serialization.
        @type exception: DeserializationError

        @param name: Name of the option that cannot be deserialized.
        @type name: str

        @param raw_value: Raw value that cannot be deserialized.
        @type raw_value: str

        @return: Value to be used based on raw value.
        """
        LOG.error("Unable to deserialize value of '%s' from '%s': %s.", name, raw_value, exception)
        return self.property_for_option_name(name)._default

    def migrate(self, version):
        """
        Migrate options in backend from a given version to current one.

        @param version: Version to migrate. Can be None if config does not exist or does not contain value for that option.

        @note: Called even when versions match.
        """
        self.set_value(self.CONFIG_VERSION_OPTION_NAME, self.CONFIG_VERSION)

#{ Access backend

    @abstractmethod
    def get_value(self, name):
        """
        Return Raw Value for a given name or None if no value exists and default should be used.

        @param name: Name of the option to get.
        @type name: str

        @rtype: str or None
        """
        pass

    @abstractmethod
    def set_value(self, name, raw_value):
        """
        Set new Raw Value for a given name.

        @param name: Name of the option to set.
        @type name: str

        @param raw_value: Value to set. Must be serialized.
        @type raw_value: str
        """
        pass

    @abstractmethod
    def del_value(self, name):
        """
        Remove value for a given name.

        @param name: Name of the option to delete.
        @type name: str
        """
        pass

    @abstractmethod
    def get_array_value(self, name):
        """
        Return an array of Raw Values for a given name.

        @param name: Name of the array option to get.
        @type name: str

        @rtype: list or None
        """
        pass

    @abstractmethod
    def set_array_value(self, name, value):
        """
        Set new value which is an array of Raw Values for a given name.

        @param name: Name of the array option to set.
        @type name: str

        @param value:  Value to set. Must be a list of serialized values.
        @type value: list
        """
        pass

    @abstractmethod
    def get_dict_value(self, name):
        """
        Return a dict of Raw Values for a given name.

        @param name: Name of the dict option to get.
        @type name: str

        @rtype: dict or None
        """
        pass

    @abstractmethod
    def set_dict_value(self, name, value):
        """
        Set new value which is a dict of Raw Values for a given name.

        @param name: Name of the dict option to set.
        @type name: str

        @param value:  Value to set. Must be a dict of serialized values.
        @type value: dict
        """
        pass
#}
