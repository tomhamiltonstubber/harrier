import hashlib
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from time import time
from typing import Optional

import yaml
from jinja2 import Environment, FileSystemLoader
from misaka import HtmlRenderer, Markdown
from pydantic import BaseModel, validator

from .common import Config, HarrierProblem

FRONT_MATTER_REGEX = re.compile(r'^---[ \t]*(.*)\n---[ \t]*\n', re.S)
# extensions where we want to do anything except just copy the file to the output dir
OUTPUT_HTML = {'.html', '.md'}
MAYBE_RENDER = {'.xml'}
URI_NOT_ALLOWED = re.compile(r'[^a-zA-Z0-9_\-/.]')
DATE_REGEX = re.compile(r'(\d{4})-(\d{2})-(\d{2})-?(.*)')
URI_IS_TEMPLATE = re.compile('[{}]')
DEFAULT_TEMPLATE = 'main.jinja'

logger = logging.getLogger('harrier.build')


def build_som(config: Config):
    som_builder = BuildSOM(config)
    return som_builder()


def render(config: Config, som: dict, build_cache=None):
    start = time()
    dist_dir: Path = som['dist_dir']

    rndr = HtmlRenderer()
    md = Markdown(rndr)

    env = Environment(loader=FileSystemLoader([
        str(config.get_tmp_dir()),
        str(config.theme_dir / 'templates'),
    ]))
    checked_dirs = set()
    gen, copy = 0, 0
    for p in page_gen(som['pages']):
        outfile: Path = p['outfile'].resolve()
        out_dir = outfile.parent
        if out_dir not in checked_dirs:
            # this will raise an exception if somehow outfile is outside dis_dir
            out_dir.relative_to(dist_dir)
            out_dir.mkdir(exist_ok=True, parents=True)
            checked_dirs.add(out_dir)

        infile: Path = p['infile']
        if 'template' in p:
            template_file = p['template']
            try:
                if p['render']:
                    content_template = env.get_template(str(p['content_template']))
                    content = content_template.render(page=p, site=som)
                else:
                    content = p.pop('content')

                if infile.suffix == '.md':
                    content = md(content)

                if template_file:
                    template = env.get_template(template_file)
                    rendered = template.render(content=content, page=p, site=som)
                else:
                    rendered = content
            except Exception as e:
                logger.exception('%s: %s %s', infile, e.__class__.__name__, e)
                raise
            else:
                rendered_b = rendered.encode()
                if build_cache is not None:
                    out_hash = hashlib.md5(rendered_b).digest()
                    if build_cache.get(infile) == out_hash:
                        # file hasn't changed
                        continue
                    else:
                        build_cache[infile] = out_hash
                gen += 1
                outfile.write_bytes(rendered_b)
        else:
            if build_cache is not None:
                mtime = infile.stat().st_mtime
                if build_cache.get(infile) == mtime:
                    # file hasn't changed
                    continue
                else:
                    build_cache[infile] = mtime
            copy += 1
            shutil.copy(infile, outfile)
    logger.info('generated %d files, copied %d files in %0.2fs', gen, copy, time() - start)
    return build_cache


class BuildSOM:
    def __init__(self, config: Config):
        self.config = config
        self.tmp_dir = config.get_tmp_dir()
        self.all_defaults = {
            'template': DEFAULT_TEMPLATE,
            **config.defaults.pop('all', {})
        }
        self.path_defaults = [
            (re.compile(k), v)
            for k, v in config.defaults.items() if v
        ]
        self.files = 0
        self.template_files = 0

    def __call__(self):
        logger.info('Building "%s"...', self.config.pages_dir)
        start = time()
        pages = self.build_dir(walk(self.config.pages_dir))
        logger.info('Built site object model with %d files, %d files to render in %0.2fs',
                    self.files, self.template_files, time() - start)
        data = {}
        return {
            **self.config.dict(),
            **dict(pages=pages, data=data)
        }

    def build_dir(self, paths):
        d = {}
        for name, p in paths:
            if not isinstance(p, Path):
                d[name] = self.build_dir(p)
                continue
            try:
                d[name] = self.prep_file(p)
            except Exception as e:
                raise HarrierProblem(f'{p}: {e.__class__.__name__} {e}') from e

        return d

    def prep_file(self, p):
        html_output = p.suffix in OUTPUT_HTML
        data = {
            'infile': p,
            'content_template': self.tmp_dir / p.relative_to(self.config.pages_dir)
        }
        name = p.stem if html_output else p.name

        date_match = DATE_REGEX.match(name)
        if date_match:
            *date_args, new_name = date_match.groups()
            created = datetime(*map(int, date_args))
            name = new_name or name
        else:
            created = p.stat().st_mtime
        data.update(
            title=name,
            slug='' if html_output and p.stem == 'index' else slugify(name),
            created=created
        )

        data.update(self.all_defaults)
        for regex, defaults in self.path_defaults:
            if regex.match(str(p)):
                data.update(defaults)

        maybe_render = p.suffix in MAYBE_RENDER
        apply_jinja = False
        if html_output or maybe_render:
            fm_data, content = parse_front_matter(p.read_text())
            if html_output or fm_data:
                data['content'] = content
                fm_data and data.update(fm_data)
                apply_jinja = True

        uri = data.get('uri')
        if not uri:
            parents = str(p.parent.relative_to(self.config.pages_dir)).split('/')
            data['uri'] = '/' + '/'.join([slugify(p) for p in parents] + [data['slug']])
        elif URI_IS_TEMPLATE.search(uri):
            try:
                data['uri'] = slugify(uri.format(**data))
            except KeyError as e:
                raise KeyError(f'missing format variable "{e}" for "{uri}"')

        if data.get('output', True):
            outfile = self.config.dist_dir / data['uri'][1:]
            if html_output and outfile.suffix != '.html':
                outfile /= 'index.html'
            data['outfile'] = outfile

        fd = FileData(**data)
        final_data = fd.dict(exclude=set() if apply_jinja else {'template', 'render'})
        final_data['__file__'] = 1

        # TODO do his in render to
        if apply_jinja and fd.render:
            fd.content_template.parent.mkdir(parents=True, exist_ok=True)
            fd.content_template.write_text(final_data.pop('content'))
            final_data['content_template'] = str(fd.content_template.relative_to(self.tmp_dir))
        else:
            final_data.pop('content_template')

        # logger.debug('added %s apply_jinja: %s, outfile %s', p, apply_jinja, fd.outfile)
        self.files += 1
        if apply_jinja:
            self.template_files += 1

        return final_data


def walk(path: Path):
    for p in sorted(path.iterdir(), key=lambda p_: (p_.is_dir(), p_.name)):
        yield p.name, walk(p) if p.is_dir() else p.resolve()


def parse_front_matter(s):
    m = re.match(FRONT_MATTER_REGEX, s)
    if not m:
        return None, s.strip('\r\n')
    data = yaml.load(m.groups()[0]) or {}
    return data, s[m.end():].strip('\r\n')


class FileData(BaseModel):
    infile: Path
    content_template: Path
    title: str
    slug: str
    created: datetime
    uri: str
    template: Optional[str]
    render: bool = True
    outfile: Path = None

    @validator('uri')
    def validate_uri(cls, v):
        if not v.startswith('/'):
            raise ValueError(f'uri must start with a slash: "{v}')
        invalid = URI_NOT_ALLOWED.findall(v)
        if invalid:
            invalid = ', '.join(f'"{inv}"' for inv in invalid)
            raise ValueError(f'uri contains invalid characters: {invalid}')
        return v

    class Config:
        allow_extra = True


def slugify(title):
    name = title.replace(' ', '-').lower()
    name = URI_NOT_ALLOWED.sub('', name)
    name = re.sub('-{2,}', '-', name)
    return name.strip('_-')


def page_gen(d: dict):
    for v in d.values():
        if '__file__' in v:
            if v.get('outfile'):
                yield v
        else:
            yield from page_gen(v)
