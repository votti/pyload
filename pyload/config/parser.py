# -*- coding: utf-8 -*-
# @author: vuolter

from __future__ import absolute_import, unicode_literals

import configparser
import io
import logging
import os
from builtins import bytes, int, object, oct, str
from contextlib import closing

import semver
from future import standard_library

from pyload.__about__ import __version_info__
from pyload.config.exceptions import (AlreadyExistsKeyError, InvalidValueError,
                                      VersionMismatchError)
from pyload.config.types import InputType
from pyload.utils import parse
from pyload.utils.check import isiterable, ismapping
from pyload.utils.convert import to_bytes, to_str
from pyload.utils.fs import fullpath
from pyload.utils.layer.legacy.collections import OrderedDict
from pyload.utils.struct import InscDict
from pyload.utils.web.check import isendpoint
from pyload.utils.web.parse import endpoint, socket

standard_library.install_aliases()


def _parse_address(value):
    address = value.replace(',', ':')
    return (endpoint if isendpoint(address) else socket)(address)


convert_map = {
    InputType.NA: lambda x: x,
    InputType.Str: to_str,
    InputType.Int: int,
    InputType.File: fullpath,
    InputType.Folder: lambda x: os.path.dirname(fullpath(x)),
    InputType.Password: to_str,
    InputType.Bool: bool,
    InputType.Float: float,
    InputType.Octal: oct,
    InputType.Size: parse.bytesize,
    InputType.Address: _parse_address,
    InputType.Bytes: to_bytes,
    InputType.StrList: parse.entries
}
    

class ConfigOption(object):

    __slots__ = ['allowed_values', 'default', 'desc', 'label', 'parser',
                 'type', 'value']

    DEFAULT_TYPE = InputType.Str
    
    def __init__(self, parser, value, label=None, desc=None,
                 allowed_values=None, input_type=None):
        self.parser = parser

        self.type = None
        self.value = None
        self.default = None
        self.label = None
        self.desc = None
        self.allowed_values = ()

        self._set_type(input_type)
        self._set_value(value)
        self._set_allowed(allowed_values)
        self._set_info(label, desc)

    def _set_info(self, label, desc):
        self.label = '' if label is None else to_str(label)
        self.desc = '' if desc is None else to_str(desc)

    def _set_type(self, input_type):
        if not input_type:
            input_type = self.DEFAULT_TYPE
        if input_type in InputType:
            self.type = input_type
        else:
            raise InvalidValueError(input_type)

    def _set_value(self, value):
        self.value = self.default = self._normalize_value(value)

    def _set_allowed(self, allowed):
        if not allowed:
            self.allowed_values = ()
            return

        self.allowed_values = tuple(self._normalize_value(v) for v in allowed)

    def _normalize_value(self, value):
        return value if value is None else convert_map[self.type](value)

    def reset(self):
        self.value = self.default

    def get(self):
        return self.value

    def get_default(self):
        return self.default

    def set(self, value, store=True):
        norm_value = self._normalize_value(value)
        if self.allowed_values and norm_value not in self.allowed_values:
            raise InvalidValueError(value)
        if self.value == norm_value:
            return
        self.value = norm_value
        if store:
            self.parser.store()


class ConfigSection(InscDict):
    """Provides dictionary like access for configparser."""
    __slots__ = ['desc', 'label', 'parser']

    SECTION_SEPARATOR = ':'

    def __init__(self, parser, config=None, label=None, desc=None):
        """Constructor."""
        self.parser = parser
        self.label = '' if label is None else to_str(label)
        self.desc = '' if desc is None else to_str(desc)
        self.update(config or ())

    def _to_configentry(self, value):
        if isinstance(value, (ConfigOption, ConfigSection)):
            entry_obj = value
        else:
            entry_type = value[0]
            entry_args = value[1:]
            func = ConfigSection if entry_type == 'section' else ConfigOption
            entry_obj = func(self.parser, *entry_args)
        return entry_obj

    def reset(self):
        for item in self.values():
            item.reset()

    def update(self, iterable):
        if ismapping(iterable):
            iterable = iterable.items()

        config = [(name, self._to_configentry(value)) 
                  for name, value in iterable]
        InscDict.update(self, config)

    def is_section(self, name):
        return isinstance(self.__getitem__(name), ConfigSection)

    def is_option(self, name):
        return isinstance(self.__getitem__(name), ConfigOption)

    def set(self, name, arg, *args, **kwargs):
        item = self.__getitem__(name)
        item.set(arg, *args, **kwargs)

    def get(self, name, *names):
        item = self.__getitem__(name)
        try:
            return item.get(*names)
        except TypeError:
            return item

    def get_default(self, name, *names):
        return self.__getitem__(name).get_default(*names)

    def get_section(self, name):
        if not self.is_section(name):
            raise InvalidValueError(name)
        return self.__getitem__(name)

    def get_option(self, name):
        if self.is_section(name):
            raise InvalidValueError(name)
        return self.__getitem__(name)

    def add_section(
            self, name, config=None, label=None, desc=None, store=None):
        if self.SECTION_SEPARATOR in name:
            raise InvalidValueError(name)
        if name.lower() in self.lowerkeys():
            raise AlreadyExistsKeyError(name)
        if label is None:
            label = name.strip().capitalize()
        section = ConfigSection(self.parser, config, label, desc)
        self.__setitem__(name, section)
        if store or (store is None and config):
            self.parser.store()
        return section

    def add_option(self, name, value, label=None, desc=None,
                   allowed_values=None, input_type=None, store=True):
        if name.lower() in self.lowerkeys():
            raise AlreadyExistsKeyError(name)
        if label is None:
            label = name.strip().capitalize()
        option = ConfigOption(
            self.parser, value, label, desc, allowed_values, input_type)
        self.__setitem__(name, option)
        if store:
            self.parser.store()
        return option

    def add(self, section, *args, **kwargs):
        func = self.add_section if section == 'section' else self.add_option
        return func(*args, **kwargs)

    # def __str__(self):
        # pass


class ConfigParser(ConfigSection):

    __slots__ = ['fp', 'lock', 'log', 'parser', 'path', 'version',
                 'version_info']

    DEFAULT_SECTION = configparser.DEFAULTSECT
    SELF_SECTION = ''

    def __init__(self, filename, config=None, version=__version_info__,
                 logger=None):
        self.path = fullpath(filename)
        self.version, self.version_info = self._parse_version(version)

        self.fp = io.open(filename, mode='ab+')

        if logger is None:
            self.log = logging.getLogger('null')
            self.log.addHandler(logging.NullHandler())
        else:
            self.log = logger

        ConfigSection.__init__(self, self, config)
        self._retrieve_fileconfig()

    def close(self):
        with closing(self.fp):
            self.store()

    def _retrieve_fileconfig(self):
        try:
            return self.retrieve()

        except VersionMismatchError:
            self.fp.close()
            os.rename(self.path, self.path + '.old')
            self.fp = io.open(self.path, mode='ab+')

        except Exception as exc:
            self.log.error(exc)

        self.log.warning(
            'Unable to parse configuration from `{0}`'.format(self.path))

    def _parse_version(self, value):
        if isinstance(value, semver.VersionInfo):
            version_info = value
        else:
            version_info = semver.parse_version_info(value)
        version = semver.format_version(*tuple(version_info))
        return version, version_info

    def _make_sections(self, section_id):
        section_names = section_id.split(self.SECTION_SEPARATOR)
        section = self
        for idx, name in enumerate(section_names):
            try:
                section = section.get_section(name)
            except KeyError:
                for subname in section_names[idx:]:
                    section = section.add_section(subname, store=False)
                break
        return section

    def _check_version(self, version):
        if not version:
            raise VersionMismatchError
        version_info = self._parse_version(version)[1]
        if version_info[:2] != self.version_info[:2]:
            raise VersionMismatchError

    def _new_parser(self, defaults=None):
        return configparser.ConfigParser(
            defaults=defaults,
            allow_no_value=True,
            default_section=self.DEFAULT_SECTION)

    def _make_options(self, section, section_config):
        for option_name, option_value in section_config.items():
            try:
                section.set(option_name, option_value)
            except KeyError:
                section.add_option(option_name, option_value, store=False)

    def add_section(
            self, name, config=None, label=None, desc=None, store=None):
        na = (self.DEFAULT_SECTION.lower(), self.SELF_SECTION.lower())
        if name.lower() in na:
            raise InvalidValueError(name)
        return ConfigSection.add_section(
            self, name, config, label, desc, store)

    def retrieve(self):
        parser = self._new_parser()
        parser.read_file(self.fp)

        version = parser.get(self.DEFAULT_SECTION, 'version', fallback=None)
        self._check_version(version)

        self._make_options(self, parser.pop(self.SELF_SECTION, {}))

        for section_id in parser.sections():
            section = self._make_sections(section_id)
            self._make_options(section, parser[section_id])

    def _to_filevalue(self, value):
        return ','.join(map(to_str, value)) if isiterable(value) else value

    def _to_fileconfig(self, section, section_name):
        config = OrderedDict()
        for name, item in section.loweritems():
            if section.is_section(name):
                sub_name = '{0}{1}{2}'.format(
                    section_name, self.SECTION_SEPARATOR, name)
                fc = self._to_fileconfig(item, sub_name)
                config.update(fc)
            else:
                fv = self._to_filevalue(item.get())
                config.setdefault(section_name, OrderedDict())[name] = fv
        return config

    def _gen_fileconfig(self):
        config = OrderedDict({self.SELF_SECTION: OrderedDict()})
        for name, item in self.loweritems():
            if self.is_section(name):
                fc = self._to_fileconfig(item, name)
                config.update(fc)
            else:
                fv = self._to_filevalue(item.get())
                config[self.SELF_SECTION][name] = fv
        return config

    def store(self):
        config = self._gen_fileconfig()
        parser = self._new_parser({'version': self.version})
        parser.read_dict(config)
        parser.write(self.fp)

    # def __str__(self):
        # pass