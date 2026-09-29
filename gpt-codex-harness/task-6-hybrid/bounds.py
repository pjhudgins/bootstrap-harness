"""One explicit permission notation, shared by parent and child."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType


def covers(grant, target):
    """Canonical (path, subtree) tuples; containment respects path segments."""
    base, tree = grant
    path, subtree = target
    return (base == path and (tree or not subtree)) or (tree and path.startswith(base.rstrip('/') + '/'))


@dataclass(frozen=True, init=False)
class Bounds:
    mounts: object
    grants: object
    selectors: tuple

    def __init__(self, spec, mounts):
        if not isinstance(spec, dict) or set(spec) != {'fs', 'ledger'}:
            raise ValueError('Bounds require fs and ledger objects.')
        object.__setattr__(self, 'mounts', MappingProxyType({k: Path(v).absolute() for k, v in mounts.items()}))
        grants, selectors = {}, []
        for domain, actions in (('fs', ('read', 'write', 'execute')), ('ledger', ('read', 'write'))):
            if not isinstance(spec[domain], dict) or set(spec[domain]) != set(actions):
                raise ValueError(f'{domain} requires exactly {", ".join(actions)}.')
            for action in actions:
                values = spec[domain][action]
                if not isinstance(values, list) or len(values) > 16 or any(not isinstance(v, str) for v in values):
                    raise ValueError('Each bound is a list of up to 16 selectors; [] denies access.')
                selectors.append((domain, action, tuple(dict.fromkeys(values))))
                grants[domain, action] = tuple(self.selector(v, domain) for v in values)
        for write in grants['fs', 'write']:
            for execute in grants['fs', 'execute']:
                if covers(write, execute) or covers(execute, write):
                    raise ValueError('Filesystem write and execute bounds must not overlap.')
        object.__setattr__(self, 'grants', MappingProxyType(grants))
        object.__setattr__(self, 'selectors', tuple(selectors))

    def expand(self, value):
        if not isinstance(value, str) or not value or '\x00' in value:
            raise ValueError('Provide a path.')
        value = value.replace('\\', '/')
        match = re.match(r'^(nimoi|workspace|scripts):/(.*)$', value)
        if match:
            path = self.mounts[match[1]] / match[2]
        else:
            path = Path(value)
            if path.drive and not path.is_absolute():
                raise ValueError('Drive-relative paths are not allowed.')
            if not path.is_absolute():
                path = self.mounts['nimoi'] / path
        # No resolving through links: file tools validate the actual path separately.
        if any(p == '..' for p in value.split('/') if p) or value.startswith('//'):
            raise ValueError('Traversal and network paths are not allowed.')
        try:
            path.relative_to(self.mounts['nimoi'])
        except ValueError:
            raise ValueError('Path is outside NIMOI.') from None
        return path

    def selector(self, value, domain):
        if domain == 'fs':
            if not re.match(r'^(nimoi|workspace|scripts):/', value):
                raise ValueError('Filesystem bounds use nimoi:/, workspace:/ or scripts:/.')
            tail = value.split(':/', 1)[1]
            if any(p and (not re.fullmatch(r'[A-Za-z0-9._-]+', p) or p in ('.', '..')) for p in tail.split('/')):
                raise ValueError('Bounds use simple path segments; no globs or traversal.')
            return (self.expand(value).as_posix().casefold().rstrip('/'), value.endswith('/'))
        if value == '/':
            return ('', True)
        if not value or value.startswith('/') or '//' in value:
            raise ValueError('Ledger bounds use /, name, or prefix/.')
        if any(not re.fullmatch(r'[A-Za-z0-9._-]+', p) or p in ('.', '..') for p in value.rstrip('/').split('/')):
            raise ValueError('Ledger bounds use simple names, not globs or traversal.')
        return (value.rstrip('/'), value.endswith('/'))

    def allows(self, domain, action, value):
        path = self.expand(value).as_posix().casefold().rstrip('/') if domain == 'fs' else value
        return any((domain == 'ledger' and g == ('', True)) or covers(g, (path, False))
                   for g in self.grants[domain, action])

    def require(self, domain, action, value):
        if not self.allows(domain, action, value):
            raise ValueError(f'Outside {domain}.{action} bounds: {value}')

    def child(self, spec):
        child = Bounds(spec, self.mounts)
        for key, requested in child.grants.items():
            for grant in requested:
                if not any((key[0] == 'ledger' and parent == ('', True)) or covers(parent, grant)
                           for parent in self.grants[key]):
                    raise ValueError(f'Child {".".join(key)} exceeds parent bounds.')
        origins = child.selector('nimoi:/origins/', 'fs')
        if not any(covers(g, origins) for g in child.grants['fs', 'read']):
            raise ValueError('Child fs.read must cover nimoi:/origins/ for onboarding.')
        return child

    def describe(self):
        result = {'fs': {}, 'ledger': {}}
        for domain, action, values in self.selectors:
            result[domain][action] = list(values)
        return result


def root_bounds(nimoi, workspace, scripts, config_path=None):
    ceiling = Bounds({'fs': {'read': ['nimoi:/'], 'write': ['workspace:/'], 'execute': ['scripts:/']},
                   'ledger': {'read': ['/'], 'write': ['agent/']}},
                  {'nimoi': nimoi, 'workspace': workspace, 'scripts': scripts})
    path = Path(config_path) if config_path else Path(__file__).with_name('bounds.json')
    return ceiling.child(json.loads(path.read_text(encoding='utf-8')))
