"""The add-on's own YAML files: Home Assistant silently ignores a translation file it cannot
parse (the Configuration tab then shows raw option keys without help texts)."""
from pathlib import Path

import pytest
import yaml

ADDON = Path(__file__).resolve().parent.parent


def test_config_and_translations_parse_and_cover_every_option():
    config = yaml.safe_load((ADDON / 'config.yaml').read_text())
    options = set(config['schema'])
    for lang in ('en', 'de'):
        translation = yaml.safe_load((ADDON / 'translations' / f'{lang}.yaml').read_text())
        fields = translation['configuration']
        assert set(fields) == options, lang
        assert all(fields[o].get('name') for o in options), lang


@pytest.mark.parametrize('lang', ['en', 'de'])
def test_translation_values_are_plain_strings(lang):
    fields = yaml.safe_load((ADDON / 'translations' / f'{lang}.yaml').read_text())['configuration']
    for option, entry in fields.items():
        assert all(isinstance(v, str) for v in entry.values()), option
