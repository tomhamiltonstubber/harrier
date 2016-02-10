import os
import re
from fnmatch import fnmatch

import sass
from harrier.common import HarrierKnownProblem
from jinja2 import Environment, FileSystemLoader, contextfilter

from .config import Config


def find_all_files(root, prefix=''):

    def gen():
        for d, _, files in os.walk(root):
            for f in files:
                yield prefix + os.path.relpath(os.path.join(d, f), root)
    return list(gen())


class Tool:
    ownership_regex = None

    def __init__(self, config: Config):
        self._config = config
        self.to_build = []

    def check_ownership(self, file_path) -> bool:
        """
        Return true if this converter takes care of the given file_name, else false.

        The convert might also want to record the file to process it later.
        """
        return re.match(self.ownership_regex, file_path)

    def map_path(self, fp):
        for pattern, replace in self._config.path_mapping:
            fp = re.sub(pattern, replace, fp)
        return fp

    def build(self):
        gen_count = 0
        for file_path in self.to_build:
            for new_file_path, file_content in self.convert_file(file_path):
                new_file_path = new_file_path or self.map_path(file_path)
                target_file = os.path.join(self._config.target_dir, new_file_path)
                target_dir = os.path.dirname(target_file)
                os.makedirs(target_dir, exist_ok=True)
                with open(target_file, 'wb') as f:
                    gen_count += 1
                    f.write(file_content)
        return gen_count

    def convert_file(self, file_path) -> dict:
        """
        generate files this converter is responsible for in the target directory.
        """
        raise NotImplementedError()

    @property
    def name(self):
        return self.__class__.__name__


class CopyFile(Tool):
    def check_ownership(self, file_path):
        return True

    def convert_file(self, file_path):
        full_path = os.path.join(self._config.root, file_path)
        with open(full_path, 'rb') as f:
            yield None, f.read()


class Sass(Tool):
    ownership_regex = r'.*\.s(a|c)ss$'

    def convert_file(self, file_path):
        full_path = os.path.join(self._config.root, file_path)
        content_str = sass.compile(filename=full_path)
        # TODO cope with maps etc.
        yield None, content_str.encode('utf8')


class Jinja(Tool):
    def __init__(self, config):
        super(Jinja, self).__init__(config)
        # TODO custom loader which deals with partial builds
        self._loader = FileSystemLoader(config.jinja_directories)
        self._env = Environment(loader=self._loader)
        self._env.filters.update(
            static=self.static_file_filter,
            S=self.static_file_filter,
            s=self.static_file_filter,
        )

        template_names = self._env.list_templates(filter_func=self._filter_template)
        self._template_files = set(['./' + tf for tf in template_names])
        self._ctx = self._config.context
        self._library = self._config.find_library()
        self._library_files = find_all_files(self._library) if self._library else []
        self._extra_files = []

    @contextfilter
    def static_file_filter(self, context, file_url, library=None):
        if file_url.startswith('/'):
            file_path = file_url.lstrip('/')
        else:
            file_dir = os.path.dirname(context.name)
            file_path = os.path.join(file_dir, file_url)

        file_path = file_path.replace('/./', '/').replace('//', '/')

        if library:
            return self._library_file(file_path, file_url, library)
        else:
            return self._root_file(file_path, file_url)

    def _root_file(self, file_path, file_url):
        target_path = os.path.join(self._config.target_dir, file_path)
        if os.path.exists(target_path):
            return file_url

        mapped_target_path = self.map_path(target_path)
        if os.path.exists(mapped_target_path):
            return self.map_path(file_url)
        raise HarrierKnownProblem('unable to find {} in build directory'.format(file_path))

    def _library_file(self, file_path, file_url, library):
        lib_path = self._find_lib_file(library, file_url)
        if lib_path is None:
            raise HarrierKnownProblem('unable to find {} in library directory, url: {}'.format(library, file_url))

        with open(lib_path, 'rb') as f:
            self._extra_files.append((file_path, f.read()))
        return file_url

    def _find_lib_file(self, file_path, file_url):
        file_path = re.sub('^libs?/', '', file_path)
        if file_path in self._library_files:
            return os.path.join(self._library, file_path)
        for bf in self._library_files:
            if bf.endswith(file_path) or (bf.startswith(file_path) and bf.endswith(file_url)):
                return os.path.join(self._library, bf)

    def check_ownership(self, file_path):
        return file_path in self._template_files

    def _filter_template(self, file_path):
        return any(fnmatch(file_path, m) for m in self._config.jinja_patterns)

    def convert_file(self, file_path):
        self._extra_files = []
        template = self._env.get_template(file_path)
        content_str = template.render(**self._ctx)
        yield None, content_str.encode('utf8')
        for name, content in self._extra_files:
            yield name, content
