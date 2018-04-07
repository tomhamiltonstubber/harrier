import hashlib
import logging
import tempfile
from itertools import product
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import BaseModel, validator
from yaml.error import YAMLError

from .common import HarrierProblem

logger = logging.getLogger('harrier.config')
CONFIG_FILE_TRIES = 'harrier', 'config', '_config'
CONFIG_FILE_TRIES = [Path(f'{name}.{ext}') for name, ext in product(CONFIG_FILE_TRIES, ['yml', 'yaml'])]


class WebpackConfig(BaseModel):
    cli: Path = 'node_modules/.bin/webpack-cli'
    entry: Path = 'js/index.js'
    output_path: Path = 'theme'
    output_filename = 'main.js'
    config: Path = None
    run: bool = True

    class Config:
        validate_all = True


class Config(BaseModel):
    source_dir: Path
    pages_dir: Path = 'pages'
    theme_dir: Path = 'theme'
    data_dir: Path = 'data'
    dist_dir: Path = 'dist'
    dist_dir_sass: Path = 'theme'
    dist_dir_assets: Path = 'theme/assets'
    tmp_dir: Path = None

    download: Dict[str, Any] = {}
    download_aliases: Dict[str, str] = {}

    defaults: Dict[str, Dict[str, Any]] = {}

    webpack: WebpackConfig = WebpackConfig()

    @validator('source_dir')
    def resolve_source_dir(cls, v):
        return v.resolve(strict=True)

    @validator('pages_dir', 'theme_dir', 'data_dir')
    def resolve_relative_paths(cls, v, values, **kwargs):
        return (values['source_dir'] / v).resolve()

    @validator('pages_dir', 'theme_dir')
    def is_dir(cls, v, field, **kwargs):
        if not v.is_dir():
            raise ValueError(f'{field.name} "{v}" is not a directory')
        elif not v.exists():
            raise ValueError(f'{field.name} directory "{v}" does not exist')
        else:
            return v

    @validator('dist_dir')
    def check_dist_dir(cls, v):
        p = Path(v).resolve()
        if not p.parent.exists():
            raise ValueError(f'dist_dir "{p}" parent directory does not exist')
        elif p.exists() and not p.is_dir():
            raise ValueError(f'dist_dir "{p}" is not a directory')
        else:
            return p

    @validator('theme_dir')
    def theme_templates(cls, v):
        if (v / 'templates').exists():
            return v
        else:
            raise ValueError(f'theme directory "{v}" does not contain a "templates" directory')

    @validator('webpack')
    def validate_webpack(cls, v, *, values, **kwargs):
        webpack: WebpackConfig = v
        if not webpack.run:
            return webpack

        if {'source_dir', 'theme_dir', 'source_dir', 'dist_dir'} - values.keys():
            # some values are missing, can't validate properly
            return webpack

        if not webpack.cli.is_absolute():
            webpack.cli = (values['source_dir'] / webpack.cli).resolve()

        if not webpack.cli.exists():
            logger.warning('webpack cli path "%s" does not exist, not running webpack', webpack.cli)
            webpack.run = False

        webpack.entry = (values['theme_dir'] / webpack.entry).resolve()
        if not webpack.entry.exists():
            logger.warning('webpack entry point "%s" does not exist, not running webpack', webpack.entry)
            webpack.run = False

        if webpack.config:
            webpack.config = (values['source_dir'] / webpack.config).resolve()
            if not webpack.config.exists():
                raise ValueError(f'webpack config set but does not exist "{webpack.config}", not running webpack')

        webpack.output_path = (values['dist_dir'] / webpack.output_path).resolve()
        return webpack

    def get_tmp_dir(self) -> Path:
        if self.tmp_dir:
            return self.tmp_dir
        else:
            path_hash = hashlib.md5(b'%s' % self.source_dir).hexdigest()
            return Path(tempfile.gettempdir()) / f'harrier-{path_hash}'

    class Config:
        allow_extra = True
        validate_all = True


def load_config_file(config_path: Path):
    try:
        raw_config = yaml.load(config_path.read_text()) or {}
    except YAMLError as e:
        logger.error('%s: %s', e.__class__.__name__, e)
        raise HarrierProblem(f'error loading "{config_path}"') from e
    raw_config.setdefault('source_dir', config_path.parent)
    return raw_config


def get_config(path) -> Config:
    config_path = Path(path)
    if config_path.is_file():
        config = load_config_file(config_path)
    else:
        try:
            config_path = next(config_path / f for f in CONFIG_FILE_TRIES if (config_path / f).exists())
        except StopIteration:
            config = {'source_dir': config_path}
        else:
            config = load_config_file(config_path)

    return Config(**config)
