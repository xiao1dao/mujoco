# Copyright 2022 DeepMind Technologies Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Install script for MuJoCo."""

import fnmatch
import os
import platform
import random
import re
import shutil
import string
import subprocess
import sys
import sysconfig

import setuptools
from setuptools import find_packages
from setuptools import setup
from setuptools.command import build_ext

__version__ = '2.1.3'

MUJOCO_CMAKE = 'MUJOCO_CMAKE'
MUJOCO_CMAKE_ARGS = 'MUJOCO_CMAKE_ARGS'
MUJOCO_PATH = 'MUJOCO_PATH'

EXT_PREFIX = 'mujoco.'


def get_long_description():
  """Creates a long description for the package from bundled markdown files."""
  current_dir = os.path.dirname('__file__')
  with open(os.path.join(current_dir, 'README.md')) as f:
    description = f.read()
  try:
    with open(os.path.join(current_dir, 'LICENSES_THIRD_PARTY.md')) as f:
      description = f'{description}\n{f.read()}'
  except FileNotFoundError:
    pass
  return description


def get_mujoco_lib_pattern():
  if platform.system() == 'Windows':
    return 'mujoco*.lib'
  elif platform.system() == 'Darwin':
    return 'libmujoco*.dylib'
  else:
    return 'libmujoco*.so*'


def get_external_lib_patterns():
  if platform.system() == 'Windows':
    return ['mujoco*.dll']
  elif platform.system() == 'Darwin':
    return ['libmujoco*.dylib']
  else:
    return ['libmujoco*.so*', 'libglew*.so']


def start_and_end(iterable):
  it = iter(iterable)
  while True:
    try:
      first = next(it)
      second = next(it)
      yield first, second
    except StopIteration:
      return


def tokenize_quoted_substr(input_string, quote_char, placeholders=None):
  """Replace quoted substrings with random text placeholders with no spaces."""
  # Matches quote characters not proceded with a backslash.
  pattern = re.compile(r'(?<!\\)' + quote_char)
  quote_positions = [m.start() for m in pattern.finditer(input_string)]
  if len(quote_positions) % 2:
    raise ValueError(f'unbalanced quotes {quote_char}...{quote_char}')

  output_string = ''
  placeholders = placeholders if placeholders is not None else dict()
  prev_end = -1
  for start, end in start_and_end(quote_positions):
    output_string += input_string[prev_end+1:start]
    while True:
      placeholder = ''.join(random.choices(string.ascii_lowercase, k=5))
      if placeholder not in input_string and placeholder not in output_string:
        break
    output_string += placeholder
    placeholders[placeholder] = input_string[start+1:end]
    prev_end = end
  output_string += input_string[prev_end+1:]

  return output_string, placeholders


def parse_cmake_args_from_environ(env_var_name=MUJOCO_CMAKE_ARGS):
  """Parses CMake arguments from an environment variable."""
  raw_args = os.environ.get(env_var_name, '').strip()
  unquoted, placeholders = tokenize_quoted_substr(raw_args, '"')
  unquoted, placeholders = tokenize_quoted_substr(unquoted, "'", placeholders)
  parts = re.split(r'\s+', unquoted.strip())
  out = []
  for part in parts:
    for k, v in placeholders.items():
      part = part.replace(k, v)
    part = part.replace('\\"', '"').replace("\\'", "'")
    if part:
      out.append(part)
  return out


class CMakeExtension(setuptools.Extension):
  """A Python extension that has been prebuilt by CMake.

  We do not want distutils to handle the build process for our extensions, so
  so we pass an empty list to the super constructor.
  """

  def __init__(self, name):
    super().__init__(name, sources=[])


class BuildCMakeExtension(build_ext.build_ext):
  """Uses CMake to build extensions."""

  def run(self):
    self._mujoco_library_path, self._mujoco_include_path = self._find_mujoco()
    self._configure_cmake()
    for ext in self.extensions:
      assert ext.name.startswith(EXT_PREFIX)
      assert '.' not in ext.name[len(EXT_PREFIX):]
      self.build_extension(ext)
    self._copy_external_libraries()
    self._copy_mujoco_headers()

  def _find_mujoco(self):
    if MUJOCO_PATH not in os.environ:
      raise RuntimeError(f'{MUJOCO_PATH} environment variable is not set')
    library_path = None
    include_path = None
    for directory, _, filenames in os.walk(os.environ['MUJOCO_PATH']):
      if fnmatch.filter(filenames, get_mujoco_lib_pattern()):
        library_path = directory
      if fnmatch.filter(filenames, 'mujoco.h'):
        include_path = directory
      if library_path and include_path:
        return library_path, include_path
    raise RuntimeError('Cannot find MuJoCo library and/or include paths')

  def _copy_external_libraries(self):
    dst = os.path.dirname(self.get_ext_fullpath(self.extensions[0].name))
    for directory, _, filenames in os.walk(os.environ['MUJOCO_PATH']):
      for pattern in get_external_lib_patterns():
        for filename in fnmatch.filter(filenames, pattern):
          shutil.copyfile(os.path.join(directory, filename),
                          os.path.join(dst, filename))

  def _copy_mujoco_headers(self):
    dst = os.path.join(
        os.path.dirname(self.get_ext_fullpath(self.extensions[0].name)),
        'include')
    os.mkdir(dst)
    for directory, _, filenames in os.walk(self._mujoco_include_path):
      for filename in fnmatch.filter(filenames, '*.h'):
        shutil.copyfile(os.path.join(directory, filename),
                        os.path.join(dst, filename))

  def _configure_cmake(self):
    """Check for CMake."""
    cmake = os.environ.get(MUJOCO_CMAKE, 'cmake')
    build_cfg = 'Debug' if self.debug else 'Release'
    cmake_module_path = os.path.join(os.path.dirname(__file__), 'cmake')
    cmake_args = [
        f'-DPython3_ROOT_DIR={sys.prefix}',
        f'-DPython3_EXECUTABLE={sys.executable}',
        f'-DMUJOCO_LIBRARY_DIR={self._mujoco_library_path}',
        f'-DMUJOCO_INCLUDE_DIR={self._mujoco_include_path}',
        f'-DCMAKE_MODULE_PATH={cmake_module_path}',
        f'-DCMAKE_BUILD_TYPE={build_cfg}',
        f'-DCMAKE_LIBRARY_OUTPUT_DIRECTORY={self.build_temp}',
        f'-DCMAKE_INTERPROCEDURAL_OPTIMIZATION={"OFF" if self.debug else "ON"}',
        '-DCMAKE_Fortran_COMPILER:STRING=',
        '-DCMAKE_VERBOSE_MAKEFILE=ON',
        '-DBUILD_TESTING=OFF',
    ]
    if platform.system() != 'Windows':
      cmake_args.extend([
          f'-DPython3_LIBRARY={sysconfig.get_paths()["stdlib"]}',
          f'-DPython3_INCLUDE_DIR={sysconfig.get_paths()["include"]}',
      ])
    if platform.system() == 'Darwin' and os.environ.get('ARCHFLAGS'):
      osx_archs = []
      if '-arch x86_64' in os.environ['ARCHFLAGS']:
        osx_archs.append('x86_64')
      if '-arch arm64' in os.environ['ARCHFLAGS']:
        osx_archs.append('arm64')
      cmake_args.append(f'-DCMAKE_OSX_ARCHITECTURES={";".join(osx_archs)}')

    cmake_args.extend(parse_cmake_args_from_environ())
    os.makedirs(self.build_temp, exist_ok=True)

    if platform.system() == 'Windows':
      cmake_args = [arg.replace('\\', '/') for arg in cmake_args]

    print('Configuring CMake with the following arguments:')
    for arg in cmake_args:
      print(f'    {arg}')
    subprocess.check_call(
        [cmake] + cmake_args +
        [os.path.join(os.path.dirname(__file__), 'mujoco')],
        cwd=self.build_temp)

    print('Building all extensions with CMake')
    subprocess.check_call(
        [cmake, '--build', '.', f'-j{os.cpu_count()}', '--config', build_cfg],
        cwd=self.build_temp)

  def build_extension(self, ext):
    dest_path = self.get_ext_fullpath(ext.name)
    build_path = os.path.join(self.build_temp, os.path.basename(dest_path))
    subprocess.check_call(['cp', build_path, dest_path])


def find_data_files(package_dir, patterns):
  """Recursively finds files whose names match the given shell patterns."""
  paths = set()
  for directory, _, filenames in os.walk(package_dir):
    for pattern in patterns:
      for filename in fnmatch.filter(filenames, pattern):
        # NB: paths must be relative to the package directory.
        relative_dirpath = os.path.relpath(directory, package_dir)
        paths.add(os.path.join(relative_dirpath, filename))
  return list(paths)


setup(
    name='mujoco',
    version=__version__,
    author='DeepMind',
    author_email='mujoco@deepmind.com',
    description='MuJoCo Physics Simulator',
    long_description=get_long_description(),
    long_description_content_type='text/markdown',
    url='https://github.com/deepmind/mujoco',
    license='Apache License 2.0',
    classifiers=[
        'License :: OSI Approved :: Apache Software License',
    ],
    cmdclass=dict(build_ext=BuildCMakeExtension),
    ext_modules=[
        CMakeExtension('mujoco._callbacks'),
        CMakeExtension('mujoco._constants'),
        CMakeExtension('mujoco._enums'),
        CMakeExtension('mujoco._errors'),
        CMakeExtension('mujoco._functions'),
        CMakeExtension('mujoco._render'),
        CMakeExtension('mujoco._rollout'),
        CMakeExtension('mujoco._structs'),
    ],
    python_requires='>=3.7',
    install_requires=[
        'absl-py',
        'glfw',
        'numpy',
        'pyopengl',
    ],
    tests_require=[
        'absl-py',
        'glfw',
        'numpy',
        'pyopengl',
    ],
    test_suite='mujoco',
    packages=find_packages(),
    package_data={
        'mujoco':
            find_data_files(
                package_dir='mujoco',
                patterns=[
                    'libmujoco*.dylib',
                    'libmujoco*.so*',
                    'mujoco*.dll',
                    'libglew*.so*',
                    'mujoco.h',
                    'mj*.h',
                ]),
    },
)
