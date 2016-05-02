import logging

import pytest
from harrier.build import build
from harrier.common import HarrierProblem
from harrier.config import Config

from tests.conftest import gettree, mktree


def test_build_scss(tmpworkdir):
    mktree(tmpworkdir, {'styles.scss': 'a { b { color: blue; } }'})
    config = Config()
    config.setup()
    build(config)
    assert gettree(tmpworkdir.join('build')) == {
        'styles.css': 'a b {\n  color: blue; }\n'
    }


def test_build_sass(tmpworkdir):
    mktree(tmpworkdir, {'styles.sass': 'a\n  b\n    color: blue;'})
    config = Config()
    config.setup()
    build(config)
    assert gettree(tmpworkdir.join('build')) == {
        'styles.css': 'a b {\n  color: blue; }\n'
    }


def test_sass_exclude(tmpworkdir):
    mktree(tmpworkdir, {
        'src': {
            '_foo.scss': '$primary-colour: #016997;',
            'bar.scss': """\
@import 'foo';
body {
  color: $primary-colour;
}"""
        },
        'harrier.yml': '\nroot: src'
    })
    config = Config()
    config.setup()
    build(config)
    assert gettree(tmpworkdir.join('build')) == {'bar.css': 'body {\n  color: #016997; }\n'}


def test_sass_precision_8(tmpworkdir):
    mktree(tmpworkdir, {'styles.scss': 'a { width: (100 / 6)px}'})
    config = Config()
    config.setup()
    build(config)
    assert gettree(tmpworkdir.join('build')) == {
        'styles.css': 'a {\n  width: 16.66666667 px; }\n'
    }


def test_sass_precision_5(tmpworkdir):
    mktree(tmpworkdir, {'styles.scss': 'a { width: (100 / 6)px}'})
    config = Config(config_dict={'sass': {'precision': 5}})
    config.setup()
    build(config)
    assert gettree(tmpworkdir.join('build')) == {
        'styles.css': 'a {\n  width: 16.66667 px; }\n'
    }


def test_build_scss_bad(tmpworkdir, logcap):
    logcap.set_level(logging.ERROR)
    mktree(tmpworkdir, {'styles.scss': 'x = 42'})
    config = Config()
    config.setup()
    with pytest.raises(HarrierProblem) as excinfo:
        build(config)
    assert excinfo.value.args[0] == 'Error compiling SASS'
    assert logcap.log == """Error: Invalid CSS after "x": expected "{", was "= 42"
        on line 1 of styles.scss
>> x = 42
   -^
"""