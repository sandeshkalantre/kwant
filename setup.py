#!/usr/bin/env python3

# Copyright 2011-2016 Kwant authors.
#
# This file is part of Kwant.  It is subject to the license terms in the file
# LICENSE.rst found in the top-level directory of this distribution and at
# http://kwant-project.org/license.  A list of Kwant authors can be found in
# the file AUTHORS.rst at the top-level directory of this distribution and at
# http://kwant-project.org/authors.

from __future__ import print_function

import sys


def ensure_python(required_version):
    v = sys.version_info
    if v[:3] < required_version:
        error = "This version of Kwant requires Python {} or above.".format(
            ".".join(str(p) for p in required_version))
        if v[0] == 2:
            error += "\nKwant 1.1 is the last version to support Python 2."
        print(error, file=sys.stderr)
        sys.exit(1)

ensure_python((3, 4))

import re
import os
import glob
import imp
import subprocess
import configparser
import collections
from setuptools import setup, find_packages, Extension, Command
from distutils.errors import DistutilsError, CCompilerError
from distutils.command.build import build as build_orig
from setuptools.command.sdist import sdist as sdist_orig
from setuptools.command.build_ext import build_ext as build_ext_orig
from setuptools.command.test import test as test_orig


STATIC_VERSION_PATH = ('kwant', '_kwant_version.py')

distr_root = os.path.dirname(os.path.abspath(__file__))


def configure_extensions(exts, aliases=(), build_summary=None):
    """Modify extension configuration according to the configuration file

    `exts` must be a dict of (name, kwargs) tuples that can be used like this:
    `Extension(name, **kwargs).  This function modifies the kwargs according to
    the configuration file.

    This function modifies `sys.argv`.
    """
    global config_file, config_file_present

    #### Determine the name of the configuration file.
    config_file_option = '--configfile'
    # Handle command line option
    for i, opt in enumerate(sys.argv):
        if not opt.startswith(config_file_option):
            continue
        l, _, config_file = opt.partition('=')
        if l != config_file_option or not config_file:
            print('error: Expecting {}=PATH'.format(config_file_option),
                  file=sys.stderr)
            sys.exit(1)
        sys.argv.pop(i)
        break
    else:
        config_file = 'build.conf'

    #### Read build configuration file.
    configs = configparser.ConfigParser()
    try:
        with open(config_file) as f:
            configs.read_file(f)
    except IOError:
        config_file_present = False
    else:
        config_file_present = True

    #### Handle section aliases.
    for short, long in aliases:
        if short in configs:
            if long in configs:
                print('Error: both {} and {} sections present in {}.'.format(
                    short, long, config_file))
                sys.exit(1)
            configs[long] = configs[short]
            del configs[short]

    #### Apply config from file.  Use [DEFAULT] section for missing sections.
    defaultconfig = configs.defaults()
    for name, kwargs in exts.items():
        config = configs[name] if name in configs else defaultconfig
        for key, value in config.items():

            # Most, but not all, keys are lists of strings
            if key == 'language':
                pass
            elif key == 'optional':
                value = bool(int(value))
            else:
                value = value.split()

            if key == 'define_macros':
                value = [tuple(entry.split('=', maxsplit=1))
                         for entry in value]
                value = [(entry[0], None) if len(entry) == 1 else entry
                         for entry in value]

            if key in kwargs:
                msg = 'Caution: user config in file {} shadows {}.{}.'
                if build_summary is not None:
                    build_summary.append(msg.format(config_file, name, key))
            kwargs[key] = value

        kwargs.setdefault('depends', []).append(config_file)
        if config is not defaultconfig:
            del configs[name]

    unknown_sections = configs.sections()
    if unknown_sections:
        print('Error: Unknown sections in file {}: {}'.format(
            config_file, ', '.join(unknown_sections)))
        sys.exit(1)

    return exts


def get_version():
    global version, version_is_from_git

    # Let Kwant itself determine its own version.  We cannot simply import
    # kwant, as it is not built yet.
    _dont_write_bytecode_saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    _common = imp.load_source('_common', 'kwant/_common.py')
    sys.dont_write_bytecode = _dont_write_bytecode_saved

    version = _common.version
    version_is_from_git = _common.version_is_from_git


def init_cython():
    """Set the global variable `cythonize` (and other related globals).

    The variable `cythonize` can be in three states:

    * If Cython should be run and is ready, it contains the `cythonize()`
      function.

    * If Cython is not to be run, it contains `False`.

    * If Cython should, but cannot be run it contains `None`.  A help message
      on how to solve the problem is stored in `cython_help`.

    This function modifies `sys.argv`.
    """
    global cythonize, cython_help

    cython_option = '--cython'
    required_cython_version = (0, 22)
    try:
        sys.argv.remove(cython_option)
        cythonize = True
    except ValueError:
        cythonize = version_is_from_git

    if cythonize:
        try:
            import Cython
            from Cython.Build import cythonize
        except ImportError:
            cythonize = None
        else:
            #### Get Cython version.
            match = re.match('([0-9.]*)(.*)', Cython.__version__)
            cython_version = [int(n) for n in match.group(1).split('.')]
            # Decrease version if the version string contains a suffix.
            if match.group(2):
                while cython_version[-1] == 0:
                    cython_version.pop()
                cython_version[-1] -= 1
            cython_version = tuple(cython_version)

            if cython_version < required_cython_version:
                cythonize = None

        if cythonize is None:
            msg = ("Install Cython >= {0} or use"
                    " a source distribution (tarball) of Kwant.")
            ver = '.'.join(str(e) for e in required_cython_version)
            cython_help = msg.format(ver)
    else:
        msg = "Run setup.py with the {} option to enable Cython."
        cython_help = msg.format(cython_option)


def banner(title=''):
    starred = title.center(79, '*')
    return '\n' + starred if title else starred


class build_ext(build_ext_orig):
    def run(self):
        if not config_file_present:
            # Create an empty config file if none is present so that the
            # extensions will not be rebuilt each time.  Only depending on the
            # config file if it is present would make it impossible to detect a
            # necessary rebuild due to a deleted config file.
            with open(config_file, 'w') as f:
                f.write('# Build configuration created by setup.py '
                        '- feel free to modify.\n')

        try:
            super().run()
        except (DistutilsError, CCompilerError):
            error_msg = self.__error_msg.format(
                header=banner(' Error '), sep=banner())
            print(error_msg.format(file=config_file, summary=build_summary),
                  file=sys.stderr)
            raise
        print(banner(' Build summary '), *build_summary, sep='\n')
        print(banner())

    __error_msg = """{header}
The compilation of Kwant has failed.  Please examine the error message
above and consult the installation instructions in README.rst.
You might have to customize {{file}}.

Build configuration was:

{{summary}}
{sep}
"""


class build_tut(Command):
    description = "build the tutorial scripts"
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        tut_dir = 'tutorial'
        if not os.path.exists(tut_dir):
            os.mkdir(tut_dir)
        for in_fname in glob.glob('doc/source/tutorial/*.py'):
            out_fname = os.path.join(tut_dir, os.path.basename(in_fname))
            with open(in_fname) as in_file:
                with open(out_fname, 'w') as out_file:
                    for line in in_file:
                        if not line.startswith('#HIDDEN'):
                            out_file.write(line)


# Our version of the "build" command also makes sure the tutorial is made.
# Even though the tutorial is not necessary for installation, and "build" is
# supposed to make everything needed to install, this is a robust way to ensure
# that the tutorial is present.
class build(build_orig):
    sub_commands = [('build_tut', None)] + build_orig.sub_commands

    def run(self):
        super().run()
        write_version(os.path.join(self.build_lib, *STATIC_VERSION_PATH))


def git_lsfiles():
    if not version_is_from_git:
        return

    try:
        p = subprocess.Popen(['git', 'ls-files'], cwd=distr_root,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return

    if p.wait() != 0:
        return
    return p.communicate()[0].decode().split('\n')[:-1]


# Make the command "sdist" depend on "build".  This verifies that the
# distribution in the current state actually builds.  It also makes sure that
# the Cython-made C files and the tutorial will be included in the source
# distribution and that they will be up-to-date.
class sdist(sdist_orig):
    sub_commands = [('build', None)] + sdist_orig.sub_commands

    def run(self):
        """
        Create MANIFEST.in from git if possible, otherwise check that MANIFEST.in
        is present.

        Right now (2015) generating MANIFEST.in seems to be the only way to
        include files in the source distribution that setuptools does not think
        should be there.  Setting include_package_data to True makes setuptools
        include *.pyx and other source files in the binary distribution.
        """
        manifest_in_file = 'MANIFEST.in'
        manifest = os.path.join(distr_root, manifest_in_file)
        names = git_lsfiles()
        if names is None:
            if not (os.path.isfile(manifest) and os.access(manifest, os.R_OK)):
                print("Error:", manifest_in_file,
                      "file is missing and Git is not available"
                      " to regenerate it.", file=sys.stderr)
                sys.exit(1)
        else:
            with open(manifest, 'w') as f:
                for name in names:
                    a, sep, b = name.rpartition('/')
                    if b == '.gitignore':
                        continue
                    stem, dot, extension = b.rpartition('.')
                    f.write('include {}'.format(name))
                    if extension == 'pyx':
                        f.write(''.join([' ', a, sep, stem, dot, 'c']))
                    f.write('\n')

        super().run()

        if names is None:
            msg = ("Git was not available to generate the list of files to be "
                   "included in the\nsource distribution. The old {} was used.")
            msg = msg.format(manifest_in_file)
            print(banner(' Caution '), msg, banner(), sep='\n', file=sys.stderr)

    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        write_version(os.path.join(base_dir, *STATIC_VERSION_PATH))


# The following class is based on a recipe in
# http://doc.pytest.org/en/latest/goodpractices.html#manual-integration.
class test(test_orig):
    user_options = [('pytest-args=', 'a', "Arguments to pass to pytest")]

    def initialize_options(self):
        super().initialize_options()
        self.pytest_args = ''

    def run_tests(self):
        import shlex
        try:
            import pytest
        except:
            print('The Python package "pytest" is required to run tests.',
                  file=sys.stderr)
            sys.exit(1)
        errno = pytest.main(shlex.split(self.pytest_args))
        sys.exit(errno)


def write_version(fname):
    # This could be a hard link, so try to delete it first.  Is there any way
    # to do this atomically together with opening?
    try:
        os.remove(fname)
    except OSError:
        pass
    with open(fname, 'w') as f:
        f.write("# This file has been created by setup.py.\n")
        f.write("version = '{}'\n".format(version))


def long_description():
    text = []
    try:
        with open('README.rst') as f:
            for line in f:
                if line.startswith('See also in this directory:'):
                    break
                text.append(line.rstrip())
            while text[-1] == "":
                text.pop()
    except:
        return ''
    return '\n'.join(text)


def search_mumps():
    """Return the configuration for MUMPS if it is available in a known way.

    This is known to work with the MUMPS provided by the Debian package
    libmumps-scotch-dev."""

    libs = ['zmumps_scotch', 'mumps_common_scotch', 'pord', 'mpiseq_scotch',
            'gfortran']

    cmd = ['gcc']
    cmd.extend(['-l' + lib for lib in libs])
    cmd.extend(['-o/dev/null', '-xc', '-'])
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        pass
    else:
        p.communicate(input=b'int main() {}\n')
        if p.wait() == 0:
            return {'libraries': libs}
    return {}


def configure_special_extensions(exts, build_summary):
    #### Special config for LAPACK.
    lapack = exts['kwant.linalg.lapack']
    if 'libraries' in lapack:
        build_summary.append('User-configured LAPACK and BLAS')
    else:
        lapack['libraries'] = ['lapack', 'blas']
        build_summary.append('Default LAPACK and BLAS')

    #### Special config for MUMPS.
    mumps = exts['kwant.linalg._mumps']
    if 'libraries' in mumps:
        build_summary.append('User-configured MUMPS')
    else:
        kwargs = search_mumps()
        if kwargs:
            for key, value in kwargs.items():
                mumps.setdefault(key, []).extend(value)
            build_summary.append('Auto-configured MUMPS')
        else:
            mumps = None
            del exts['kwant.linalg._mumps']
            build_summary.append('No MUMPS support')

    if mumps:
        # Copy config from LAPACK.
        for key, value in lapack.items():
            if key not in ['sources', 'depends']:
                mumps.setdefault(key, []).extend(value)

    return exts


def maybe_cythonize(exts):
    """Prepare a list of `Extension` instances, ready for `setup()`.

    The argument `exts` must be a mapping of names to kwargs to be passed
    on to `Extension`.

    If Cython is to be run, create the extensions and calls `cythonize()` on
    them.  If Cython is not to be run, replace .pyx file with .c or .cpp,
    check timestamps, and create the extensions.
    """
    if cythonize:
        return cythonize([Extension(name, **kwargs)
                          for name, kwargs in exts.items()],
                         language_level=3,
                         compiler_directives={'linetrace': True})

    # Cython is not going to be run: replace pyx extension by that of
    # the shipped translated file.

    result = []
    problematic_files = []
    for name, kwargs in exts.items():
        language = kwargs.get('language')
        if language is None:
            ext = '.c'
        elif language == 'c':
            ext = '.c'
        elif language == 'c++':
            ext = '.cpp'
        else:
            print('Unknown language: {}'.format(language), file=sys.stderr)
            sys.exit(1)

        pyx_files = []
        cythonized_files = []
        sources = []
        for f in kwargs['sources']:
            if f.endswith('.pyx'):
                pyx_files.append(f)
                f = f.rstrip('.pyx') + ext
                cythonized_files.append(f)
            sources.append(f)
        kwargs['sources'] = sources

        # Complain if cythonized files are older than Cython source files.
        try:
            cythonized_oldest = min(os.stat(f).st_mtime
                                    for f in cythonized_files)
        except OSError:
            msg = "Cython-generated file {} is missing."
            print(banner(" Error "), msg.format(f), "",
                  cython_help, banner(), sep="\n", file=sys.stderr)
            sys.exit(1)

        for f in pyx_files + kwargs.get('depends', []):
            if f == config_file:
                # The config file is only a dependency for the compilation
                # of the cythonized file, not for the cythonization.
                continue
            if os.stat(f).st_mtime > cythonized_oldest:
                problematic_files.append(f)

        result.append(Extension(name, **kwargs))

    if problematic_files:
        msg = ("Some Cython source files are newer than files that have "
               "been derived from them:\n{}")
        msg = msg.format(", ".join(problematic_files))

        # Cython should be run but won't.  Signal an error if this is because
        # Cython *cannot* be run, warn otherwise.
        error = cythonize is None
        if cythonize is False:
            dontworry = ('(Do not worry about this if you are building Kwant '
                         'from unmodified sources,\n'
                         'e.g. with "pip install".)\n\n')
            msg = dontworry + msg

        print(banner(" Error " if error else " Caution "), msg, "",
              cython_help, banner(), sep="\n", file=sys.stderr)
        if error:
            sys.exit(1)

    return result


def main():
    exts = collections.OrderedDict([
        ('kwant._system',
         dict(sources=['kwant/_system.pyx'],
              include_dirs=['kwant/graph'])),
        ('kwant.operator',
         dict(sources=['kwant/operator.pyx'],
              include_dirs=['kwant/graph'])),
        ('kwant.graph.core',
         dict(sources=['kwant/graph/core.pyx'],
              depends=['kwant/graph/core.pxd', 'kwant/graph/defs.h',
                       'kwant/graph/defs.pxd'])),
        ('kwant.graph.utils',
         dict(sources=['kwant/graph/utils.pyx'],
              depends=['kwant/graph/defs.h', 'kwant/graph/defs.pxd',
                       'kwant/graph/core.pxd'])),
        ('kwant.graph.slicer',
         dict(sources=['kwant/graph/slicer.pyx',
                       'kwant/graph/c_slicer/partitioner.cc',
                       'kwant/graph/c_slicer/slicer.cc'],
              depends=['kwant/graph/defs.h', 'kwant/graph/defs.pxd',
                       'kwant/graph/core.pxd', 'kwant/graph/c_slicer.pxd',
                       'kwant/graph/c_slicer/bucket_list.h',
                       'kwant/graph/c_slicer/graphwrap.h',
                       'kwant/graph/c_slicer/partitioner.h',
                       'kwant/graph/c_slicer/slicer.h'])),
        ('kwant.linalg.lapack',
         dict(sources=['kwant/linalg/lapack.pyx'],
              depends=['kwant/linalg/f_lapack.pxd'])),
        ('kwant.linalg._mumps',
         dict(sources=['kwant/linalg/_mumps.pyx'],
              depends=['kwant/linalg/cmumps.pxd']))])

    # Add NumPy header path to include_dirs of all the extensions.
    try:
        import numpy
    except ImportError:
        print(banner(' Caution '), 'NumPy header directory cannot be determined'
              ' ("import numpy" failed).', banner(), sep='\n', file=sys.stderr)
    else:
        numpy_include = numpy.get_include()
        for ext in exts.values():
            ext.setdefault('include_dirs', []).append(numpy_include)

    aliases = [('lapack', 'kwant.linalg.lapack'),
               ('mumps', 'kwant.linalg._mumps')]

    get_version()
    init_cython()

    global build_summary
    build_summary = []
    exts = configure_extensions(exts, aliases, build_summary)
    exts = configure_special_extensions(exts, build_summary)
    exts = maybe_cythonize(exts)

    classifiers = """\
        Development Status :: 5 - Production/Stable
        Intended Audience :: Science/Research
        Intended Audience :: Developers
        Programming Language :: Python :: 3 :: Only
        Topic :: Software Development
        Topic :: Scientific/Engineering
        Operating System :: POSIX
        Operating System :: Unix
        Operating System :: MacOS :: MacOS X
        Operating System :: Microsoft :: Windows"""

    setup(name='kwant',
          version=version,
          author='C. W. Groth (CEA), M. Wimmer, '
                 'A. R. Akhmerov, X. Waintal (CEA), and others',
          author_email='authors@kwant-project.org',
          description=("Package for numerical quantum transport calculations "
                       "(Python 3 version)"),
          long_description=long_description(),
          platforms=["Unix", "Linux", "Mac OS-X", "Windows"],
          url="http://kwant-project.org/",
          license="BSD",
          packages=find_packages('.'),
          cmdclass={'build': build,
                    'sdist': sdist,
                    'build_ext': build_ext,
                    'build_tut': build_tut,
                    'test': test},
          ext_modules=exts,
          install_requires=['numpy > 1.6.1', 'scipy >= 0.11.0', 'tinyarray >= 1.2.0'],
          extras_require={'plotting': 'matplotlib >= 1.2'},
          classifiers=[c.strip() for c in classifiers.split('\n')])

if __name__ == '__main__':
    main()
